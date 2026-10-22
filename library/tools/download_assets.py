#!/usr/bin/env python3
"""Scan / download / update de recursos 3D (apenas stdlib).

Modos:
  scan      resolve metadados nas APIs oficiais e lista ficheiros candidatos e
            hosts de download; NÃO descarrega ficheiros de modelos.
  download  descarrega entradas aprovadas (licença CC0-1.0 verificada, host
            confirmado, seleção inequívoca) para library/assets/<categoria>/<id>/.
  update    compara com o que foi descarregado e só substitui o que mudou na
            origem e não foi modificado localmente.

Segurança: HTTPS obrigatório, lista de hosts permitidos por fonte, redirecionamentos
verificados salto a salto, limites de tamanho, ZIP seguro, caminhos de destino
calculados apenas a partir de categorias permitidas.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Callable, Dict, List, Optional, Set

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (  # noqa: E402
    ALLOWED_EXTENSIONS, CATEGORIES, DEFAULT_MAX_FILES_PER_ASSET, DEFAULT_MAX_FILE_MB,
    LOCAL_ID_RE, SOURCE_ASSET_ID_RE, LibraryError, UnsafePathError, asset_dir,
    formats_from_files, iter_asset_dirs, license_gate, load_manifests, load_sources,
    md5_file, read_asset_metadata, safe_join, safe_relative_path, save_json,
    sha256_file, utcnow, validate_entry_shape,
)

TOOL_VERSION = "0.3.2"
USER_AGENT = "Aurelhopedro-3D-library/0.1 (+https://github.com/Aurelhopedro/3D)"
URL_KEYS = ("url", "downloadLink", "rawLink")
RESERVED_ROOT_NAMES = frozenset({"metadata.json", "license.txt"})
HARD_CAPS = {"max_downloads": 20, "max_total_mb": 500, "max_file_mb": 200}
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED_MB = 300

# Regex para commit SHA completo (40 hex) — NÃO aceita curto nem branch names
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


# --------------------------------------------------------------------------
# Erros
# --------------------------------------------------------------------------
class Blocked(LibraryError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class IncompleteDownload(LibraryError):
    pass


class TooLarge(LibraryError):
    pass


# --------------------------------------------------------------------------
# HTTP seguro
# --------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


def _local_test_allowed() -> bool:
    return os.environ.get("LIB_TEST_ALLOW_LOCAL") == "1"


def check_url(url: str, allowed_hosts: List[str]) -> str:
    """Valida esquema e host. Devolve o host em minúsculas."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if not host:
        raise Blocked("blocked_invalid_url", url[:120])
    if p.scheme == "https":
        pass
    elif p.scheme == "http" and host in ("127.0.0.1", "localhost") and _local_test_allowed():
        pass
    else:
        raise Blocked("blocked_url_scheme", p.scheme)
    allowed = [h.lower() for h in allowed_hosts]
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise Blocked("blocked_host_unconfirmed", host)
    return host


def open_url(url: str, allowed_hosts: List[str], timeout: float = 30.0, max_redirects: int = 3):
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(max_redirects + 1):
        check_url(current, allowed_hosts)
        req = urllib.request.Request(current, headers={"User-Agent": USER_AGENT})
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                loc = exc.headers.get("Location")
                if not loc:
                    raise
                current = urllib.parse.urljoin(current, loc)
                continue
            raise
    raise Blocked("blocked_too_many_redirects", url[:120])


def fetch_json(url: str, allowed_hosts: List[str], max_bytes: int = 20 * 1024 * 1024) -> Any:
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            with open_url(url, allowed_hosts) as resp:
                raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise TooLarge("resposta JSON demasiado grande")
            return json.loads(raw.decode("utf-8"))
        except (Blocked, TooLarge):
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 4))
    raise LibraryError(f"falha de rede/JSON: {last}")


