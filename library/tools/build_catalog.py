#!/usr/bin/env python3
"""Gera o catálogo consolidado (apenas stdlib).

Junta o que está DECLARADO nos manifestos com o que existe REALMENTE em
assets/ (metadata.json) e escreve:
  catalog/assets.json     lista pesquisável de recursos
  catalog/licenses.json   licenças em uso, contagens e evidências
  reports/catalog_summary.md
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (  # noqa: E402
    CATEGORIES, LibraryError, asset_dir, license_gate, load_json, load_manifests, load_sources,
    read_asset_metadata, save_json, utcnow,
)


def _validation_index(root: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    p = root / "reports" / "validate_report.json"
    if not p.exists():
        return {}
    try:
        data = load_json(p)
    except (OSError, ValueError):
        return {}
    return {f"{r['category']}/{r['id']}": r for r in data.get("results", [])}


def build(root: pathlib.Path) -> Dict[str, Any]:
    sources = load_sources(root)
    validation = _validation_index(root)
    items: List[Dict[str, Any]] = []
    for e in load_manifests(root, "all"):
        cat, lid = e["category"], e.get("id")
        meta = None
        try:
            meta = read_asset_metadata(asset_dir(root, cat, lid))
        except common.UnsafePathError:
            pass
        ok, reason = license_gate(e, sources)
        downloaded = bool(meta and meta.get("download_status") == "downloaded")
        if downloaded:
            status = "downloaded"
        elif not ok:
            status = reason
        elif sources.get(e.get("source"), {}).get("resolver") == "manual_pending":
            status = "manual_pending"
        else:
            status = "approved_pending_download"
        v = validation.get(f"{cat}/{lid}")
        files = (meta or {}).get("files", [])
        items.append({
            "id": lid, "name": e.get("name"), "category": cat, "subcategory": e.get("subcategory"),
            "source": e.get("source"), "source_url": e.get("source_url"),
            "license": e.get("license"), "license_verified": e.get("license_verified") is True,
            "commercial_use": e.get("commercial_use"),
            "redistribution_allowed": e.get("redistribution_allowed"),
            "formats": (meta or {}).get("format") or e.get("formats_wanted") or [],
            "rigged": (meta or {}).get("rigged", e.get("rigged")),
            "animated": (meta or {}).get("animated", e.get("animated")),
            "status": status, "path": f"assets/{cat}/{lid}" if downloaded else None,
            "file_count": len(files), "total_bytes": sum(f.get("size", 0) for f in files),
            "validation": None if v is None else ("ok" if v["ok"] else "failed"),
        })
    items.sort(key=lambda x: (x["category"], x["id"] or ""))
    by_cat = {c: 0 for c in CATEGORIES}
    by_status: Dict[str, int] = {}
    for it in items:
        by_cat[it["category"]] += 1
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
    return {
        "schema_version": 1, "generated_at": utcnow(),
        "totals": {"assets": len(items), "downloaded": by_status.get("downloaded", 0),
                   "by_category": by_cat, "by_status": by_status,
                   "downloaded_bytes": sum(i["total_bytes"] for i in items)},
        "assets": items,
    }


def build_licenses(root: pathlib.Path, catalog: Dict[str, Any]) -> Dict[str, Any]:
    sources = load_sources(root)
    usage: Dict[str, Dict[str, Any]] = {}
    for it in catalog["assets"]:
        key = it["license"] or "unknown"
        u = usage.setdefault(key, {"count": 0, "verified": 0})
        u["count"] += 1
        u["verified"] += 1 if it["license_verified"] else 0
    return {
        "schema_version": 1, "generated_at": utcnow(),
        "auto_download_licenses": sorted(common.AUTO_DOWNLOAD_LICENSES),
        "usage": usage,
        "sources": {k: {"content_license": s.get("content_license"),
                        "verified": s.get("content_license_verified") is True,
                        "evidence_url": s.get("license_evidence_url"),
                        "verified_on": s.get("license_verified_on")}
                    for k, s in sorted(sources.items())},
    }


def summary_markdown(cat: Dict[str, Any]) -> str:
    t = cat["totals"]
    lines = ["# Catálogo da biblioteca 3D", "", f"Gerado em {cat['generated_at']}.", "",
             f"Recursos declarados: **{t['assets']}** · descarregados: **{t['downloaded']}** "
             f"({t['downloaded_bytes']} bytes)", "", "| Categoria | Recursos |", "|---|---|"]
    for c, n in t["by_category"].items():
        lines.append(f"| {c} | {n} |")
    lines += ["", "| Estado | Recursos |", "|---|---|"]
    for s, n in sorted(t["by_status"].items()):
        lines.append(f"| {s} | {n} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(common.ROOT))
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    try:
        catalog = build(root)
        licenses = build_licenses(root, catalog)
    except (LibraryError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    save_json(root / "catalog" / "assets.json", catalog)
    save_json(root / "catalog" / "licenses.json", licenses)
    md = summary_markdown(catalog)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "catalog_summary.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
