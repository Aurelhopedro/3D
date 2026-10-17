"""
B9 — Modelo Feminino Experimental — Olhos Nativos do MPFB.

DERIVADA DA B7. Todas as correções da B7 são preservadas:
  CORRECCAO 1 (B7) — Y negativo = frente no MPFB
  CORRECCAO 2 (B7) — Sem vestido
  CORRECCAO 3 (B7) — Cabelo frontal corrigido

NOVIDADE B9 — Sistema ocular nativo com dupla abordagem:

  ABORDAGEM A (prioritária):
    Tentar aproveitar a geometria dos grupos helper-l-eye e helper-r-eye
    como base para os globos oculares. Se os grupos tiverem faces próprias
    e geometria volumétrica adequada, separar como objecto independente.

  ABORDAGEM B (fallback automático):
    Se a geometria dos helpers não for adequada (plana, sem faces, ou poucos
    vértices), criar novos globos oculares usando os centróides verificados
    pelos diagnósticos:
      LEFT_EYE_CENTER  ≈ ( 0.03078, -0.12454, 1.54615) — ANTES da escala
      RIGHT_EYE_CENTER ≈ (-0.03078, -0.12454, 1.54615) — ANTES da escala

    Estes centros são REFERÊNCIAS do diagnóstico, não constantes universais.
    O script adapta-os proporcionalmente à altura final da personagem.

REGRA FUNDAMENTAL:
  Não modificar B5, B7 ou B8. Ficheiro completamente independente.
  Os centros dos grupos helper-eye são calculados em tempo real —
  não são constantes hardcoded.

IMAGENS GERADAS:
  b9_01_full_front.png    — corpo completo
  b9_02_face_front.png    — rosto de frente
  b9_03_face_side.png     — rosto lateral
  b9_04_face_quarter.png  — rosto em quarto de volta
  b9_05_eyes_closeup.png  — olhos em grande plano

OUTPUTS:
  female_b9.blend
  female_b9.glb
  b9_report.json
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

# Referências do diagnóstico (escala MPFB original, antes da escala para 1.70m)
# Usadas apenas como fallback proporcional se os grupos helper não tiverem geometria.
DIAG_LEFT_EYE  = Vector(( 0.03078, -0.12454, 1.54615))
DIAG_RIGHT_EYE = Vector((-0.03078, -0.12454, 1.54615))

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-b9")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender":    bpy.app.version_string,
    "version":    "b9",
    "status":     "started",
    "eye_method": None,          # "A_native_geometry" | "B_new_spheres_from_centroid"
    "eye_centers_used": {},
    "helper_group_analysis": {},
    "components": {},
    "validations": {},
    "visual_limitations": [],
    "renders": {},
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS (idênticos ao B7)
# ─────────────────────────────────────────────────────────────────────────────

def srgb_to_linear(c: float) -> float:
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgba(hex_color: str, alpha: float = 1.0) -> tuple:
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
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic

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


# ─────────────────────────────────────────────────────────────────────────────
# ESCALA (idêntica ao B7)
# ─────────────────────────────────────────────────────────────────────────────

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
# ANÁLISE DE GRUPO DE VÉRTICES
# ─────────────────────────────────────────────────────────────────────────────

def analyse_vertex_group(obj, group_name):
    """
    Analisa um grupo de vértices e devolve dict com:
      vertex_count, face_count, center, dimensions, geometry_type, reusable
    Devolve None se o grupo não existir.
    """
    if group_name not in obj.vertex_groups:
        return None

    gidx = obj.vertex_groups[group_name].index
    wmat = obj.matrix_world

    group_verts = []
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == gidx and g.weight > 0:
                group_verts.append((v.index, wmat @ v.co))
                break

    if not group_verts:
        return {"vertex_count": 0, "group_exists": True, "note": "sem vértices com peso > 0"}

    vidx_set = {vi for vi, _ in group_verts}
    coords    = [wco for _, wco in group_verts]

    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]

    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    cz = sum(zs) / len(zs)

    face_count = sum(
        1 for f in obj.data.polygons
        if all(vi in vidx_set for vi in f.vertices)
    )

    x_range = max(xs) - min(xs)
    y_range = max(ys) - min(ys)
    z_range = max(zs) - min(zs)
    flatness = y_range / max(z_range, x_range, 1e-6)

    if len(group_verts) < 10:
        geo_type = "landmark_too_few_verts"
        reusable = False
    elif face_count == 0:
        geo_type = "weight_region_no_faces"
        reusable = False
    elif flatness < 0.25:
        geo_type = "flat_helper_surface"
        reusable = False
    elif flatness > 0.6 and face_count > 20:
        geo_type = "volumetric_eyeball_candidate"
        reusable = True
    else:
        geo_type = "intermediate_needs_validation"
        reusable = False

    return {
        "vertex_count":  len(group_verts),
        "face_count":    face_count,
        "center":        Vector((cx, cy, cz)),
        "dimensions":    {"x": x_range, "y": y_range, "z": z_range},
        "flatness":      flatness,
        "geometry_type": geo_type,
        "reusable":      reusable,
        "vertex_indices": sorted(vidx_set),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ABORDAGEM A — Separar geometria nativa
# ─────────────────────────────────────────────────────────────────────────────

def build_eyes_approach_a(human, group_data_L, group_data_R,
                           sclera_mat, iris_mat, pupil_mat, head_radius):
    """
    Tenta criar olhos a partir da geometria nativa do MPFB.
    Separa os vértices do grupo helper-*-eye, cria um novo objecto,
    e adiciona íris + pupila por cima.
    Devolve lista de nomes de objectos criados, ou None se falhar.
    """
    objects_created = []

    for side, gdata in [("L", group_data_L), ("R", group_data_R)]:
        group_name = f"helper-{'l' if side == 'L' else 'r'}-eye"
        try:
            # Duplicar o body
            deselect_all()
            human.select_set(True)
            bpy.context.view_layer.objects.active = human
            bpy.ops.object.duplicate(linked=False)
            dup = bpy.context.active_object
            dup.name = f"B9_Sclera_Native_{side}"

            # Modo edição: apagar tudo excepto o grupo
            vidx_set = set(gdata["vertex_indices"])
            bpy.ops.object.mode_set(mode="EDIT")
            bm = bmesh.from_edit_mesh(dup.data)
            bm.verts.ensure_lookup_table()
            to_delete = [v for v in bm.verts if v.index not in vidx_set]
            bmesh.ops.delete(bm, geom=to_delete, context="VERTS")
            bmesh.update_edit_mesh(dup.data)
            bpy.ops.object.mode_set(mode="OBJECT")

            # Verificar se sobrou algo
            if len(dup.data.vertices) < 10:
                bpy.data.objects.remove(dup, do_unlink=True)
                return None

            assign_material(dup, sclera_mat)
            objects_created.append(dup.name)

            # Centro do objecto para posicionar íris e pupila
            ec = gdata["center"]
            hr = head_radius
            iris_r  = hr * 0.135 * 0.60
            pupil_r = iris_r * 0.50
            depth   = hr * 0.135 * 0.82

            bpy.ops.mesh.primitive_cylinder_add(
                vertices=24, radius=iris_r, depth=hr * 0.135 * 0.12,
                location=(ec.x, ec.y - depth * 0.78, ec.z)
            )
            ir = bpy.context.active_object
            ir.name = f"B9_Iris_{side}"
            assign_material(ir, iris_mat)
            objects_created.append(ir.name)

            bpy.ops.mesh.primitive_cylinder_add(
                vertices=20, radius=pupil_r, depth=hr * 0.135 * 0.08,
                location=(ec.x, ec.y - depth * 0.82, ec.z)
            )
            pu = bpy.context.active_object
            pu.name = f"B9_Pupil_{side}"
            assign_material(pu, pupil_mat)
            objects_created.append(pu.name)

        except Exception as e:
            report["visual_limitations"].append(
                f"Abordagem A falhou para olho {side}: {e}"
            )
            return None

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# ABORDAGEM B — Novos globos com centros verificados
# ─────────────────────────────────────────────────────────────────────────────

def build_eyes_approach_b(center_L, center_R, head_radius,
                           sclera_mat, iris_mat, pupil_mat):
    """
    Cria globos oculares novos posicionados pelos centros verificados.
    center_L e center_R são Vector() em coordenadas de mundo.
    """
    objects_created = []

    for side, center in [("L", center_L), ("R", center_R)]:
        ex, ey, ez = center.x, center.y, center.z

        sclera_r     = head_radius * 0.135
        iris_r       = sclera_r * 0.60
        pupil_r      = iris_r   * 0.50
        depth_offset = sclera_r * 0.82

        # Esclera — esfera ligeiramente dentro do rosto
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24, ring_count=12,
            radius=sclera_r,
            location=(ex, ey - sclera_r * 0.05, ez)
        )
        sc = bpy.context.active_object
        sc.name = f"B9_Sclera_{side}"
        assign_material(sc, sclera_mat)
        objects_created.append(sc.name)

        # Íris — disco orientado para Y negativo (frente)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32, radius=iris_r, depth=sclera_r * 0.12,
            location=(ex, ey - depth_offset * 0.78, ez)
        )
        ir = bpy.context.active_object
        ir.name = f"B9_Iris_{side}"
        assign_material(ir, iris_mat)
        objects_created.append(ir.name)

        # Pupila — disco concêntrico
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=24, radius=pupil_r, depth=sclera_r * 0.08,
            location=(ex, ey - depth_offset * 0.82, ez)
        )
        pu = bpy.context.active_object
        pu.name = f"B9_Pupil_{side}"
        assign_material(pu, pupil_mat)
        objects_created.append(pu.name)

        # Reflexo luminoso discreto
        refl_r = sclera_r * 0.08
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=8, ring_count=6,
            radius=refl_r,
            location=(
                ex + sclera_r * 0.12,
                ey - depth_offset * 0.88,
                ez + sclera_r * 0.14
            )
        )
        rf = bpy.context.active_object
        rf.name = f"B9_Reflex_{side}"
        refl_mat = make_material(f"B9_Reflex_{side}", "#FFFFFF",
                                 roughness=0.0, metallic=0.1, alpha=0.85)
        assign_material(rf, refl_mat)
        objects_created.append(rf.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CABELO (idêntico ao B7 — CORRECCAO 3 preservada)
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head_center_z, head_radius, waist_z, shoulder_z, bust_z):
    hair_mat = make_material("B9_Hair", COLOR_HAIR, roughness=0.85)
    objects_created = []

    CAP_SEGS  = 24
    CAP_RINGS = 10
    cap_r = head_radius * 1.03

    cap_mesh = bpy.data.meshes.new("B9_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("B9_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    pole_z = head_center_z + cap_r
    pole   = bm.verts.new((0.0, 0.0, pole_z))

    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi = (math.pi * 0.52) * ri / CAP_RINGS
        z   = head_center_z + cap_r * math.cos(phi)
        r   = cap_r * math.sin(phi)
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
            bm.faces.new([
                rings[ri][s], rings[ri][s_next],
                rings[ri + 1][s_next], rings[ri + 1][s],
            ])

    bm.normal_update()
    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    N_STRANDS   = 24
    STRAND_SEGS = 8
    FRONT_PHI   = 3.0 * math.pi / 2.0
    FRONT_RANGE = math.radians(60)

    for i in range(N_STRANDS):
        azimuth = 2.0 * math.pi * i / N_STRANDS
        diff = abs(azimuth - FRONT_PHI)
        if diff > math.pi:
            diff = 2.0 * math.pi - diff
        is_frontal = diff < FRONT_RANGE

        if is_frontal:
            phi_birth         = math.pi * 0.30
            strand_len_factor = 0.75
            swing_back        = 0.45
        else:
            phi_birth         = math.pi * 0.15 + math.pi * 0.20 * ((i % 4) / 3.0)
            strand_len_factor = 1.0
            swing_back        = 0.0

        root_x = cap_r * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cap_r * math.sin(phi_birth) * math.sin(azimuth)
        root_z = head_center_z + cap_r * math.cos(phi_birth)
        tip_z  = bust_z + (shoulder_z - bust_z) * strand_len_factor * 0.6

        cdata = bpy.data.curves.new(name=f"B9_Strand_{i:02d}", type="CURVE")
        cdata.dimensions      = "3D"
        cdata.bevel_depth     = 0.006 + 0.003 * (i % 3)
        cdata.bevel_resolution = 2
        cdata.use_fill_caps   = True

        sp = cdata.splines.new("NURBS")
        sp.points.add(STRAND_SEGS - 1)

        for pi_idx in range(STRAND_SEGS):
            t        = pi_idx / (STRAND_SEGS - 1)
            pz       = root_z + (tip_z - root_z) * t
            splay    = 1.0 + t * 0.35
            swing_y  = swing_back * cap_r * t * 0.40
            px       = root_x * splay
            py       = root_y * splay + swing_y
            sp.points[pi_idx].co = (px, py, pz, 1.0)

        sp.use_endpoint_u = True
        sp.order_u        = min(4, STRAND_SEGS)

        strand_obj = bpy.data.objects.new(f"B9_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CALÇADO (idêntico ao B7)
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z):
    shoe_mat = make_material("B9_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)

    SHOE_L   = 0.24
    SHOE_W   = 0.09
    SHOE_H   = 0.045
    SHOE_SEP = 0.10

    objects_created = []
    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx   = sign * SHOE_SEP / 2.0
        mesh_data = bpy.data.meshes.new(f"B9_Shoe_{side}_mesh")
        shoe_obj  = bpy.data.objects.new(f"B9_Shoe_{side}", mesh_data)
        bpy.context.collection.objects.link(shoe_obj)

        bm  = bmesh.new()
        hw  = SHOE_W / 2.0
        hl  = SHOE_L / 2.0
        thw = hw * 0.35
        bz  = body_min_z

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

        bm.normal_update()
        bm.to_mesh(mesh_data)
        bm.free()
        mesh_data.update()
        assign_material(shoe_obj, shoe_mat)
        objects_created.append(shoe_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=64, w=768, h=768):
    sc = bpy.context.scene
    sc.render.engine    = "CYCLES"
    sc.cycles.device    = "CPU"
    sc.cycles.samples   = samples
    sc.render.resolution_x          = w
    sc.render.resolution_y          = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.05, 0.05, 0.07)


def add_3pt_lighting(target, scene_scale=1.0):
    for nm in ("B9_Key", "B9_Fill", "B9_Rim"):
        for o in list(bpy.data.objects):
            if o.name == nm:
                bpy.data.objects.remove(o, do_unlink=True)

    tx, ty, tz = target.x, target.y, target.z
    s = scene_scale

    bpy.ops.object.light_add(type="AREA", location=(tx + s, ty - s * 2, tz + s))
    key = bpy.context.active_object
    key.name         = "B9_Key"
    key.data.energy  = 800 * s
    key.data.size    = 0.8 * s
    d = target - key.location
    key.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

    bpy.ops.object.light_add(type="AREA", location=(tx - s * 0.8, ty - s * 1.5, tz + s * 0.5))
    fill = bpy.context.active_object
    fill.name        = "B9_Fill"
    fill.data.energy = 300 * s
    fill.data.size   = 1.0 * s
    d = target - fill.location
    fill.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

    bpy.ops.object.light_add(type="AREA", location=(tx, ty + s * 1.5, tz + s * 0.8))
    rim = bpy.context.active_object
    rim.name        = "B9_Rim"
    rim.data.energy = 200 * s
    rim.data.size   = 0.5 * s
    d = target - rim.location
    rim.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def place_camera(location, target, ortho=False, scale=1.0, name="B9_Cam"):
    for o in list(bpy.data.objects):
        if o.name == name:
            bpy.data.objects.remove(o, do_unlink=True)

    bpy.ops.object.camera_add(location=location)
    cam = bpy.context.active_object
    cam.name = name
    if ortho:
        cam.data.type        = "ORTHO"
        cam.data.ortho_scale = scale
    else:
        cam.data.type  = "PERSP"
        cam.data.lens  = 85.0
    d = Vector(target) - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def render_to(path, samples=64):
    bpy.context.scene.cycles.samples   = samples
    bpy.context.scene.render.filepath  = str(path)
    bpy.ops.render.render(write_still=True)
    exists = Path(path).exists()
    report["renders"][Path(path).stem] = {
        "rendered": exists,
        "file": Path(path).name,
    }
    return exists


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUÇÃO DA PERSONAGEM
# ─────────────────────────────────────────────────────────────────────────────

try:

    # ── INSTALAR MPFB ────────────────────────────────────────────────────────
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

    report["components"]["mpfb"] = {"status": "installed_and_active", "module": module_id}

    # ── LIMPAR CENA ──────────────────────────────────────────────────────────
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    # ── CRIAR HUMANO ─────────────────────────────────────────────────────────
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

    report["components"]["human_base"] = {
        "name":       human.name,
        "vertices":   len(human.data.vertices),
        "shape_keys": len(human.data.shape_keys.key_blocks),
    }

    # ── MORPH FEMININO (idêntico ao B7) ──────────────────────────────────────
    keys         = human.data.shape_keys.key_blocks
    z_before     = evaluated_z_coords(human)

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

    report["components"]["female_morph"] = {
        "changed_vertices": changed_v,
        "status": "confirmed",
    }

    # ── ESCALA PARA 1,70 m ───────────────────────────────────────────────────
    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_height = hi["h_final_m"]
    body_min_z   = hi["body_min_z"]
    body_max_z   = hi["body_max_z"]

    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(
            f"Altura final ({world_height:.4f} m) fora da tolerancia "
            f"(alvo {TARGET_HEIGHT_M} m +/- {HEIGHT_TOLERANCE_M} m)")

    report["components"]["height"] = {
        "world_height_initial_m": hi["h_initial_m"],
        "scale_factor":           hi["factor"],
        "world_height_final_m":   world_height,
        "body_min_z":             body_min_z,
        "body_max_z":             body_max_z,
        "within_tolerance":       True,
    }

    # ── REFERÊNCIAS PROPORCIONAIS ─────────────────────────────────────────────
    body_h        = body_max_z - body_min_z
    waist_z       = body_min_z + body_h * 0.575
    bust_z        = body_min_z + body_h * 0.695
    shoulder_z    = body_min_z + body_h * 0.800
    head_center_z = body_min_z + body_h * 0.920
    head_radius   = body_h * 0.075

    # ── MATERIAL DE PELE ─────────────────────────────────────────────────────
    skin_mat = make_material("B9_Skin", COLOR_SKIN, roughness=0.52, subsurface=0.18)
    assign_material(human, skin_mat)

    # ── MATERIAIS OCULARES ───────────────────────────────────────────────────
    sclera_mat = make_material("B9_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material("B9_Iris",   COLOR_IRIS,   roughness=0.15)
    pupil_mat  = make_material("B9_Pupil",  COLOR_PUPIL,  roughness=0.05)

    # ── ANÁLISE DOS GRUPOS HELPER-EYE ────────────────────────────────────────
    gdata_L = analyse_vertex_group(human, "helper-l-eye")
    gdata_R = analyse_vertex_group(human, "helper-r-eye")

    report["helper_group_analysis"] = {
        "helper_l_eye": {
            k: (list(v) if isinstance(v, Vector) else v)
            for k, v in (gdata_L or {}).items()
            if k != "vertex_indices"
        } if gdata_L else None,
        "helper_r_eye": {
            k: (list(v) if isinstance(v, Vector) else v)
            for k, v in (gdata_R or {}).items()
            if k != "vertex_indices"
        } if gdata_R else None,
    }

    # ── DECISÃO: ABORDAGEM A ou B ─────────────────────────────────────────────
    can_use_A = (
        gdata_L is not None and
        gdata_R is not None and
        gdata_L.get("reusable", False) and
        gdata_R.get("reusable", False)
    )

    eye_objects = []

    if can_use_A:
        # Tentar Abordagem A
        eye_objs_A = build_eyes_approach_a(
            human, gdata_L, gdata_R,
            sclera_mat, iris_mat, pupil_mat, head_radius
        )
        if eye_objs_A:
            eye_objects = eye_objs_A
            report["eye_method"] = "A_native_geometry"
            report["eye_centers_used"] = {
                "L": list(gdata_L["center"]),
                "R": list(gdata_R["center"]),
                "source": "helper_vertex_group_centroid_native_geometry_separated",
            }
        else:
            can_use_A = False  # A falhou, cair para B

    if not can_use_A:
        # Abordagem B — centros calculados a partir dos grupos (ou fallback proporcional)
        if gdata_L and gdata_L.get("center") and gdata_R and gdata_R.get("center"):
            center_L = gdata_L["center"]
            center_R = gdata_R["center"]
            center_src = "helper_vertex_group_centroid_realtime"
        else:
            # Escalar os centros do diagnóstico para a altura actual
            scale_f  = world_height / 1.65   # altura MPFB base ≈ 1.65m
            center_L = Vector((
                DIAG_LEFT_EYE.x  * scale_f,
                DIAG_LEFT_EYE.y  * scale_f,
                body_min_z + (DIAG_LEFT_EYE.z - 0.0) * scale_f
            ))
            center_R = Vector((
                DIAG_RIGHT_EYE.x * scale_f,
                DIAG_RIGHT_EYE.y * scale_f,
                body_min_z + (DIAG_RIGHT_EYE.z - 0.0) * scale_f
            ))
            center_src = "diagnostic_reference_scaled_proportionally"

        eye_objects = build_eyes_approach_b(
            center_L, center_R, head_radius,
            sclera_mat, iris_mat, pupil_mat
        )
        report["eye_method"] = "B_new_spheres_from_centroid"
        report["eye_centers_used"] = {
            "L":      list(center_L),
            "R":      list(center_R),
            "source": center_src,
        }

    report["components"]["eyes"] = {
        "objects": eye_objects,
        "method":  report["eye_method"],
        "orientation": "iris_pupil_facing_Y_negative_MPFB_front",
    }

    # ── CABELO (B7 preservado) ────────────────────────────────────────────────
    hair_objects = build_hair(head_center_z, head_radius,
                              waist_z, shoulder_z, bust_z)
    report["components"]["hair"] = {
        "objects": hair_objects,
        "cap":     "B9_HairCap",
        "strands": len(hair_objects) - 1,
    }

    # ── CALÇADO ───────────────────────────────────────────────────────────────
    shoe_objects = build_shoes(body_min_z)
    report["components"]["shoes"] = {"objects": shoe_objects}

    # ── VALIDAÇÕES ───────────────────────────────────────────────────────────
    expected_base = {
        human.name, "B9_HairCap",
        "B9_Shoe_L", "B9_Shoe_R",
    }
    for i in range(24):
        expected_base.add(f"B9_Strand_{i:02d}")

    # Adicionar objectos oculares esperados
    for side in ("L", "R"):
        expected_base.add(f"B9_Iris_{side}")
        expected_base.add(f"B9_Pupil_{side}")
        if report["eye_method"] == "A_native_geometry":
            expected_base.add(f"B9_Sclera_Native_{side}")
        else:
            expected_base.add(f"B9_Sclera_{side}")
            expected_base.add(f"B9_Reflex_{side}")

    scene_objects = {o.name for o in bpy.data.objects}
    missing = expected_base - scene_objects

    report["validations"]["scene_objects"] = {
        "expected_count": len(expected_base),
        "found_count":    len(scene_objects),
        "missing":        sorted(missing),
        "pass":           len(missing) == 0,
    }
    report["validations"]["world_height"] = {
        "value_m":    world_height,
        "target_m":   TARGET_HEIGHT_M,
        "tolerance_m": HEIGHT_TOLERANCE_M,
        "pass":        abs(world_height - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M,
    }
    report["validations"]["eye_method_valid"] = {
        "method": report["eye_method"],
        "pass":   report["eye_method"] is not None,
    }
    report["validations"]["overall_pass"] = all(
        v.get("pass", False) for v in report["validations"].values()
    )

    # ── GRAVAR .BLEND E EXPORTAR GLB ─────────────────────────────────────────
    blend_path = out / "female_b9.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "female_b9.glb"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path),
        export_format="GLB",
        use_selection=True,
        export_cameras=False,
        export_lights=False,
        export_materials="EXPORT",
        export_apply=True,
    )
    if not glb_path.exists():
        raise RuntimeError(f"GLB nao exportado: {glb_path}")
    report["glb_bytes"] = glb_path.stat().st_size

    # ── IMAGENS DE AVALIAÇÃO ─────────────────────────────────────────────────
    setup_render(samples=64)

    # Centros para posicionamento de câmara
    body_center = Vector((0, 0, (body_min_z + body_max_z) / 2))
    head_center  = Vector((0, 0, head_center_z))
    face_y_front = -head_radius * 1.6

    # Iluminação 3-pontos para o corpo completo
    add_3pt_lighting(body_center, scene_scale=body_h * 0.8)

    # b9_01 — corpo completo frontal
    place_camera(
        (0, face_y_front * 2.8, (body_min_z + body_max_z) / 2),
        body_center, ortho=False, name="B9_Cam"
    )
    render_to(out / "b9_01_full_front.png")

    # b9_02 — rosto frontal
    add_3pt_lighting(head_center, scene_scale=head_radius * 2.5)
    place_camera(
        (0, face_y_front * 3.5, head_center_z),
        head_center, ortho=False, name="B9_Cam"
    )
    render_to(out / "b9_02_face_front.png")

    # b9_03 — rosto lateral
    place_camera(
        (head_radius * 3.5, face_y_front * 0.5, head_center_z),
        head_center, ortho=False, name="B9_Cam"
    )
    render_to(out / "b9_03_face_side.png")

    # b9_04 — quarto de volta
    place_camera(
        (head_radius * 2.2, face_y_front * 2.2, head_center_z + head_radius * 0.2),
        head_center, ortho=False, name="B9_Cam"
    )
    render_to(out / "b9_04_face_quarter.png")

    # b9_05 — olhos em grande plano
    eye_cx = report["eye_centers_used"].get("L", [0, 0, head_center_z])
    eye_target = Vector((
        0,
        eye_cx[1] if len(eye_cx) > 1 else -head_radius,
        eye_cx[2] if len(eye_cx) > 2 else head_center_z
    ))
    place_camera(
        (0, eye_target.y - head_radius * 4, eye_target.z),
        eye_target, ortho=True, scale=head_radius * 0.9, name="B9_Cam"
    )
    add_3pt_lighting(eye_target, scene_scale=head_radius)
    render_to(out / "b9_05_eyes_closeup.png", samples=96)

    report["status"] = "success_b9_complete"

    report["visual_limitations"].extend([
        "Morph feminino experimental — nao utiliza presets oficiais do MPFB",
        "Cabelo em curvas NURBS com bevel — sem sistema de particulas",
        "Calcado em prisma bmesh basico",
        "Nao ha rigging nem armature — personagem em pose T",
        "Sem vestuario",
        f"Metodo ocular: {report['eye_method']}",
    ])

except Exception as exc:
    report["status"] = "failed"
    report["error"]  = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "b9_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print("MPFB_B9_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_b9_complete":
    sys.exit(1)