def fetch_file(url: str, dest: pathlib.Path, allowed_hosts: List[str], max_bytes: int,
               expected_size: Optional[int] = None, retries: int = 2) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with open_url(url, allowed_hosts) as resp:
                cl_raw = resp.headers.get("Content-Length")
                cl = int(cl_raw) if cl_raw and cl_raw.isdigit() else None
                if cl is not None and cl > max_bytes:
                    raise TooLarge(f"Content-Length {cl} > limite {max_bytes}")
                written = 0
                with open(part, "wb") as fh:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise TooLarge(f"download excedeu o limite {max_bytes}")
                        fh.write(chunk)
            if cl is not None and written != cl:
                raise IncompleteDownload(f"recebidos {written} de {cl} bytes")
            if expected_size is not None and written != expected_size:
                raise IncompleteDownload(f"tamanho {written} != esperado {expected_size}")
            part.replace(dest)
            return written
        except (Blocked, TooLarge):
            part.unlink(missing_ok=True)
            raise
        except (IncompleteDownload, urllib.error.URLError, TimeoutError, OSError, ConnectionError,
                http_incomplete()) as exc:
            last = exc
            part.unlink(missing_ok=True)
            time.sleep(min(2 ** attempt, 4))
    if isinstance(last, IncompleteDownload):
        raise last
    raise LibraryError(f"falha de rede: {last}")


def http_incomplete():
    import http.client
    return http.client.IncompleteRead


# --------------------------------------------------------------------------
# Candidatos de download (percorrimento genérico de JSON)
# --------------------------------------------------------------------------
def collect_candidates(node: Any, keypath: tuple = ()) -> List[Dict[str, Any]]:
    """Encontra dicts com um URL http(s) em qualquer profundidade."""
    out: List[Dict[str, Any]] = []
    if isinstance(node, dict):
        url = None
        for k in URL_KEYS:
            v = node.get(k)
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                url = v
                break
        if url:
            size = node.get("size")
            md5 = node.get("md5")
            name = node.get("fileName") or node.get("filename") or node.get("name")
            out.append({
                "url": url,
                "keypath": list(keypath),
                "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                "md5": md5.lower() if isinstance(md5, str) and len(md5) == 32 else None,
                "name": name if isinstance(name, str) else None,
            })
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                out.extend(collect_candidates(v, keypath + (str(k),)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(collect_candidates(v, keypath + (str(i),)))
    seen = set()
    unique = []
    for c in out:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        unique.append(c)
    return unique


def candidate_rel_path(c: Dict[str, Any]) -> str:
    if c.get("name"):
        try:
            return str(safe_relative_path(c["name"]))
        except UnsafePathError:
            pass
    last = c["keypath"][-1] if c["keypath"] else ""
    if ("/" in last or "." in last) and not last.lower().startswith("http"):
        try:
            return str(safe_relative_path(last))
        except UnsafePathError:
            pass
    base = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(c["url"]).path))
    return base or "download.bin"


def _haystack(c: Dict[str, Any]) -> str:
    return (" ".join(c["keypath"]) + " " + c["url"]).lower()


def select_candidates(cands: List[Dict[str, Any]], formats_wanted: List[str],
                      selector: List[str]) -> List[Dict[str, Any]]:
    out = cands
    if formats_wanted:
        fw = [f.lower() for f in formats_wanted]
        out = [c for c in out if any(f in _haystack(c) for f in fw)]
    if selector:
        sel = [s.lower() for s in selector]
        out = [c for c in out if all(s in _haystack(c) for s in sel)]
    return out


# --------------------------------------------------------------------------
# Resolvers (um por fonte)
# --------------------------------------------------------------------------

def _find_parent_dicts_of_urls(node: Any, target_urls: Set[str]) -> List[Any]:
    """Encontra os dicts da API que contêm diretamente um dos URLs alvo."""
    found: List[Any] = []
    if isinstance(node, dict):
        for k in URL_KEYS:
            v = node.get(k)
            if isinstance(v, str) and v in target_urls:
                found.append(node)
                return found
        for v in node.values():
            found.extend(_find_parent_dicts_of_urls(v, target_urls))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_parent_dicts_of_urls(item, target_urls))
    return found


def _polyhaven_expand_includes(entry_dicts: List[Any]) -> List[Dict[str, Any]]:
    """Extrai dependências da chave 'include' de uma lista de dicts da API Poly Haven."""
    extras: List[Dict[str, Any]] = []
    seen_urls: set = set()

    for entry_dict in entry_dicts:
        if not isinstance(entry_dict, dict):
            continue
        inc = entry_dict.get("include")
        if not isinstance(inc, dict):
            continue
        for rel_path, info in inc.items():
            if not isinstance(info, dict):
                continue
            url = None
            for k in URL_KEYS:
                v = info.get(k)
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    url = v
                    break
            if not url or url in seen_urls:
                continue
            try:
                safe_relative_path(rel_path)
            except UnsafePathError:
                continue
            seen_urls.add(url)
            size = info.get("size")
            md5 = info.get("md5")
            extras.append({
                "url": url,
                "keypath": ["include", rel_path],
                "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                "md5": md5.lower() if isinstance(md5, str) and len(md5) == 32 else None,
                "name": rel_path,
                "is_dependency": True,
            })

    return extras


