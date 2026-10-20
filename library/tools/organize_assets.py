#!/usr/bin/env python3
"""Garante a estrutura padronizada da biblioteca, sem mover nem apagar nada.

- Cria (se faltarem) assets/<categoria>/, presets/* e reports/.
- Para cada recurso com metadata.json, cria LICENSE.txt se estiver em falta.
- Reporta pastas sem metadata.json, ficheiros soltos e categorias desconhecidas.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
from common import CATEGORIES, LibraryError, read_asset_metadata, save_json, utcnow  # noqa: E402

PRESET_DIRS = ("characters", "scenes", "vehicles", "fashion")


def organize(root: pathlib.Path) -> Dict[str, Any]:
    created: List[str] = []
    warnings: List[str] = []
    for cat in CATEGORIES:
        d = root / "assets" / cat
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            created.append(f"assets/{cat}")
    for name in PRESET_DIRS:
        d = root / "presets" / name
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            created.append(f"presets/{name}")
    (root / "reports").mkdir(parents=True, exist_ok=True)

    base = root / "assets"
    for child in sorted(base.iterdir()):
        if child.name in CATEGORIES:
            continue
        if child.name.startswith("."):
            continue
        warnings.append(f"item inesperado em assets/: {child.name} (não movido)")

    licenses_written: List[str] = []
    for cat in CATEGORIES:
        cdir = base / cat
        for child in sorted(cdir.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_file():
                warnings.append(f"ficheiro solto em assets/{cat}/: {child.name} (não movido)")
                continue
            meta = read_asset_metadata(child)
            if meta is None:
                warnings.append(f"assets/{cat}/{child.name} sem metadata.json válido (não alterado)")
                continue
            if not (child / "LICENSE.txt").exists():
                lic = (f"Licença: {meta.get('license')}\n"
                       f"Evidência: {meta.get('license_url')}\n"
                       f"Fonte: {meta.get('source_url') or meta.get('source')}\n"
                       "Aviso recriado pela biblioteca a partir de metadata.json.\n")
                (child / "LICENSE.txt").write_text(lic, encoding="utf-8")
                licenses_written.append(f"assets/{cat}/{child.name}/LICENSE.txt")
    return {"tool": "organize_assets", "generated_at": utcnow(), "created_dirs": created,
            "license_files_written": licenses_written, "warnings": warnings}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(common.ROOT))
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    try:
        report = organize(root)
    except (LibraryError, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    save_json(root / "reports" / "organize_report.json", report)
    print(f"Pastas criadas: {len(report['created_dirs'])} · avisos: {len(report['warnings'])}")
    for w in report["warnings"]:
        print(f"- {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
