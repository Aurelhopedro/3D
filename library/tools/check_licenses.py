#!/usr/bin/env python3
"""Relatório de conformidade de licenças (apenas stdlib).

Lê fontes + manifestos, aplica a mesma regra de bloqueio usada no download
(common.license_gate) e escreve reports/license_report.json e .md.
Recursos CC-BY/GPL/MIT/etc. ficam registados à parte (sem download automático).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (  # noqa: E402
    AUTO_DOWNLOAD_LICENSES, CATEGORIES, LibraryError, license_gate, load_manifests,
    load_sources, save_json, utcnow, validate_entry_shape,
)


def build_report(root: pathlib.Path, category: str = "all") -> Dict[str, Any]:
    sources = load_sources(root)
    entries = load_manifests(root, category)
    rows: List[Dict[str, Any]] = []
    for e in entries:
        problems = validate_entry_shape(e)
        ok, reason = license_gate(e, sources)
        if problems:
            ok, reason = False, "invalid_manifest_entry"
        lic = e.get("license")
        rows.append({
            "id": e.get("id"), "category": e.get("category"), "source": e.get("source"),
            "license": lic, "license_verified": e.get("license_verified") is True,
            "license_evidence_url": e.get("license_evidence_url"),
            "auto_download_allowed": ok, "reason": reason, "problems": problems,
            "needs_separate_review": bool(lic) and lic not in AUTO_DOWNLOAD_LICENSES,
        })
    by_license: Dict[str, int] = {}
    for r in rows:
        key = r["license"] or "unknown"
        by_license[key] = by_license.get(key, 0) + 1
    source_rows = []
    for key, s in sorted(sources.items()):
        source_rows.append({
            "source": key, "name": s.get("name"), "content_license": s.get("content_license"),
            "content_license_verified": s.get("content_license_verified") is True,
            "evidence_url": s.get("license_evidence_url"), "status": s.get("status"),
            "download_hosts_confirmed": bool(s.get("allowed_download_hosts")),
        })
    return {
        "tool": "check_licenses", "generated_at": utcnow(), "category": category,
        "auto_download_licenses": sorted(AUTO_DOWNLOAD_LICENSES),
        "totals": {"entries": len(rows),
                   "auto_download_allowed": sum(1 for r in rows if r["auto_download_allowed"]),
                   "blocked": sum(1 for r in rows if not r["auto_download_allowed"]),
                   "by_license": by_license},
        "sources": source_rows, "entries": rows,
    }


def to_markdown(report: Dict[str, Any]) -> str:
    t = report["totals"]
    lines = ["# Relatório de licenças", "",
             f"Gerado em {report['generated_at']}. Entradas: {t['entries']} · "
             f"download automático permitido: {t['auto_download_allowed']} · bloqueadas: {t['blocked']}", "",
             "## Fontes", "", "| Fonte | Licença do conteúdo | Verificada | Hosts confirmados | Estado |",
             "|---|---|---|---|---|"]
    for s in report["sources"]:
        lines.append(f"| {s['name']} | {s['content_license']} | {s['content_license_verified']} | "
                     f"{s['download_hosts_confirmed']} | {s['status']} |")
    lines += ["", "## Recursos", "", "| Recurso | Categoria | Licença | Download automático | Motivo |",
              "|---|---|---|---|---|"]
    for r in report["entries"]:
        lines.append(f"| `{r['id']}` | {r['category']} | {r['license']} | "
                     f"{r['auto_download_allowed']} | {r['reason']} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(common.ROOT))
    ap.add_argument("--category", default="all", choices=["all", *CATEGORIES])
    ap.add_argument("--strict", action="store_true",
                    help="sai com erro se existir alguma entrada com problemas estruturais")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    try:
        report = build_report(root, args.category)
    except (LibraryError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    save_json(root / "reports" / "license_report.json", report)
    md = to_markdown(report)
    (root / "reports" / "license_report.md").write_text(md, encoding="utf-8")
    print(md)
    if args.strict and any(r["problems"] for r in report["entries"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
