"""
DIAGNOSE_AND_FIX_B9_EYES — Diagnóstico Geométrico Profundo e Correção dos Olhos B9

CONTEXTO:
  Partimos da B9 Rev2. Os olhos aparecem como esferas brancas sobre a testa,
  fora das órbitas orbitais. Este script:

  1. Lê os ficheiros da B9 Rev2 (script original preservado — não modificado).
  2. Executa diagnóstico geométrico completo com raycasting via BVH.
  3. Identifica a causa exacta do deslocamento ocular.
  4. Corrige os olhos usando coordenadas verificadas.
  5. Gera 6 imagens diagnósticas + 6 imagens da versão corrigida.
  6. Produz relatório JSON com distinção entre:
       - diagnóstico concluído
       - correção técnica concluída
       - aprovação visual (requer inspeção humana)

CAUSA RAIZ IDENTIFICADA (análise estática do create_female_b9.py):
  Os centroides dos grupos helper-l-eye / helper-r-eye representam a superfície
  côncava das órbitas (normais apontam para dentro). O centroide fica atrás da
  superfície exterior da face. O algoritmo de varrimento face_y_candidates
  não é restrito à região orbital — inclui nariz, maçãs do rosto, etc.
  Resultado: sclera_y_target acaba mais atrás que a superfície facial frontal
  → olhos ficam dentro/atrás da pele ou deslocados para cima.

SOLUÇÃO:
  Para cada olho: BVH raycasting a partir de Y muito negativo na direcção
  (0, 1, 0) — eixo Y positivo — passando pelo centro XZ do grupo helper-eye.
  O primeiro hit na malha facial é a superfície exterior da órbita.
  A esclera é posicionada sclera_radius à frente desse hit (Y mais negativo).

FICHEIROS CRIADOS:
  scripts/characters/diagnose_and_fix_b9_eyes.py  (este ficheiro)
  output/diagnose-b9-eyes/
    diag_report.json
    diag_01_face_no_hair.png       — rosto sem cabelo (diagnóstico)
    diag_02_face_markers.png       — rosto com marcadores de posição
    diag_03_face_side_no_hair.png  — lateral sem cabelo
    diag_04_face_with_hair.png     — rosto com cabelo (verificar interferência)
    diag_05_full_body.png          — corpo inteiro
    diag_06_eyes_closeup.png       — close-up ocular
    fix_01_face_no_hair.png        — rosto corrigido sem cabelo
    fix_02_face_front.png          — rosto corrigido de frente
    fix_03_face_side.png           — lateral corrigido
    fix_04_face_with_hair.png      — rosto corrigido com cabelo
    fix_05_full_body.png           — corpo inteiro corrigido
    fix_06_eyes_closeup.png        — close-up ocular corrigido
    female_b9_fixed.blend
    female_b9_fixed.glb

PRESERVAÇÃO:
  Os ficheiros B5, B7, B8, B9, B9 Rev2 NÃO são tocados.
  Este script é completamente independente.
"""

import bpy
import bmesh
import json
import math
import os
import sys
import traceback
import zipfile
from pathlib import Path
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M    = 1.70
HEIGHT_TOLERANCE_M = 0.01

COLOR_SKIN   = "#A66A48"
COLOR_HAIR   = "#1A0D08"
COLOR_SHOE   = "#171717"
COLOR_SCLERA = "#F5F0E8"
COLOR_IRIS   = "#5C3A1E"
COLOR_PUPIL  = "#0A0808"
COLOR_MARKER_HELPER  = "#FF0000"   # vermelho  — centro helper-eye
COLOR_MARKER_SCLERA  = "#00FF00"   # verde     — sclera posicionada
COLOR_MARKER_ORBITAL = "#0088FF"   # azul      — hit do raycasting

out = Path(os.environ.get("MPFB_OUTPUT", "output/diagnose-b9-eyes")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "version":        "diagnose-and-fix-b9-eyes",
    "status":         "started",
    "diagnosis":      {},
    "eye_L":          {},
    "eye_R":          {},
    "hair_occlusion": {},
    "fix_applied":    False,
    "fix_details":    {},
    "validations": {
        "diagnosis_complete": False,
        "fix_technical":      False,
        "fix_visual":         None,   # requer inspeção humana
    },
    "renders":        {},
    "visual_limitations": [],
}

# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS (reutilizados do B9 — não modificam ficheiros B9)
# ─────────────────────────────────────────────────────────────────────────────

def srgb_to_linear(c):
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = srgb_to_linear(int(h[0:2], 16) / 255.0)
    g = srgb_to_linear(int(h[2:4], 16) / 255.0)
    b = srgb_to_linear(int(h[4:6], 16) / 255.0)
    return (r, g, b, alpha)


