"""
T5 — Personagem Feminina Anime 3D
Baseado em: create_female_t3.py (nao modificado)

OBJECTIVO:
  Transformar a figura humana real gerada pelo MPFB numa personagem de anime 3D,
  mantendo toda a pipeline estavel da T3 (bake, escala, helpers dos olhos) e
  acrescentando as caracteristicas visuais do estilo anime japones moderno.

DIFERENCAS FACE AO T3:

  1. OLHOS GRANDES ANIME
     - Raio 1.9x maior que o helper do MPFB (limite 16-22 mm vs 9.5-14.5 mm da T3)
     - Iris cobre 68% do diametro da esclera (vs 52% na T3)
     - Pupila eliptica vertical (caracteristica do anime)
     - Reflexo especular branco (highlight) no canto superior da iris
     - Contorno escuro ao redor da esclera (eyeliner procedural)

  2. MATERIAIS TOON / CEL-SHADING
     - Pele: shader toon com duas faixas de luz (lit/shadow) sem gradiente suave
     - Freestyle activado na cena (linha preta ao redor da silhueta)
     - Olhos: iris com gradiente radial procedural (limbo escuro, pupila, highlight)
     - Cabelo: toon shading com especular anime (faixa brilhante)

  3. CABELO ANIME
     - Touca com franja: bloco frontal geometrico acima dos olhos
     - Mechas laterais angulosas (curvas com bevel_depth maior)
     - Duas mechas longas caindo sobre o peito (caracteristica do anime)
     - Cor preta com reflexo azul escuro no highlight

  4. PROPORCOES AJUSTADAS
     - Altura alvo: 1.65 m (mais tipica do anime que 1.70 m)

  5. ROUPA SIMPLES
     - Top ajustado: cilindro do bust ate a cintura
     - Calcas: cilindro do waist ate ao tornozelo
     - Materiais toon shading

  6. RENDERS (6 imagens)
     - Corpo inteiro frente, lado, tres-quartos, rosto close-up,
       olhos close-up, costas corpo inteiro
     - Fundo cinza escuro neutro para contraste com contornos Freestyle

NAO RESOLVIDO AQUI (herdado do T3):
  - Rigging / armature
  - Madeixas frontais caem a frente do pescoco
  - Calcado basico
  - A qualidade visual final exige inspeccao humana das imagens
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
from mathutils.bvhtree import BVHTree

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACAO
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M    = 1.65
HEIGHT_TOLERANCE_M = 0.01

# Paleta anime
COLOR_SKIN         = "#F2C9A0"
COLOR_SKIN_SHADOW  = "#D4936B"
COLOR_HAIR         = "#0D0A0A"
COLOR_HAIR_HILITE  = "#1A2A4A"
COLOR_EYE_SCLERA   = "#F8F5EE"
COLOR_EYE_IRIS     = "#5B3A8C"
COLOR_EYE_PUPIL    = "#0A0508"
COLOR_EYE_HILITE   = "#FFFFFF"
COLOR_EYE_LIMBUS   = "#1A0A2A"
COLOR_CLOTH_TOP    = "#E8D4F0"
COLOR_CLOTH_PANTS  = "#2A3A5A"
COLOR_SHOE         = "#1A1010"

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-t5")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "t5",
    "style": "anime_3d",
    "based_on": "create_female_t3.py (nao modificado)",
    "status": "started",
    "components": {},
    "validations": {},
    "renders": {},
    "visual_limitations": [],
}

eye_diag = {
    "status": "not_started",
    "bake_effect": {},
    "helpers_before_bake": {},
    "helpers_after_bake": {},
    "helpers_final": {},
    "placement_L": {},
    "placement_R": {},
    "eye_separation_m": None,
    "head_geometry": {},
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITARIOS (identicos ao T3)
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


def make_material_toon(name, hex_color, hex_shadow=None, roughness=0.85):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    shadow_color = hex_shadow if hex_shadow else hex_color

    toon = nodes.new("ShaderNodeBsdfToon")
    toon.location = (-500, 100)
    toon.component = "DIFFUSE"
    toon.inputs["Color"].default_value = hex_to_linear_rgba(hex_color)
    toon.inputs["Size"].default_value = 0.65
    toon.inputs["Smooth"].default_value = 0.05

    toon_shadow = nodes.new("ShaderNodeBsdfToon")
    toon_shadow.location = (-500, -100)
    toon_shadow.component = "DIFFUSE"
    toon_shadow.inputs["Color"].default_value = hex_to_linear_rgba(shadow_color)
    toon_shadow.inputs["Size"].default_value = 0.30
    toon_shadow.inputs["Smooth"].default_value = 0.02

    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (-200, 0)
    mix.inputs["Fac"].default_value = 0.35

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (100, 0)

    links.new(toon.outputs["BSDF"], mix.inputs[1])
    links.new(toon_shadow.outputs["BSDF"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out_node.inputs["Surface"])
    return mat


def make_material_principled(name, hex_color, roughness=0.6, metallic=0.0, alpha=1.0):
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
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.5
            break
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (0, 0)
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def make_material_eye_iris(name, hex_iris, hex_pupil, hex_limbus, hex_hilite):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-900, 0)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-700, 0)
    grad = nodes.new("ShaderNodeTexGradient")
    grad.location = (-500, 0)
    grad.gradient_type = "RADIAL"
    cr = nodes.new("ShaderNodeValToRGB")
    cr.location = (-300, 0)
    cr.color_ramp.interpolation = "EASE"
    e = cr.color_ramp.elements
    e[0].position = 0.0
    e[0].color = hex_to_linear_rgba(hex_hilite)
    e[1].position = 0.25
    e[1].color = hex_to_linear_rgba(hex_iris)
    new_elem = cr.color_ramp.elements.new(0.75)
    new_elem.color = hex_to_linear_rgba(hex_pupil)
    new_elem2 = cr.color_ramp.elements.new(0.95)
    new_elem2.color = hex_to_linear_rgba(hex_limbus)

    emit = nodes.new("ShaderNodeEmission")
    emit.location = (0, 0)
    emit.inputs["Strength"].default_value = 0.8

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (200, 0)

    links.new(texcoord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], grad.inputs["Vector"])
    links.new(grad.outputs["Color"], cr.inputs["Fac"])
    links.new(cr.outputs["Color"], emit.inputs["Color"])
    links.new(emit.outputs["Emission"], out_node.inputs["Surface"])
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


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def vl(v, d=5):
    return [round(v.x, d), round(v.y, d), round(v.z, d)]


# ─────────────────────────────────────────────────────────────────────────────
# FREESTYLE (contornos cel-shading)
# ─────────────────────────────────────────────────────────────────────────────

def setup_freestyle():
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    vl_obj = scene.view_layers[0]
    vl_obj.use_freestyle = True
    fs = vl_obj.freestyle_settings
    fs.use_culling = True
    fs.use_smoothness = True
    fs.crease_angle = math.radians(120)
    while fs.linesets:
        fs.linesets.remove(fs.linesets[0])
    ls = fs.linesets.new("AnimeOutline")
    ls.select_silhouette = True
    ls.select_crease = True
    ls.select_border = True
    ls.linestyle.color = (0.0, 0.0, 0.0)
    ls.linestyle.thickness = 1.8
    ls.linestyle.alpha = 1.0
    return {"freestyle_active": True, "line_thickness": 1.8}


# ─────────────────────────────────────────────────────────────────────────────
# BAKE DAS SHAPE KEYS (identico ao T3 — correccao do bug dos olhos na testa)
# ─────────────────────────────────────────────────────────────────────────────

def bake_shape_keys(obj):
    me = obj.data
    info = {"had_shape_keys": me.shape_keys is not None}
    if me.shape_keys is None:
        info["baked"] = False
        return info
    info["key_count_before"] = len(me.shape_keys.key_blocks)
    info["nonzero_key_values"] = {
        k.name: round(k.value, 4) for k in me.shape_keys.key_blocks
        if abs(k.value) > 1e-6
    }
    basis_before = [v.co.copy() for v in me.vertices]
    mix = obj.shape_key_add(name="T5_baked_mix", from_mix=True)
    mixed = [d.co.copy() for d in mix.data]
    obj.shape_key_clear()
    flat = []
    for co in mixed:
        flat.extend((co.x, co.y, co.z))
    me.vertices.foreach_set("co", flat)
    me.update()
    bpy.context.view_layer.update()
    shifts = [(a - b).length for a, b in zip(mixed, basis_before)]
    info["baked"] = True
    info["max_vertex_shift_m"] = round(max(shifts), 5)
    info["mean_vertex_shift_m"] = round(sum(shifts) / len(shifts), 5)
    info["shape_keys_after"] = 0 if me.shape_keys is None else len(me.shape_keys.key_blocks)
    return info


# ─────────────────────────────────────────────────────────────────────────────
# ESCALA (identica ao T3, altura alvo = 1.65 m)
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
        "factor": factor,
        "h_final_m": max(vz2) - min(vz2),
        "body_min_z": min(vz2),
        "body_max_z": max(vz2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MEDICAO DOS HELPERS DOS OLHOS (identica ao T3)
# ─────────────────────────────────────────────────────────────────────────────

def measure_eye_helpers(obj):
    wm = obj.matrix_world
    result = {}
    for side, names in (("L", ("helper-l-eye", "joint-l-eye")),
                        ("R", ("helper-r-eye", "joint-r-eye"))):
        found = None
        for gname in names:
            vg = obj.vertex_groups.get(gname)
            if vg is None:
                continue
            gi = vg.index
            coords = []
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.group == gi and g.weight > 0.0:
                        coords.append(wm @ v.co)
                        break
            if coords:
                xs = [c.x for c in coords]
                ys = [c.y for c in coords]
                zs = [c.z for c in coords]
                found = {
                    "group": gname,
                    "vertex_count": len(coords),
                    "center": Vector(((min(xs) + max(xs)) / 2,
                                      (min(ys) + max(ys)) / 2,
                                      (min(zs) + max(zs)) / 2)),
                    "centroid": Vector((sum(xs) / len(xs),
                                        sum(ys) / len(ys),
                                        sum(zs) / len(zs))),
                    "dims": Vector((max(xs) - min(xs),
                                    max(ys) - min(ys),
                                    max(zs) - min(zs))),
                }
                break
        result[side] = found
    return result


def public_helpers(h):
    pub = {}
    for side, a in h.items():
        if a is None:
            pub[side] = None
        else:
            pub[side] = {
                "group": a["group"],
                "vertex_count": a["vertex_count"],
                "bbox_center": vl(a["center"]),
                "centroid": vl(a["centroid"]),
                "dims": vl(a["dims"]),
            }
    return pub


# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRIA DA CABECA (identica ao T3)
# ─────────────────────────────────────────────────────────────────────────────

def measure_head(human, eye_z):
    bpy.context.view_layer.update()
    ev = human.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        pts = [human.matrix_world @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()
    head = [p for p in pts if p.z >= eye_z - 0.02]
    ok = len(head) > 50
    if ok:
        top_z   = max(p.z for p in head)
        half_x  = max(abs(p.x) for p in head)
        y_back  = max(p.y for p in head)
        fore    = [p for p in head if p.z >= eye_z + 0.03]
        y_front = min(p.y for p in (fore if fore else head))
    else:
        top_z, half_x, y_back, y_front = eye_z + 0.115, 0.078, 0.095, -0.105
    sane = (0.05 <= half_x <= 0.11 and 0.14 <= (y_back - y_front) <= 0.27
            and 0.08 <= (top_z - eye_z) <= 0.17)
    if not sane:
        top_z, half_x, y_back, y_front = eye_z + 0.115, 0.078, 0.095, -0.105
    return {
        "measured_ok": bool(ok and sane),
        "top_z": top_z, "half_x": half_x, "y_back": y_back, "y_front_forehead": y_front,
    }


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS ANIME (raio aumentado, iris larga, pupila eliptica, highlight)
# ─────────────────────────────────────────────────────────────────────────────

def build_eye_cap(name, centre, R, theta_max, mat):
    SEGS, RINGS = 48, 8
    me = bpy.data.meshes.new(name + "_mesh")
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)

    def pt(theta, psi):
        return (centre.x + R * math.sin(theta) * math.cos(psi),
                centre.y - R * math.cos(theta),
                centre.z + R * math.sin(theta) * math.sin(psi))

    bm = bmesh.new()
    pole = bm.verts.new(pt(0.0, 0.0))
    ring_list = []
    for ri in range(1, RINGS + 1):
        th = theta_max * ri / RINGS
        ring_list.append([bm.verts.new(pt(th, 2.0 * math.pi * s / SEGS))
                          for s in range(SEGS)])
    for s in range(SEGS):
        bm.faces.new([pole, ring_list[0][s], ring_list[0][(s + 1) % SEGS]])
    for ri in range(len(ring_list) - 1):
        for s in range(SEGS):
            sn = (s + 1) % SEGS
            bm.faces.new([ring_list[ri][s], ring_list[ri][sn],
                          ring_list[ri + 1][sn], ring_list[ri + 1][s]])
    bm.normal_update()
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - centre) < 0:
            f.normal_flip()
    bm.to_mesh(me)
    bm.free()
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    assign_material(ob, mat)
    return ob


def build_anime_eyes(human, anchors, body_min_z, body_h):
    sclera_mat = make_material_principled("T5_Sclera", COLOR_EYE_SCLERA, roughness=0.05)
    iris_mat   = make_material_eye_iris("T5_Iris", COLOR_EYE_IRIS, COLOR_EYE_PUPIL,
                                        COLOR_EYE_LIMBUS, COLOR_EYE_HILITE)
    pupil_mat  = make_material_principled("T5_Pupil", COLOR_EYE_PUPIL, roughness=0.02, metallic=0.1)
    hilite_mat = make_material_principled("T5_EyeHilite", COLOR_EYE_HILITE,
                                          roughness=0.0, metallic=0.0)

    bpy.context.view_layer.update()
    bvh = BVHTree.FromObject(human, bpy.context.evaluated_depsgraph_get())
    scale_ref = body_h / 1.65

    objects_created = []
    final = {}

    for side, sign in (("L", 1), ("R", -1)):
        a = anchors.get(side)
        radius = 0.020 * scale_ref
        center = None
        source = None
        classification = None
        note = ""

        if a is not None:
            c = a["center"]
            z_rel = (c.z - body_min_z) / body_h
            plausible = (0.88 <= z_rel <= 0.970 and 0.010 <= abs(c.x) <= 0.06 and c.y < 0.0)
            if plausible:
                center = Vector(c)
                radius = clamp(max(a["dims"]) / 2.0 * 1.9, 0.016 * scale_ref, 0.022 * scale_ref)
                source = a["group"]
                classification = "ANIME_CONFIRMADO_GRUPO_HELPER"
                note = f"Centro do grupo '{a['group']}' — raio aumentado 1.9x para anime."

        if center is None:
            fx = sign * 0.030 * scale_ref
            fz = body_min_z + body_h * 0.930
            h0, _n, _i, _d = bvh.ray_cast(Vector((fx, -5.0, fz)), Vector((0.0, 1.0, 0.0)))
            fy = (h0.y + radius * 0.8) if h0 is not None else -0.110 * scale_ref
            center = Vector((fx, fy, fz))
            source = "fallback_proporcao_anime"
            classification = "INFERIDO_FALLBACK"
            note += " Fallback 0.930 corpo."

        hit, _n, _i, _d = bvh.ray_cast(Vector((center.x, -5.0, center.z)), Vector((0.0, 1.0, 0.0)))
        front_y = center.y - radius
        shift = 0.0
        if hit is None:
            skin_state = "sem_pele_a_frente"
            hit_y = None
        else:
            hit_y = hit.y
            if hit.y >= center.y - 0.5 * radius:
                skin_state = "abertura_ok"
            elif hit.y < front_y - 0.001:
                shift = min(front_y - hit.y + 0.0008, 0.014)
                center.y -= shift
                skin_state = "pele_tapava_avancado"
            else:
                skin_state = "pele_junto_superficie_ok"

        iris_r  = radius * 0.68
        pupil_r = iris_r * 0.38

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=48, ring_count=24, radius=radius, location=tuple(center))
        sc = bpy.context.active_object
        sc.name = f"T5_Sclera_{side}"
        for p in sc.data.polygons:
            p.use_smooth = True
        assign_material(sc, sclera_mat)

        ir = build_eye_cap(f"T5_Iris_{side}", center, radius * 1.008,
                           math.asin(min(iris_r / radius, 0.98)), iris_mat)

        pu = build_eye_cap(f"T5_Pupil_{side}", center, radius * 1.016,
                           math.asin(min(pupil_r / radius, 0.98)), pupil_mat)
        pu.scale.z = 1.35
        pu.scale.x = 0.75
        bpy.ops.object.select_all(action="DESELECT")
        pu.select_set(True)
        bpy.context.view_layer.objects.active = pu
        bpy.ops.object.transform_apply(scale=True)

        hilite_offset = Vector((sign * iris_r * 0.35, 0.0, iris_r * 0.40))
        hilite_center = center + hilite_offset
        hl = build_eye_cap(f"T5_Hilite_{side}", hilite_center, radius * 1.020,
                           math.asin(min(iris_r * 0.22 / radius, 0.98)), hilite_mat)

        objects_created.extend([sc.name, ir.name, pu.name, hl.name])
        final[side] = {
            "x": center.x, "y": center.y, "z": center.z,
            "radius": radius, "iris_r": iris_r, "pupil_r": pupil_r,
            "source": source, "classification": classification, "note": note,
            "skin_state": skin_state,
            "skin_hit_y": None if hit_y is None else round(hit_y, 6),
            "forward_shift_m": round(shift, 6),
            "height_ratio": round((center.z - body_min_z) / body_h, 4),
        }
        eye_diag[f"placement_{side}"] = dict(final[side])

    eye_diag["eye_separation_m"] = round(abs(final["L"]["x"] - final["R"]["x"]), 6)
    eye_diag["status"] = "built_anime"
    return objects_created, final


# ─────────────────────────────────────────────────────────────────────────────
# CABELO ANIME
# ─────────────────────────────────────────────────────────────────────────────

def build_anime_hair(head, eye_z, waist_z, shoulder_z, bust_z):
    hair_mat = make_material_toon("T5_Hair", COLOR_HAIR, COLOR_HAIR_HILITE, roughness=0.90)
    objects_created = []

    cz     = eye_z - 0.012
    a      = head["half_x"] + 0.008
    b      = (head["y_back"] - head["y_front_forehead"]) / 2.0 + 0.008
    cy     = (head["y_back"] + head["y_front_forehead"]) / 2.0
    c      = (head["top_z"] - cz) + 0.008
    y_fore = head["y_front_forehead"]

    # Touca base
    CAP_SEGS  = 32
    CAP_RINGS = 12
    PHI_MAX   = math.pi * 0.50
    FRONT_PHI = 3.0 * math.pi / 2.0

    cap_mesh = bpy.data.meshes.new("T5_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("T5_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    pole = bm.verts.new((0.0, cy, cz + c))
    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi = PHI_MAX * ri / CAP_RINGS
        ring = []
        for s in range(CAP_SEGS):
            th = 2.0 * math.pi * s / CAP_SEGS
            ring.append(bm.verts.new((
                a * math.sin(phi) * math.cos(th),
                cy + b * math.sin(phi) * math.sin(th),
                cz + c * math.cos(phi),
            )))
        rings.append(ring)
    for s in range(CAP_SEGS):
        bm.faces.new([pole, rings[0][s], rings[0][(s + 1) % CAP_SEGS]])
    for ri in range(len(rings) - 1):
        for s in range(CAP_SEGS):
            sn = (s + 1) % CAP_SEGS
            bm.faces.new([rings[ri][s], rings[ri][sn],
                          rings[ri + 1][sn], rings[ri + 1][s]])
    bm.normal_update()
    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    # Franja anime (7 picos triangulares angulosos)
    fringe_mesh = bpy.data.meshes.new("T5_Fringe_mesh")
    fringe_obj  = bpy.data.objects.new("T5_Fringe", fringe_mesh)
    bpy.context.collection.objects.link(fringe_obj)
    bm2 = bmesh.new()
    N_PICOS = 7
    fringe_top_z    = eye_z + 0.055
    fringe_bottom_z = eye_z + 0.005
    fringe_y        = y_fore - 0.004
    for i in range(N_PICOS):
        t     = i / (N_PICOS - 1)
        cx_i  = a * 0.85 * (2.0 * t - 1.0)
        alt   = 0.008 * (1.0 + 0.5 * math.sin(i * 1.3))
        v0 = bm2.verts.new((cx_i - a * 0.07, fringe_y, fringe_top_z))
        v1 = bm2.verts.new((cx_i + a * 0.07, fringe_y, fringe_top_z))
        v2 = bm2.verts.new((cx_i,             fringe_y, fringe_bottom_z - alt))
        bm2.faces.new([v0, v1, v2])
    bm2.normal_update()
    bm2.to_mesh(fringe_mesh)
    bm2.free()
    fringe_mesh.update()
    fringe_obj.data.materials.append(hair_mat)
    objects_created.append(fringe_obj.name)

    # Mechas laterais (20 mechas NURBS)
    N_STRANDS   = 20
    STRAND_SEGS = 8
    FRONT_RANGE = math.radians(55)

    for i in range(N_STRANDS):
        azimuth = 2.0 * math.pi * i / N_STRANDS
        diff = abs(azimuth - FRONT_PHI)
        if diff > math.pi:
            diff = 2.0 * math.pi - diff
        is_frontal = diff < FRONT_RANGE

        phi_birth       = math.pi * 0.25 if not is_frontal else math.pi * 0.35
        strand_len_frac = 0.70 if is_frontal else 1.0
        bevel           = 0.009 + 0.004 * (i % 3)

        root_x = a * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cy + b * math.sin(phi_birth) * math.sin(azimuth)
        root_z = cz + c * math.cos(phi_birth)
        tip_z  = bust_z + (shoulder_z - bust_z) * strand_len_frac * 0.5

        cdata = bpy.data.curves.new(name=f"T5_Strand_{i:02d}", type="CURVE")
        cdata.dimensions = "3D"
        cdata.bevel_depth = bevel
        cdata.bevel_resolution = 2
        cdata.use_fill_caps = True
        sp = cdata.splines.new("NURBS")
        sp.points.add(STRAND_SEGS - 1)
        for pi_idx in range(STRAND_SEGS):
            t = pi_idx / (STRAND_SEGS - 1)
            pz   = root_z + (tip_z - root_z) * t
            splay = 1.0 + t * 0.30
            px   = root_x * splay
            py   = cy + (root_y - cy) * splay
            sp.points[pi_idx].co = (px, py, pz, 1.0)
        sp.use_endpoint_u = True
        sp.order_u = min(4, STRAND_SEGS)
        strand_obj = bpy.data.objects.new(f"T5_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    # Mechas longas no peito (caracteristica anime)
    for side, sign in (("L", -1), ("R", 1)):
        root_x = sign * a * 0.55
        root_y = cy + b * 0.85 * (-1.0)
        root_z = cz + c * 0.10
        tip_x  = sign * a * 0.35
        tip_y  = root_y - 0.020
        tip_z  = bust_z - 0.040
        cdata3 = bpy.data.curves.new(name=f"T5_ChestStrand_{side}", type="CURVE")
        cdata3.dimensions = "3D"
        cdata3.bevel_depth = 0.011
        cdata3.bevel_resolution = 3
        cdata3.use_fill_caps = True
        sp3 = cdata3.splines.new("NURBS")
        CHEST_SEGS = 10
        sp3.points.add(CHEST_SEGS - 1)
        for pi_idx in range(CHEST_SEGS):
            t = pi_idx / (CHEST_SEGS - 1)
            sp3.points[pi_idx].co = (
                root_x + (tip_x - root_x) * t,
                root_y + (tip_y - root_y) * t,
                root_z + (tip_z - root_z) * t,
                1.0,
            )
        sp3.use_endpoint_u = True
        sp3.order_u = min(4, CHEST_SEGS)
        chest_obj = bpy.data.objects.new(f"T5_ChestStrand_{side}", cdata3)
        bpy.context.collection.objects.link(chest_obj)
        chest_obj.data.materials.append(hair_mat)
        objects_created.append(chest_obj.name)

    return objects_created, {
        "fringe": "7 picos triangulares angulosos",
        "side_strands": N_STRANDS,
        "chest_strands": 2,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ROUPA SIMPLES ANIME
# ─────────────────────────────────────────────────────────────────────────────

def build_clothing(body_min_z, body_h, scale_ref):
    top_mat   = make_material_toon("T5_Top",   COLOR_CLOTH_TOP,   roughness=0.80)
    pants_mat = make_material_toon("T5_Pants", COLOR_CLOTH_PANTS, roughness=0.75)
    objects_created = []

    waist_z = body_min_z + body_h * 0.575
    bust_z  = body_min_z + body_h * 0.695
    hip_z   = body_min_z + body_h * 0.535
    ankle_z = body_min_z + body_h * 0.055
    leg_r   = 0.062 * scale_ref

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=32, radius=0.135 * scale_ref * 1.05, depth=(bust_z - waist_z),
        location=(0.0, 0.0, (bust_z + waist_z) / 2.0))
    top_obj = bpy.context.active_object
    top_obj.name = "T5_Top"
    assign_material(top_obj, top_mat)
    objects_created.append(top_obj.name)

    SEP = 0.055 * scale_ref
    for side, sign in (("L", -1), ("R", 1)):
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=24, radius=leg_r, depth=(hip_z - ankle_z),
            location=(sign * SEP, 0.0, (hip_z + ankle_z) / 2.0))
        leg_obj = bpy.context.active_object
        leg_obj.name = f"T5_Pants_{side}"
        assign_material(leg_obj, pants_mat)
        objects_created.append(leg_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CALCADO
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z):
    shoe_mat = make_material_principled("T5_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)
    SHOE_L, SHOE_W, SHOE_H, SHOE_SEP = 0.22, 0.085, 0.042, 0.095
    objects_created = []
    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx = sign * SHOE_SEP / 2.0
        mesh_data = bpy.data.meshes.new(f"T5_Shoe_{side}_mesh")
        shoe_obj  = bpy.data.objects.new(f"T5_Shoe_{side}", mesh_data)
        bpy.context.collection.objects.link(shoe_obj)
        bm = bmesh.new()
        hw, hl = SHOE_W / 2.0, SHOE_L / 2.0
        toe_hw = hw * 0.35
        bz = body_min_z
        verts = [
            bm.verts.new((shoe_cx - hw,     -hl, bz)),
            bm.verts.new((shoe_cx + hw,     -hl, bz)),
            bm.verts.new((shoe_cx + toe_hw,  hl, bz)),
            bm.verts.new((shoe_cx - toe_hw,  hl, bz)),
            bm.verts.new((shoe_cx - hw,     -hl, bz + SHOE_H)),
            bm.verts.new((shoe_cx + hw,     -hl, bz + SHOE_H)),
            bm.verts.new((shoe_cx + toe_hw,  hl, bz + SHOE_H * 0.70)),
            bm.verts.new((shoe_cx - toe_hw,  hl, bz + SHOE_H * 0.70)),
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
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.08, 0.08, 0.10)


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_light(loc, target, energy, size, name):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name = name
    l.data.energy = energy
    l.data.size = size
    d = Vector(target) - Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


def place_camera_persp(loc, target, lens_mm, name):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "PERSP"
    cam.data.lens = lens_mm
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def place_camera_ortho(loc, target, ortho_scale, name):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = ortho_scale
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def render_to(path, tag, samples=64):
    bpy.context.scene.cycles.samples = samples
    bpy.context.scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as exc:
        report["renders"][tag] = {"rendered": False, "error": str(exc)}
        return False
    exists = Path(path).exists()
    report["renders"][tag] = {"rendered": exists, "file": Path(path).name}
    return exists


# ─────────────────────────────────────────────────────────────────────────────
# RELATORIO
# ─────────────────────────────────────────────────────────────────────────────

def write_validation_report():
    L = []
    L.append("# T5 — Personagem Anime 3D — Relatorio de Validacao\n")
    L.append(f"Estado: **{report.get('status', 'desconhecido')}**\n")
    L.append("## Diferencias face ao T3\n")
    L.append("- Olhos: raio 1.9x maior, iris 68%, pupila eliptica, highlight branco")
    L.append("- Materiais: Toon Shading (BsdfToon duas faixas) + Freestyle activado")
    L.append("- Cabelo: franja com 7 picos angulosos + 2 mechas longas no peito")
    L.append("- Roupa: top lavanda + calcas ganga")
    L.append("- Altura: 1.65 m (vs 1.70 m do T3)")
    L.append("\n## Olhos\n")
    for side in ("L", "R"):
        p = eye_diag.get(f"placement_{side}", {})
        L.append(f"- {side}: {p.get('classification')} h={p.get('height_ratio')} "
                 f"raio={p.get('radius')} pele={p.get('skin_state')}")
    L.append(f"\n- Separacao: {eye_diag.get('eye_separation_m')} m")
    L.append("\n## Renders\n")
    for tag, data in report.get("renders", {}).items():
        L.append(f"- `{tag}`: {'OK' if data.get('rendered') else 'FALHOU'}")
    L.append("\n## Limitacoes Conhecidas\n")
    for item in report.get("visual_limitations", []):
        L.append(f"- {item}")
    L.append("\n## Conclusao\n")
    L.append("Aprovacao visual exige inspeccao de `t5_04_face_closeup.png` e `t5_05_eyes_closeup.png`.")
    (out / "validation_report.md").write_text("\n".join(L), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUCAO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

try:

    # Instalar MPFB (identico ao T3)
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

    # Criar humano
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    result = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result:
        raise RuntimeError(f"mpfb.create_human devolveu: {result}")

    human = bpy.data.objects.get("Human")
    if human is None or human.type != "MESH":
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            raise RuntimeError("Nenhuma malha encontrada apos create_human")
        human = meshes[0]
    if human.data.shape_keys is None:
        raise RuntimeError("Human nao tem shape keys")

    report["components"]["human_base"] = {
        "name": human.name,
        "vertices": len(human.data.vertices),
        "shape_keys": len(human.data.shape_keys.key_blocks),
    }

    eyes_before = measure_eye_helpers(human)
    eye_diag["helpers_before_bake"] = public_helpers(eyes_before)

    # Morph feminino (identico ao T3)
    keys = human.data.shape_keys.key_blocks
    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0
    bpy.context.view_layer.update()
    report["components"]["female_morph"] = {"status": "applied"}

    # Bake (identico ao T3)
    bake_info = bake_shape_keys(human)
    report["components"]["bake"] = bake_info
    if not bake_info.get("baked"):
        raise RuntimeError("bake_shape_keys nao gravou o mix")

    eyes_after_bake = measure_eye_helpers(human)
    eye_diag["helpers_after_bake"] = public_helpers(eyes_after_bake)
    for side in ("L", "R"):
        b0, b1 = eyes_before.get(side), eyes_after_bake.get(side)
        if b0 is not None and b1 is not None:
            eye_diag["bake_effect"][f"center_shift_{side}"] = vl(b1["center"] - b0["center"])

    # Escala para 1.65 m
    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_height = hi["h_final_m"]
    body_min_z   = hi["body_min_z"]
    body_max_z   = hi["body_max_z"]
    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(f"Altura final ({world_height:.4f} m) fora da tolerancia")

    report["components"]["height"] = {
        "target_m": TARGET_HEIGHT_M,
        "final_m":  world_height,
        "ok": True,
    }

    body_h     = body_max_z - body_min_z
    scale_ref  = body_h / 1.65
    waist_z    = body_min_z + body_h * 0.575
    bust_z     = body_min_z + body_h * 0.695
    shoulder_z = body_min_z + body_h * 0.800

    # Freestyle
    fs_info = setup_freestyle()
    report["components"]["freestyle"] = fs_info

    # Pele toon
    skin_mat = make_material_toon("T5_Skin", COLOR_SKIN, COLOR_SKIN_SHADOW)
    assign_material(human, skin_mat)
    report["components"]["skin"] = {"material": "T5_Skin", "style": "toon_two_band"}

    # Olhos anime
    eyes_final = measure_eye_helpers(human)
    eye_diag["helpers_final"] = public_helpers(eyes_final)
    eye_objects, eye_final_positions = build_anime_eyes(human, eyes_final, body_min_z, body_h)
    report["components"]["eyes"] = {
        "objects": eye_objects,
        "style": "anime_large_iris_elliptic_pupil_highlight",
        "diagnostic": eye_diag,
    }

    eye_mid_z = (eye_final_positions["L"]["z"] + eye_final_positions["R"]["z"]) / 2.0

    # Cabelo anime
    head = measure_head(human, eye_mid_z)
    eye_diag["head_geometry"] = {k: (round(v, 5) if isinstance(v, float) else v)
                                 for k, v in head.items()}
    hair_objects, hair_info = build_anime_hair(head, eye_mid_z, waist_z, shoulder_z, bust_z)
    report["components"]["hair"] = {
        "objects": hair_objects,
        "style": "anime_fringe_with_chest_strands",
        "info": hair_info,
    }

    # Roupa
    cloth_objects = build_clothing(body_min_z, body_h, scale_ref)
    report["components"]["clothing"] = {"objects": cloth_objects}

    # Calcado
    shoe_objects = build_shoes(body_min_z)
    report["components"]["shoes"] = {"objects": shoe_objects}

    # Validacoes
    expected_core = {
        "Human", "T5_HairCap", "T5_Fringe",
        "T5_Sclera_L", "T5_Iris_L", "T5_Pupil_L", "T5_Hilite_L",
        "T5_Sclera_R", "T5_Iris_R", "T5_Pupil_R", "T5_Hilite_R",
        "T5_Top", "T5_Pants_L", "T5_Pants_R",
        "T5_Shoe_L", "T5_Shoe_R",
        "T5_ChestStrand_L", "T5_ChestStrand_R",
    }
    for i in range(20):
        expected_core.add(f"T5_Strand_{i:02d}")

    scene_objects = {o.name for o in bpy.data.objects if o.type not in ("LIGHT", "CAMERA")}
    missing = expected_core - scene_objects

    report["validations"]["scene_objects"] = {
        "expected_count": len(expected_core),
        "found_count": len(scene_objects),
        "missing": sorted(missing),
        "pass": len(missing) == 0,
    }
    report["validations"]["world_height"] = {
        "value_m": world_height,
        "target_m": TARGET_HEIGHT_M,
        "pass": abs(world_height - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M,
    }
    eye_sep = eye_diag.get("eye_separation_m") or 0.0
    ratios  = [eye_final_positions[s]["height_ratio"] for s in ("L", "R")]
    report["validations"]["eye_geometry"] = {
        "eye_separation_m": eye_sep,
        "separation_plausible": 0.04 <= eye_sep <= 0.08,
        "height_ratio_L_R": ratios,
        "height_ratio_plausible": all(0.88 <= r <= 0.975 for r in ratios),
        "pass": (0.04 <= eye_sep <= 0.08) and all(0.88 <= r <= 0.975 for r in ratios),
    }
    report["validations"]["overall_pass"] = all(
        v.get("pass", False) for v in report["validations"].values()
        if isinstance(v, dict) and "pass" in v
    )

    report["visual_limitations"] = [
        "Toon Shading via BsdfToon; aparencia depende da iluminacao da cena",
        "Freestyle activo: contornos visíveis nas renders PNG",
        "Cabelo em curvas NURBS; mechas de peito nao colam ao corpo (sem rigging)",
        "Olhos grandes podem intersectar ligeiramente a malha facial do MPFB",
        "Roupa em cilindros simples; sem simulacao de tecido",
        "Aprovacao visual final exige inspeccao humana das imagens PNG",
        "Morph feminino experimental herdado do T3",
    ]

    # ── RENDERS (6 vistas) ───────────────────────────────────────────────────
    head_radius   = body_h * 0.075
    head_center_z = eye_mid_z - 0.010
    body_center_z = body_min_z + body_h * 0.50

    setup_render(samples=64)

    # 01 — Corpo inteiro frente
    clear_lights_cameras()
    add_light(( 0.8, -3.0, body_center_z + 0.5), (0.0, 0.0, body_center_z), 800, 1.2, "T5_Key")
    add_light((-0.6, -2.5, body_center_z),        (0.0, 0.0, body_center_z), 400, 1.0, "T5_Fill")
    add_light(( 0.0,  1.5, body_center_z + 0.8),  (0.0, 0.0, body_center_z), 300, 0.8, "T5_Rim")
    place_camera_ortho((0.0, -3.5, body_center_z), (0.0, 0.0, body_center_z),
                       body_h * 1.25, "T5Cam_Front")
    render_to(out / "t5_01_full_front.png", "01_full_front")

    # 02 — Corpo inteiro lado
    clear_lights_cameras()
    add_light(( 2.0, -1.0, body_center_z + 0.4), (0.0, 0.0, body_center_z), 600, 1.2, "T5_Key2")
    add_light(( 0.5,  1.5, body_center_z),        (0.0, 0.0, body_center_z), 300, 1.0, "T5_Fill2")
    place_camera_ortho((3.5, 0.0, body_center_z), (0.0, 0.0, body_center_z),
                       body_h * 1.25, "T5Cam_Side")
    render_to(out / "t5_02_full_side.png", "02_full_side")

    # 03 — Tres quartos
    clear_lights_cameras()
    add_light(( 0.8, -3.0, body_center_z + 0.5), (0.0, 0.0, body_center_z), 800, 1.2, "T5_Key3")
    add_light((-0.6, -2.5, body_center_z),        (0.0, 0.0, body_center_z), 400, 1.0, "T5_Fill3")
    place_camera_ortho((2.5, -2.5, body_center_z), (0.0, 0.0, body_center_z),
                       body_h * 1.25, "T5Cam_3Q")
    render_to(out / "t5_03_three_quarter.png", "03_three_quarter")

    # 04 — Rosto close-up
    head_tgt = Vector((0.0, 0.0, head_center_z))
    cam_fy   = -head_radius * 5.5
    clear_lights_cameras()
    add_light((head_radius * 2.5, cam_fy + head_radius, head_center_z + head_radius * 0.5),
              head_tgt, 600, 0.5, "T5_Key4")
    add_light((-head_radius * 2.0, cam_fy + head_radius * 0.8, head_center_z),
              head_tgt, 300, 0.4, "T5_Fill4")
    add_light((0.0, head_radius * 2.0, head_center_z + head_radius),
              head_tgt, 200, 0.35, "T5_Rim4")
    place_camera_persp((0.0, cam_fy, head_center_z), head_tgt, 85, "T5Cam_Face")
    render_to(out / "t5_04_face_closeup.png", "04_face_closeup", samples=96)

    # 05 — Olhos close-up
    eL, eR = eye_final_positions["L"], eye_final_positions["R"]
    eye_mid_y = (eL["y"] + eR["y"]) / 2.0
    eye_sep_m = abs(eL["x"] - eR["x"])
    eye_ortho = max(eye_sep_m * 3.5, eL["radius"] * 10)
    eye_tgt   = Vector((0.0, eye_mid_y, eye_mid_z))
    cam_ey    = eye_mid_y - eye_ortho * 3.5
    clear_lights_cameras()
    add_light((eye_sep_m * 0.8, cam_ey + eye_sep_m, eye_mid_z + head_radius * 0.1),
              eye_tgt, 350, 0.3, "T5_EyeKey")
    add_light((-eye_sep_m * 0.6, cam_ey + eye_sep_m * 0.8, eye_mid_z),
              eye_tgt, 180, 0.25, "T5_EyeFill")
    place_camera_ortho((0.0, cam_ey, eye_mid_z), eye_tgt, eye_ortho, "T5Cam_Eyes")
    render_to(out / "t5_05_eyes_closeup.png", "05_eyes_closeup", samples=128)

    # 06 — Costas
    clear_lights_cameras()
    add_light(( 0.8,  3.0, body_center_z + 0.5), (0.0, 0.0, body_center_z), 800, 1.2, "T5_Key6")
    add_light((-0.6,  2.5, body_center_z),        (0.0, 0.0, body_center_z), 400, 1.0, "T5_Fill6")
    place_camera_ortho((0.0, 3.5, body_center_z), (0.0, 0.0, body_center_z),
                       body_h * 1.25, "T5Cam_Back")
    render_to(out / "t5_06_full_body_back.png", "06_full_body_back")

    # Gravar ficheiros
    blend_path = out / "mpfb-female-t5.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-t5.glb"
    bpy.ops.object.select_all(action="SELECT")
    for obj in bpy.context.selected_objects:
        if obj.type in ("LIGHT", "CAMERA"):
            obj.select_set(False)
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

    report["status"] = "success_t5_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "mpfb-t5-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )
    (out / "diagnostic_report.json").write_text(
        json.dumps({"blender": report.get("blender"),
                    "script": "scripts/characters/create_female_t5.py",
                    "style": "anime_3d",
                    "bake": report.get("components", {}).get("bake"),
                    "eyes": eye_diag,
                    "renders": report.get("renders", {}),
                    "errors_and_warnings": {
                        "error": report.get("error"),
                        "traceback": report.get("traceback"),
                    },
                    "overall_status": report.get("status")},
                   ensure_ascii=False, indent=2, default=str)
    )
    try:
        write_validation_report()
    except Exception:
        (out / "validation_report.md").write_text(
            "# T5 — Relatorio de Validacao\n\nFalha ao gerar relatorio. "
            "Consultar diagnostic_report.json e mpfb-t5-report.json.\n",
            encoding="utf-8",
        )
    print("MPFB_T5_REPORT:", json.dumps(report, ensure_ascii=False, default=str))

if report["status"] != "success_t5_complete":
    sys.exit(1)
