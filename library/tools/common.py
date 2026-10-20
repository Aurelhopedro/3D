"""Funções partilhadas pelas ferramentas da biblioteca 3D (apenas stdlib).

Princípios:
- Nenhum caminho de destino vem de manifestos externos: é sempre calculado a
  partir de CATEGORIES + um id validado por regex.
- Apenas licenças em AUTO_DOWNLOAD_LICENSES podem ser descarregadas
  automaticamente, e só com verificação explícita e evidência (URL).
- Nada aqui executa código proveniente de pacotes descarregados.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]  # pasta library/

CATEGORIES: Tuple[str, ...] = (
    "characters", "hair", "clothing", "vehicles", "environments",
    "architecture", "furniture", "nature", "animals", "animations",
    "materials", "lighting",
)

# Licenças que podem ser descarregadas automaticamente na Fase 1.
AUTO_DOWNLOAD_LICENSES = frozenset({"CC0-1.0"})

# Extensões aceites dentro de um recurso. Outras são ignoradas e reportadas.
ALLOWED_EXTENSIONS = frozenset({
    ".glb", ".gltf", ".bin", ".obj", ".mtl", ".fbx", ".blend", ".vrm", ".stl",
    ".usd", ".usda", ".usdc", ".usdz", ".mtlx",
    ".png", ".jpg", ".jpeg", ".webp", ".exr", ".hdr", ".tga", ".bmp",
    ".tif", ".tiff",
    ".json", ".txt", ".md",
})

MODEL_FORMATS = {
    ".glb": "glb", ".gltf": "gltf", ".obj": "obj", ".fbx": "fbx",
    ".blend": "blend", ".vrm": "vrm", ".stl": "stl", ".usd": "usd",
    ".usda": "usd", ".usdc": "usd", ".usdz": "usdz",
}

LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
SOURCE_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
SOURCE_KEY_RE = re.compile(r"^[a-z0-9_]{1,32}$")

DEFAULT_MAX_FILE_MB = 50
DEFAULT_MAX_FILES_PER_ASSET = 40


class LibraryError(Exception):
    """Erro controlado das ferramentas."""


class UnsafePathError(LibraryError):
    pass


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path: pathlib.Path) -> str:
    h = hashlib.md5()  # noqa: S324 - apenas para comparar com md5 publicado pela fonte
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)


def safe_relative_path(rel: str) -> pathlib.PurePosixPath:
    """Valida um caminho relativo vindo de fora (ZIP, API). Levanta UnsafePathError."""
    if not isinstance(rel, str) or not rel:
        raise UnsafePathError("caminho vazio")
    if "\x00" in rel or any(ord(c) < 32 for c in rel):
        raise UnsafePathError("caracteres de controlo no caminho")
    if "\\" in rel:
        raise UnsafePathError("barra invertida no caminho")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise UnsafePathError("caminho absoluto")
    # Validar os segmentos do texto ORIGINAL: PurePosixPath normaliza "a//b" e "./a".
    if any(seg in ("", ".", "..") for seg in rel.split("/")):
        raise UnsafePathError("segmento perigoso no caminho")
    p = pathlib.PurePosixPath(rel)
    parts = p.parts
    if not parts:
        raise UnsafePathError("segmento perigoso no caminho")
    if len(rel) > 240:
        raise UnsafePathError("caminho demasiado longo")
    return p


def safe_join(base: pathlib.Path, rel: str) -> pathlib.Path:
    """Junta base + rel garantindo que o resultado fica dentro de base."""
    p = safe_relative_path(rel)
    base_resolved = base.resolve()
    target = (base_resolved / pathlib.Path(*p.parts)).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise UnsafePathError("fuga do diretório base") from exc
    return target


def asset_dir(root: pathlib.Path, category: str, local_id: str) -> pathlib.Path:
    """Destino de um recurso, calculado só de categoria permitida + id validado."""
    if category not in CATEGORIES:
        raise UnsafePathError(f"categoria não permitida: {category!r}")
    if not isinstance(local_id, str) or not LOCAL_ID_RE.match(local_id):
        raise UnsafePathError(f"id inválido: {local_id!r}")
    return root / "assets" / category / local_id


def load_sources(root: pathlib.Path) -> Dict[str, Any]:
    data = load_json(root / "catalog" / "sources.json")
    return data.get("sources", {})


def load_manifests(root: pathlib.Path, category: str = "all") -> List[Dict[str, Any]]:
    """Lê manifestos e devolve entradas normalizadas (com 'category' do ficheiro)."""
    cats = CATEGORIES if category == "all" else (category,)
    if category != "all" and category not in CATEGORIES:
        raise LibraryError(f"categoria desconhecida: {category!r}")
    entries: List[Dict[str, Any]] = []
    for cat in cats:
        path = root / "manifests" / f"{cat}.json"
        if not path.exists():
            continue
        data = load_json(path)
        if data.get("category") != cat:
            raise LibraryError(f"{path.name}: campo 'category' diferente do nome do ficheiro")
        for raw in data.get("assets", []):
            entry = dict(raw)
            entry["category"] = cat  # a categoria vem SEMPRE do ficheiro, nunca da entrada
            entries.append(entry)
    return entries


def license_gate(entry: Dict[str, Any], sources: Dict[str, Any]) -> Tuple[bool, str]:
    """Decide se uma entrada pode ser descarregada automaticamente.

    Devolve (True, "ok") ou (False, motivo_de_bloqueio).
    """
    lic = entry.get("license")
    if not lic:
        return False, "blocked_license_unverified"
    if lic not in AUTO_DOWNLOAD_LICENSES:
        return False, "blocked_license_not_auto_downloadable"
    if entry.get("license_verified") is not True:
        return False, "blocked_license_unverified"
    if not entry.get("license_evidence_url"):
        return False, "blocked_license_evidence_missing"
    src_key = entry.get("source")
    src = sources.get(src_key) if isinstance(src_key, str) else None
    if not src:
        return False, "blocked_source_unknown"
    if src.get("content_license") != lic:
        return False, "blocked_source_license_mismatch"
    if src.get("content_license_verified") is not True:
        return False, "blocked_source_license_unverified"
    if entry.get("redistribution_allowed") is not True:
        return False, "blocked_redistribution_unconfirmed"
    return True, "ok"


def validate_entry_shape(entry: Dict[str, Any]) -> List[str]:
    """Problemas estruturais de uma entrada de manifesto (lista vazia = ok)."""
    problems: List[str] = []
    lid = entry.get("id")
    if not isinstance(lid, str) or not LOCAL_ID_RE.match(lid):
        problems.append(f"id inválido: {lid!r}")
    if entry.get("category") not in CATEGORIES:
        problems.append(f"categoria inválida: {entry.get('category')!r}")
    sid = entry.get("source_asset_id")
    if sid is not None and (not isinstance(sid, str) or not SOURCE_ASSET_ID_RE.match(sid)):
        problems.append(f"source_asset_id inválido: {sid!r}")
    skey = entry.get("source")
    if not isinstance(skey, str) or not SOURCE_KEY_RE.match(skey):
        problems.append(f"source inválida: {skey!r}")
    return problems


def formats_from_files(paths: List[str]) -> List[str]:
    found = set()
    for p in paths:
        ext = pathlib.PurePosixPath(p).suffix.lower()
        if ext in MODEL_FORMATS:
            found.add(MODEL_FORMATS[ext])
    return sorted(found)


def read_asset_metadata(adir: pathlib.Path) -> Optional[Dict[str, Any]]:
    mpath = adir / "metadata.json"
    if not mpath.exists():
        return None
    try:
        return load_json(mpath)
    except (json.JSONDecodeError, OSError):
        return None


def iter_asset_dirs(root: pathlib.Path):
    """Itera (categoria, pasta_do_recurso) para pastas de recursos existentes."""
    base = root / "assets"
    for cat in CATEGORIES:
        cdir = base / cat
        if not cdir.is_dir():
            continue
        for child in sorted(cdir.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                yield cat, child
