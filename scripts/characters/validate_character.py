"""
validate_character.py — Valida a geometria da modelo humana feminina.

Verifica:
  - Número de objetos / vértices / faces
  - Dimensões totais (altura)
  - Materiais presentes
  - UVs presentes
  - Normais sem defeitos evidentes
  - Presença de rig
"""

import bpy          # type: ignore
import bmesh        # type: ignore
from mathutils import Vector  # type: ignore


def validate(state):
    report = {
        "passed": True,
        "warnings": [],
        "errors": [],
        "details": {},
    }

    # Recolher todos os objetos de malha na cena
    mesh_objects = [o for o in bpy.data.objects if o.type == "MESH"]
    arm_objects = [o for o in bpy.data.objects if o.type == "ARMATURE"]

    total_verts = sum(len(o.data.vertices) for o in mesh_objects)
    total_faces = sum(len(o.data.polygons) for o in mesh_objects)

    report["details"]["mesh_object_count"] = len(mesh_objects)
    report["details"]["total_vertices"] = total_verts
    report["details"]["total_faces"] = total_faces
    report["details"]["armature_count"] = len(arm_objects)
    report["details"]["has_rig"] = len(arm_objects) > 0

    # Verificar altura (bounding box de todos os objetos)
    all_coords = []
    for obj in mesh_objects:
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            all_coords.append(world_co.z)

    if all_coords:
        height = max(all_coords) - min(all_coords)
        report["details"]["bounding_height_m"] = round(height, 3)
        if height < 1.50 or height > 2.10:
            report["warnings"].append(
                f"Altura estimada {height:.2f} m fora do intervalo esperado (1.50–2.10 m)"
            )
    else:
        report["errors"].append("Nenhuma geometria encontrada na cena.")
        report["passed"] = False

    # Verificar materiais
    objs_no_mat = [o.name for o in mesh_objects if not o.data.materials]
    if objs_no_mat:
        report["warnings"].append(f"Objetos sem material: {objs_no_mat}")

    # Verificar UVs
    objs_no_uv = [o.name for o in mesh_objects if not o.data.uv_layers]
    if objs_no_uv:
        report["warnings"].append(f"Objetos sem UV map: {objs_no_uv}")

    # Verificar normais (faces com área zero = degeneradas)
    degenerate_count = 0
    for obj in mesh_objects[:10]:  # limitar para não bloquear por demasiado tempo
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        for face in bm.faces:
            if face.calc_area() < 1e-8:
                degenerate_count += 1
        bm.free()

    if degenerate_count > 0:
        report["warnings"].append(
            f"{degenerate_count} face(s) degenerada(s) encontrada(s) (área ~0)."
        )

    report["details"]["degenerate_faces_sample"] = degenerate_count

    if report["errors"]:
        report["passed"] = False

    return report


def run(job, state):
    result = validate(state)
    state["validation"] = result
    return result