def resolve_polyhaven(src: Dict[str, Any], entry: Dict[str, Any], fetch: Callable[[str], Any]):
    """Resolve candidatos da Poly Haven, incluindo dependências glTF."""
    api = src["api_base"].rstrip("/")
    data = fetch(f"{api}/files/{urllib.parse.quote(entry['source_asset_id'])}")

    wanted = [w.lower() for w in (entry.get("formats_wanted") or [])]
    if wanted and isinstance(data, dict):
        node = {k: v for k, v in data.items() if k.lower() in wanted}
        if not node:
            node = data
    else:
        node = data

    all_cands = collect_candidates(node)
    selector = entry.get("selector") or []
    formats_wanted = entry.get("formats_wanted") or []
    principal = select_candidates(all_cands, formats_wanted, selector)

    if not principal:
        return all_cands

    selected_urls = {c["url"] for c in principal}
    parent_dicts = _find_parent_dicts_of_urls(node, selected_urls)
    dep_cands = _polyhaven_expand_includes(parent_dicts)
    dep_cands = [c for c in dep_cands if c["url"] not in selected_urls]

    return principal + dep_cands


def resolve_ambientcg(src: Dict[str, Any], entry: Dict[str, Any], fetch: Callable[[str], Any]):
    api = src["api_base"].rstrip("/")
    q = urllib.parse.urlencode({"id": entry["source_asset_id"], "include": "downloads"})
    data = fetch(f"{api}?{q}")
    return collect_candidates(data)


def resolve_github_zip(src: Dict[str, Any], entry: Dict[str, Any],
                       fetch: Callable[[str], Any]) -> List[Dict[str, Any]]:
    """Resolver para repositórios GitHub públicos — descarrega o ZIP de um commit fixo.

    A entrada no manifesto deve conter:
      github_repo   : "owner/repo"        (ex: "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0")
      github_commit : SHA completo 40 hex  (imutável — nunca um branch name ou tag)
      github_files  : ["path/in/repo"]    (ficheiros a extrair do ZIP; [] = todos os permitidos)

    Segurança:
      - Commit SHA de 40 hex obrigatório — rejeita branch names, tags, SHAs curtos.
      - ZIP descarregado de codeload.github.com (host imutável para commits SHA).
      - Extracção com remoção de prefixo e filtragem por github_files.
      - Nenhum código do repositório externo é executado.
    """
    repo = entry.get("github_repo", "")
    commit = entry.get("github_commit", "")
    files_wanted: List[str] = entry.get("github_files") or []

    if not repo or "/" not in repo:
        raise Blocked("blocked_github_no_repo", repr(repo))
    if not commit or not COMMIT_SHA_RE.match(commit):
        raise Blocked("blocked_github_no_commit",
                      f"commit deve ser SHA de 40 hex; recebido: {commit!r}")

    zip_url = f"https://codeload.github.com/{repo}/zip/{commit}"
    zip_name = repo.split("/")[-1] + f"_{commit[:8]}.zip"

    return [{
        "url": zip_url,
        "keypath": ["github_zip", repo, commit],
        "size": None,
        "md5": None,
        "name": zip_name,
        "_github_repo": repo,
        "_github_commit": commit,
        "_github_files": files_wanted,
    }]


def resolve_zip_url(src: Dict[str, Any], entry: Dict[str, Any],
                    fetch: Callable[[str], Any]) -> List[Dict[str, Any]]:
    """Resolver para pacotes ZIP com URL directa (ex: MakeHuman Community).

    A entrada no manifesto deve conter:
      zip_url    : URL HTTPS oficial do pacote
      zip_sha256 : SHA-256 do arquivo (recomendado; None = sem verificação de integridade)

    Em modo de testes locais (LIB_TEST_ALLOW_LOCAL=1), aceita também http://127.0.0.1.
    """
    zip_url = entry.get("zip_url", "")
    if not zip_url:
        raise Blocked("blocked_zip_url_missing_or_insecure", "zip_url ausente")

    p = urllib.parse.urlparse(zip_url)
    is_https = p.scheme == "https"
    is_local_test = (p.scheme == "http"
                     and (p.hostname or "") in ("127.0.0.1", "localhost")
                     and _local_test_allowed())
    if not is_https and not is_local_test:
        raise Blocked("blocked_zip_url_missing_or_insecure", repr(zip_url)[:80])

    zip_name = os.path.basename(p.path) or "asset.zip"
    if not zip_name.lower().endswith(".zip"):
        zip_name += ".zip"

    return [{
        "url": zip_url,
        "keypath": ["zip_url", zip_url],
        "size": None,
        "md5": None,
        "name": zip_name,
        "_zip_sha256": entry.get("zip_sha256"),
    }]