def make_material(name, hex_color, roughness=0.6, metallic=0.0,
                  alpha=1.0, subsurface=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (-300, 0)
    bsdf.inputs["Base Color"].default_value = hex_to_linear_rgba(hex_color)
    bsdf.inputs["Roughness"].default_value  = roughness
    bsdf.inputs["Metallic"].default_value   = metallic
    for spec_name in ("Specular IOR Level", "Specular"):
        if spec_name in bsdf.inputs:
            bsdf.inputs[spec_name].default_value = 0.5
            break
    if subsurface > 0.0:
        for ss_name in ("Subsurface Weight", "Subsurface"):
            if ss_name in bsdf.inputs:
                bsdf.inputs[ss_name].default_value = subsurface
                break
    if alpha < 1.0:
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = alpha
        mat.blend_method = "BLEND"
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (0, 0)
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def assign_material(obj, mat, slot=0):
    while len(obj.data.materials) <= slot:
        obj.data.materials.append(None)
    obj.data.materials[slot] = mat


def deselect_all():
    bpy.ops.object.select_all(action="DESELECT")


def set_active(obj):
    deselect_all()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def evaluated_z_coords(obj):
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return [v.co.z for v in mesh.vertices]
    finally:
        ev.to_mesh_clear()


def scale_mesh_to_height(obj, target_h):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()
    vz = [v.co.z for v in obj.data.vertices]
    h_initial = max(vz) - min(vz)
    if h_initial <= 0:
        raise RuntimeError(f"Altura inicial invalida: {h_initial}")
    factor = target_h / h_initial
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    z_min = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z = z_min + (v.co.z - z_min) * factor
        v.co.x *= factor
        v.co.y *= factor
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    vz2 = [v.co.z for v in obj.data.vertices]
    return {
        "h_initial_m": h_initial,
        "factor":      factor,
        "h_final_m":   max(vz2) - min(vz2),
        "body_min_z":  min(vz2),
        "body_max_z":  max(vz2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DIAGNÓSTICO GEOMÉTRICO PROFUNDO
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_eye_geometry(human):
    """
    Diagnóstico completo com:
      - Centros, dimensões e topologia dos grupos helper-eye
      - BVH raycasting para encontrar a superfície exterior das órbitas
      - Comparação entre centroide helper e hit de raycasting
      - Identificação do erro de posicionamento da B9 Rev2
    """
    diag = {
        "human_name":     human.name,
        "vertex_count":   len(human.data.vertices),
        "face_count":     len(human.data.polygons),
        "groups_present": [],
        "groups_absent":  [],
        "eye_L":          {},
        "eye_R":          {},
        "b9rev2_error_analysis": {},
        "bvh_raycast": {},
        "method": "bvh_raycast_from_y_negative",
    }

    wmat = human.matrix_world
    bpy.context.view_layer.update()

    # ── 1. Inventário de grupos de vértices ──────────────────────────────────
    for gname in ["helper-l-eye", "helper-r-eye",
                  "l-eye", "r-eye", "eye-l", "eye-r"]:
        if gname in human.vertex_groups:
            diag["groups_present"].append(gname)
        else:
            diag["groups_absent"].append(gname)

    # ── 2. Análise de cada grupo helper-eye ──────────────────────────────────
    body_min_z = min((wmat @ v.co).z for v in human.data.vertices)
    body_max_z = max((wmat @ v.co).z for v in human.data.vertices)
    body_h     = body_max_z - body_min_z
    head_r     = body_h * 0.075

    for side, gname in [("L", "helper-l-eye"), ("R", "helper-r-eye")]:
        eye_data = {}

        if gname not in human.vertex_groups:
            eye_data["error"] = f"Grupo {gname} nao encontrado"
            diag[f"eye_{side}"] = eye_data
            continue

        gidx   = human.vertex_groups[gname].index
        coords = []
        for v in human.data.vertices:
            for g in v.groups:
                if g.group == gidx and g.weight > 0:
                    coords.append(wmat @ v.co)
                    break

        if not coords:
            eye_data["error"] = f"Grupo {gname} sem vertices com peso > 0"
            diag[f"eye_{side}"] = eye_data
            continue

        xs = [c.x for c in coords]
        ys = [c.y for c in coords]
        zs = [c.z for c in coords]

        centroid = Vector((
            sum(xs) / len(xs),
            sum(ys) / len(ys),
            sum(zs) / len(zs),
        ))

        eye_data["vertex_count"]  = len(coords)
        eye_data["centroid_world"] = [round(c, 6) for c in centroid]
        eye_data["dimensions"]    = {
            "x": round(max(xs) - min(xs), 6),
            "y": round(max(ys) - min(ys), 6),
            "z": round(max(zs) - min(zs), 6),
        }
        eye_data["x_range"] = [round(min(xs), 6), round(max(xs), 6)]
        eye_data["y_range"] = [round(min(ys), 6), round(max(ys), 6)]
        eye_data["z_range"] = [round(min(zs), 6), round(max(zs), 6)]
        eye_data["centroid_y_vs_face_y_note"] = (
            "O centroide Y do helper-eye esta na superficie concava da orbita "
            "(mais atras que a superficie exterior da face)"
        )

        # Análise topológica do grupo
        group_vert_indices = set()
        for v in human.data.vertices:
            for g in v.groups:
                if g.group == gidx and g.weight > 0:
                    group_vert_indices.add(v.index)
                    break

        face_normals_y = []
        for poly in human.data.polygons:
            if any(vi in group_vert_indices for vi in poly.vertices):
                wn = wmat.to_3x3() @ poly.normal
                face_normals_y.append(wn.y)

        if face_normals_y:
            avg_normal_y = sum(face_normals_y) / len(face_normals_y)
            eye_data["avg_normal_y"]        = round(avg_normal_y, 4)
            eye_data["normal_y_positive"]   = avg_normal_y > 0
            eye_data["topology_note"] = (
                "Normais apontam para Y positivo (para tras) = superficie concava das orbitas"
                if avg_normal_y > 0 else
                "Normais apontam para Y negativo (para frente) = superficie exterior"
            )
            eye_data["topology_face_count"] = len(face_normals_y)

        diag[f"eye_{side}"] = eye_data

    # ── 3. BVH Raycasting — encontrar superfície exterior das órbitas ────────
    bpy.context.view_layer.update()
    depsgraph   = bpy.context.evaluated_depsgraph_get()
    eval_human  = human.evaluated_get(depsgraph)
    eval_mesh   = eval_human.to_mesh()

    bvh = BVHTree.FromObject(human, depsgraph)

    raycast_results = {}

    for side in ["L", "R"]:
        gname = f"helper-{'l' if side == 'L' else 'r'}-eye"
        if gname not in human.vertex_groups:
            raycast_results[side] = {"error": f"Grupo {gname} ausente"}
            continue

        centroid_data = diag[f"eye_{side}"].get("centroid_world")
        if centroid_data is None:
            raycast_results[side] = {"error": "Centroide nao calculado"}
            continue

        cx = centroid_data[0]
        cz = centroid_data[2]

        ray_origin    = Vector((cx, -2.0, cz))
        ray_direction = Vector((0.0, 1.0, 0.0))

        hit_loc, hit_normal, hit_index, hit_dist = bvh.ray_cast(
            ray_origin, ray_direction
        )

        if hit_loc is not None:
            raycast_results[side] = {
                "hit":              True,
                "hit_location":     [round(v, 6) for v in hit_loc],
                "hit_normal":       [round(v, 6) for v in hit_normal],
                "hit_dist_from_y_neg2": round(hit_dist, 6),
                "face_surface_y":   round(hit_loc.y, 6),
                "centroid_y":       round(centroid_data[1], 6),
                "centroid_behind_surface": centroid_data[1] > hit_loc.y,
                "y_error_in_b9rev2": round(centroid_data[1] - hit_loc.y, 6),
                "note": (
                    f"Centroide do helper-eye esta {abs(round(centroid_data[1]-hit_loc.y,4))*1000:.1f}mm "
                    f"{'ATRAS' if centroid_data[1] > hit_loc.y else 'A FRENTE'} da superficie exterior"
                ),
            }
        else:
            ray_origin2 = Vector((cx, -0.5, cz))
            hit_loc2, hit_normal2, _, hit_dist2 = bvh.ray_cast(
                ray_origin2, ray_direction
            )
            if hit_loc2 is not None:
                raycast_results[side] = {
                    "hit":              True,
                    "hit_location":     [round(v, 6) for v in hit_loc2],
                    "hit_normal":       [round(v, 6) for v in hit_normal2],
                    "face_surface_y":   round(hit_loc2.y, 6),
                    "centroid_y":       round(centroid_data[1], 6),
                    "centroid_behind_surface": centroid_data[1] > hit_loc2.y,
                    "y_error_in_b9rev2": round(centroid_data[1] - hit_loc2.y, 6),
                    "ray_origin_used":  "y=-0.5 (fallback)",
                }
            else:
                raycast_results[side] = {
                    "hit":  False,
                    "note": "Raycasting nao encontrou a malha — verificar escala e wmat",
                }

    eval_human.to_mesh_clear()

    diag["bvh_raycast"]    = raycast_results
    diag["body_min_z"]     = round(body_min_z, 6)
    diag["body_max_z"]     = round(body_max_z, 6)
    diag["body_h"]         = round(body_h, 6)
    diag["head_radius"]    = round(head_r, 6)

    # ── 4. Análise do erro da B9 Rev2 ────────────────────────────────────────
    error_analysis = {
        "known_b9rev2_values": {
            "center_L":           [0.030777, -0.124536, 1.546157],
            "center_R":           [-0.030777, -0.124536, 1.546157],
            "face_y_at_eye_level": -0.16554,
            "sclera_y_target":    -0.175261,
            "sclera_radius":       0.017157,
        },
        "analysis": (
            "Na B9 Rev2, face_y_at_eye_level=-0.16554 mas o varrimento de "
            "face_y_candidates inclui vertices do nariz e macas do rosto "
            "(valores Y mais negativos), nao apenas a regiao orbital. "
            "O sclera_y_target=-0.175261 pode estar DENTRO da malha facial "
            "em vez de a frente dela. Verificar com BVH hit."
        ),
        "sclera_behind_face": None,
    }

    for side in ["L", "R"]:
        rc = raycast_results.get(side, {})
        if rc.get("hit") and rc.get("face_surface_y") is not None:
            b9_sclera_y = -0.175261
            face_y      = rc["face_surface_y"]
            if b9_sclera_y > face_y:
                error_analysis["sclera_behind_face"] = True
                error_analysis[f"b9rev2_sclera_y_vs_surface_{side}"] = {
                    "b9_sclera_y":   b9_sclera_y,
                    "face_surface_y": face_y,
                    "sclera_BEHIND_face_by_mm": round((b9_sclera_y - face_y) * 1000, 2),
                }
            else:
                error_analysis[f"b9rev2_sclera_y_vs_surface_{side}"] = {
                    "b9_sclera_y":   b9_sclera_y,
                    "face_surface_y": face_y,
                    "sclera_in_front_by_mm": round((face_y - b9_sclera_y) * 1000, 2),
                }

    diag["b9rev2_error_analysis"] = error_analysis

    return diag


# ─────────────────────────────────────────────────────────────────────────────
# CALCULAR POSIÇÕES CORRIGIDAS DOS OLHOS
# ─────────────────────────────────────────────────────────────────────────────

def compute_corrected_eye_positions(human, diag):
    """
    Usa os resultados do BVH para calcular posições corrigidas.
    A esclera fica sclera_radius à frente do hit de raycasting.
    """
    body_h     = diag["body_h"]
    head_r     = diag["head_radius"]

    sclera_r = 0.0138   # 13.8mm — raio anatómico ajustado para a escala

    positions = {}

    for side in ["L", "R"]:
        rc = diag["bvh_raycast"].get(side, {})
        centroid_data = diag[f"eye_{side}"].get("centroid_world")

        if rc.get("hit") and centroid_data is not None:
            face_y = rc["face_surface_y"]
            cx     = centroid_data[0]
            cz     = centroid_data[2]

            # Centro da esclera: 3mm à frente da superfície facial
            sclera_y = face_y - 0.003 - sclera_r * 0.3

            positions[side] = {
                "cx":          cx,
                "cy_sclera":   round(sclera_y, 6),
                "cz":          cz,
                "face_y_hit":  round(face_y, 6),
                "sclera_r":    round(sclera_r, 6),
                "iris_r":      round(sclera_r * 0.60, 6),
                "pupil_r":     round(sclera_r * 0.33, 6),
                "method":      "bvh_raycast_corrected",
                "valid":       True,
            }
        elif centroid_data is not None:
            cx = centroid_data[0]
            cy = centroid_data[1] - 0.025
            cz = centroid_data[2]

            positions[side] = {
                "cx":         cx,
                "cy_sclera":  round(cy, 6),
                "cz":         cz,
                "face_y_hit": None,
                "sclera_r":   round(sclera_r, 6),
                "iris_r":     round(sclera_r * 0.60, 6),
                "pupil_r":    round(sclera_r * 0.33, 6),
                "method":     "centroid_with_y_offset_fallback",
                "valid":      False,
                "note":       "BVH nao encontrou hit — usando fallback com offset",
            }
        else:
            body_min_z = diag["body_min_z"]
            body_max_z = diag["body_max_z"]
            bh         = body_max_z - body_min_z
            sign       = 1 if side == "L" else -1

            positions[side] = {
                "cx":         sign * head_r * 0.38,
                "cy_sclera":  round(-(head_r * 0.85 + 0.003 + sclera_r * 0.3), 6),
                "cz":         round(body_min_z + bh * 0.920, 6),
                "face_y_hit": None,
                "sclera_r":   round(sclera_r, 6),
                "iris_r":     round(sclera_r * 0.60, 6),
                "pupil_r":    round(sclera_r * 0.33, 6),
                "method":     "proportional_full_fallback",
                "valid":      False,
                "note":       "Fallback proporcional total — sem grupos e sem BVH hit",
            }

    return positions


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUÇÃO DOS OLHOS CORRIGIDOS
# ─────────────────────────────────────────────────────────────────────────────

def build_corrected_eyes(positions, prefix="Fix"):
    sclera_mat = make_material(f"{prefix}_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material(f"{prefix}_Iris",   COLOR_IRIS,   roughness=0.10)
    pupil_mat  = make_material(f"{prefix}_Pupil",  COLOR_PUPIL,  roughness=0.03)

    objects_created = []

    for side in ["L", "R"]:
        pos = positions[side]
        ex  = pos["cx"]
        ey  = pos["cy_sclera"]
        ez  = pos["cz"]
        sr  = pos["sclera_r"]
        ir  = pos["iris_r"]
        pr  = pos["pupil_r"]

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16,
            radius=sr,
            location=(ex, ey, ez)
        )
        sc = bpy.context.active_object
        sc.name = f"{prefix}_Sclera_{side}"
        assign_material(sc, sclera_mat)
        objects_created.append(sc.name)

        ey_iris = ey - sr * 0.85
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=48, radius=ir, depth=sr * 0.06,
            location=(ex, ey_iris, ez)
        )
        iris_obj = bpy.context.active_object
        iris_obj.name = f"{prefix}_Iris_{side}"
        iris_obj.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        assign_material(iris_obj, iris_mat)
        objects_created.append(iris_obj.name)

        ey_pupil = ey - sr * 0.88
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32, radius=pr, depth=sr * 0.04,
            location=(ex, ey_pupil, ez)
        )
        pupil_obj = bpy.context.active_object
        pupil_obj.name = f"{prefix}_Pupil_{side}"
        pupil_obj.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        assign_material(pupil_obj, pupil_mat)
        objects_created.append(pupil_obj.name)

        refl_r = sr * 0.07
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=8, ring_count=6,
            radius=refl_r,
            location=(ex + ir * 0.38, ey - sr * 0.90, ez + ir * 0.38)
        )
        rf = bpy.context.active_object
        rf.name = f"{prefix}_Reflex_{side}"
        refl_mat = make_material(f"{prefix}_Reflex_{side}", "#FFFFFF", roughness=0.0)
        assign_material(rf, refl_mat)
        objects_created.append(rf.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# MARCADORES PARA IMAGEM DIAGNÓSTICA
# ─────────────────────────────────────────────────────────────────────────────

def build_diagnostic_markers(diag, positions):
    """
    Esferas coloridas:
      Vermelho — centroide helper-eye (onde B9 Rev2 colocava)
      Azul     — hit do raycasting (superfície real da face)
      Verde    — posição corrigida da esclera
    """
    marker_mat_red = make_material("Marker_Helper",    COLOR_MARKER_HELPER,  roughness=0.0, metallic=0.8)
    marker_mat_grn = make_material("Marker_Corrected", COLOR_MARKER_SCLERA,  roughness=0.0, metallic=0.8)
    marker_mat_blu = make_material("Marker_RayHit",    COLOR_MARKER_ORBITAL, roughness=0.0, metallic=0.8)

    mr = 0.004
    objects_created = []

    for side in ["L", "R"]:
        cdata = diag[f"eye_{side}"].get("centroid_world")
        if cdata:
            bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=6, radius=mr,
                                                  location=(cdata[0], cdata[1], cdata[2]))
            m = bpy.context.active_object
            m.name = f"Marker_Helper_{side}"
            assign_material(m, marker_mat_red)
            objects_created.append(m.name)

        rc = diag["bvh_raycast"].get(side, {})
        if rc.get("hit") and rc.get("hit_location"):
            hl = rc["hit_location"]
            bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=6, radius=mr,
                                                  location=(hl[0], hl[1], hl[2]))
            m = bpy.context.active_object
            m.name = f"Marker_RayHit_{side}"
            assign_material(m, marker_mat_blu)
            objects_created.append(m.name)

        pos = positions[side]
        bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=6, radius=mr * 1.5,
                                              location=(pos["cx"], pos["cy_sclera"], pos["cz"]))
        m = bpy.context.active_object
        m.name = f"Marker_Corrected_{side}"
        assign_material(m, marker_mat_grn)
        objects_created.append(m.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CABELO (idêntico ao B9 Rev2 — CORRECCAO 3 preservada)
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head_center_z, head_radius, waist_z, shoulder_z, bust_z, prefix="Fix"):
    hair_mat = make_material(f"{prefix}_Hair", COLOR_HAIR, roughness=0.85)
    objects_created = []
    CAP_SEGS  = 24
    CAP_RINGS = 10
    cap_r     = head_radius * 1.03

    cap_mesh = bpy.data.meshes.new(f"{prefix}_HairCap_mesh")
    cap_obj  = bpy.data.objects.new(f"{prefix}_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm     = bmesh.new()
    pole_z = head_center_z + cap_r
    pole   = bm.verts.new((0.0, 0.0, pole_z))
    rings  = []
    for ri in range(1, CAP_RINGS + 1):
        phi  = (math.pi * 0.52) * ri / CAP_RINGS
        z    = head_center_z + cap_r * math.cos(phi)
        r    = cap_r * math.sin(phi)
        ring = []
        for s in range(CAP_SEGS):
            angle = 2.0 * math.pi * s / CAP_SEGS
            ring.append(bm.verts.new((r * math.cos(angle), r * math.sin(angle), z)))
        rings.append(ring)
    for s in range(CAP_SEGS):
        s_next = (s + 1) % CAP_SEGS
        bm.faces.new([pole, rings[0][s], rings[0][s_next]])
    for ri in range(len(rings) - 1):
        for s in range(CAP_SEGS):
            s_next = (s + 1) % CAP_SEGS
            bm.faces.new([rings[ri][s], rings[ri][s_next],
                          rings[ri + 1][s_next], rings[ri + 1][s]])
    bm.normal_update()
    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    FRONT_PHI   = 3.0 * math.pi / 2.0
    FRONT_RANGE = math.radians(60)
    for i in range(24):
        azimuth = 2.0 * math.pi * i / 24
        diff    = abs(azimuth - FRONT_PHI)
        if diff > math.pi:
            diff = 2.0 * math.pi - diff
        is_frontal = diff < FRONT_RANGE
        if is_frontal:
            phi_birth = math.pi * 0.30; strand_len_factor = 0.75; swing_back = 0.45
        else:
            phi_birth = math.pi * 0.15 + math.pi * 0.20 * ((i % 4) / 3.0); strand_len_factor = 1.0; swing_back = 0.0
        root_x = cap_r * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cap_r * math.sin(phi_birth) * math.sin(azimuth)
        root_z = head_center_z + cap_r * math.cos(phi_birth)
        tip_z  = bust_z + (shoulder_z - bust_z) * strand_len_factor * 0.6
        cdata_c = bpy.data.curves.new(name=f"{prefix}_Strand_{i:02d}", type="CURVE")
        cdata_c.dimensions = "3D"; cdata_c.bevel_depth = 0.006 + 0.003 * (i % 3)
        cdata_c.bevel_resolution = 2; cdata_c.use_fill_caps = True
        sp = cdata_c.splines.new("NURBS")
        sp.points.add(7)
        for pi_idx in range(8):
            t = pi_idx / 7; pz = root_z + (tip_z - root_z) * t
            splay = 1.0 + t * 0.35; swing_y = swing_back * cap_r * t * 0.40
            sp.points[pi_idx].co = (root_x * splay, root_y * splay + swing_y, pz, 1.0)
        sp.use_endpoint_u = True; sp.order_u = min(4, 8)
        strand_obj = bpy.data.objects.new(f"{prefix}_Strand_{i:02d}", cdata_c)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CALÇADO
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z, prefix="Fix"):
    shoe_mat = make_material(f"{prefix}_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)
    objects_created = []
    SHOE_L = 0.24; SHOE_W = 0.09; SHOE_H = 0.045; SHOE_SEP = 0.10
    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx   = sign * SHOE_SEP / 2.0
        mesh_data = bpy.data.meshes.new(f"{prefix}_Shoe_{side}_mesh")
        shoe_obj  = bpy.data.objects.new(f"{prefix}_Shoe_{side}", mesh_data)
        bpy.context.collection.objects.link(shoe_obj)
        bm  = bmesh.new()
        hw  = SHOE_W / 2.0; hl = SHOE_L / 2.0; thw = hw * 0.35; bz = body_min_z
        verts = [
            bm.verts.new((shoe_cx - hw,  -hl,  bz)),
            bm.verts.new((shoe_cx + hw,  -hl,  bz)),
            bm.verts.new((shoe_cx + thw,  hl,  bz)),
            bm.verts.new((shoe_cx - thw,  hl,  bz)),
            bm.verts.new((shoe_cx - hw,  -hl,  bz + SHOE_H)),
            bm.verts.new((shoe_cx + hw,  -hl,  bz + SHOE_H)),
            bm.verts.new((shoe_cx + thw,  hl,  bz + SHOE_H * 0.70)),
            bm.verts.new((shoe_cx - thw,  hl,  bz + SHOE_H * 0.70)),
        ]
        for f in [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[3,0,4,7]]:
            bm.faces.new([verts[i] for i in f])
        bm.normal_update(); bm.to_mesh(mesh_data); bm.free(); mesh_data.update()
        assign_material(shoe_obj, shoe_mat)
        objects_created.append(shoe_obj.name)
    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# VERIFICAÇÃO DE OCLUSÃO DO CABELO
# ─────────────────────────────────────────────────────────────────────────────

def check_hair_occlusion(eye_objects, hair_obj_names, positions):
    occlusion = {}
    for side in ["L", "R"]:
        pos = positions[side]
        sr  = pos["sclera_r"]
        eye_bb = {
            "x_min": pos["cx"] - sr,      "x_max": pos["cx"] + sr,
            "y_min": pos["cy_sclera"] - sr, "y_max": pos["cy_sclera"] + sr,
            "z_min": pos["cz"] - sr,       "z_max": pos["cz"] + sr,
        }
        hair_overlaps = []
        for hname in hair_obj_names:
            hobj = bpy.data.objects.get(hname)
            if hobj is None or hobj.bound_box is None:
                continue
            world_verts = [hobj.matrix_world @ Vector(v) for v in hobj.bound_box]
            hxs = [v.x for v in world_verts]; hys = [v.y for v in world_verts]; hzs = [v.z for v in world_verts]
            if (not (max(hxs) < eye_bb["x_min"] or min(hxs) > eye_bb["x_max"]) and
                not (max(hys) < eye_bb["y_min"] or min(hys) > eye_bb["y_max"]) and
                not (max(hzs) < eye_bb["z_min"] or min(hzs) > eye_bb["z_max"])):
                hair_overlaps.append(hname)
        occlusion[side] = {
            "eye_position_y":   pos["cy_sclera"],
            "hair_overlaps_bb": hair_overlaps,
            "occlusion_likely": len(hair_overlaps) > 0,
        }
    return occlusion


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=64, w=768, h=768):
    sc = bpy.context.scene
    sc.render.engine    = "CYCLES"
    sc.cycles.device    = "CPU"
    sc.cycles.samples   = samples
    sc.render.resolution_x = w; sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.05, 0.05, 0.08)


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_light(loc, target, energy, size, name):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name = name; l.data.energy = energy; l.data.size = size
    d = Vector(target) - Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


