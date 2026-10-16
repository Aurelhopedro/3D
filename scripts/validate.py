"""
validate.py — Validação da cena e produção do relatório metadata.json.

Operação: "validate_model"

A função build_metadata() é chamada por main.py no FIM de TODOS os jobs,
mesmo que a operação principal tenha falhado — para garantir que existe
sempre um registo honesto do que aconteceu (ficheiros gerados, erros,
avisos, contagem de vértices/faces, etc.).
"""

import os
import bpy  # type: ignore


def _scene_summary():
    objects = []
    total_verts = 0
    total_faces = 0
    for obj in bpy.data.objects:
        entry = {"name": obj.name, "type": obj.type}
        if obj.type == "MESH":
            depsgraph = bpy.context.evaluated_depsgraph_get()
            obj_eval = obj.evaluated_get(depsgraph)
            mesh_eval = obj_eval.to_mesh()
            entry["vertices"] = len(mesh_eval.vertices)
            entry["faces"] = len(mesh_eval.polygons)
            total_verts += entry["vertices"]
            total_faces += entry["faces"]
            obj_eval.to_mesh_clear()
        objects.append(entry)
    return objects, total_verts, total_faces


def _file_report(path):
    if path and os.path.isfile(path):
        return {"exists": True, "size_bytes": os.path.getsize(path)}
    return {"exists": False, "size_bytes": 0}


def run(job, state):
    """Entry point para a operação standalone 'validate_model'."""
    objects, total_verts, total_faces = _scene_summary()
    return {
        "objects": objects,
        "total_vertices": total_verts,
        "total_faces": total_faces,
    }


def build_metadata(job, output_dir, result, errors, warnings, started_at, finished_at):
    objects, total_verts, total_faces = _scene_summary()

    files = {
        "blend": _file_report(result.get("blend_path")),
        "glb": _file_report(result.get("glb_path")),
        "obj": _file_report(result.get("obj_path")),
        "fbx": _file_report(result.get("fbx_path")),
        "preview": _file_report(result.get("preview_path")),
    }

    return {
        "job_id": job.get("job_id"),
        "operation": job.get("operation"),
        "parameters": job.get("parameters"),
        "started_at": started_at,
        "finished_at": finished_at,
        "success": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "scene": {
            "object_count": len(objects),
            "objects": objects,
            "total_vertices": total_verts,
            "total_faces": total_faces,
        },
        "files": files,
        "blender_version": bpy.app.version_string,
    }
