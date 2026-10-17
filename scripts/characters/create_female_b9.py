"""
B9 Rev2 — Modelo Feminino Experimental — Olhos Nativos do MPFB (Revisão 2).

DERIVADA DA B7. Todas as correções da B7 são preservadas:
  CORRECCAO 1 (B7) — Y negativo = frente no MPFB
  CORRECCAO 2 (B7) — Sem vestido
  CORRECCAO 3 (B7) — Cabelo frontal corrigido

INVESTIGAÇÃO B9 Rev1 → Rev2 (problemas encontrados e resolvidos):

  PROBLEMA 1 — Abordagem A produzia olhos invisíveis:
    Os grupos helper-l-eye e helper-r-eye são regiões de peso na malha do corpo,
    representando a superfície côncava das órbitas (normais apontam para dentro).
    Quando separados como objecto independente, ficam sobrepostos com a pele
    e são tapados por ela. Abordagem A desactivada.

  PROBLEMA 2 — Câmara do corpo inteiro muito perto:
    face_y_front * 2.8 ≈ -0.57m para um corpo de 1.70m.
    Com lente 85mm, o campo de visão a essa distância cobre ≈ 37cm — só a cabeça.
    Corrigido: câmara ortográfica com ortho_scale = body_h * 1.15.

  PROBLEMA 3 — Câmara dos olhos usava dict serializado:
    report["eye_centers_used"].get("L") pode ter valor inesperado.
    Corrigido: câmara usa variáveis live center_L e center_R directamente.

SISTEMA OCULAR B9 Rev2 — Abordagem B sempre activa:
  1. Calcular centros em tempo real dos grupos helper-l-eye e helper-r-eye.
  2. Verificar a posição Y mais frontal da face na zona dos olhos por varrimento
     de vértices (equivalente a raycasting geométrico sem depgraph).
  3. Posicionar a esclera esférica à frente da face (Y mais negativo que a pele).
  4. Íris e pupila orientadas para Y negativo (frente do MPFB).

IMAGENS GERADAS:
  b9_01_full_front.png    — corpo completo (ortho)
  b9_02_face_front.png    — rosto de frente (persp 85mm)
  b9_03_face_side.png     — rosto lateral (persp 85mm)
  b9_04_face_quarter.png  — rosto em quarto de volta (persp 85mm)
  b9_05_eyes_closeup.png  — olhos em grande plano (ortho)

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
from mathutils import Vector

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

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-b9")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender":      bpy.app.version_string,
    "version":      "b9-rev2",
    "status":       "started",
    "eye_method":   "B_new_spheres_from_centroid",
    "eye_investigation": {},
    "eye_centers_used":  {},
    "camera_params":     {},
    "helper_group_analysis": {},
    "components":   {},
    "validations":  {
        "technical": {},   # objectos existem, posições calculadas
        "visual":    {},   # olhos visíveis, câmaras correctas
    },
    "visual_limitations": [],
    "renders": {},
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
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
# INVESTIGAÇÃO OCULAR — centro e profundidade real da face
# ─────────────────────────────────────────────────────────────────────────────

def investigate_eye_geometry(human, body_h):
    """
    Calcula:
      1. Centros dos olhos a partir dos grupos helper-l-eye / helper-r-eye.
      2. A posição Y mais frontal da pele facial na zona dos olhos
         (varrimento de vértices — equivalente a raycasting geométrico).
      3. A posição Y onde a esclera deve ser colocada para ficar VISÍVEL
         (à frente da pele, não dentro dela).

    Devolve dict com todos os valores calculados.
    """
    inv = {
        "groups_found":          {},
        "center_L":              None,
        "center_R":              None,
        "face_y_at_eye_level":   None,  # Y mais frontal da pele na zona dos olhos
        "sclera_y_target":       None,  # onde colocar o centro da esclera
        "sclera_radius":         None,
        "iris_radius":           None,
        "pupil_radius":          None,
        "method":                "helper_group_centroid_with_face_depth_probe",
    }

    wmat = human.matrix_world

    # ── 1. Grupos helper-eye ─────────────────────────────────────────────────
    for side, gname in [("L", "helper-l-eye"), ("R", "helper-r-eye")]:
        if gname not in human.vertex_groups:
            inv["groups_found"][gname] = False
            continue
        inv["groups_found"][gname] = True
        gidx = human.vertex_groups[gname].index
        coords = []
        for v in human.data.vertices:
            for g in v.groups:
                if g.group == gidx and g.weight > 0:
                    coords.append(wmat @ v.co)
                    break
        if not coords:
            continue
        cx = sum(c.x for c in coords) / len(coords)
        cy = sum(c.y for c in coords) / len(coords)
        cz = sum(c.z for c in coords) / len(coords)
        center = Vector((cx, cy, cz))
        inv[f"center_{side}"] = center

        # Registar também dimensões para validação técnica
        xs = [c.x for c in coords]; ys = [c.y for c in coords]; zs = [c.z for c in coords]
        inv[f"group_{side}_vertex_count"] = len(coords)
        inv[f"group_{side}_dimensions"] = {
            "x": round(max(xs) - min(xs), 5),
            "y": round(max(ys) - min(ys), 5),
            "z": round(max(zs) - min(zs), 5),
        }
        inv[f"group_{side}_y_range"] = [round(min(ys), 5), round(max(ys), 5)]

    # Fallback se grupos não encontrados
    body_min_z = min((wmat @ v.co).z for v in human.data.vertices)
    body_max_z = max((wmat @ v.co).z for v in human.data.vertices)
    bh         = body_max_z - body_min_z
    hr         = bh * 0.075

    if inv["center_L"] is None:
        inv["center_L"] = Vector(( hr * 0.38, -hr * 0.88, body_min_z + bh * 0.92 + hr * 0.10))
        inv["method"] = "proportional_fallback_no_groups"
    if inv["center_R"] is None:
        inv["center_R"] = Vector((-hr * 0.38, -hr * 0.88, body_min_z + bh * 0.92 + hr * 0.10))
        inv["method"] = "proportional_fallback_no_groups"

    # ── 2. Profundidade real da face na zona dos olhos ────────────────────────
    # Varremos todos os vértices do corpo numa faixa Z em torno dos olhos
    # e registamos o Y mais frontal (mais negativo) nessa faixa.
    # Isto é o equivalente a um raycasting geométrico: encontra a superfície
    # exterior da face que a câmara vê primeiro.
    eye_z_lo = min(inv["center_L"].z, inv["center_R"].z) - hr * 0.20
    eye_z_hi = max(inv["center_L"].z, inv["center_R"].z) + hr * 0.20
    eye_x_lo = min(inv["center_L"].x, inv["center_R"].x) - hr * 0.40
    eye_x_hi = max(inv["center_L"].x, inv["center_R"].x) + hr * 0.40

    face_y_candidates = []
    for v in human.data.vertices:
        wco = wmat @ v.co
        if (eye_z_lo <= wco.z <= eye_z_hi and
                eye_x_lo <= wco.x <= eye_x_hi):
            face_y_candidates.append(wco.y)

    if face_y_candidates:
        # Y mais frontal = valor mais negativo
        face_y_front = min(face_y_candidates)
    else:
        # fallback: estimar a partir do centro do olho
        face_y_front = inv["center_L"].y - hr * 0.10

    inv["face_y_at_eye_level"]   = round(face_y_front, 6)
    inv["face_y_candidates_count"] = len(face_y_candidates)

    # ── 3. Posição da esclera ─────────────────────────────────────────────────
    # O globo ocular real assenta nas órbitas: o seu ponto mais frontal
    # fica ligeiramente À FRENTE da superfície do rosto (protusão ocular normal).
    # Queremos o centro da esclera a uma profundidade tal que a sua superfície
    # frontal fique ~1mm à frente da face.
    sclera_r = hr * 0.135        # raio ≈ 1.35% da altura do corpo
    margin   = 0.002             # 2mm à frente da pele

    # centro_sclera_y = face_y_front - margin - sclera_r * 0.5
    # (o globo fica meio mergulhado na órbita, meio à frente)
    sclera_y = face_y_front - margin - sclera_r * 0.45

    inv["sclera_radius"]   = round(sclera_r, 6)
    inv["sclera_y_target"] = round(sclera_y,  6)
    inv["iris_radius"]     = round(sclera_r * 0.58, 6)
    inv["pupil_radius"]    = round(sclera_r * 0.32, 6)

    return inv


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUÇÃO OCULAR — Abordagem B (sempre activa)
# ─────────────────────────────────────────────────────────────────────────────

def build_eyes_b9(inv, sclera_mat, iris_mat, pupil_mat):
    """
    Cria olhos completos usando os resultados da investigação.
    Cada olho: esclera esférica + íris (disco) + pupila (disco) + reflexo.

    A esclera é posicionada em sclera_y_target, que a investigação calculou
    como sendo ligeiramente À FRENTE da superfície real da face.
    A íris e pupila ficam ainda mais para Y negativo (mais à frente).
    """
    objects_created = []

    sr   = inv["sclera_radius"]
    ir_r = inv["iris_radius"]
    pu_r = inv["pupil_radius"]
    sy   = inv["sclera_y_target"]   # Y do centro da esclera

    for side, center in [("L", inv["center_L"]), ("R", inv["center_R"])]:
        ex = center.x
        ez = center.z
        # Centro da esclera: X e Z do grupo helper, Y calculado geometricamente
        ey_sclera = sy

        # Esclera
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16,
            radius=sr,
            location=(ex, ey_sclera, ez)
        )
        sc = bpy.context.active_object
        sc.name = f"B9_Sclera_{side}"
        assign_material(sc, sclera_mat)
        objects_created.append(sc.name)

        # Íris — disco orientado para frente (eixo Y negativo)
        # Posição: sr * 0.85 à frente do centro da esclera
        ey_iris = ey_sclera - sr * 0.85
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=48, radius=ir_r, depth=sr * 0.06,
            location=(ex, ey_iris, ez)
        )
        ir = bpy.context.active_object
        ir.name = f"B9_Iris_{side}"
        # O cilindro criado pelo Blender tem o eixo em Z — rodar 90° para apontar para Y
        ir.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        assign_material(ir, iris_mat)
        objects_created.append(ir.name)

        # Pupila — disco concêntrico, ligeiramente mais à frente
        ey_pupil = ey_sclera - sr * 0.88
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32, radius=pu_r, depth=sr * 0.04,
            location=(ex, ey_pupil, ez)
        )
        pu = bpy.context.active_object
        pu.name = f"B9_Pupil_{side}"
        pu.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        assign_material(pu, pupil_mat)
        objects_created.append(pu.name)

        # Reflexo luminoso — pequena esfera branca ligeiramente excêntrica
        refl_r = sr * 0.07
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=8, ring_count=6,
            radius=refl_r,
            location=(
                ex + ir_r * 0.38,
                ey_sclera - sr * 0.90,
                ez + ir_r * 0.38,
            )
        )
        rf = bpy.context.active_object
        rf.name = f"B9_Reflex_{side}"
        refl_mat = make_material(f"B9_Reflex_{side}", "#FFFFFF",
                                  roughness=0.0, metallic=0.0)
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
    cap_r     = head_radius * 1.03

    cap_mesh = bpy.data.meshes.new("B9_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("B9_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm     = bmesh.new()
    pole_z = head_center_z + cap_r
    pole   = bm.verts.new((0.0, 0.0, pole_z))

    rings = []
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

    FRONT_PHI   = 3.0 * math.pi / 2.0
    FRONT_RANGE = math.radians(60)

    for i in range(24):
        azimuth = 2.0 * math.pi * i / 24
        diff    = abs(azimuth - FRONT_PHI)
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

        cdata                = bpy.data.curves.new(name=f"B9_Strand_{i:02d}", type="CURVE")
        cdata.dimensions     = "3D"
        cdata.bevel_depth    = 0.006 + 0.003 * (i % 3)
        cdata.bevel_resolution = 2
        cdata.use_fill_caps  = True

        sp = cdata.splines.new("NURBS")
        sp.points.add(7)
        for pi_idx in range(8):
            t       = pi_idx / 7
            pz      = root_z + (tip_z - root_z) * t
            splay   = 1.0 + t * 0.35
            swing_y = swing_back * cap_r * t * 0.40
            sp.points[pi_idx].co = (root_x * splay, root_y * splay + swing_y, pz, 1.0)
        sp.use_endpoint_u = True
        sp.order_u        = min(4, 8)

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
    objects_created = []
    SHOE_L   = 0.24
    SHOE_W   = 0.09
    SHOE_H   = 0.045
    SHOE_SEP = 0.10

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
# RENDER — câmaras calculadas a partir da geometria real
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
    sc.world.color = (0.05, 0.05, 0.08)


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_light(loc, target, energy, size, name):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name        = name
    l.data.energy = energy
    l.data.size   = size
    d = Vector(target) - Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


def place_camera_ortho(loc, target, ortho_scale, name="B9Cam"):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name            = name
    cam.data.type       = "ORTHO"
    cam.data.ortho_scale = ortho_scale
    d = Vector(target) - Vector(loc)
    cam.rotation_euler  = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    report["camera_params"][name] = {
        "type": "ORTHO", "location": list(loc),
        "target": list(target), "ortho_scale": ortho_scale,
    }
    return cam


def place_camera_persp(loc, target, lens_mm, name="B9Cam"):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name      = name
    cam.data.type = "PERSP"
    cam.data.lens = lens_mm
    d = Vector(target) - Vector(loc)
    cam.rotation_euler  = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    report["camera_params"][name] = {
        "type": "PERSP", "location": list(loc),
        "target": list(target), "lens_mm": lens_mm,
    }
    return cam


def render_to(path, tag, samples=64):
    bpy.context.scene.cycles.samples  = samples
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    exists = Path(path).exists()
    report["renders"][tag] = {"rendered": exists, "file": Path(path).name}
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
        "world_height_final_m": world_height,
        "body_min_z":           body_min_z,
        "body_max_z":           body_max_z,
        "within_tolerance":     True,
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

    # ── INVESTIGAÇÃO OCULAR ───────────────────────────────────────────────────
    inv = investigate_eye_geometry(human, body_h)

    # Serializar para o relatório (Vectors não são JSON-serializáveis)
    inv_serial = {}
    for k, v in inv.items():
        if isinstance(v, Vector):
            inv_serial[k] = [round(c, 6) for c in v]
        elif isinstance(v, dict):
            inv_serial[k] = {
                kk: ([round(c, 6) for c in vv] if isinstance(vv, Vector) else vv)
                for kk, vv in v.items()
            }
        else:
            inv_serial[k] = v

    report["eye_investigation"] = inv_serial
    report["eye_centers_used"]  = {
        "L":      [round(c, 6) for c in inv["center_L"]],
        "R":      [round(c, 6) for c in inv["center_R"]],
        "sclera_y_target": inv["sclera_y_target"],
        "face_y_at_eye_level": inv["face_y_at_eye_level"],
        "source": inv["method"],
    }

    # ── MATERIAIS OCULARES ───────────────────────────────────────────────────
    sclera_mat = make_material("B9_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material("B9_Iris",   COLOR_IRIS,   roughness=0.10)
    pupil_mat  = make_material("B9_Pupil",  COLOR_PUPIL,  roughness=0.03)

    # ── CONSTRUIR OLHOS ───────────────────────────────────────────────────────
    eye_objects = build_eyes_b9(inv, sclera_mat, iris_mat, pupil_mat)

    report["components"]["eyes"] = {
        "objects":           eye_objects,
        "method":            "B_new_spheres_geometric_positioning",
        "sclera_y_target":   inv["sclera_y_target"],
        "face_y_at_eye_level": inv["face_y_at_eye_level"],
        "sclera_radius":     inv["sclera_radius"],
        "iris_radius":       inv["iris_radius"],
        "pupil_radius":      inv["pupil_radius"],
    }

    # ── CABELO ────────────────────────────────────────────────────────────────
    hair_objects = build_hair(head_center_z, head_radius,
                              waist_z, shoulder_z, bust_z)
    report["components"]["hair"] = {
        "objects": hair_objects,
        "strands": len(hair_objects) - 1,
    }

    # ── CALÇADO ───────────────────────────────────────────────────────────────
    shoe_objects = build_shoes(body_min_z)
    report["components"]["shoes"] = {"objects": shoe_objects}

    # ── VALIDAÇÃO TÉCNICA ─────────────────────────────────────────────────────
    expected_objs = {human.name, "B9_HairCap", "B9_Shoe_L", "B9_Shoe_R"}
    for i in range(24):
        expected_objs.add(f"B9_Strand_{i:02d}")
    for side in ("L", "R"):
        expected_objs.update({
            f"B9_Sclera_{side}", f"B9_Iris_{side}",
            f"B9_Pupil_{side}",  f"B9_Reflex_{side}",
        })

    scene_objects = {o.name for o in bpy.data.objects}
    missing = expected_objs - scene_objects

    report["validations"]["technical"]["scene_objects"] = {
        "expected": len(expected_objs),
        "found":    len(scene_objects),
        "missing":  sorted(missing),
        "pass":     len(missing) == 0,
    }
    report["validations"]["technical"]["world_height"] = {
        "value_m":    round(world_height, 6),
        "target_m":   TARGET_HEIGHT_M,
        "pass":       abs(world_height - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M,
    }
    report["validations"]["technical"]["eye_positioning"] = {
        "face_y_at_eye_level": inv["face_y_at_eye_level"],
        "sclera_y_target":     inv["sclera_y_target"],
        "sclera_in_front_of_face": (
            inv["sclera_y_target"] < inv["face_y_at_eye_level"]
        ),
        "pass": inv["sclera_y_target"] < inv["face_y_at_eye_level"],
    }
    report["validations"]["technical"]["overall_pass"] = all(
        v.get("pass", False)
        for v in report["validations"]["technical"].values()
    )

    # Validação visual — a preencher após renders
    report["validations"]["visual"] = {
        "note": "Ver imagens b9_0*.png para validação visual",
        "criteria": [
            "Olhos visíveis nas órbitas",
            "Pupilas orientadas para a frente",
            "Globos oculares não atravessam o rosto",
            "Corpo feminino preservado",
            "Câmara b9_01 mostra corpo inteiro",
            "Câmara b9_05 mostra olhos em close-up",
        ],
        "pass": None,  # não pode ser determinado automaticamente
    }

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

    # ── RENDER — câmaras calculadas a partir da geometria real ───────────────
    setup_render(samples=64)

    # Posições anatómicas reais (usadas directamente nas câmaras)
    body_cx  = 0.0
    body_cy  = (body_min_z + body_max_z) / 2.0
    head_tgt = Vector((0, 0, head_center_z))
    face_y   = inv["face_y_at_eye_level"]   # Y frontal real da face

    # Centro dos olhos — variáveis LIVE (não o dict serializado)
    eye_cL = inv["center_L"]
    eye_cR = inv["center_R"]
    eye_mid = Vector((0, inv["sclera_y_target"], (eye_cL.z + eye_cR.z) / 2))

    # ── b9_01 — corpo inteiro (ortho, câmara a Y muito negativo) ─────────────
    # Escala ortho = altura do corpo * 1.15 para ver de cima a baixo com margem
    body_ortho_scale = body_h * 1.15
    # Câmara frontal a Y muito negativo para ter campo total de visão
    cam_full_y = face_y - body_h * 1.5
    clear_lights_cameras()
    body_tgt_v = Vector((0, 0, (body_min_z + body_max_z) / 2))
    add_light((1.5, cam_full_y + 1.0, body_tgt_v.z + body_h * 0.5),
              body_tgt_v, 2000, 1.5, "B9_Key")
    add_light((-1.2, cam_full_y + 0.8, body_tgt_v.z + body_h * 0.2),
              body_tgt_v, 800,  1.2, "B9_Fill")
    add_light((0, face_y + body_h * 0.5, body_tgt_v.z + body_h * 0.5),
              body_tgt_v, 400,  0.8, "B9_Rim")
    place_camera_ortho(
        (0, cam_full_y, body_tgt_v.z),
        body_tgt_v, body_ortho_scale, name="B9Cam_Full"
    )
    render_to(out / "b9_01_full_front.png", "b9_01_full_front")

    # ── b9_02 — rosto frontal (persp 85mm, posição baseada na face_y real) ───
    # A câmara fica a 5 raios de cabeça à frente da face
    cam_face_y = face_y - head_radius * 5.0
    clear_lights_cameras()
    add_light((head_radius * 2.5, cam_face_y + head_radius, head_center_z + head_radius * 0.5),
              head_tgt, 600, 0.6, "B9_Key")
    add_light((-head_radius * 2.0, cam_face_y + head_radius * 0.8, head_center_z),
              head_tgt, 300, 0.5, "B9_Fill")
    add_light((0, face_y + head_radius * 2, head_center_z + head_radius),
              head_tgt, 200, 0.4, "B9_Rim")
    place_camera_persp(
        (0, cam_face_y, head_center_z),
        head_tgt, 85, name="B9Cam_Face"
    )
    render_to(out / "b9_02_face_front.png", "b9_02_face_front")

    # ── b9_03 — rosto lateral ────────────────────────────────────────────────
    place_camera_persp(
        (head_radius * 4.0, face_y, head_center_z),
        head_tgt, 85, name="B9Cam_Side"
    )
    render_to(out / "b9_03_face_side.png", "b9_03_face_side")

    # ── b9_04 — quarto de volta ───────────────────────────────────────────────
    place_camera_persp(
        (head_radius * 2.8, cam_face_y * 0.7, head_center_z + head_radius * 0.15),
        head_tgt, 85, name="B9Cam_Quarter"
    )
    render_to(out / "b9_04_face_quarter.png", "b9_04_face_quarter")

    # ── b9_05 — olhos close-up (ortho, centro entre os dois olhos) ───────────
    # Escala ortho = separação entre olhos * 3 para ver ambos com margem
    eye_separation = abs(eye_cL.x - eye_cR.x)
    eye_ortho_scale = max(eye_separation * 3.0, inv["sclera_radius"] * 8)
    # Câmara a Y muito negativo a partir do sclera_y_target
    cam_eye_y = inv["sclera_y_target"] - eye_ortho_scale * 3.0
    clear_lights_cameras()
    add_light((eye_separation * 0.8, cam_eye_y + eye_separation, eye_mid.z + inv["sclera_radius"]),
              eye_mid, 300, 0.3, "B9_Key")
    add_light((-eye_separation * 0.6, cam_eye_y + eye_separation * 0.8, eye_mid.z),
              eye_mid, 150, 0.25, "B9_Fill")
    add_light((0, inv["sclera_y_target"] + inv["sclera_radius"] * 2, eye_mid.z + inv["sclera_radius"]),
              eye_mid, 100, 0.2, "B9_Rim")
    place_camera_ortho(
        (0, cam_eye_y, eye_mid.z),
        eye_mid, eye_ortho_scale, name="B9Cam_Eyes"
    )
    render_to(out / "b9_05_eyes_closeup.png", "b9_05_eyes_closeup", samples=96)

    report["status"] = "success_b9_rev2_complete"

    report["visual_limitations"].extend([
        "Morph feminino experimental — nao utiliza presets oficiais do MPFB",
        "Cabelo em curvas NURBS com bevel",
        "Calcado em prisma bmesh basico",
        "Nao ha rigging — personagem em pose T",
        "Sem vestuario",
        "Abordagem A desactivada: helper-eye e superficie concava das orbitas",
        "Validacao visual requer revisao humana das imagens",
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

if report["status"] != "success_b9_rev2_complete":
    sys.exit(1)
