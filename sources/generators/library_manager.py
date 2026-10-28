"""
Gestão de geradores e ferramentas integradas.

Lê config/libraries.json e regista ferramentas de geração procedural
disponíveis para uso nos workflows. NÃO executa código externo.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]  # raiz do repositório


def load_libraries() -> List[Dict[str, Any]]:
    p = ROOT / "config" / "libraries.json"
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("libraries", [])


def list_enabled() -> List[Dict[str, Any]]:
    return [lib for lib in load_libraries() if lib.get("enabled")]


def list_integrated() -> List[Dict[str, Any]]:
    return [lib for lib in load_libraries() if lib.get("status") == "integrated_in_pipeline"]


def summary(libs: List[Dict[str, Any]]) -> str:
    lines = ["# Bibliotecas e Ferramentas 3D", ""]
    by_status: Dict[str, List[str]] = {}
    for lib in libs:
        s = lib.get("status", "unknown")
        by_status.setdefault(s, []).append(lib.get("name", lib["id"]))

    for status, names in sorted(by_status.items()):
        lines.append(f"## {status}")
        for n in names:
            lines.append(f"- {n}")
        lines.append("")

    lines += [
        "| ID | Nome | Tipo | Licença código | Estado | Ativo |",
        "|---|---|---|---|---|---|",
    ]
    for lib in libs:
        lines.append(
            f"| `{lib['id']}` "
            f"| {lib['name']} "
            f"| {lib.get('kind', '?')} "
            f"| {lib.get('code_license') or lib.get('content_license') or 'unknown'} "
            f"| {lib.get('status', '?')} "
            f"| {'✅' if lib.get('enabled') else '❌'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Gere e lista bibliotecas/ferramentas 3D.")
    ap.add_argument("--list", action="store_true", help="Listar todas as bibliotecas")
    ap.add_argument("--enabled", action="store_true", help="Listar apenas as ativas")
    ap.add_argument("--integrated", action="store_true", help="Listar apenas as integradas no pipeline")
    args = ap.parse_args(argv)

    try:
        libs = load_libraries()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERRO ao ler config/libraries.json: {exc}", file=sys.stderr)
        return 2

    if args.enabled:
        libs = list_enabled()
    elif args.integrated:
        libs = list_integrated()

    print(summary(libs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