RESOLVERS: Dict[str, Callable] = {
    "polyhaven": resolve_polyhaven,
    "ambientcg": resolve_ambientcg,
    "github_zip": resolve_github_zip,
    "zip_url": resolve_zip_url,
}


# --------------------------------------------------------------------------
# ZIP seguro
# --------------------------------------------------------------------------
def _github_strip_prefix(rel: str, repo: str, commit: str) -> Optional[str]:
    """Remove o prefixo automático que o GitHub injeta em ZIPs de commit.

    O GitHub gera: <nome-do-repo>-<commit[:7]>/ficheiro
    Exemplo: KayKit-Character-Pack-Adventures-1.0-672074b/Knight.glb → Knight.glb

    Fallback: se o prefixo esperado não coincidir, remove qualquer primeiro segmento.
    Devolve "" para entradas de directório raiz (a ignorar) e None se impossível.
    """
    repo_name = repo.split("/")[-1]
    commit_short = commit[:7]
    prefix = f"{repo_name}-{commit_short}/"
    if rel.startswith(prefix):
        return rel[len(prefix):]
    parts = rel.split("/", 1)
    if len(parts) == 2:
        return parts[1]
    return None


def _github_file_wanted(inner_path: str, files_wanted: List[str]) -> bool:
    """Verifica se inner_path está na lista de ficheiros pretendidos.

    files_wanted vazio → todos aceites.
    Comparação case-insensitive; aceita coincidência no sufixo do caminho.
    """
    if not files_wanted:
        return True
    ip_lower = inner_path.lower()
    for wanted in files_wanted:
        w_lower = wanted.lower()
        if ip_lower == w_lower or ip_lower.endswith("/" + w_lower):
            return True
    return False


