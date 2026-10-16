"""
create_model.py — Geração paramétrica de modelos 3D primitivos.

Operação: "create_model"

Suporta geradores funcionais para: cube, sphere, cylinder, cone, plane,
torus — e composições simples (lista de primitivas em "composition").

Este módulo NÃO tenta transformar descrições textuais livres em modelos
realistas: isso exigiria um gerador/algoritmo dedicado que ainda não existe
nesta versão (ver docs/ARCHITECTURE.md, secção "Trabalho futuro").
"""

import bpy  # type: ignore

import materials

SUPPORTED_TYPES = ("cube", "sphere", "cylinder", "cone", "plane", "torus")


def _clear_scene():
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
    except RuntimeError:
        # Fallback que não depende do contexto de um operador do Blender.
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _create_primitive(ptype, size, location):
    ptype = (ptype or "cube").lower()
    if ptype == "cube":
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    elif ptype == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=size / 2, location=location)
    elif ptype == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=size / 2, depth=size, location=location)
    elif ptype == "cone":
        bpy.ops.mesh.primitive_cone_add(radius1=size / 2, depth=size, location=location)
    elif ptype == "plane":
        bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    elif ptype == "torus":
        bpy.ops.mesh.primitive_torus_add(
            major_radius=size / 2, minor_radius=size / 6, location=location
        )
    else:
        raise ValueError(
            f"Tipo de primitiva '{ptype}' não suportado. Tipos suportados: {SUPPORTED_TYPES}"
        )
    return bpy.context.active_object


def run(job, state):
    params = job.get("parameters", {})
    clear = params.get("clear_scene", True)

    if clear:
        _clear_scene()

    composition = params.get("composition")
    created = []

    if composition and isinstance(composition, list):
        for item in composition:
            obj = _create_primitive(
                item.get("type", "cube"),
                float(item.get("size", 2)),
                tuple(item.get("location", (0, 0, 0))),
            )
            obj.name = item.get("name", obj.name)
            color = item.get("color")
            if color:
                materials.apply_color(
                    obj, color, item.get("metallic", 0.0), item.get("roughness", 0.5)
                )
            created.append(obj.name)
    else:
        ptype = params.get("type", "cube")
        size = float(params.get("size", 2))
        location = tuple(params.get("location", (0, 0, 0)))
        obj = _create_primitive(ptype, size, location)
        obj.name = params.get("name", obj.name)
        color = params.get("color")
        if color:
            materials.apply_color(
                obj, color, params.get("metallic", 0.0), params.get("roughness", 0.5)
            )
        created.append(obj.name)

    return {"objects_created": created}
