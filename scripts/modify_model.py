"""
modify_model.py — Modificação estruturada de um projeto Blender existente.

Operação: "modify_model"

Carrega um ficheiro .blend indicado em parameters["source_blend"] e aplica
escala / rotação / posição / modificadores suportados / material. O ficheiro
original NUNCA é sobrescrito por este módulo — quem decide o caminho final
de gravação é sempre main.py (via outputs.blend), que grava uma nova versão.
"""

import math
import bpy  # type: ignore

import materials

SUPPORTED_MODIFIERS = {
    "subdivision": "SUBSURF",
    "bevel": "BEVEL",
    "decimate": "DECIMATE",
    "solidify": "SOLIDIFY",
}


def _load_source(source_blend):
    if not source_blend:
        raise ValueError("'source_blend' é obrigatório para a operação 'modify_model'.")
    with bpy.data.libraries.load(source_blend, link=False) as (data_from, data_to):
        data_to.objects = data_from.objects
    loaded = [o for o in data_to.objects if o is not None]
    for obj in loaded:
        bpy.context.collection.objects.link(obj)
    return loaded


def _resolve_targets(params, loaded_objects):
    names = params.get("objects")
    if names:
        targets = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    elif loaded_objects:
        targets = loaded_objects
    else:
        targets = list(bpy.data.objects)
    if not targets:
        raise ValueError("Nenhum objeto alvo encontrado para modificar.")
    return targets


def run(job, state):
    params = job.get("parameters", {})
    loaded = _load_source(params.get("source_blend"))
    targets = _resolve_targets(params, loaded)

    applied = {"scale": False, "rotation": False, "position": False, "modifiers": [], "material": False}

    for obj in targets:
        if "scale" in params:
            s = params["scale"]
            obj.scale = (s, s, s) if isinstance(s, (int, float)) else tuple(s)
            applied["scale"] = True

        if "rotation_degrees" in params:
            rx, ry, rz = params["rotation_degrees"]
            obj.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
            applied["rotation"] = True

        if "position" in params:
            obj.location = tuple(params["position"])
            applied["position"] = True

        for mod_request in params.get("modifiers", []):
            mod_type = SUPPORTED_MODIFIERS.get(mod_request.get("type"))
            if mod_type is None:
                raise ValueError(
                    f"Modificador '{mod_request.get('type')}' não suportado. "
                    f"Suportados: {sorted(SUPPORTED_MODIFIERS)}"
                )
            modifier = obj.modifiers.new(name=mod_request.get("type"), type=mod_type)
            for key, value in mod_request.get("settings", {}).items():
                if hasattr(modifier, key):
                    setattr(modifier, key, value)
            applied["modifiers"].append({"object": obj.name, "type": mod_request.get("type")})

        if "color" in params:
            materials.apply_color(
                obj, params["color"], params.get("metallic", 0.0), params.get("roughness", 0.5)
            )
            applied["material"] = True

    return {"objects_modified": [o.name for o in targets], "changes": applied}
