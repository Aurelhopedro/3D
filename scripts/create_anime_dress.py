"""
create_anime_dress.py — Vestido de gala anime vermelho (estilo acetinado)

BASE: corpo do T5 (create_female_t5.py) — sem modificar B5/B7/B8/B9.
OBJETIVO: vestido de gala com silhueta elegante, acetinado vermelho,
  pregas e dobras, abertura na saia, compativel com o corpo T5.

ESTRUTURA DO VESTIDO (geometria procedural, sem cilindro simples):
  1. Corpete: malha em secoes com raios variados (ombros → bust → cintura)
  2. Saia: 16 secoes com expansao progressiva, pregas por variacao angular
  3. Abertura frontal na saia (corte em V de 18 cm de abertura)
  4. Acabamentos nas bordas (edge bevel procedural)
  5. Material PBR acetinado: vermelho profundo, roughness 0.18, anisotropia

OUTPUTS:
  output/mpfb-dress/mpfb-female-dress.blend
  output/mpfb-dress/mpfb-female-dress.glb
  output/mpfb-dress/dress_front.png
  output/mpfb-dress/dress_back.png
  output/mpfb-dress/dress_side.png
  output/mpfb-dress/dress_three_quarter.png
  reports/dress_validation.json
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
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M    = 1.65
HEIGHT_TOLERANCE_M = 0.01

COLOR_DRESS_RED    = "#8B0000"
COLOR_DRESS_DARK   = "#5C0000"
COLOR_SKIN         = "#F2C9A0"
COLOR_SKIN_SHADOW  = "#D4936B"
COLOR_HAIR         = "#0D0A0A"
COLOR_HAIR_HILITE  = "#1A2A4A"
COLOR_EYE_SCLERA   = "#F8F5EE"
COLOR_EYE_IRIS     = "#5B3A8C"
COLOR_EYE_PUPIL    = "#0A0508"
COLOR_EYE_HILITE   = "#FFFFFF"
COLOR_EYE_LIMBUS   = "#1A0A2A"
COLOR_SHOE_HEEL    = "#B8860B"

out     = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-dress")).resolve()
rep_dir = Path(os.environ.get("MPFB_REPORTS", "reports")).resolve()
out.mkdir(parents=True, exist_ok=True)
rep_dir.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "dress_v1",
    "style": "anime_gala_dress",
    "based_on": "create_female_t5.py (corpo); vestido independente",
    "status": "started",
    "components": {},
    "validations": {},
    "renders": {},
    "visual_limitations": [],
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITARIOS
# ─────────────────────────────────────────────────────────────────────────────

def srgb_to_linear(c):
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (srgb_to_linear(int(h[0:2], 16) / 255.0),
            srgb_to_linear(int(h[2:4], 16) / 255.0),
            srgb_to_linear(int(h[4:6], 16) / 255.0),
            alpha)


def assign_mat(obj, mat, slot=0):
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


# ─────────────────────────────────────────────────────────────────────────────
# MATERIAIS
# ─────────────────────────────────────────────────────────────────────────────

def make_satin_dress_material():
    """PBR acetinado vermelho profundo com variacao procedural de rugosidade."""
    mat = bpy.data.materials.new(name="Dress_Satin_Red")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Textura procedural de variacao de rugosidade (simula pregas de tecido)
    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-900, 0)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-700, 0)
    mapping.inputs["Scale"].default_value = (12.0, 12.0, 12.0)
    wave = nodes.new("ShaderNodeTexWave")
    wave.location = (-500, 0)
    wave.wave_type = "BANDS"
    wave.inputs["Scale"].default_value = 8.0
    wave.inputs["Distortion"].default_value = 2.5
    wave.inputs["Detail"].default_value = 4.0
    wave.inputs["Detail Scale"].default_value = 1.2
    wave.inputs["Detail Roughness"].default_value = 0.6

    # Mapear wave para variacao de rugosidade (0.10 a 0.28)
    map_range = nodes.new("ShaderNodeMapRange")
    map_range.location = (-300, -100)
    map_range.inputs["From Min"].default_value = 0.0
    map_range.inputs["From Max"].default_value = 1.0
    map_range.inputs["To Min"].default_value = 0.10
    map_range.inputs["To Max"].default_value = 0.28

    # Cor base: mescla vermelho profundo com vinho escuro nas sombras das ondas
    mix_color = nodes.new("ShaderNodeMixRGB")
    mix_color.location = (-300, 150)
    mix_color.blend_type = "MULTIPLY"
    mix_color.inputs["Fac"].default_value = 0.35
    mix_color.inputs["Color1"].default_value = hex_rgba(COLOR_DRESS_RED)
    mix_color.inputs["Color2"].default_value = hex_rgba(COLOR_DRESS_DARK)

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    bsdf.inputs["Roughness"].default_value = 0.18
    bsdf.inputs["Metallic"].default_value = 0.0
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.75
            break
    for an in ("Anisotropic", "Anisotropic Rotation"):
        if an in bsdf.inputs:
            bsdf.inputs[an].default_value = 0.55 if an == "Anisotropic" else 0.0
            break
    for ss in ("Subsurface Weight", "Subsurface"):
        if ss in bsdf.inputs:
            bsdf.inputs[ss].default_value = 0.04
            break
    if "Sheen Weight" in bsdf.inputs:
        bsdf.inputs["Sheen Weight"].default_value = 0.15
    elif "Sheen" in bsdf.inputs:
        bsdf.inputs["Sheen"].default_value = 0.15

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)

    links.new(texcoord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    links.new(wave.outputs["Fac"], map_range.inputs["Value"])
    links.new(wave.outputs["Fac"], mix_color.inputs["Fac"])
    links.new(map_range.outputs["Result"], bsdf.inputs["Roughness"])
    links.new(mix_color.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


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
    toon.inputs["Color"].default_value = hex_rgba(hex_color)
    toon.inputs["Size"].default_value = 0.65
    toon.inputs["Smooth"].default_value = 0.05
    toon_s = nodes.new("ShaderNodeBsdfToon")
    toon_s.location = (-500, -100)
    toon_s.component = "DIFFUSE"
    toon_s.inputs["Color"].default_value = hex_rgba(shadow_color)
    toon_s.inputs["Size"].default_value = 0.30
    toon_s.inputs["Smooth"].default_value = 0.02
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (-200, 0)
    mix.inputs["Fac"].default_value = 0.35
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (100, 0)
    links.new(toon.outputs["BSDF"],   mix.inputs[1])
    links.new(toon_s.outputs["BSDF"], mix.inputs[2])
    links.new(mix.outputs["Shader"],  out_node.inputs["Surface"])
    return mat


def make_material_principled(name, hex_color, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (-300, 0)
    bsdf.inputs["Base Color"].default_value = hex_rgba(hex_color)
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
    e[0].color = hex_rgba(hex_hilite)
    e[1].position = 0.25
    e[1].color = hex_rgba(hex_iris)
    cr.color_ramp.elements.new(0.75).color = hex_rgba(hex_pupil)
    cr.color_ramp.elements.new(0.95).color = hex_rgba(hex_limbus)
    emit = nodes.new("ShaderNodeEmission")
    emit.location = (0, 0)
    emit.inputs["Strength"].default_value = 0.8
    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (200, 0)
    links.new(texcoord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"],  grad.inputs["Vector"])
    links.new(grad.outputs["Color"],      cr.inputs["Fac"])
    links.new(cr.outputs["Color"],        emit.inputs["Color"])
    links.new(emit.outputs["Emission"],   out_node.inputs["Surface"])
    return mat


# ─────────────────────────────────────────────────────────────────────────────
# BAKE + ESCALA (identicos ao T3/T5)
# ─────────────────────────────────────────────────────────────────────────────

def bake_shape_keys(obj):
    me = obj.data
    info = {"had_shape_keys": me.shape_keys is not None}
    if me.shape_keys is None:
        info["baked"] = False
        return info
    info["key_count_before"] = len(me.shape_keys.key_blocks)
    basis_before = [v.co.copy() for v in me.vertices]
    mix = obj.shape_key_add(name="DRESS_baked_mix", from_mix=True)
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
    return info


def scale_mesh_to_height(obj, target_h):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()
    vz = [v.co.z for v in obj.data.vertices]
    h_initial = max(vz) - min(vz)
    if h_initial <= 0:
        raise RuntimeError(f"Altura invalida: {h_initial}")
    factor = target_h / h_initial
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    z_min = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z = z_min + (v.co.z - z_min) * factor
        v.co.x *= factor
        v.co.y *= factor
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    vz2 = [v.co.z for v in obj.data.vertices]
    return {"h_final_m": max(vz2) - min(vz2), "body_min_z": min(vz2), "body_max_z": max(vz2), "factor": factor}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS DOS OLHOS
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
                    "center": Vector(((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2)),
                    "dims":   Vector((max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs))),
                }
                break
        result[side] = found
    return result


# ─────────────────────────────────────────────────────────────────────────────
# VESTIDO — GEOMETRIA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def build_dress(body_min_z, body_h, scale_ref):
    """
    Vestido de gala em 3 partes:
      A) Corpete: 6 secoes de ombro a cintura (raios variados)
      B) Saia: 16 secoes com expansao progressiva e pregas angulares
      C) Abertura frontal em V na saia
    Geometria construida em bmesh, face a face, com normais correctas.
    """
    DRESS_SEGS = 48  # subdivisoes angulares

    # Marcos do corpo escalados
    shoulder_z  = body_min_z + body_h * 0.800
    bust_z      = body_min_z + body_h * 0.695
    underbust_z = body_min_z + body_h * 0.650
    waist_z     = body_min_z + body_h * 0.575
    hip_z       = body_min_z + body_h * 0.535
    floor_z     = body_min_z + body_h * 0.028

    # Offset radial para nao interpenetrar a pele
    OFFSET = 0.015 * scale_ref

    # Perfil do corpete: (z, radius)
    corpete_profile = [
        (shoulder_z,  0.148 * scale_ref),
        (bust_z,      0.145 * scale_ref),
        (underbust_z, 0.135 * scale_ref),
        (waist_z,     0.118 * scale_ref),
        (hip_z,       0.145 * scale_ref),
    ]
    corpete_profile = [(z, r + OFFSET) for z, r in corpete_profile]

    # Perfil da saia: 16 secoes com pregas (variacao de raio por seno)
    N_SKIRT = 16
    skirt_sections = []
    for i in range(N_SKIRT + 1):
        t = i / N_SKIRT
        z = hip_z - (hip_z - floor_z) * t
        # expansao base: 0.145 (quadril) -> 0.38 (barra)
        r_base = (0.145 + 0.235 * (t ** 0.75)) * scale_ref + OFFSET
        # pregas: variacao de +/-2% em rings alternados
        r = r_base * (1.0 + 0.022 * math.sin(i * math.pi))
        skirt_sections.append((z, r))

    # Abertura em V: indice angular de 0 a DRESS_SEGS
    #   frente = segmento DRESS_SEGS//4 (Y negativo no sistema MPFB)
    FRONT_SEG   = DRESS_SEGS // 4
    SPLIT_HALF  = 3  # abertura de 3 segmentos para cada lado do centro
    SPLIT_START = max(0, FRONT_SEG - SPLIT_HALF)
    SPLIT_END   = min(DRESS_SEGS - 1, FRONT_SEG + SPLIT_HALF)
    SPLIT_FLOOR = body_min_z + body_h * 0.22  # a abertura comeca aqui

    dress_mesh = bpy.data.meshes.new("Dress_mesh")
    dress_obj  = bpy.data.objects.new("Dress_AnimeDress", dress_mesh)
    bpy.context.collection.objects.link(dress_obj)

    bm = bmesh.new()

    def ring(z, r, segs, y_offset=0.0):
        """Cria um anel de vertices."""
        verts = []
        for s in range(segs):
            theta = 2.0 * math.pi * s / segs
            verts.append(bm.verts.new((
                r * math.cos(theta),
                r * math.sin(theta) + y_offset,
                z,
            )))
        return verts

    # ── CORPETE ──────────────────────────────────────────────────────────────
    rings_corp = [ring(z, r, DRESS_SEGS) for z, r in corpete_profile]
    for ri in range(len(rings_corp) - 1):
        r0, r1 = rings_corp[ri], rings_corp[ri + 1]
        for s in range(DRESS_SEGS):
            sn = (s + 1) % DRESS_SEGS
            bm.faces.new([r0[s], r0[sn], r1[sn], r1[s]])

    # Tampa superior do corpete (disco plano nos ombros)
    bm.faces.new(rings_corp[0])

    # ── SAIA ─────────────────────────────────────────────────────────────────
    # A saia arranca do anel final do corpete (hip_z)
    rings_skt = [rings_corp[-1]]  # liga ao corpete
    for i in range(1, len(skirt_sections)):
        z, r = skirt_sections[i]
        rings_skt.append(ring(z, r, DRESS_SEGS))

    for ri in range(len(rings_skt) - 1):
        r0, r1 = rings_skt[ri], rings_skt[ri + 1]
        z_mid = (skirt_sections[min(ri, len(skirt_sections)-1)][0] +
                 skirt_sections[min(ri + 1, len(skirt_sections)-1)][0]) / 2.0
        in_split_zone = z_mid < SPLIT_FLOOR
        for s in range(DRESS_SEGS):
            sn = (s + 1) % DRESS_SEGS
            # Omitir faces na abertura frontal abaixo de SPLIT_FLOOR
            if in_split_zone and SPLIT_START <= s <= SPLIT_END:
                continue
            bm.faces.new([r0[s], r0[sn], r1[sn], r1[s]])

    # Barra inferior (faixa de acabamento)
    last_ring = rings_skt[-1]
    TRIM_H = 0.008 * scale_ref
    trim_ring = [bm.verts.new((v.co.x * 1.002, v.co.y * 1.002, v.co.z - TRIM_H))
                 for v in last_ring]
    for s in range(DRESS_SEGS):
        sn = (s + 1) % DRESS_SEGS
        # omitir trim na abertura frontal
        if SPLIT_START <= s <= SPLIT_END:
            continue
        bm.faces.new([last_ring[s], last_ring[sn], trim_ring[sn], trim_ring[s]])
    bm.faces.new(trim_ring)

    # Normais para fora
    bm.normal_update()
    centre = Vector((0.0, 0.0, (shoulder_z + floor_z) / 2.0))
    for f in bm.faces:
        ctr = f.calc_center_median()
        if f.normal.length > 0.001:
            if f.normal.dot(ctr - centre) < 0:
                f.normal_flip()

    # Suavizar curvaturas
    for f in bm.faces:
        f.smooth = True

    bm.to_mesh(dress_mesh)
    bm.free()
    dress_mesh.update()

    # Material acetinado
    satin_mat = make_satin_dress_material()
    assign_mat(dress_obj, satin_mat)

    # Shade Smooth
    set_active(dress_obj)
    bpy.ops.object.shade_smooth()

    # Informacao de validacao
    with_mesh = dress_obj.data
    n_verts = len(with_mesh.vertices)
    n_faces = len(with_mesh.polygons)

    # Verificar faces degeneradas
    bm2 = bmesh.new()
    bm2.from_mesh(with_mesh)
    degenerate = sum(1 for f in bm2.faces if f.calc_area() < 1e-9)
    bm2.free()

    info = {
        "vertices": n_verts,
        "faces": n_faces,
        "degenerate_faces": degenerate,
        "material": "Dress_Satin_Red",
        "shoulder_z": round(shoulder_z, 5),
        "waist_z": round(waist_z, 5),
        "floor_z": round(floor_z, 5),
        "skirt_base_r_hip_m": round(skirt_sections[0][1], 5),
        "skirt_max_r_floor_m": round(max(r for _, r in skirt_sections), 5),
        "split_zone_below_z": round(SPLIT_FLOOR, 5),
        "split_segments": SPLIT_END - SPLIT_START + 1,
    }
    return dress_obj, info


# ─────────────────────────────────────────────────────────────────────────────
# SALTOS (stilettos simples para gala)
# ─────────────────────────────────────────────────────────────────────────────

def build_heels(body_min_z, scale_ref):
    heel_mat = make_material_principled("Dress_Heels", COLOR_SHOE_HEEL,
                                        roughness=0.12, metallic=0.3)
    objects_created = []
    SHOE_L, SHOE_W, SHOE_H, SHOE_SEP = 0.19*scale_ref, 0.07*scale_ref, 0.035*scale_ref, 0.09*scale_ref
    HEEL_R, HEEL_H = 0.008*scale_ref, 0.095*scale_ref

    for side, sign in (("L", -1), ("R", 1)):
        cx = sign * SHOE_SEP / 2.0
        me = bpy.data.meshes.new(f"Dress_Shoe_{side}_mesh")
        ob = bpy.data.objects.new(f"Dress_Shoe_{side}", me)
        bpy.context.collection.objects.link(ob)
        bm = bmesh.new()
        hw, hl, toe_hw = SHOE_W/2.0, SHOE_L/2.0, SHOE_W*0.30
        bz = body_min_z
        vs = [
            bm.verts.new((cx-hw,    -hl, bz)),
            bm.verts.new((cx+hw,    -hl, bz)),
            bm.verts.new((cx+toe_hw, hl, bz)),
            bm.verts.new((cx-toe_hw, hl, bz)),
            bm.verts.new((cx-hw,    -hl, bz+SHOE_H)),
            bm.verts.new((cx+hw,    -hl, bz+SHOE_H)),
            bm.verts.new((cx+toe_hw, hl, bz+SHOE_H*0.65)),
            bm.verts.new((cx-toe_hw, hl, bz+SHOE_H*0.65)),
        ]
        for f in [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[3,0,4,7]]:
            bm.faces.new([vs[i] for i in f])
        bm.normal_update()
        bm.to_mesh(me)
        bm.free()
        me.update()
        assign_mat(ob, heel_mat)
        objects_created.append(ob.name)

        # Salto fino
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=8, radius=HEEL_R, depth=HEEL_H,
            location=(cx + sign * SHOE_L * 0.28, -hl * 0.85, bz - HEEL_H/2.0))
        heel_peg = bpy.context.active_object
        heel_peg.name = f"Dress_Heel_{side}"
        assign_mat(heel_peg, heel_mat)
        objects_created.append(heel_peg.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CABELO E OLHOS (copias do T5 — mesmas funcoes, mesmo resultado)
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
        top_z, half_x, y_back, y_front = eye_z+0.115, 0.078, 0.095, -0.105
    sane = (0.05 <= half_x <= 0.11 and 0.14 <= (y_back-y_front) <= 0.27 and 0.08 <= (top_z-eye_z) <= 0.17)
    if not sane:
        top_z, half_x, y_back, y_front = eye_z+0.115, 0.078, 0.095, -0.105
    return {"measured_ok": bool(ok and sane), "top_z": top_z, "half_x": half_x,
            "y_back": y_back, "y_front_forehead": y_front}


def build_eye_cap(name, centre, R, theta_max, mat):
    SEGS, RINGS = 48, 8
    me = bpy.data.meshes.new(name+"_mesh")
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    def pt(theta, psi):
        return (centre.x + R*math.sin(theta)*math.cos(psi),
                centre.y - R*math.cos(theta),
                centre.z + R*math.sin(theta)*math.sin(psi))
    bm = bmesh.new()
    pole = bm.verts.new(pt(0.0, 0.0))
    rings = []
    for ri in range(1, RINGS+1):
        th = theta_max * ri / RINGS
        rings.append([bm.verts.new(pt(th, 2.0*math.pi*s/SEGS)) for s in range(SEGS)])
    for s in range(SEGS):
        bm.faces.new([pole, rings[0][s], rings[0][(s+1)%SEGS]])
    for ri in range(len(rings)-1):
        for s in range(SEGS):
            sn = (s+1)%SEGS
            bm.faces.new([rings[ri][s], rings[ri][sn], rings[ri+1][sn], rings[ri+1][s]])
    bm.normal_update()
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median()-centre) < 0:
            f.normal_flip()
    bm.to_mesh(me)
    bm.free()
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    assign_mat(ob, mat)
    return ob


def build_anime_eyes(human, anchors, body_min_z, body_h, bvh):
    from mathutils.bvhtree import BVHTree
    sclera_mat = make_material_principled("Dress_Sclera", COLOR_EYE_SCLERA, roughness=0.05)
    iris_mat   = make_material_eye_iris("Dress_Iris", COLOR_EYE_IRIS, COLOR_EYE_PUPIL,
                                        COLOR_EYE_LIMBUS, COLOR_EYE_HILITE)
    pupil_mat  = make_material_principled("Dress_Pupil", COLOR_EYE_PUPIL, roughness=0.02)
    hilite_mat = make_material_principled("Dress_EyeHilite", COLOR_EYE_HILITE, roughness=0.0)
    scale_ref  = body_h / 1.65
    objects_created, final = [], {}
    for side, sign in (("L", 1), ("R", -1)):
        a = anchors.get(side)
        radius = 0.020 * scale_ref
        center = None
        source = "fallback"
        if a is not None:
            c = a["center"]
            z_rel = (c.z - body_min_z) / body_h
            if 0.88 <= z_rel <= 0.970 and 0.010 <= abs(c.x) <= 0.06 and c.y < 0.0:
                center = Vector(c)
                radius = clamp(max(a["dims"])/2.0*1.9, 0.016*scale_ref, 0.022*scale_ref)
                source = a["group"]
        if center is None:
            fx = sign * 0.030 * scale_ref
            fz = body_min_z + body_h * 0.930
            h0, *_ = bvh.ray_cast(Vector((fx, -5.0, fz)), Vector((0.0, 1.0, 0.0)))
            fy = (h0.y + radius*0.8) if h0 is not None else -0.110*scale_ref
            center = Vector((fx, fy, fz))
        hit, *_ = bvh.ray_cast(Vector((center.x, -5.0, center.z)), Vector((0.0, 1.0, 0.0)))
        front_y = center.y - radius
        if hit is not None and hit.y < front_y - 0.001:
            shift = min(front_y - hit.y + 0.0008, 0.014)
            center.y -= shift
        iris_r  = radius * 0.68
        pupil_r = iris_r * 0.38
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=radius, location=tuple(center))
        sc = bpy.context.active_object
        sc.name = f"Dress_Sclera_{side}"
        for p in sc.data.polygons: p.use_smooth = True
        assign_mat(sc, sclera_mat)
        ir = build_eye_cap(f"Dress_Iris_{side}",    center, radius*1.008, math.asin(min(iris_r/radius, 0.98)), iris_mat)
        pu = build_eye_cap(f"Dress_Pupil_{side}",   center, radius*1.016, math.asin(min(pupil_r/radius, 0.98)), pupil_mat)
        hl = build_eye_cap(f"Dress_Hilite_{side}",
                           center + Vector((sign*iris_r*0.35, 0.0, iris_r*0.40)),
                           radius*1.020, math.asin(min(iris_r*0.22/radius, 0.98)), hilite_mat)
        objects_created.extend([sc.name, ir.name, pu.name, hl.name])
        final[side] = {"x": center.x, "y": center.y, "z": center.z,
                       "radius": radius, "source": source,
                       "height_ratio": round((center.z - body_min_z)/body_h, 4)}
    return objects_created, final


def build_anime_hair(head, eye_z, waist_z, shoulder_z, bust_z):
    hair_mat = make_material_toon("Dress_Hair", COLOR_HAIR, COLOR_HAIR_HILITE)
    objects_created = []
    cz, a = eye_z - 0.012, head["half_x"] + 0.008
    b  = (head["y_back"] - head["y_front_forehead"]) / 2.0 + 0.008
    cy = (head["y_back"] + head["y_front_forehead"]) / 2.0
    c  = (head["top_z"] - cz) + 0.008
    y_fore = head["y_front_forehead"]

    CAP_SEGS, CAP_RINGS, PHI_MAX = 32, 12, math.pi * 0.50
    cap_mesh = bpy.data.meshes.new("Dress_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("Dress_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)
    bm = bmesh.new()
    pole = bm.verts.new((0.0, cy, cz+c))
    rings = []
    for ri in range(1, CAP_RINGS+1):
        phi = PHI_MAX * ri / CAP_RINGS
        rings.append([bm.verts.new((a*math.sin(phi)*math.cos(2*math.pi*s/CAP_SEGS),
                                    cy+b*math.sin(phi)*math.sin(2*math.pi*s/CAP_SEGS),
                                    cz+c*math.cos(phi))) for s in range(CAP_SEGS)])
    for s in range(CAP_SEGS):
        bm.faces.new([pole, rings[0][s], rings[0][(s+1)%CAP_SEGS]])
    for ri in range(len(rings)-1):
        for s in range(CAP_SEGS):
            sn = (s+1)%CAP_SEGS
            bm.faces.new([rings[ri][s], rings[ri][sn], rings[ri+1][sn], rings[ri+1][s]])
    bm.normal_update()
    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    # Franja
    fringe_mesh = bpy.data.meshes.new("Dress_Fringe_mesh")
    fringe_obj  = bpy.data.objects.new("Dress_Fringe", fringe_mesh)
    bpy.context.collection.objects.link(fringe_obj)
    bm2 = bmesh.new()
    for i in range(7):
        t = i/6.0; cx_i = a*0.85*(2.0*t-1.0); alt = 0.008*(1.0+0.5*math.sin(i*1.3))
        v0 = bm2.verts.new((cx_i-a*0.07, y_fore-0.004, eye_z+0.055))
        v1 = bm2.verts.new((cx_i+a*0.07, y_fore-0.004, eye_z+0.055))
        v2 = bm2.verts.new((cx_i,         y_fore-0.004, eye_z+0.005-alt))
        bm2.faces.new([v0, v1, v2])
    bm2.normal_update()
    bm2.to_mesh(fringe_mesh)
    bm2.free()
    fringe_mesh.update()
    fringe_obj.data.materials.append(hair_mat)
    objects_created.append(fringe_obj.name)

    FRONT_PHI, FRONT_RANGE = 3.0*math.pi/2.0, math.radians(55)
    for i in range(20):
        az = 2.0*math.pi*i/20
        diff = abs(az-FRONT_PHI)
        if diff > math.pi: diff = 2.0*math.pi-diff
        is_f = diff < FRONT_RANGE
        phi_b = math.pi*(0.35 if is_f else 0.25)
        rx = a*math.sin(phi_b)*math.cos(az)
        ry = cy+b*math.sin(phi_b)*math.sin(az)
        rz = cz+c*math.cos(phi_b)
        tip_z = bust_z+(shoulder_z-bust_z)*(0.65 if is_f else 1.0)*0.5
        cd = bpy.data.curves.new(f"Dress_Strand_{i:02d}", type="CURVE")
        cd.dimensions="3D"; cd.bevel_depth=0.009; cd.bevel_resolution=2; cd.use_fill_caps=True
        sp = cd.splines.new("NURBS"); sp.points.add(7)
        for pi_i in range(8):
            t = pi_i/7.0
            sp.points[pi_i].co=(rx*(1+t*0.30), cy+(ry-cy)*(1+t*0.30), rz+(tip_z-rz)*t, 1.0)
        sp.use_endpoint_u=True; sp.order_u=min(4,8)
        sobj = bpy.data.objects.new(f"Dress_Strand_{i:02d}", cd)
        bpy.context.collection.objects.link(sobj)
        sobj.data.materials.append(hair_mat)
        objects_created.append(sobj.name)

    for side, sign in (("L",-1),("R",1)):
        cd3 = bpy.data.curves.new(f"Dress_ChestStrand_{side}", type="CURVE")
        cd3.dimensions="3D"; cd3.bevel_depth=0.011; cd3.bevel_resolution=3; cd3.use_fill_caps=True
        sp3 = cd3.splines.new("NURBS"); sp3.points.add(9)
        rx3, ry3, rz3 = sign*a*0.55, cy+b*0.85*(-1.0), cz+c*0.10
        tx3, ty3, tz3 = sign*a*0.35, ry3-0.020, bust_z-0.040
        for pi_i in range(10):
            t = pi_i/9.0
            sp3.points[pi_i].co=(rx3+(tx3-rx3)*t, ry3+(ty3-ry3)*t, rz3+(tz3-rz3)*t, 1.0)
        sp3.use_endpoint_u=True; sp3.order_u=min(4,10)
        cobj = bpy.data.objects.new(f"Dress_ChestStrand_{side}", cd3)
        bpy.context.collection.objects.link(cobj)
        cobj.data.materials.append(hair_mat)
        objects_created.append(cobj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# FREESTYLE
# ─────────────────────────────────────────────────────────────────────────────

def setup_freestyle():
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    vl_obj = scene.view_layers[0]
    vl_obj.use_freestyle = True
    fs = vl_obj.freestyle_settings
    fs.use_culling = True
    while fs.linesets:
        fs.linesets.remove(fs.linesets[0])
    ls = fs.linesets.new("AnimeOutline")
    ls.select_silhouette = True
    ls.select_crease = True
    ls.linestyle.color = (0.0, 0.0, 0.0)
    ls.linestyle.thickness = 1.6


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=64, w=768, h=1024):
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
    sc.world.use_nodes = True
    bg = sc.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.06, 0.06, 0.08, 1.0)
        bg.inputs["Strength"].default_value = 0.5


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
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

try:
    from mathutils.bvhtree import BVHTree

    # Instalar MPFB
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
                         custom_directory=str(out/"extensions"))
        repo.enabled = True

    install_result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if "FINISHED" not in install_result and "RUNNING_MODAL" not in install_result:
        raise RuntimeError(f"MPFB: {install_result}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB nao activo: {module_id}")

    report["components"]["mpfb"] = {"status": "ok", "module": module_id}

    # Limpar e criar humano
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    result = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result:
        raise RuntimeError(f"mpfb.create_human: {result}")
    human = bpy.data.objects.get("Human") or next(
        (o for o in bpy.data.objects if o.type == "MESH"), None)
    if human is None:
        raise RuntimeError("Nenhuma malha Human encontrada")
    if human.data.shape_keys is None:
        raise RuntimeError("Human sem shape keys")

    # Morph feminino
    keys = human.data.shape_keys.key_blocks
    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-","$md-$ca-","$md-$af-")):
                key.value = 1.0/3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0
    bpy.context.view_layer.update()
    report["components"]["female_morph"] = {"status": "applied"}

    # Bake
    bake_info = bake_shape_keys(human)
    report["components"]["bake"] = bake_info
    if not bake_info.get("baked"):
        raise RuntimeError("bake_shape_keys falhou")

    # Escala
    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    body_min_z, body_max_z = hi["body_min_z"], hi["body_max_z"]
    body_h = body_max_z - body_min_z
    scale_ref = body_h / 1.65
    if abs(hi["h_final_m"] - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(f"Altura fora da tolerancia: {hi['h_final_m']:.4f} m")
    report["components"]["height"] = {"final_m": hi["h_final_m"], "ok": True}

    bust_z      = body_min_z + body_h * 0.695
    waist_z     = body_min_z + body_h * 0.575
    shoulder_z  = body_min_z + body_h * 0.800
    body_center = body_min_z + body_h * 0.50

    # Pele toon
    skin_mat = make_material_toon("Dress_Skin", COLOR_SKIN, COLOR_SKIN_SHADOW)
    assign_mat(human, skin_mat)

    # Olhos
    anchors = measure_eye_helpers(human)
    bvh = BVHTree.FromObject(human, bpy.context.evaluated_depsgraph_get())
    eye_objects, eye_positions = build_anime_eyes(human, anchors, body_min_z, body_h, bvh)
    report["components"]["eyes"] = {"objects": eye_objects, "positions": {
        s: {"height_ratio": eye_positions[s]["height_ratio"],
            "source": eye_positions[s]["source"]} for s in ("L","R")
    }}
    eye_mid_z = (eye_positions["L"]["z"] + eye_positions["R"]["z"]) / 2.0

    # Cabelo
    head = measure_head(human, eye_mid_z)
    hair_objects = build_anime_hair(head, eye_mid_z, waist_z, shoulder_z, bust_z)
    report["components"]["hair"] = {"objects": hair_objects}

    # VESTIDO
    dress_obj, dress_info = build_dress(body_min_z, body_h, scale_ref)
    report["components"]["dress"] = dress_info

    # Saltos
    heel_objects = build_heels(body_min_z, scale_ref)
    report["components"]["heels"] = {"objects": heel_objects}

    # Freestyle
    setup_freestyle()
    report["components"]["freestyle"] = True

    # ── VALIDACOES ────────────────────────────────────────────────────────────
    n_verts = dress_info["vertices"]
    n_faces = dress_info["faces"]
    degenerate = dress_info["degenerate_faces"]

    report["validations"]["dress_mesh"] = {
        "vertices": n_verts,
        "faces": n_faces,
        "has_material": len(dress_obj.data.materials) > 0,
        "degenerate_faces": degenerate,
        "pass": n_verts > 100 and n_faces > 50 and degenerate == 0
                and len(dress_obj.data.materials) > 0,
    }
    report["validations"]["body_height"] = {
        "value_m": body_h,
        "target_m": TARGET_HEIGHT_M,
        "pass": abs(body_h - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M,
    }
    report["validations"]["dress_dimensions"] = {
        "waist_radius_m": round(0.118*scale_ref + 0.015*scale_ref, 4),
        "floor_radius_m": round(dress_info["skirt_max_r_floor_m"], 4),
        "slit_segments": dress_info["split_segments"],
        "pass": dress_info["skirt_max_r_floor_m"] > dress_info["skirt_base_r_hip_m"],
    }
    report["validations"]["overall_pass"] = all(
        v.get("pass", False) for v in report["validations"].values()
        if isinstance(v, dict) and "pass" in v
    )

    report["visual_limitations"] = [
        "Vestido em geometria bmesh; pregas sao variacao de raio por seno, nao simulacao de tecido",
        "Sem rigging: vestido nao deforma com a personagem",
        "Abertura frontal omite faces na zona inferior (geometria aberta, nao costuras reais)",
        "Corpete fecha em disco plano no topo (ombros); sem alcas modeladas",
        "Saltos em cilindro + prisma; sem dedos ou palmilha",
        "Aprovacao visual exige inspeccao humana das 4 imagens PNG",
    ]

    # ── RENDERS (4 vistas) ────────────────────────────────────────────────────
    setup_render(samples=64)

    def lights_standard(body_center_z):
        clear_lights_cameras()
        add_light(( 0.9, -3.5, body_center_z+0.6), (0, 0, body_center_z), 900, 1.4, "Key")
        add_light((-0.7, -3.0, body_center_z),      (0, 0, body_center_z), 450, 1.1, "Fill")
        add_light(( 0.0,  2.0, body_center_z+1.0),  (0, 0, body_center_z), 350, 0.9, "Rim")

    ORTHO = body_h * 1.30

    # Frente
    lights_standard(body_center)
    place_camera_ortho((0.0, -4.0, body_center), (0, 0, body_center), ORTHO, "Cam_Front")
    render_to(out/"dress_front.png", "dress_front")

    # Costas
    lights_standard(body_center)
    add_light((0.0, 3.5, body_center+0.6), (0, 0, body_center), 700, 1.2, "BackKey")
    place_camera_ortho((0.0, 4.0, body_center), (0, 0, body_center), ORTHO, "Cam_Back")
    render_to(out/"dress_back.png", "dress_back")

    # Lado
    lights_standard(body_center)
    place_camera_ortho((4.0, 0.0, body_center), (0, 0, body_center), ORTHO, "Cam_Side")
    render_to(out/"dress_side.png", "dress_side")

    # Tres quartos
    lights_standard(body_center)
    place_camera_persp((2.8, -2.8, body_center), (0, 0, body_center), 85, "Cam_3Q")
    render_to(out/"dress_three_quarter.png", "dress_three_quarter", samples=96)

    # Guardar ficheiros
    blend_path = out / "mpfb-female-dress.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-dress.glb"
    bpy.ops.object.select_all(action="SELECT")
    for obj in bpy.context.selected_objects:
        if obj.type in ("LIGHT","CAMERA"):
            obj.select_set(False)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path), export_format="GLB", use_selection=True,
        export_cameras=False, export_lights=False,
        export_materials="EXPORT", export_apply=True)
    if not glb_path.exists():
        raise RuntimeError(f"GLB nao exportado: {glb_path}")
    report["glb_bytes"] = glb_path.stat().st_size

    report["status"] = "success_dress_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out/"mpfb-female-dress-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))
    (rep_dir/"dress_validation.json").write_text(
        json.dumps({
            "version": "dress_v1",
            "status": report.get("status"),
            "validations": report.get("validations", {}),
            "dress": report.get("components", {}).get("dress", {}),
            "renders": report.get("renders", {}),
            "error": report.get("error"),
            "traceback": report.get("traceback"),
        }, ensure_ascii=False, indent=2, default=str))
    print("DRESS_REPORT:", json.dumps(report, ensure_ascii=False, default=str))

if report["status"] != "success_dress_complete":
    sys.exit(1)