def extract_zip(zpath: pathlib.Path, dest: pathlib.Path, max_uncompressed: int,
                github_repo: str = "", github_commit: str = "",
                github_files: Optional[List[str]] = None) -> Dict[str, Any]:
    """Extrai ZIP com protecções de segurança completas.

    Quando github_repo e github_commit são fornecidos:
      - Remove o prefixo automático do GitHub (<repo>-<sha7>/)
      - Filtra por github_files (se não vazio)

    Protecções: ZIP Slip, symlinks, zip bombs, extensões não permitidas,
    caminhos absolutos, ZIP corrompido, e nomes reservados na raiz
    (metadata.json / LICENSE.txt pertencem ao gestor e nunca vêm do ZIP).
    """
    skipped: List[str] = []
    extracted: List[str] = []
    is_github = bool(github_repo and github_commit)
    files_wanted: List[str] = github_files or []

    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile as exc:
        raise Blocked("failed_corrupt_zip", str(exc)) from exc
    with zf:
        if zf.testzip() is not None:
            raise Blocked("failed_corrupt_zip", "CRC inválido")
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise Blocked("blocked_zip_too_many_entries", str(len(infos)))
        total_declared = 0
        for info in infos:
            try:
                safe_relative_path(info.filename.rstrip("/") or "placeholder")
            except UnsafePathError as exc:
                raise Blocked("blocked_zip_unsafe_path", f"{info.filename!r}: {exc}") from exc
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise Blocked("blocked_zip_symlink", info.filename)
            total_declared += info.file_size
        if total_declared > max_uncompressed:
            raise Blocked("blocked_zip_too_large", f"{total_declared} bytes")

        written_total = 0
        for info in infos:
            if info.is_dir():
                continue
            rel = info.filename

            if is_github:
                inner = _github_strip_prefix(rel, github_repo, github_commit)
                if inner is None or inner == "":
                    skipped.append(rel)
                    continue
                if not _github_file_wanted(inner, files_wanted):
                    skipped.append(rel)
                    continue
                rel = inner

            # Nomes reservados na raiz: o gestor escreve os seus próprios
            # metadata.json e LICENSE.txt; um ZIP nunca os pode fornecer/sobrescrever.
            if "/" not in rel and rel.lower() in RESERVED_ROOT_NAMES:
                skipped.append(rel)
                continue

            ext = pathlib.PurePosixPath(rel).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                skipped.append(rel)
                continue
            try:
                target = safe_join(dest, rel)
            except UnsafePathError as exc:
                raise Blocked("blocked_zip_unsafe_path", f"{rel!r}: {exc}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src_fh, open(target, "wb") as out_fh:
                while True:
                    chunk = src_fh.read(256 * 1024)
                    if not chunk:
                        break
                    written_total += len(chunk)
                    if written_total > max_uncompressed:
                        raise Blocked("blocked_zip_too_large", "excedeu durante a extração")
                    out_fh.write(chunk)
            extracted.append(rel)
    return {"extracted": extracted, "skipped_extensions": skipped}


# --------------------------------------------------------------------------
# Contexto e processamento
# --------------------------------------------------------------------------
class Context:
    def __init__(self, root: pathlib.Path, mode: str, max_downloads: int, max_total_mb: int,
                 max_file_mb: int, max_files_per_asset: int,
                 fetch_json_fn: Optional[Callable[[str, List[str]], Any]] = None,
                 fetch_file_fn: Optional[Callable[..., int]] = None):
        self.root = root
        self.mode = mode
        self.max_downloads = min(max_downloads, HARD_CAPS["max_downloads"])
        self.max_total_bytes = min(max_total_mb, HARD_CAPS["max_total_mb"]) * 1024 * 1024
        self.max_file_bytes = min(max_file_mb, HARD_CAPS["max_file_mb"]) * 1024 * 1024
        self.max_files_per_asset = max_files_per_asset
        self.fetch_json = fetch_json_fn or fetch_json
        self.fetch_file = fetch_file_fn or fetch_file
        self.downloaded_assets = 0
        self.total_bytes = 0
        self.sources = load_sources(root)
        self.fingerprints = self._existing_fingerprints()

    def _existing_fingerprints(self) -> Dict[str, str]:
        fps: Dict[str, str] = {}
        for _cat, adir in iter_asset_dirs(self.root):
            meta = read_asset_metadata(adir)
            if meta and meta.get("fingerprint") and meta.get("download_status") == "downloaded":
                fps[meta["fingerprint"]] = meta.get("id", adir.name)
        return fps


def fingerprint_of(files: List[Dict[str, Any]]) -> str:
    import hashlib
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["path"]):
        h.update(f"{f['path']}:{f['sha256']}\n".encode())
    return h.hexdigest()


def license_notice(entry: Dict[str, Any], meta_date: str) -> str:
    return (
        f"Licença: {entry.get('license')} (Creative Commons Zero v1.0 Universal)\n"
        "Texto legal: https://creativecommons.org/publicdomain/zero/1.0/\n"
        f"Evidência da verificação: {entry.get('license_evidence_url')}\n"
        f"Verificada em: {entry.get('license_verified_on')}\n"
        f"Fonte: {entry.get('source_url') or entry.get('source')}\n"
        f"Autoria: {entry.get('author') or 'não registada pela biblioteca (consultar a fonte)'}\n"
        f"Obrigações de atribuição: {entry.get('attribution') or 'nenhuma registada'}\n"
        f"Gerado pela biblioteca em: {meta_date}\n"
        "\nNota: este ficheiro é um aviso gerado automaticamente. O texto integral da licença\n"
        "não foi descarregado; consultar o URL do texto legal acima.\n"
    )


def _result(entry: Dict[str, Any], status: str, **extra: Any) -> Dict[str, Any]:
    r = {"id": entry.get("id"), "category": entry.get("category"),
         "source": entry.get("source"), "status": status}
    r.update(extra)
    return r


def _locally_modified(adir: pathlib.Path, meta: Dict[str, Any]) -> bool:
    for f in meta.get("files", []):
        try:
            p = safe_join(adir, f["path"])
        except UnsafePathError:
            return True
        if not p.is_file() or sha256_file(p) != f.get("sha256"):
            return True
    return False


def _source_files_changed(meta: Dict[str, Any], cands: List[Dict[str, Any]]) -> Optional[bool]:
    old = {(f.get("md5"), f.get("size")) for f in meta.get("source_files", [])}
    new = {(c.get("md5"), c.get("size")) for c in cands}
    if all(m is None and s is None for m, s in old | new):
        return None
    return old != new


