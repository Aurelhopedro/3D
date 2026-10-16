"""
optimize.py — Otimização / redução de polígonos e relatórios técnicos.

Operação: "optimize_model"

Por omissão aplica um modificador Decimate NÃO destrutivo (a malha editável
original é preservada). Só aplica permanentemente a geometria se
parameters["apply_destructive"] for True — conforme exigido pelo projeto
("evita operações destrutivas sem conservar uma versão editável").
"""

import bpy  # type: ignore


def _mesh_counts(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    verts = len(mesh_eval.vertices)
    faces = len(mesh_eval.polygons)
    obj_eval.to_mesh_clear()
    return verts, faces


def run(job, state):
    params = job.get("parameters", {})
    ratio = float(params.get("ratio", 0.5))
    if not 0 < ratio <= 1:
        raise ValueError("'ratio' tem de estar entre 0 (exclusivo) e 1 (inclusivo).")

    names = params.get("objects")
    targets = (
        [bpy.data.objects[n] for n in names if n in bpy.data.objects]
        if names
        else [o for o in bpy.data.objects if o.type == "MESH"]
    )
    if not targets:
        raise ValueError("Nenhum objeto do tipo MESH encontrado para otimizar.")

    destructive = bool(params.get("apply_destructive", False))
    report = []

    for obj in targets:
        before_v, before_f = _mesh_counts(obj)

        mod = obj.modifiers.new(name="Optimize_Decimate", type="DECIMATE")
        mod.ratio = ratio

        if destructive:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            obj_eval = obj.evaluated_get(depsgraph)
            new_mesh = bpy.data.meshes.new_from_object(obj_eval)
            old_mesh = obj.data
            obj.modifiers.remove(mod)
            obj.data = new_mesh
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)

        after_v, after_f = _mesh_counts(obj)

        report.append({
            "object": obj.name,
            "vertices_before": before_v,
            "faces_before": before_f,
            "vertices_after": after_v,
            "faces_after": after_f,
            "ratio_applied": ratio,
            "destructive": destructive,
        })

    return {"optimization_report": report}
