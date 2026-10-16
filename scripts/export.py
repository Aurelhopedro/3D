"""
export.py — Exportação de modelos para os formatos finais.

Operação: "export_model"

Suporta sempre: BLEND (nativo) e GLB.
Suporta ADICIONALMENTE OBJ e FBX, mas só declara sucesso depois de testar
realmente o exportador — nunca assume que um formato está disponível.
"""

import os
import bpy  # type: ignore


def _enable_addon(module_name):
    try:
        bpy.ops.preferences.addon_enable(module=module_name)
        return True
    except Exception:
        return False


def export_blend(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    return os.path.isfile(path)


def export_glb(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not hasattr(bpy.ops.export_scene, "gltf"):
        _enable_addon("io_scene_gltf2")
    if not hasattr(bpy.ops.export_scene, "gltf"):
        raise RuntimeError("Exportador glTF/GLB não está disponível nesta instalação do Blender.")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB")
    if not os.path.isfile(path):
        raise RuntimeError(f"A exportação GLB terminou mas o ficheiro não foi criado: {path}")
    return path


def export_obj(path):
    """Devolve False (sem lançar exceção) se o exportador OBJ não existir nesta versão."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if hasattr(bpy.ops.wm, "obj_export"):
        bpy.ops.wm.obj_export(filepath=path)
    elif hasattr(bpy.ops.export_scene, "obj"):
        bpy.ops.export_scene.obj(filepath=path)
    else:
        return False
    return os.path.isfile(path)


def export_fbx(path):
    """Devolve False (sem lançar exceção) se o exportador FBX não existir nesta versão."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not hasattr(bpy.ops.export_scene, "fbx"):
        _enable_addon("io_scene_fbx")
    if not hasattr(bpy.ops.export_scene, "fbx"):
        return False
    bpy.ops.export_scene.fbx(filepath=path)
    return os.path.isfile(path)


def run(job, state):
    params = job.get("parameters", {})
    formats = params.get("formats", ["blend", "glb"])
    output_dir = state["output_dir"]
    results = {}

    if "blend" in formats:
        path = os.path.join(output_dir, "model.blend")
        results["blend"] = path if export_blend(path) else None

    if "glb" in formats:
        path = os.path.join(output_dir, "model.glb")
        results["glb"] = export_glb(path)

    if "obj" in formats:
        path = os.path.join(output_dir, "model.obj")
        results["obj"] = path if export_obj(path) else None

    if "fbx" in formats:
        path = os.path.join(output_dir, "model.fbx")
        results["fbx"] = path if export_fbx(path) else None

    return {"exported": results}