def process_asset(ctx: Context, entry: Dict[str, Any], asset_id_filter: Optional[str]) -> Dict[str, Any]:
    problems = validate_entry_shape(entry)
    if problems:
        return _result(entry, "invalid_manifest_entry", problems=problems)

    ok, reason = license_gate(entry, ctx.sources)
    if not ok:
        return _result(entry, reason)

    src = ctx.sources[entry["source"]]
    resolver_name = src.get("resolver")
    if resolver_name == "manual_pending":
        return _result(entry, "manual_pending", detail="sem API/URL de download documentado")
    resolver = RESOLVERS.get(resolver_name)
    if resolver is None:
        return _result(entry, "blocked_no_resolver", detail=str(resolver_name))
    sid = entry.get("source_asset_id")
    if not isinstance(sid, str) or not SOURCE_ASSET_ID_RE.match(sid):
        return _result(entry, "blocked_missing_source_asset_id")

    adir = asset_dir(ctx.root, entry["category"], entry["id"])
    meta = read_asset_metadata(adir)
    already = bool(meta and meta.get("download_status") == "downloaded")

    if ctx.mode == "download" and already:
        return _result(entry, "skipped_already_downloaded")
    if ctx.mode == "update" and not already:
        return _result(entry, "skipped_not_downloaded")

    api_hosts = src.get("api_hosts", [])
    try:
        cands = resolver(src, entry, lambda u: ctx.fetch_json(u, api_hosts))
    except Blocked as exc:
        return _result(entry, exc.code, detail=exc.detail)
    except Exception as exc:  # noqa: BLE001
        return _result(entry, "failed_resolve", error=str(exc)[:300])

    principal_cands = [c for c in cands if not c.get("is_dependency")]
    dep_cands = [c for c in cands if c.get("is_dependency")]

    selected_principal = select_candidates(
        principal_cands, entry.get("formats_wanted") or [], entry.get("selector") or []
    )

    selected = selected_principal + dep_cands if selected_principal else []

    all_for_report = cands
    allowed_hosts = list(src.get("allowed_download_hosts", []))
    hosts_seen = sorted({(urllib.parse.urlparse(c["url"]).hostname or "").lower() for c in all_for_report})
    hosts_unconfirmed = [h for h in hosts_seen
                         if not any(h == a.lower() or h.endswith("." + a.lower()) for a in allowed_hosts)]
    report_cands = [{"url": c["url"], "keypath": c["keypath"], "size": c["size"], "md5": c["md5"],
                     "is_dependency": c.get("is_dependency", False)}
                    for c in all_for_report[:50]]
    common_info = {"candidates_total": len(all_for_report), "candidates_selected": len(selected),
                   "candidates": report_cands, "hosts_observed": hosts_seen,
                   "hosts_unconfirmed": hosts_unconfirmed}

    if ctx.mode == "scan":
        return _result(entry, "scanned", **common_info)

    if not selected:
        return _result(entry, "no_candidates_selected", **common_info)
    if len(selected) > ctx.max_files_per_asset:
        return _result(entry, "blocked_ambiguous_selection",
                       detail=f"{len(selected)} ficheiros; refinar 'selector'", **common_info)

    if ctx.mode == "update":
        changed = _source_files_changed(meta or {}, selected)
        if changed is None:
            return _result(entry, "update_check_inconclusive",
                           detail="a fonte não fornece md5/tamanho para comparar")
        if not changed:
            return _result(entry, "up_to_date")
        if _locally_modified(adir, meta or {}):
            return _result(entry, "update_available_local_modified_skip")

    if ctx.downloaded_assets >= ctx.max_downloads:
        return _result(entry, "skipped_download_limit")

    try:
        outcome = _download_asset(ctx, entry, src, selected, allowed_hosts, adir)
    except Blocked as exc:
        return _result(entry, exc.code, detail=exc.detail, **common_info)
    except TooLarge as exc:
        return _result(entry, "blocked_size_limit", detail=str(exc))
    except IncompleteDownload as exc:
        return _result(entry, "failed_incomplete_download", detail=str(exc))
    except LibraryError as exc:
        return _result(entry, "failed_download", detail=str(exc)[:300])
    if outcome.get("duplicate_of"):
        return _result(entry, "skipped_duplicate_content", duplicate_of=outcome["duplicate_of"])
    ctx.downloaded_assets += 1
    return _result(entry, "downloaded", files=outcome["file_count"], bytes=outcome["bytes"],
                   skipped_extensions=outcome["skipped_extensions"])


