#!/usr/bin/env python3
"""Valida recursos já descarregados (apenas stdlib).

Verifica: existência, tamanho e sha256 de cada ficheiro registado, formatos,
ficheiros fora do registo, dependências em falta (.gltf -> .bin/texturas,
.obj -> .mtl) e coerência da licença nos metadados.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (  # noqa: E402
    ALLOWED_EXTENSIONS, AUTO_DOWNLOAD_LICENSES, CATEGORIES, LibraryError, UnsafePathError,
    iter_asset_dirs, read_asset_metadata, safe_join, save_json, sha256_file, utcnow,
)


def _gltf_dependencies(path: pathlib.Path) -> List[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    uris = []
    for key in ("buffers", "images"):
        for item in data.get(key, []) or []:
            uri = item.get("uri") if isinstance(item, dict) else None
            if isinstance(uri, str) and not uri.startswith("data:"):
                uris.append(urllib.parse.unquote(uri))
    return uris


def _obj_dependencies(path: pathlib.Path) -> List[str]:
    deps = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i > 5000:
                    break
                if line.startswith("mtllib "):
                    deps.append(line.split(None, 1)[1].strip())
    except OSError:
        pass
    return deps


def validate_asset(category: str, adir: pathlib.Path) -> Dict[str, Any]:
    issues: List[str] = []
    meta = read_asset_metadata(adir)
    if meta is None:
        return {"id": adir.name, "category": category, "ok": False,
                "issues": ["metadata.json ausente ou inválido"]}
    if meta.get("category") != category:
        issues.append("categoria dos metadados difere da pasta")
    if meta.get("id") != adir.name:
        issues.append("id dos metadados difere do nome da pasta")
    if meta.get("download_status") == "downloaded":
        if meta.get("license") not in AUTO_DOWNLOAD_LICENSES or meta.get("license_verified") is not True:
            issues.append("licença não verificada/permitida num recurso marcado como descarregado")
        if not (adir / "LICENSE.txt").is_file():
            issues.append("LICENSE.txt ausente")
    recorded = {}
    for f in meta.get("files", []):
        rel = f.get("path", "")
        try:
            p = safe_join(adir, rel)
        except UnsafePathError:
            issues.append(f"caminho inseguro nos metadados: {rel!r}")
            continue
        recorded[rel] = p
        if not p.is_file():
            issues.append(f"ficheiro em falta: {rel}")
            continue
        if p.stat().st_size != f.get("size"):
            issues.append(f"tamanho diferente: {rel}")
        if sha256_file(p) != f.get("sha256"):
            issues.append(f"sha256 diferente: {rel}")
        ext = pathlib.PurePosixPath(rel).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            issues.append(f"extensão inesperada: {rel}")
        if ext == ".gltf":
            for dep in _gltf_dependencies(p):
                try:
                    dp = safe_join(p.parent, dep)
                except UnsafePathError:
                    issues.append(f"dependência insegura em {rel}: {dep!r}")
                    continue
                if not dp.is_file():
                    issues.append(f"dependência em falta de {rel}: {dep}")
        if ext == ".obj":
            for dep in _obj_dependencies(p):
                try:
                    dp = safe_join(p.parent, dep)
                except UnsafePathError:
                    issues.append(f"dependência insegura em {rel}: {dep!r}")
                    continue
                if not dp.is_file():
                    issues.append(f"dependência em falta de {rel}: {dep}")
    known = set(recorded) | {"metadata.json", "LICENSE.txt"}
    for p in sorted(adir.rglob("*")):
        if p.is_file():
            rp = p.relative_to(adir).as_posix()
            if rp not in known:
                issues.append(f"ficheiro fora do registo: {rp}")
    return {"id": adir.name, "category": category, "ok": not issues, "issues": issues,
            "formats": meta.get("format")}


def run(root: pathlib.Path, category: str = "all") -> Dict[str, Any]:
    results = []
    for cat, adir in iter_asset_dirs(root):
        if category != "all" and cat != category:
            continue
        results.append(validate_asset(cat, adir))
    return {
        "tool": "validate_assets", "generated_at": utcnow(), "category": category,
        "checked": len(results), "ok": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "note": "nenhum recurso descarregado para validar" if not results else None,
        "results": results,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(common.ROOT))
    ap.add_argument("--category", default="all", choices=["all", *CATEGORIES])
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    try:
        report = run(root, args.category)
    except (LibraryError, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    save_json(root / "reports" / "validate_report.json", report)
    print(f"Validados: {report['checked']} · ok: {report['ok']} · com problemas: {report['failed']}")
    for r in report["results"]:
        if not r["ok"]:
            print(f"- {r['category']}/{r['id']}")
            for i in r["issues"]:
                print(f"    * {i}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
