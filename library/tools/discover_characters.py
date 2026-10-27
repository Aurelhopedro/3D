#!/usr/bin/env python3
"""Descoberta de personagens a partir de fontes registadas (apenas stdlib).

Uso:
  python3 -I library/tools/discover_characters.py [--source SOURCE] [--dry-run]

Fontes suportadas nesta versão:
  - 100avatars  : lê o JSON de cada coleção R1/R2/R3 de arweave e reporta candidatos.
                  NÃO descarrega os VRM (resolver ainda não implementado).
  - kaykit      : lê o manifesto de personagens e reporta o estado das entradas KayKit.

Fontes não integráveis nesta versão (documentadas em sources.json):
  - ara3dgen    : gerador local (Node.js), sem API de download.
  - three_ws    : plataforma de agentes 3D, sem catálogo CC0 estático.

O relatório é escrito em library/reports/discover_characters_<fonte>_<data>.json.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# Adicionar ferramentas ao path
_TOOLS = pathlib.Path(__file__).resolve().parent
_ROOT = _TOOLS.parent
sys.path.insert(0, str(_TOOLS))
import common  # noqa: E402

USER_AGENT = "Aurelhopedro-3D-library/0.1 (+https://github.com/Aurelhopedro/3D)"
TIMEOUT = 30


# --------------------------------------------------------------------------
# HTTP simples (apenas GET, sem redirects perigosos)
# --------------------------------------------------------------------------
def _fetch_json(url: str, allowed_hosts: Optional[List[str]] = None,
                max_bytes: int = 10 * 1024 * 1024) -> Any:
    """Descarrega e analisa um JSON. Lança RuntimeError em caso de falha."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https":
        raise RuntimeError(f"URL não HTTPS: {url}")
    if allowed_hosts and not any(
        host == h.lower() or host.endswith("." + h.lower()) for h in allowed_hosts
    ):
        raise RuntimeError(f"host não permitido: {host}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise RuntimeError("resposta demasiado grande")
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            time.sleep(min(2 ** attempt, 4))
    raise RuntimeError(f"falha ao obter {url}: {last_exc}")


# --------------------------------------------------------------------------
# Descoberta — 100Avatars
# --------------------------------------------------------------------------
_100AV_COLLECTIONS = {
    "r1": "https://raw.githubusercontent.com/ToxSam/open-source-avatars/main/data/avatars/100avatars-r1.json",
    "r2": "https://raw.githubusercontent.com/ToxSam/open-source-avatars/main/data/avatars/100avatars-r2.json",
    "r3": "https://raw.githubusercontent.com/ToxSam/open-source-avatars/main/data/avatars/100avatars-r3.json",
}
_100AV_ALLOWED_HOSTS = ["raw.githubusercontent.com"]

# IDs já presentes no manifesto (não duplicar)
_MANIFEST_100AV_IDS: set = set()


def _100av_candidate_id(series: str, number: str, name: str) -> str:
    safe_name = re.sub(r"[^a-z0-9]", "_", name.lower()).strip("_")
    return f"100av_{series}_{number.zfill(3)}_{safe_name}"


def discover_100avatars(dry_run: bool = False) -> Dict[str, Any]:
    """Lê os catálogos JSON das coleções 100Avatars e devolve um relatório."""
    # Carregar IDs já no manifesto para não duplicar
    existing_entries = common.load_manifests(_ROOT, "characters")
    existing_ids = {e["id"] for e in existing_entries}

    discovered: List[Dict[str, Any]] = []
    errors: List[str] = []

    for series, url in _100AV_COLLECTIONS.items():
        try:
            avatars = _fetch_json(url, _100AV_ALLOWED_HOSTS)
            if not isinstance(avatars, list):
                errors.append(f"{series}: resposta inesperada (não é lista)")
                continue
            for av in avatars:
                if not isinstance(av, dict):
                    continue
                name = av.get("name", "")
                number = str(av.get("metadata", {}).get("number", ""))
                model_url = av.get("model_file_url", "")
                fmt = (av.get("format") or "").lower()
                project_id = av.get("project_id", "")
                is_draft = av.get("is_draft", True)
                is_public = av.get("is_public", False)

                if is_draft or not is_public:
                    continue
                if not model_url.startswith("https://arweave.net/"):
                    errors.append(f"{series}/{name}: URL de modelo inesperada: {model_url}")
                    continue

                cid = _100av_candidate_id(series, number, name)
                status = "already_in_manifest" if cid in existing_ids else "new_candidate"

                discovered.append({
                    "proposed_id": cid,
                    "name": name,
                    "series": series,
                    "number": number,
                    "format": fmt,
                    "model_url": model_url,
                    "thumbnail_url": av.get("thumbnail_url"),
                    "project_id": project_id,
                    "source_avatar_id": av.get("id"),
                    "license": "CC0-1.0",
                    "license_status": "APPROVED",
                    "download_blocked_reason": "resolver_pending — arweave.net direct_url não implementado; .vrm requer addon Blender",
                    "status": status,
                })
        except Exception as exc:
            errors.append(f"{series}: {exc}")

    new_candidates = [d for d in discovered if d["status"] == "new_candidate"]
    in_manifest = [d for d in discovered if d["status"] == "already_in_manifest"]

    return {
        "source": "100avatars",
        "generated_at": common.utcnow(),
        "dry_run": dry_run,
        "total_discovered": len(discovered),
        "new_candidates": len(new_candidates),
        "already_in_manifest": len(in_manifest),
        "approved_license": len(discovered),
        "blocked_download": len(discovered),
        "block_reason": "resolver_pending — arweave.net direct_url não implementado; .vrm requer addon Blender",
        "errors": errors,
        "candidates": discovered,
        "action_required": [
            "Implementar resolver 'direct_url' em download_assets.py para arweave.net",
            "Adicionar .vrm a ALLOWED_EXTENSIONS em common.py (já existe mas confirmar)",
            "Verificar se Blender importa .vrm nativamente na versão instalada (addon VRM4Blender pode ser necessário)",
            "Após resolver estes pontos, executar 'download' com max_downloads=5 para os primeiros 5 candidatos",
        ],
    }


# --------------------------------------------------------------------------
# Descoberta — KayKit (relatório de estado)
# --------------------------------------------------------------------------
def discover_kaykit() -> Dict[str, Any]:
    """Reporta o estado das entradas KayKit nos manifestos."""
    entries = common.load_manifests(_ROOT, "characters")
    kaykit = [e for e in entries if e.get("source") == "kaykit"]
    approved = [e for e in kaykit if e.get("license_verified") and e.get("redistribution_allowed")]
    pending_download = [e for e in approved if e.get("download_status") == "pending"]
    downloaded = [e for e in approved if e.get("download_status") == "downloaded"]
    return {
        "source": "kaykit",
        "generated_at": common.utcnow(),
        "total_in_manifest": len(kaykit),
        "approved_license": len(approved),
        "pending_download": len(pending_download),
        "downloaded": len(downloaded),
        "pending_ids": [e["id"] for e in pending_download],
        "downloaded_ids": [e["id"] for e in downloaded],
    }


# --------------------------------------------------------------------------
# Ponto de entrada
# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--source", default="all",
                    choices=["all", "100avatars", "kaykit"],
                    help="Fonte a descobrir (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Não escrever ficheiros; só imprimir o relatório")
    args = ap.parse_args(argv)

    reports: Dict[str, Any] = {}
    ok = True

    if args.source in ("all", "100avatars"):
        try:
            rep = discover_100avatars(dry_run=args.dry_run)
            reports["100avatars"] = rep
            print(f"\n=== 100Avatars ===")
            print(f"  Descobertos:      {rep['total_discovered']}")
            print(f"  Novos candidatos: {rep['new_candidates']}")
            print(f"  Já no manifesto:  {rep['already_in_manifest']}")
            print(f"  Bloqueio:         {rep['block_reason']}")
            if rep["errors"]:
                print(f"  Erros:            {len(rep['errors'])}")
                for e in rep["errors"]:
                    print(f"    ! {e}")
        except Exception as exc:
            print(f"ERRO 100avatars: {exc}", file=sys.stderr)
            ok = False

    if args.source in ("all", "kaykit"):
        rep = discover_kaykit()
        reports["kaykit"] = rep
        print(f"\n=== KayKit ===")
        print(f"  No manifesto:    {rep['total_in_manifest']}")
        print(f"  Aprovados:       {rep['approved_license']}")
        print(f"  Por descarregar: {rep['pending_download']} — {rep['pending_ids']}")
        print(f"  Descarregados:   {rep['downloaded']} — {rep['downloaded_ids']}")

    # Guardar relatório
    if not args.dry_run:
        today = common.utcnow()[:10].replace("-", "")
        for src, rep in reports.items():
            out = _ROOT / "reports" / f"discover_{src}_{today}.json"
            common.save_json(out, rep)
            print(f"\nRelatório guardado: {out}")

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