def _download_asset(ctx: Context, entry: Dict[str, Any], src: Dict[str, Any],
                    selected: List[Dict[str, Any]], allowed_hosts: List[str],
                    adir: pathlib.Path) -> Dict[str, Any]:
    staging_root = (ctx.root / ".staging").resolve()
    stage = staging_root / f"{entry['id']}-{os.getpid()}"
    asset_stage = stage / "asset"
    try:
        asset_stage.mkdir(parents=True, exist_ok=True)
        seen_rel: set = set()
        source_files: List[Dict[str, Any]] = []
        skipped_ext: List[str] = []
        bytes_total = 0
        max_uncompressed = MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024

        for c in selected:
            rel = candidate_rel_path(c)
            if rel.lower() in seen_rel:
                raise Blocked("blocked_duplicate_path", rel)
            seen_rel.add(rel.lower())
            if "/" not in rel and rel.lower() in RESERVED_ROOT_NAMES:
                skipped_ext.append(rel)
                continue
            ext = pathlib.PurePosixPath(rel).suffix.lower()
            is_zip = ext == ".zip"
            if not is_zip and ext not in ALLOWED_EXTENSIONS:
                skipped_ext.append(rel)
                continue
            target = safe_join(asset_stage, rel)
            remaining = ctx.max_total_bytes - ctx.total_bytes - bytes_total
            if remaining <= 0:
                raise Blocked("blocked_total_size_limit")
            n = ctx.fetch_file(c["url"], target, allowed_hosts,
                               min(ctx.max_file_bytes, remaining), c.get("size"))
            bytes_total += n
            if c.get("md5") and md5_file(target) != c["md5"]:
                raise Blocked("failed_checksum", f"md5 diferente em {rel}")

            if is_zip and c.get("_zip_sha256"):
                actual = sha256_file(target)
                if actual != c["_zip_sha256"]:
                    raise Blocked("failed_checksum",
                                  f"sha256 do ZIP diferente: esperado {c['_zip_sha256']}, obtido {actual}")

            source_files.append({"url": c["url"], "md5": c.get("md5"), "size": c.get("size"),
                                 "sha256": sha256_file(target), "path": rel})
            if is_zip:
                dest_dir = target.parent
                gh_repo = c.get("_github_repo", "")
                gh_commit = c.get("_github_commit", "")
                gh_files = c.get("_github_files") or []
                info = extract_zip(target, dest_dir, max_uncompressed,
                                   github_repo=gh_repo,
                                   github_commit=gh_commit,
                                   github_files=gh_files)
                skipped_ext.extend(info["skipped_extensions"])
                target.unlink()

        files: List[Dict[str, Any]] = []
        for p in sorted(asset_stage.rglob("*")):
            if not p.is_file():
                continue
            rp = p.relative_to(asset_stage).as_posix()
            files.append({"path": rp, "size": p.stat().st_size, "sha256": sha256_file(p)})
        if not files:
            raise Blocked("failed_no_valid_files")

        fp = fingerprint_of(files)
        dup = ctx.fingerprints.get(fp)
        if dup and dup != entry["id"]:
            return {"duplicate_of": dup}

        now = utcnow()
        metadata = {
            "id": entry["id"],
            "name": entry.get("name"),
            "category": entry["category"],
            "subcategory": entry.get("subcategory"),
            "source": entry["source"],
            "source_asset_id": entry.get("source_asset_id"),
            "source_url": entry.get("source_url"),
            "author": entry.get("author"),
            "license": entry["license"],
            "license_url": entry.get("license_evidence_url"),
            "license_verified": True,
            "license_verified_on": entry.get("license_verified_on"),
            "commercial_use": entry.get("commercial_use"),
            "redistribution_allowed": entry.get("redistribution_allowed"),
            "attribution": entry.get("attribution"),
            "format": formats_from_files([f["path"] for f in files]) or None,
            "rigged": entry.get("rigged"),
            "animated": entry.get("animated"),
            "download_status": "downloaded",
            "downloaded_at": now,
            "version": None,
            "fingerprint": fp,
            "files": files,
            "source_files": source_files,
            "skipped_extensions": sorted(set(skipped_ext)),
            "tool_version": TOOL_VERSION,
            "github_repo": entry.get("github_repo"),
            "github_commit": entry.get("github_commit"),
        }
        save_json(asset_stage / "metadata.json", metadata)
        (asset_stage / "LICENSE.txt").write_text(license_notice(entry, now), encoding="utf-8")

        adir.parent.mkdir(parents=True, exist_ok=True)
        if adir.exists():
            old_meta = read_asset_metadata(adir)
            expected_parent = (ctx.root / "assets" / entry["category"]).resolve()
            if (not old_meta or old_meta.get("id") != entry["id"]
                    or adir.resolve().parent != expected_parent):
                raise Blocked("blocked_existing_dir_not_managed", str(adir.name))
            shutil.rmtree(adir)
        shutil.move(str(asset_stage), str(adir))
        ctx.total_bytes += bytes_total
        ctx.fingerprints[fp] = entry["id"]
        return {"file_count": len(files), "bytes": bytes_total,
                "skipped_extensions": sorted(set(skipped_ext))}
    finally:
        try:
            if stage.resolve().parent == staging_root and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------