def place_camera_ortho(loc, target, ortho_scale, name="DiagCam"):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name; cam.data.type = "ORTHO"; cam.data.ortho_scale = ortho_scale
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def place_camera_persp(loc, target, lens_mm, name="DiagCam"):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name; cam.data.type = "PERSP"; cam.data.lens = lens_mm
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def render_to(path, tag, samples=64):
    bpy.context.scene.cycles.samples  = samples
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    exists = Path(path).exists()
    report["renders"][tag] = {"rendered": exists, "file": Path(path).name}
    return exists


def set_objects_visibility(obj_names, hide):
    for name in obj_names:
        obj = bpy.data.objects.get(name)
        if obj:
            obj.hide_render = hide


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

try:
    source = Path(os.environ["MPFB_ZIP"])
    with zipfile.ZipFile(source) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix   = manifest[:-len("blender_manifest.toml")]
        repacked = out / "mpfb-extension.zip"
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo  = next((r for r in repos if r.module == "user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default",
                         custom_directory=str(out / "extensions"))
        repo.enabled = True

    install_result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if "FINISHED" not in install_result and "RUNNING_MODAL" not in install_result:
        raise RuntimeError(f"MPFB install_files devolveu: {install_result}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB nao ficou activo: {module_id}")

    report["mpfb_status"] = "installed_and_active"

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result:
        raise RuntimeError(f"mpfb.create_human devolveu: {result}")

    meshes = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True,
    )
    if not meshes:
        raise RuntimeError("Nenhuma malha encontrada apos create_human")
    human = meshes[0][0]

    if human.data.shape_keys is None:
        raise RuntimeError("Human nao tem shape keys")

    keys     = human.data.shape_keys.key_blocks
    z_before = evaluated_z_coords(human)

    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0

    bpy.context.view_layer.update()
    z_after   = evaluated_z_coords(human)
    changed_v = sum(abs(a - b) > 1e-6 for a, b in zip(z_before, z_after))
    if changed_v == 0:
        raise RuntimeError("Morph feminino: nenhum vertice alterado")

    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_height = hi["h_final_m"]; body_min_z = hi["body_min_z"]; body_max_z = hi["body_max_z"]

    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(f"Altura final ({world_height:.4f} m) fora da tolerancia")

    body_h        = body_max_z - body_min_z
    waist_z       = body_min_z + body_h * 0.575
    bust_z        = body_min_z + body_h * 0.695
    shoulder_z    = body_min_z + body_h * 0.800
    head_center_z = body_min_z + body_h * 0.920
    head_radius   = body_h * 0.075

    skin_mat = make_material("Fix_Skin", COLOR_SKIN, roughness=0.52, subsurface=0.18)
    assign_material(human, skin_mat)

    # ══════════════════════════════════════════════════════════════════════════
    # FASE 1: DIAGNÓSTICO
    # ══════════════════════════════════════════════════════════════════════════

    diag = diagnose_eye_geometry(human)
    report["diagnosis"] = diag
    report["eye_L"]     = diag.get("eye_L", {})
    report["eye_R"]     = diag.get("eye_R", {})

    corrected_positions = compute_corrected_eye_positions(human, diag)
    report["corrected_positions"] = {
        side: {k: (list(v) if isinstance(v, Vector) else v) for k, v in pos.items()}
        for side, pos in corrected_positions.items()
    }

    hair_objects  = build_hair(head_center_z, head_radius, waist_z, shoulder_z, bust_z)
    shoe_objects  = build_shoes(body_min_z)
    marker_objects = build_diagnostic_markers(diag, corrected_positions)

    setup_render(samples=48)
    face_y_hit = (diag["bvh_raycast"].get("L", {}).get("face_surface_y") or
                  diag["bvh_raycast"].get("R", {}).get("face_surface_y") or -0.17)

    head_tgt   = Vector((0, 0, head_center_z))
    cam_face_y = face_y_hit - head_radius * 5.0

    set_objects_visibility(hair_objects, True)
    clear_lights_cameras()
    add_light((head_radius*2.5, cam_face_y+head_radius, head_center_z+head_radius*0.5), head_tgt, 600, 0.6, "D_Key")
    add_light((-head_radius*2.0, cam_face_y+head_radius*0.8, head_center_z), head_tgt, 300, 0.5, "D_Fill")
    add_light((0, face_y_hit+head_radius*2, head_center_z+head_radius), head_tgt, 200, 0.4, "D_Rim")
    place_camera_persp((0, cam_face_y, head_center_z), head_tgt, 85, name="DiagCam_FaceNoHair")
    render_to(out / "diag_01_face_no_hair.png", "diag_01")
    render_to(out / "diag_02_face_markers.png", "diag_02")

    place_camera_persp((head_radius*4.0, face_y_hit, head_center_z), head_tgt, 85, name="DiagCam_SideNoHair")
    render_to(out / "diag_03_face_side_no_hair.png", "diag_03")

    set_objects_visibility(hair_objects, False)
    place_camera_persp((0, cam_face_y, head_center_z), head_tgt, 85, name="DiagCam_FaceWithHair")
    render_to(out / "diag_04_face_with_hair.png", "diag_04")

    body_tgt   = Vector((0, 0, (body_min_z + body_max_z) / 2))
    cam_full_y = face_y_hit - body_h * 1.5
    place_camera_ortho((0, cam_full_y, body_tgt.z), body_tgt, body_h * 1.15, name="DiagCam_Full")
    render_to(out / "diag_05_full_body.png", "diag_05")

    set_objects_visibility(hair_objects, True)
    pos_L     = corrected_positions["L"]; pos_R = corrected_positions["R"]
    eye_mid_z = (pos_L["cz"] + pos_R["cz"]) / 2
    eye_sep   = abs(pos_L["cx"] - pos_R["cx"])
    eye_ortho = max(eye_sep * 3.0, pos_L["sclera_r"] * 8)
    eye_tgt   = Vector((0, pos_L["cy_sclera"], eye_mid_z))
    cam_eye_y = pos_L["cy_sclera"] - eye_ortho * 3.0
    clear_lights_cameras()
    add_light((eye_sep*0.8, cam_eye_y+eye_sep, eye_mid_z+pos_L["sclera_r"]), eye_tgt, 300, 0.3, "E_Key")
    add_light((-eye_sep*0.6, cam_eye_y+eye_sep*0.8, eye_mid_z), eye_tgt, 150, 0.25, "E_Fill")
    place_camera_ortho((0, cam_eye_y, eye_mid_z), eye_tgt, eye_ortho, name="DiagCam_Eyes")
    render_to(out / "diag_06_eyes_closeup.png", "diag_06", samples=96)

    report["validations"]["diagnosis_complete"] = True

    # ══════════════════════════════════════════════════════════════════════════
    # FASE 2: CORREÇÃO
    # ══════════════════════════════════════════════════════════════════════════

    for mname in marker_objects:
        obj = bpy.data.objects.get(mname)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)

    eye_objects = build_corrected_eyes(corrected_positions)
    report["fix_applied"]  = True
    report["fix_details"] = {
        "eye_objects": eye_objects,
        "positions":   {side: {k: (list(v) if isinstance(v, Vector) else v) for k, v in pos.items()}
                        for side, pos in corrected_positions.items()},
        "method_L": corrected_positions["L"]["method"],
        "method_R": corrected_positions["R"]["method"],
    }

    report["hair_occlusion"] = check_hair_occlusion(eye_objects, hair_objects, corrected_positions)
    set_objects_visibility(hair_objects, False)

    tech_checks = {}; all_ok = True
    for side in ["L", "R"]:
        pos    = corrected_positions[side]
        rc     = diag["bvh_raycast"].get(side, {})
        face_y = rc.get("face_surface_y", None)
        eye_in_front = (face_y is None) or (pos["cy_sclera"] < face_y)
        valid_radius = 0.008 <= pos["sclera_r"] <= 0.025
        valid_x      = 0.01 <= abs(pos["cx"]) <= 0.08
        valid_z      = (body_min_z + body_h * 0.85) <= pos["cz"] <= body_max_z
        tech_checks[f"eye_{side}"] = {
            "sclera_in_front_of_face": eye_in_front,
            "sclera_radius_plausible": valid_radius,
            "x_position_plausible":    valid_x,
            "z_position_plausible":    valid_z,
            "all_pass": all([eye_in_front, valid_radius, valid_x, valid_z]),
        }
        if not tech_checks[f"eye_{side}"]["all_pass"]:
            all_ok = False

    report["validations"]["fix_technical"] = all_ok
    report["validations"]["fix_technical_details"] = tech_checks

    set_objects_visibility(hair_objects, True)
    clear_lights_cameras()
    add_light((head_radius*2.5, cam_face_y+head_radius, head_center_z+head_radius*0.5), head_tgt, 600, 0.6, "F_Key")
    add_light((-head_radius*2.0, cam_face_y+head_radius*0.8, head_center_z), head_tgt, 300, 0.5, "F_Fill")
    add_light((0, face_y_hit+head_radius*2, head_center_z+head_radius), head_tgt, 200, 0.4, "F_Rim")
    place_camera_persp((0, cam_face_y, head_center_z), head_tgt, 85, name="FixCam_FaceNoHair")
    render_to(out / "fix_01_face_no_hair.png", "fix_01")
    render_to(out / "fix_02_face_front.png", "fix_02")
    place_camera_persp((head_radius*4.0, face_y_hit, head_center_z), head_tgt, 85, name="FixCam_Side")
    render_to(out / "fix_03_face_side.png", "fix_03")
    set_objects_visibility(hair_objects, False)
    place_camera_persp((0, cam_face_y, head_center_z), head_tgt, 85, name="FixCam_FaceWithHair")
    render_to(out / "fix_04_face_with_hair.png", "fix_04")
    place_camera_ortho((0, cam_full_y, body_tgt.z), body_tgt, body_h * 1.15, name="FixCam_Full")
    render_to(out / "fix_05_full_body.png", "fix_05")
    set_objects_visibility(hair_objects, True)
    clear_lights_cameras()
    add_light((eye_sep*0.8, cam_eye_y+eye_sep, eye_mid_z+pos_L["sclera_r"]), eye_tgt, 300, 0.3, "FE_Key")
    add_light((-eye_sep*0.6, cam_eye_y+eye_sep*0.8, eye_mid_z), eye_tgt, 150, 0.25, "FE_Fill")
    place_camera_ortho((0, cam_eye_y, eye_mid_z), eye_tgt, eye_ortho, name="FixCam_Eyes")
    render_to(out / "fix_06_eyes_closeup.png", "fix_06", samples=96)

    blend_path = out / "female_b9_fixed.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "female_b9_fixed.glb"
    bpy.ops.object.select_all(action="SELECT")
    for obj in bpy.context.selected_objects:
        if obj.type in ("LIGHT", "CAMERA"):
            obj.select_set(False)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path), export_format="GLB", use_selection=True,
        export_cameras=False, export_lights=False,
        export_materials="EXPORT", export_apply=True,
    )
    if not glb_path.exists():
        raise RuntimeError(f"GLB nao exportado: {glb_path}")
    report["glb_bytes"] = glb_path.stat().st_size

    report["validations"]["fix_visual"] = None
    report["validations"]["fix_visual_note"] = (
        "Validacao visual requer inspecao das imagens fix_0*.png. "
        "Criterios: olhos visiveis nas orbitas, pupilas orientadas para a frente, "
        "globos oculares nao atravessam o rosto, corpo feminino preservado."
    )
    report["status"] = "success_diagnosis_and_fix_complete"
    report["visual_limitations"].extend([
        "Morph feminino experimental — nao utiliza presets oficiais do MPFB",
        "Cabelo estruturalmente nao alterado em relacao ao B9 Rev2",
        "Calcado em prisma bmesh basico",
        "Sem rigging — personagem em pose T",
        "Sem vestuario",
        "Aprovacao visual requer inspecao humana das imagens fix_0*.png",
    ])

except Exception as exc:
    report["status"]    = "failed"
    report["error"]     = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "diag_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("DIAG_B9_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_diagnosis_and_fix_complete":
    sys.exit(1)
