#!/usr/bin/env python3
"""
main.py — Orquestrador do sistema de trabalhos 3D (repositório "3D").

Corre DENTRO do Python embutido no Blender, invocado como:

    blender --background --python scripts/main.py -- <job.json> [output_dir]

Responsabilidades:
    1. Ler e validar um ficheiro de trabalho (job) em JSON.
    2. Identificar a operação pedida e chamar o módulo correspondente.
    3. Gerar os outputs pedidos (blend / glb / obj / fbx / preview).
    4. Produzir SEMPRE um metadata.json com o resultado da execução.
    5. Comunicar erros de forma clara — nunca falha silenciosamente.

Este ficheiro não deve ser executado com um Python comum: o módulo "bpy"
só existe dentro do interpretador do Blender.
"""

import json
import os
import sys
import traceback
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import create_model   # noqa: E402
import modify_model   # noqa: E402
import materials       # noqa: E402
import optimize          # noqa: E402
import animate             # noqa: E402
import render                # noqa: E402
import export                  # noqa: E402
import validate                  # noqa: E402

OPERATIONS = {
    "create_model": create_model.run,
    "modify_model": modify_model.run,
    "apply_material": materials.run,
    "optimize_model": optimize.run,
    "animate_model": animate.run,
    "render_model": render.run,
    "export_model": export.run,
    "validate_model": validate.run,
}

REQUIRED_JOB_FIELDS = ("job_id", "operation", "parameters")


def _parse_cli_args():
    """Extrai os argumentos passados depois de '--' na linha de comandos do Blender."""
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if not argv:
        raise SystemExit(
            "Uso: blender --background --python scripts/main.py -- <job.json> [output_dir]"
        )
    job_path = argv[0]
    output_dir = argv[1] if len(argv) > 1 else None
    return job_path, output_dir


def _load_job(job_path):
    if not os.path.isfile(job_path):
        raise FileNotFoundError(f"Ficheiro de trabalho não encontrado: {job_path}")
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    missing = [field for field in REQUIRED_JOB_FIELDS if field not in job]
    if missing:
        raise ValueError(f"Campos obrigatórios em falta no job JSON: {missing}")
    if job["operation"] not in OPERATIONS:
        raise ValueError(
            f"Operação desconhecida '{job['operation']}'. "
            f"Operações suportadas: {sorted(OPERATIONS)}"
        )
    if not isinstance(job.get("parameters"), dict):
        raise ValueError("'parameters' tem de ser um objeto JSON.")
    job.setdefault("outputs", {})
    return job


def _prepare_output_dir(job, output_dir):
    if not output_dir:
        output_dir = os.path.join("output", job["job_id"])
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    errors = []
    warnings = []
    result = {}

    try:
        job_path, cli_output_dir = _parse_cli_args()
        job = _load_job(job_path)
    except Exception as exc:
        print(f"[main.py] ERRO FATAL ao carregar o job: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    output_dir = _prepare_output_dir(job, cli_output_dir)
    state = {
        "job": job,
        "output_dir": output_dir,
        "blend_path": os.path.join(output_dir, "model.blend"),
    }

    # 1. Executa a operação principal pedida pelo job
    try:
        op_name = job["operation"]
        print(f"[main.py] A executar operação '{op_name}' (job_id={job['job_id']})")
        op_result = OPERATIONS[op_name](job, state)
        result["operation_result"] = op_result or {}
    except Exception as exc:
        msg = f"Falha na operação '{job['operation']}': {exc}"
        print(f"[main.py] ERRO: {msg}", file=sys.stderr)
        traceback.print_exc()
        errors.append(msg)

    outputs_requested = job.get("outputs", {}) or {}

    # 2. Guarda o .blend (por omissão True, a menos que explicitamente desativado)
    if outputs_requested.get("blend", True) and not errors:
        try:
            import bpy  # type: ignore
            bpy.ops.wm.save_as_mainfile(filepath=state["blend_path"])
            result["blend_path"] = state["blend_path"]
            print(f"[main.py] .blend guardado em {state['blend_path']}")
        except Exception as exc:
            msg = f"Falha ao guardar o .blend: {exc}"
            print(f"[main.py] ERRO: {msg}", file=sys.stderr)
            errors.append(msg)

    # 3. Exporta GLB se pedido
    if outputs_requested.get("glb") and not errors:
        try:
            glb_path = os.path.join(output_dir, "model.glb")
            export.export_glb(glb_path)
            result["glb_path"] = glb_path
        except Exception as exc:
            msg = f"Falha ao exportar GLB: {exc}"
            print(f"[main.py] ERRO: {msg}", file=sys.stderr)
            errors.append(msg)

    # 4. Exporta formatos extra (OBJ / FBX) se pedidos explicitamente — nunca bloqueia o job
    for fmt, exporter in (("obj", export.export_obj), ("fbx", export.export_fbx)):
        if outputs_requested.get(fmt) and not errors:
            try:
                path = os.path.join(output_dir, f"model.{fmt}")
                ok = exporter(path)
                if ok:
                    result[f"{fmt}_path"] = path
                else:
                    warnings.append(
                        f"Exportador '{fmt}' não está disponível nesta versão do Blender."
                    )
            except Exception as exc:
                warnings.append(f"Falha ao exportar {fmt.upper()}: {exc}")

    # 5. Gera pré-visualização se pedido
    if outputs_requested.get("preview") and not errors:
        try:
            preview_path = os.path.join(output_dir, "preview.png")
            render.render_preview(preview_path)
            result["preview_path"] = preview_path
        except Exception as exc:
            msg = f"Falha ao gerar pré-visualização: {exc}"
            print(f"[main.py] ERRO: {msg}", file=sys.stderr)
            errors.append(msg)

    # 6. Validação final + metadata.json — corre SEMPRE, mesmo com erros acima
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        metadata = validate.build_metadata(
            job=job,
            output_dir=output_dir,
            result=result,
            errors=errors,
            warnings=warnings,
            started_at=started_at,
            finished_at=finished_at,
        )
        metadata_path = os.path.join(output_dir, "metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        print(f"[main.py] metadata.json escrito em {metadata_path}")
    except Exception as exc:
        print(f"[main.py] ERRO ao escrever metadata.json: {exc}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"Falha ao escrever metadata.json: {exc}")

    print("[main.py] RESUMO:", json.dumps(
        {"job_id": job["job_id"], "sucesso": not errors, "erros": errors, "avisos": warnings},
        ensure_ascii=False,
    ))

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