def run(root: pathlib.Path, mode: str, category: str = "all", asset_id: Optional[str] = None,
        max_downloads: int = 3, max_total_mb: int = 100, max_file_mb: int = DEFAULT_MAX_FILE_MB,
        max_files_per_asset: int = DEFAULT_MAX_FILES_PER_ASSET,
        fetch_json_fn=None, fetch_file_fn=None) -> Dict[str, Any]:
    if mode not in ("scan", "download", "update"):
        raise LibraryError(f"modo inválido: {mode!r}")
    if asset_id and not LOCAL_ID_RE.match(asset_id):
        raise LibraryError(f"asset_id inválido: {asset_id!r}")
    ctx = Context(root, mode, max_downloads, max_total_mb, max_file_mb, max_files_per_asset,
                  fetch_json_fn, fetch_file_fn)
    entries = load_manifests(root, category)
    if asset_id:
        entries = [e for e in entries if e.get("id") == asset_id]
    results = [process_asset(ctx, e, asset_id) for e in entries]
    summary: Dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    report = {
        "tool": "download_assets", "tool_version": TOOL_VERSION, "mode": mode,
        "generated_at": utcnow(), "category": category, "asset_id": asset_id,
        "limits": {"max_downloads": ctx.max_downloads,
                   "max_total_mb": ctx.max_total_bytes // (1024 * 1024),
                   "max_file_mb": ctx.max_file_bytes // (1024 * 1024),
                   "max_files_per_asset": ctx.max_files_per_asset},
        "selected_entries": len(entries),
        "downloaded_assets": ctx.downloaded_assets,
        "downloaded_bytes": ctx.total_bytes,
        "summary": summary,
        "results": results,
    }
    if not entries:
        report["note"] = "nenhum recurso selecionado (categoria/asset_id sem entradas)"
    return report


def write_summary_markdown(report: Dict[str, Any]) -> str:
    lines = [f"### Biblioteca 3D — {report['mode']}", "",
             f"Entradas selecionadas: **{report['selected_entries']}** · "
             f"descarregados: **{report['downloaded_assets']}** "
             f"({report['downloaded_bytes']} bytes)", "",
             "| Recurso | Categoria | Estado |", "|---|---|---|"]
    for r in report["results"]:
        lines.append(f"| `{r['id']}` | {r['category']} | {r['status']} |")
    if not report["results"]:
        lines.append("| — | — | nenhum recurso selecionado |")
    obs = sorted({h for r in report["results"] for h in r.get("hosts_unconfirmed", [])})
    if obs:
        lines += ["", "Hosts de download observados e ainda não confirmados em `sources.json`: "
                  + ", ".join(f"`{h}`" for h in obs)]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", default=str(common.ROOT))
    ap.add_argument("--mode", required=True, choices=["scan", "download", "update"])
    ap.add_argument("--category", default="all", choices=["all", *CATEGORIES])
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--max-downloads", type=int, default=3)
    ap.add_argument("--max-total-mb", type=int, default=100)
    ap.add_argument("--max-file-mb", type=int, default=DEFAULT_MAX_FILE_MB)
    ap.add_argument("--max-files-per-asset", type=int, default=DEFAULT_MAX_FILES_PER_ASSET)
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    try:
        report = run(root, args.mode, args.category, args.asset_id or None, args.max_downloads,
                     args.max_total_mb, args.max_file_mb, args.max_files_per_asset)
    except LibraryError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    save_json(root / "reports" / f"{args.mode}_report.json", report)
    md = write_summary_markdown(report)
    print(md)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(md)
    failed = [r for r in report["results"] if str(r["status"]).startswith("failed_")]
    if args.mode == "scan":
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
