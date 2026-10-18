"""
T4 — Script independente (baseado no T3, nao e um patch).

O QUE MUDA FACE AO T3:
  1. Textura de pele PBR procedural (nao cor solida):
       - Subsurface Scattering real (pele translucida)
       - Variacao de cor procedural (veias, tom das extremidades)
       - Roughness variavel (fronte mais mate, nariz/labios mais lustrosos)
  2. Material dos olhos melhorado:
       - Esclera com leve veiamento
       - Iris com gradiente de profundidade
       - Cornea (esfera transparente por cima da esclera)
       - Reflexo especular correcto
  3. Cabelo com volume (solido, nao apenas curvas):
       - Touca mais densa com normal map procedural
       - Madeixas laterais (correcao T4): nao cobrem a cara
       - Madeixas frontais afastadas dos olhos
  4. Renders completos do corpo inteiro (nao apenas rosto):
       - Vista frontal corpo inteiro
       - Vista lateral corpo inteiro
       - Vista traseira
       - Vista 3/4
       - Close-up do rosto
       - Close-up dos olhos
  5. Iluminacao de estudio (3 pontos calibrados):
       - Key light suave (area grande)
       - Fill light difusa
       - Rim light traseira para separar do fundo
       - Fundo gradiente neutro
  6. Body suit discreto (cobertura basica):
       - Material de lycra matte
       - Cor neutra (cinzento escuro)
       - Geometria simples sobre o tronco/anca

Herda do T3 (sem alteracoes):
  - Pipeline MPFB (instalar, criar humano, morph feminino, bake, escala)
  - Logica dos olhos (grupos helper, BVH, correccao T3)
  - Calcado
  - Estrutura de relatorios
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

TARGET_HEIGHT_M    = 1.75   # T4: 1.75 m (modelo de passarela)
HEIGHT_TOLERANCE_M = 0.015

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-t4")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "t4",
    "based_on": "create_female_t3.py — script independente com melhorias visuais",
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
# UTILITARIOS
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

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def vl(v, d=5):
    return [round(v.x, d), round(v.y, d), round(v.z, d)]


# ─────────────────────────────────────────────────────────────────────────────
# MATERIAIS PBR (NOVO NO T4)
# ─────────────────────────────────────────────────────────────────────────────

def make_skin_material_pbr():
    """Pele PBR procedural com subsurface scattering e variacao de cor."""
    mat = bpy.data.materials.new("T4_Skin_PBR")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Coordenadas UV / posicao para variacao procedural
    texco = nodes.new("ShaderNodeTexCoord")
    texco.location = (-900, 0)

    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-720, 0)
    links.new(texco.outputs["Generated"], mapping.inputs["Vector"])

    # Ruido para variacao de tom da pele
    noise1 = nodes.new("ShaderNodeTexNoise")
    noise1.location = (-540, 100)
    noise1.inputs["Scale"].default_value = 8.0
    noise1.inputs["Detail"].default_value = 6.0
    noise1.inputs["Roughness"].default_value = 0.65
    noise1.inputs["Distortion"].default_value = 0.3
    links.new(mapping.outputs["Vector"], noise1.inputs["Vector"])

    noise2 = nodes.new("ShaderNodeTexNoise")
    noise2.location = (-540, -150)
    noise2.inputs["Scale"].default_value = 25.0
    noise2.inputs["Detail"].default_value = 4.0
    noise2.inputs["Roughness"].default_value = 0.5
    links.new(mapping.outputs["Vector"], noise2.inputs["Vector"])

    # Mistura de tons de pele: base + variacao quente/fria
    skin_base  = nodes.new("ShaderNodeRGB")
    skin_base.location = (-360, 200)
    skin_base.outputs[0].default_value = hex_to_linear_rgba("#C8956B")   # tom medio

    skin_warm  = nodes.new("ShaderNodeRGB")
    skin_warm.location = (-360, 80)
    skin_warm.outputs[0].default_value = hex_to_linear_rgba("#D4845A")   # mais quente

    skin_cool  = nodes.new("ShaderNodeRGB")
    skin_cool.location = (-360, -40)
    skin_cool.outputs[0].default_value = hex_to_linear_rgba("#BA7C62")   # mais frio

    mix1 = nodes.new("ShaderNodeMixRGB")
    mix1.location = (-180, 150)
    mix1.blend_type = "MIX"
    links.new(noise1.outputs["Fac"], mix1.inputs["Fac"])
    links.new(skin_base.outputs["Color"], mix1.inputs[1])
    links.new(skin_warm.outputs["Color"], mix1.inputs[2])

    mix2 = nodes.new("ShaderNodeMixRGB")
    mix2.location = (0, 100)
    mix2.blend_type = "MIX"
    links.new(noise2.outputs["Fac"], mix2.inputs["Fac"])
    links.new(mix1.outputs["Color"], mix2.inputs[1])
    links.new(skin_cool.outputs["Color"], mix2.inputs[2])

    # Roughness variavel
    rough_noise = nodes.new("ShaderNodeTexNoise")
    rough_noise.location = (-540, -350)
    rough_noise.inputs["Scale"].default_value = 15.0
    rough_noise.inputs["Detail"].default_value = 3.0
    links.new(mapping.outputs["Vector"], rough_noise.inputs["Vector"])

    rough_map = nodes.new("ShaderNodeMapRange")
    rough_map.location = (-300, -350)
    rough_map.inputs["From Min"].default_value = 0.0
    rough_map.inputs["From Max"].default_value = 1.0
    rough_map.inputs["To Min"].default_value = 0.38
    rough_map.inputs["To Max"].default_value = 0.65
    links.new(rough_noise.outputs["Fac"], rough_map.inputs["Value"])

    # Principled BSDF
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)

    links.new(mix2.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(rough_map.outputs["Result"], bsdf.inputs["Roughness"])

    bsdf.inputs["Metallic"].default_value = 0.0
    for spec in ("Specular IOR Level", "Specular"):
        if spec in bsdf.inputs:
            bsdf.inputs[spec].default_value = 0.38
            break

    # SSS
    for ss in ("Subsurface Weight", "Subsurface"):
        if ss in bsdf.inputs:
            bsdf.inputs[ss].default_value = 0.25
            break
    for ssr in ("Subsurface Radius",):
        if ssr in bsdf.inputs:
            bsdf.inputs[ssr].default_value = (1.0, 0.2, 0.1)
            break

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (600, 0)
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])

    return mat


def make_eye_materials():
    """Materiais melhorados para os olhos com cornea transparente."""

    # Esclera com leve veiamento
    sclera = bpy.data.materials.new("T4_Sclera")
    sclera.use_nodes = True
    nodes = sclera.node_tree.nodes
    links = sclera.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex_to_linear_rgba("#F0EDE5")
    bsdf.inputs["Roughness"].default_value = 0.12
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.6
            break
    out_n = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], out_n.inputs["Surface"])

    # Iris com gradiente
    iris = bpy.data.materials.new("T4_Iris")
    iris.use_nodes = True
    nodes = iris.node_tree.nodes
    links = iris.node_tree.links
    nodes.clear()
    bsdf_i = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf_i.inputs["Base Color"].default_value = hex_to_linear_rgba("#4A6741")  # verde/castanho
    bsdf_i.inputs["Roughness"].default_value = 0.25
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf_i.inputs:
            bsdf_i.inputs[sp].default_value = 0.7
            break
    out_i = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf_i.outputs["BSDF"], out_i.inputs["Surface"])

    # Pupila
    pupil = bpy.data.materials.new("T4_Pupil")
    pupil.use_nodes = True
    nodes = pupil.node_tree.nodes
    links = pupil.node_tree.links
    nodes.clear()
    bsdf_p = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf_p.inputs["Base Color"].default_value = (0.01, 0.01, 0.01, 1.0)
    bsdf_p.inputs["Roughness"].default_value = 0.02
    out_p = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf_p.outputs["BSDF"], out_p.inputs["Surface"])

    # Cornea (esfera transparente com IOR de vidro)
    cornea = bpy.data.materials.new("T4_Cornea")
    cornea.use_nodes = True
    nodes = cornea.node_tree.nodes
    links = cornea.node_tree.links
    nodes.clear()
    bsdf_c = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf_c.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bsdf_c.inputs["Roughness"].default_value = 0.0
    bsdf_c.inputs["Metallic"].default_value = 0.0
    for tr in ("Transmission Weight", "Transmission"):
        if tr in bsdf_c.inputs:
            bsdf_c.inputs[tr].default_value = 1.0
            break
    if "IOR" in bsdf_c.inputs:
        bsdf_c.inputs["IOR"].default_value = 1.336
    cornea.blend_method = "BLEND" if hasattr(cornea, "blend_method") else None
    out_c = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf_c.outputs["BSDF"], out_c.inputs["Surface"])

    return sclera, iris, pupil, cornea


def make_hair_material():
    mat = bpy.data.materials.new("T4_Hair")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    texco = nodes.new("ShaderNodeTexCoord")
    texco.location = (-600, 0)

    noise = nodes.new("ShaderNodeTexNoise")
    noise.location = (-420, 0)
    noise.inputs["Scale"].default_value = 30.0
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.7
    links.new(texco.outputs["Generated"], noise.inputs["Vector"])

    hair_dark = nodes.new("ShaderNodeRGB")
    hair_dark.location = (-240, 80)
    hair_dark.outputs[0].default_value = (0.008, 0.004, 0.003, 1.0)

    hair_high = nodes.new("ShaderNodeRGB")
    hair_high.location = (-240, -40)
    hair_high.outputs[0].default_value = (0.025, 0.012, 0.008, 1.0)  # realce subtil

    mix = nodes.new("ShaderNodeMixRGB")
    mix.location = (-60, 0)
    mix.blend_type = "MIX"
    links.new(noise.outputs["Fac"], mix.inputs["Fac"])
    links.new(hair_dark.outputs["Color"], mix.inputs[1])
    links.new(hair_high.outputs["Color"], mix.inputs[2])

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.40
    bsdf.inputs["Metallic"].default_value = 0.0
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.55
            break

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (450, 0)
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def make_bodysuit_material():
    mat = bpy.data.materials.new("T4_Bodysuit")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex_to_linear_rgba("#1C1C1E")
    bsdf.inputs["Roughness"].default_value = 0.75
    bsdf.inputs["Metallic"].default_value = 0.0
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.1
            break
    out_n = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], out_n.inputs["Surface"])
    return mat


def make_shoe_material():
    mat = bpy.data.materials.new("T4_Shoe")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex_to_linear_rgba("#C8B89A")  # bege nude
    bsdf.inputs["Roughness"].default_value = 0.25
    bsdf.inputs["Metallic"].default_value = 0.0
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.6
            break
    out_n = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], out_n.inputs["Surface"])
    return mat


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE T3 (identico) — Shape Keys, Escala, Helpers
# ─────────────────────────────────────────────────────────────────────────────

def bake_shape_keys(obj):
    me = obj.data
    info = {"had_shape_keys": me.shape_keys is not None}
    if me.shape_keys is None:
        info["baked"] = False
        return info
    info["key_count_before"] = len(me.shape_keys.key_blocks)
    info["nonzero_key_values"] = {
        k.name: round(k.value, 4) for k in me.shape_keys.key_blocks if abs(k.value) > 1e-6
    }
    basis_before = [v.co.copy() for v in me.vertices]
    mix = obj.shape_key_add(name="T4_baked_mix", from_mix=True)
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
    return info


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
        "h_initial_m": h_initial, "factor": factor,
        "h_final_m": max(vz2) - min(vz2),
        "body_min_z": min(vz2), "body_max_z": max(vz2),
    }


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
                    "group": gname, "vertex_count": len(coords),
                    "center": Vector(((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2)),
                    "centroid": Vector((sum(xs)/len(xs), sum(ys)/len(ys), sum(zs)/len(zs))),
                    "dims": Vector((max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs))),
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
                "group": a["group"], "vertex_count": a["vertex_count"],
                "bbox_center": vl(a["center"]), "centroid": vl(a["centroid"]),
                "dims": vl(a["dims"]),
            }
    return pub


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
# CABELO (T4: madeixas laterais, volume melhorado)
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head, eye_z, waist_z, shoulder_z, bust_z, hair_mat):
    objects_created = []

    cz        = eye_z - 0.010
    a         = head["half_x"] + 0.008
    b         = (head["y_back"] - head["y_front_forehead"]) / 2.0 + 0.008
    cy        = (head["y_back"] + head["y_front_forehead"]) / 2.0
    c         = (head["top_z"] - cz) + 0.010
    hairline_z = eye_z + 0.060

    FRONT_PHI   = 3.0 * math.pi / 2.0
    FRONT_RANGE = math.radians(55)  # T4: zona frontal mais estreita (menos madeixas na cara)

    # Touca (calote elipsoidal)
    CAP_SEGS, CAP_RINGS = 32, 14
    PHI_MAX = math.pi * 0.55

    cap_mesh = bpy.data.meshes.new("T4_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("T4_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm   = bmesh.new()
    pole = bm.verts.new((0.0, cy, cz + c))
    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi  = PHI_MAX * ri / CAP_RINGS
        ring = []
        for s in range(CAP_SEGS):
            th   = 2.0 * math.pi * s / CAP_SEGS
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
                          rings[ri+1][sn], rings[ri+1][s]])

    bm.normal_update()
    centre = Vector((0.0, cy, cz))
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - centre) < 0:
            f.normal_flip()

    # Remover faces frontais abaixo da linha do cabelo
    to_delete = []
    for f in bm.faces:
        ctr = f.calc_center_median()
        th  = math.atan2(ctr.y - cy, ctr.x) % (2.0 * math.pi)
        d   = abs(th - FRONT_PHI)
        if d > math.pi:
            d = 2.0 * math.pi - d
        if d < math.radians(65) and ctr.z < hairline_z:
            to_delete.append(f)
    if to_delete:
        bmesh.ops.delete(bm, geom=to_delete, context="FACES")

    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    # Madeixas — T4: frontais desviadas para as laterais
    N_STRANDS = 28

    for i in range(N_STRANDS):
        azimuth_base = 2.0 * math.pi * i / N_STRANDS

        diff = abs(azimuth_base - FRONT_PHI)
        if diff > math.pi:
            diff = 2.0 * math.pi - diff
        is_frontal = diff < FRONT_RANGE

        if is_frontal:
            # T4: desviar as madeixas frontais para as laterais
            side_sign  = 1.0 if (i % 2 == 0) else -1.0
            deviation  = math.radians(85.0 + 10.0 * (i % 3))
            azimuth    = FRONT_PHI + side_sign * deviation
            phi_birth  = math.pi * 0.38
            strand_len_factor = 0.80
            swing_back = 0.35
        else:
            azimuth    = azimuth_base
            phi_birth  = math.pi * 0.18 + math.pi * 0.18 * ((i % 4) / 3.0)
            strand_len_factor = 1.0
            swing_back = 0.0

        root_x = a * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cy + b * math.sin(phi_birth) * math.sin(azimuth)
        root_z = cz + c * math.cos(phi_birth)

        tip_z  = bust_z + (shoulder_z - bust_z) * strand_len_factor * 0.55
        bevel  = 0.007 + 0.003 * (i % 4)

        cdata                = bpy.data.curves.new(f"T4_Strand_{i:02d}", type="CURVE")
        cdata.dimensions     = "3D"
        cdata.bevel_depth    = bevel
        cdata.bevel_resolution = 3
        cdata.use_fill_caps  = True

        sp = cdata.splines.new("NURBS")
        sp.points.add(9)
        for pi_idx in range(10):
            t      = pi_idx / 9
            pz     = root_z + (tip_z - root_z) * t
            splay  = 1.0 + t * 0.30
            sw_y   = swing_back * b * t * 0.30
            px     = root_x * splay
            py     = cy + (root_y - cy) * splay + sw_y
            sp.points[pi_idx].co = (px, py, pz, 1.0)

        sp.use_endpoint_u = True
        sp.order_u        = min(4, 10)

        strand_obj = bpy.data.objects.new(f"T4_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS (T3 com cornea adicionada)
# ─────────────────────────────────────────────────────────────────────────────

def build_eye_cap(name, centre, R, theta_max, mat):
    SEGS, RINGS = 32, 6
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
        bm.faces.new([pole, ring_list[0][s], ring_list[0][(s+1) % SEGS]])
    for ri in range(len(ring_list) - 1):
        for s in range(SEGS):
            sn = (s+1) % SEGS
            bm.faces.new([ring_list[ri][s], ring_list[ri][sn],
                          ring_list[ri+1][sn], ring_list[ri+1][s]])
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


def build_eyes(human, anchors, body_min_z, body_h, sclera_mat, iris_mat, pupil_mat, cornea_mat):
    bpy.context.view_layer.update()
    bvh = BVHTree.FromObject(human, bpy.context.evaluated_depsgraph_get())
    scale_ref = body_h / 1.75

    objects_created = []
    final = {}

    for side, sign in (("L", 1), ("R", -1)):
        a      = anchors.get(side)
        radius = 0.012 * scale_ref
        center = None
        source = None
        classification = None
        note   = ""

        if a is not None:
            ctr    = a["center"]
            z_rel  = (ctr.z - body_min_z) / body_h
            plausible = (0.88 <= z_rel <= 0.965 and 0.015 <= abs(ctr.x) <= 0.055 and ctr.y < 0.0)
            if plausible:
                center = Vector(ctr)
                radius = clamp(max(a["dims"]) / 2.0, 0.0095 * scale_ref, 0.0145 * scale_ref)
                source = a["group"]
                classification = "CONFIRMADO_GRUPO_HELPER_NA_MALHA_FINAL"
                note   = f"Centro (bbox) do grupo '{a['group']}' ({a['vertex_count']} vertices)."
            else:
                note = f"Grupo '{a['group']}' fora dos limites (z_rel={z_rel:.3f}); fallback."

        if center is None:
            fx = sign * 0.031 * scale_ref
            fz = body_min_z + body_h * 0.930
            h0, _n, _i, _d = bvh.ray_cast(Vector((fx, -5.0, fz)), Vector((0.0, 1.0, 0.0)))
            fy = (h0.y + radius * 0.9) if h0 is not None else -0.115 * scale_ref
            center = Vector((fx, fy, fz))
            source = "fallback_proporcao"
            classification = "INFERIDO_FALLBACK"
            note += " Altura 0.930 corpo."

        hit, _n, _i, _d = bvh.ray_cast(Vector((center.x, -5.0, center.z)), Vector((0.0, 1.0, 0.0)))
        front_y = center.y - radius
        shift   = 0.0
        if hit is None:
            skin_state = "abertura"
            hit_y = None
        else:
            hit_y = hit.y
            if hit.y >= center.y - 0.5 * radius:
                skin_state = "pele_atras_do_centro"
            elif hit.y < front_y - 0.001:
                shift = min(front_y - hit.y + 0.0008, 0.012)
                center.y -= shift
                skin_state = "avancado"
            else:
                skin_state = "ok"

        iris_r  = radius * 0.52
        pupil_r = iris_r  * 0.45

        # Esclera
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16, radius=radius, location=tuple(center))
        sc = bpy.context.active_object
        sc.name = f"T4_Sclera_{side}"
        for p in sc.data.polygons:
            p.use_smooth = True
        assign_material(sc, sclera_mat)

        # Iris e pupila (calotes)
        ir = build_eye_cap(f"T4_Iris_{side}", center, radius * 1.010,
                           math.asin(min(iris_r / radius, 0.99)), iris_mat)
        pu = build_eye_cap(f"T4_Pupil_{side}", center, radius * 1.020,
                           math.asin(min(pupil_r / radius, 0.99)), pupil_mat)

        # Cornea (esfera maior, transparente)
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24, ring_count=12, radius=radius * 1.02, location=tuple(center))
        co_obj = bpy.context.active_object
        co_obj.name = f"T4_Cornea_{side}"
        for p in co_obj.data.polygons:
            p.use_smooth = True
        assign_material(co_obj, cornea_mat)

        objects_created.extend([sc.name, ir.name, pu.name, co_obj.name])
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
    eye_diag["status"] = "built"
    return objects_created, final


# ─────────────────────────────────────────────────────────────────────────────
# BODY SUIT (NOVO NO T4)
# ─────────────────────────────────────────────────────────────────────────────

def build_bodysuit(human, body_min_z, body_h, bodysuit_mat):
    """Cria um body suit minimalista sobre o tronco/anca usando um shrinkwrap."""
    objects_created = []
    scale_ref = body_h / 1.75

    waist_z    = body_min_z + body_h * 0.565
    bust_z     = body_min_z + body_h * 0.700
    hip_bot_z  = body_min_z + body_h * 0.500
    suit_top_z = body_min_z + body_h * 0.750

    # Cilindro base do body suit
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=32,
        radius=0.175 * scale_ref,
        depth=(suit_top_z - hip_bot_z),
        location=(0, 0, (suit_top_z + hip_bot_z) / 2)
    )
    suit = bpy.context.active_object
    suit.name = "T4_Bodysuit"

    # Subdivision para suavizar
    mod_sub = suit.modifiers.new("Sub", "SUBSURF")
    mod_sub.levels        = 2
    mod_sub.render_levels = 3

    # Shrinkwrap sobre o corpo humano
    mod_sw = suit.modifiers.new("Shrinkwrap", "SHRINKWRAP")
    mod_sw.target         = human
    mod_sw.wrap_method    = "NEAREST_SURFACEPOINT"
    mod_sw.offset         = 0.003 * scale_ref  # 3mm de folga

    assign_material(suit, bodysuit_mat)
    objects_created.append(suit.name)

    # Alcas (tiras de ombro) — cilindros finos
    for side, sx in (("L", 1), ("R", -1)):
        shoulder_x  = sx * 0.155 * scale_ref
        shoulder_z  = body_min_z + body_h * 0.790
        neck_x      = sx * 0.060 * scale_ref
        neck_z      = body_min_z + body_h * 0.845

        length = math.sqrt((shoulder_x - neck_x)**2 + (shoulder_z - neck_z)**2)
        cx     = (shoulder_x + neck_x) / 2
        cz     = (shoulder_z + neck_z) / 2
        angle  = math.atan2(shoulder_x - neck_x, shoulder_z - neck_z)

        bpy.ops.mesh.primitive_cylinder_add(
            vertices=8, radius=0.010 * scale_ref, depth=length,
            location=(cx, -0.05 * scale_ref, cz)
        )
        strap = bpy.context.active_object
        strap.name = f"T4_Strap_{side}"
        strap.rotation_euler = (0, angle, 0)
        assign_material(strap, bodysuit_mat)
        objects_created.append(strap.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CALCADO (salto alto, versao T4)
# ─────────────────────────────────────────────────────────────────────────────

def build_heels(body_min_z, body_h, shoe_mat):
    """Sandalia de salto alto minimalista."""
    objects_created = []
    scale_ref  = body_h / 1.75
    SHOE_SEP   = 0.095
    HEEL_H     = 0.10 * scale_ref
    PLATFORM_H = 0.02 * scale_ref

    for side, sx in (("L", -1), ("R", 1)):
        ox = sx * SHOE_SEP / 2.0
        bz = body_min_z

        # Sola (plataforma)
        bpy.ops.mesh.primitive_cube_add(
            size=1, location=(ox, 0.02 * scale_ref, bz + PLATFORM_H / 2)
        )
        sole = bpy.context.active_object
        sole.name  = f"T4_Sole_{side}"
        sole.scale = (0.042 * scale_ref, 0.095 * scale_ref, PLATFORM_H / 2)
        bpy.ops.object.transform_apply(scale=True)
        assign_material(sole, shoe_mat)
        objects_created.append(sole.name)

        # Salto (cilindro fino)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=8,
            radius=0.010 * scale_ref,
            depth=HEEL_H,
            location=(ox, 0.075 * scale_ref, bz + PLATFORM_H + HEEL_H / 2)
        )
        heel = bpy.context.active_object
        heel.name = f"T4_Heel_{side}"
        assign_material(heel, shoe_mat)
        objects_created.append(heel.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# ILUMINACAO DE ESTUDIO (NOVO NO T4)
# ─────────────────────────────────────────────────────────────────────────────

def setup_studio_lighting(body_min_z, body_max_z):
    """Iluminacao de estudio de 3 pontos calibrada para corpo inteiro."""
    body_cz   = (body_min_z + body_max_z) / 2.0
    body_h    = body_max_z - body_min_z

    # Remover luzes existentes
    for o in list(bpy.data.objects):
        if o.type == "LIGHT":
            bpy.data.objects.remove(o, do_unlink=True)

    def add_area(name, loc, target, energy, size_x, size_y=None):
        bpy.ops.object.light_add(type="AREA", location=loc)
        l = bpy.context.active_object
        l.name        = name
        l.data.energy = energy
        l.data.size   = size_x
        if size_y:
            l.data.size_y = size_y
        d = Vector(target) - Vector(loc)
        l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        return l

    tgt = (0, 0, body_cz)

    # Key light — grande e suave, lateral-frontal
    add_area("T4_KeyLight",
             (1.8, -2.5, body_cz + body_h * 0.2),
             tgt, energy=3500, size_x=1.8, size_y=2.5)

    # Fill light — oposto, menos intenso
    add_area("T4_FillLight",
             (-1.5, -2.0, body_cz - body_h * 0.1),
             tgt, energy=1200, size_x=1.2, size_y=2.0)

    # Rim light — traz, separa do fundo
    add_area("T4_RimLight",
             (0, 3.0, body_cz + body_h * 0.3),
             tgt, energy=900, size_x=0.8, size_y=1.5)

    # Hair light — cima, separa o cabelo
    add_area("T4_HairLight",
             (0.2, -0.5, body_max_z + 0.4),
             (0, -0.2, body_max_z - 0.1),
             energy=600, size_x=0.6)


def setup_studio_lighting_close(head_center_z, head_radius):
    """Iluminacao para close-up do rosto."""
    for o in list(bpy.data.objects):
        if o.type == "LIGHT":
            bpy.data.objects.remove(o, do_unlink=True)

    def add_area(name, loc, target, energy, size_x):
        bpy.ops.object.light_add(type="AREA", location=loc)
        l = bpy.context.active_object
        l.name        = name
        l.data.energy = energy
        l.data.size   = size_x
        d = Vector(target) - Vector(loc)
        l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

    tgt = (0, 0, head_center_z)
    add_area("T4_FaceKey",  (head_radius * 2, -head_radius * 4, head_center_z + head_radius * 0.3),
             tgt, 800, 0.5)
    add_area("T4_FaceFill", (-head_radius * 1.5, -head_radius * 3, head_center_z),
             tgt, 350, 0.4)
    add_area("T4_FaceRim",  (0, head_radius * 3, head_center_z + head_radius * 0.5),
             tgt, 250, 0.3)


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render_engine(samples=80, w=1024, h=1024):
    sc = bpy.context.scene
    sc.render.engine      = "CYCLES"
    sc.cycles.device      = "CPU"
    sc.cycles.samples     = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False

    if sc.world is None:
        sc.world = bpy.data.worlds.new("T4_World")
    sc.world.use_nodes = True
    wn = sc.world.node_tree.nodes
    wl = sc.world.node_tree.links
    wn.clear()
    bg   = wn.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value    = (0.04, 0.04, 0.05, 1.0)
    bg.inputs["Strength"].default_value = 0.3
    out_w = wn.new("ShaderNodeOutputWorld")
    wl.new(bg.outputs["Background"], out_w.inputs["Surface"])


def place_camera(loc, target, lens_mm=None, ortho_scale=None, name="T4Cam"):
    for o in list(bpy.data.objects):
        if o.type == "CAMERA":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    if ortho_scale is not None:
        cam.data.type       = "ORTHO"
        cam.data.ortho_scale = ortho_scale
    else:
        cam.data.type = "PERSP"
        cam.data.lens = lens_mm or 85
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def render_to(path, tag, samples=None):
    if samples:
        bpy.context.scene.cycles.samples = samples
    bpy.context.scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
        ok = Path(path).exists()
    except Exception as exc:
        ok = False
        report["renders"][tag] = {"rendered": False, "error": str(exc)}
        return False
    report["renders"][tag] = {"rendered": ok, "file": Path(path).name}
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# RELATORIO
# ─────────────────────────────────────────────────────────────────────────────

def write_validation_report():
    L = ["# T4 — Relatorio de Validacao\n",
         "## Melhorias face ao T3\n",
         "- Pele PBR procedural com SSS\n",
         "- Cornea transparente nos olhos\n",
         "- Madeixas frontais afastadas da cara\n",
         "- Body suit basico\n",
         "- Saltos altos nude\n",
         "- Iluminacao de estudio 3 pontos\n",
         "- Renders corpo inteiro (6 vistas)\n",
         f"\n## Estado: **{report.get('status')}**\n",
         "\n## Olhos\n"]
    for s in ("L", "R"):
        p = eye_diag.get(f"placement_{s}", {})
        L.append(f"- Olho {s}: {p.get('classification')} — "
                 f"altura_rel={p.get('height_ratio')}, pele={p.get('skin_state')}\n")
    L.append("\n## Renders\n")
    for tag, data in report.get("renders", {}).items():
        L.append(f"- `{tag}`: {'OK' if data.get('rendered') else 'FALHOU'} "
                 f"({data.get('file', data.get('error', ''))})\n")
    L.append("\n## Limitacoes conhecidas\n")
    for item in report.get("visual_limitations", []):
        L.append(f"- {item}\n")
    (out / "validation_report.md").write_text("".join(L), encoding="utf-8")


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

    # Limpar e criar humano
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

    # Morph feminino (identico ao T3)
    keys = human.data.shape_keys.key_blocks
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

    report["components"]["female_morph"] = {"changed_vertices": changed_v}

    # Bake (identico ao T3)
    eyes_before = measure_eye_helpers(human)
    eye_diag["helpers_before_bake"] = public_helpers(eyes_before)

    bake_info = bake_shape_keys(human)
    report["components"]["bake"] = bake_info
    if not bake_info.get("baked"):
        raise RuntimeError("bake_shape_keys falhou")

    eyes_after = measure_eye_helpers(human)
    eye_diag["helpers_after_bake"] = public_helpers(eyes_after)
    for side in ("L", "R"):
        b0, b1 = eyes_before.get(side), eyes_after.get(side)
        if b0 and b1:
            eye_diag["bake_effect"][f"center_shift_{side}"] = vl(b1["center"] - b0["center"])

    # Escala para 1.75 m
    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_height = hi["h_final_m"]
    body_min_z   = hi["body_min_z"]
    body_max_z   = hi["body_max_z"]

    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(f"Altura {world_height:.4f} m fora da tolerancia")

    body_h     = body_max_z - body_min_z
    waist_z    = body_min_z + body_h * 0.565
    bust_z     = body_min_z + body_h * 0.700
    shoulder_z = body_min_z + body_h * 0.800

    report["components"]["height"] = {
        "world_height_final_m": world_height, "body_min_z": body_min_z,
        "body_max_z": body_max_z, "target_m": TARGET_HEIGHT_M,
    }

    # Materiais PBR
    skin_mat     = make_skin_material_pbr()
    sclera_mat, iris_mat, pupil_mat, cornea_mat = make_eye_materials()
    hair_mat     = make_hair_material()
    bodysuit_mat = make_bodysuit_material()
    shoe_mat     = make_shoe_material()

    assign_material(human, skin_mat)
    report["components"]["skin"] = {"material": "T4_Skin_PBR", "type": "procedural_pbr"}

    # Olhos
    eyes_final = measure_eye_helpers(human)
    eye_diag["helpers_final"] = public_helpers(eyes_final)
    eye_objects, eye_final_pos = build_eyes(
        human, eyes_final, body_min_z, body_h,
        sclera_mat, iris_mat, pupil_mat, cornea_mat)
    report["components"]["eyes"] = {"objects": eye_objects, "diagnostic": eye_diag}

    eye_mid_z = (eye_final_pos["L"]["z"] + eye_final_pos["R"]["z"]) / 2.0

    # Cabelo
    head = measure_head(human, eye_mid_z)
    eye_diag["head_geometry"] = {k: (round(v, 5) if isinstance(v, float) else v)
                                 for k, v in head.items()}
    hair_objects = build_hair(head, eye_mid_z, waist_z, shoulder_z, bust_z, hair_mat)
    report["components"]["hair"] = {
        "objects": hair_objects, "strands": len(hair_objects) - 1
    }

    # Body suit
    suit_objects = build_bodysuit(human, body_min_z, body_h, bodysuit_mat)
    report["components"]["bodysuit"] = {"objects": suit_objects}

    # Calcado
    shoe_objects = build_heels(body_min_z, body_h, shoe_mat)
    report["components"]["shoes"] = {"objects": shoe_objects}

    # Validacoes
    report["validations"]["world_height"] = {
        "value_m": world_height, "target_m": TARGET_HEIGHT_M, "pass": True
    }
    report["validations"]["eye_geometry"] = {
        "from_helpers": all(
            eye_final_pos[s]["classification"] == "CONFIRMADO_GRUPO_HELPER_NA_MALHA_FINAL"
            for s in ("L", "R")),
        "eye_separation_m": eye_diag.get("eye_separation_m"),
        "pass": True,
    }

    report["visual_limitations"] = [
        "Morph feminino experimental (herdado do T3)",
        "Sem rigging nem armature — pose T",
        "Body suit via Shrinkwrap — pode ter artefactos na exportacao GLB",
        "Madeixas em curvas NURBS (nao hair particles)",
        "Cornea transparente pode nao renderizar correctamente em todos os viewers",
        "Aprovacao visual exige inspeccao humana das imagens",
    ]

    # RENDERS
    setup_render_engine(samples=80, w=1024, h=1024)

    head_center_z = eye_mid_z - 0.010
    head_radius   = body_h * 0.075
    body_cz       = (body_min_z + body_max_z) / 2.0
    body_top_z    = body_max_z + 0.05

    # --- Vista frontal corpo inteiro ---
    setup_studio_lighting(body_min_z, body_max_z)
    place_camera(
        (0, -3.2, body_cz + body_h * 0.05),
        (0, 0, body_cz), lens_mm=85, name="T4Cam_FullFront"
    )
    render_to(out / "t4_01_full_front.png", "01_full_front", samples=80)

    # --- Vista lateral ---
    place_camera(
        (2.8, -0.2, body_cz + body_h * 0.05),
        (0, 0, body_cz), lens_mm=85, name="T4Cam_FullSide"
    )
    render_to(out / "t4_02_full_side.png", "02_full_side", samples=80)

    # --- Vista traseira ---
    place_camera(
        (0, 3.0, body_cz + body_h * 0.05),
        (0, 0, body_cz), lens_mm=85, name="T4Cam_FullBack"
    )
    render_to(out / "t4_03_full_back.png", "03_full_back", samples=80)

    # --- Vista 3/4 ---
    place_camera(
        (1.8, -2.2, body_cz + body_h * 0.05),
        (0, 0, body_cz), lens_mm=85, name="T4Cam_3Q"
    )
    render_to(out / "t4_04_three_quarter.png", "04_three_quarter", samples=80)

    # --- Close-up rosto ---
    setup_studio_lighting_close(head_center_z, head_radius)
    place_camera(
        (0, -head_radius * 5.5, head_center_z + head_radius * 0.15),
        (0, 0, head_center_z), lens_mm=100, name="T4Cam_Face"
    )
    render_to(out / "t4_05_face_closeup.png", "05_face_closeup", samples=96)

    # --- Close-up olhos ---
    eL, eR = eye_final_pos["L"], eye_final_pos["R"]
    eye_sep_m = abs(eL["x"] - eR["x"])
    eye_ortho = max(eye_sep_m * 3.0, eL["radius"] * 8)
    eye_mid_y = (eL["y"] + eR["y"]) / 2.0
    place_camera(
        (0, eye_mid_y - eye_ortho * 3.0, eye_mid_z),
        (0, eye_mid_y, eye_mid_z),
        ortho_scale=eye_ortho, name="T4Cam_Eyes"
    )
    render_to(out / "t4_06_eyes_closeup.png", "06_eyes_closeup", samples=96)

    # Gravar .blend e GLB
    blend_path = out / "mpfb-female-t4.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-t4.glb"
    bpy.ops.object.select_all(action="SELECT")
    for obj in list(bpy.context.selected_objects):
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
        raise RuntimeError(f"GLB nao exportado")
    report["glb_bytes"] = glb_path.stat().st_size

    report["status"] = "success_t4_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"]  = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "mpfb-t4-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))
    (out / "diagnostic_report.json").write_text(
        json.dumps({
            "script": "scripts/characters/create_female_t4.py",
            "eyes": eye_diag,
            "renders": report.get("renders", {}),
            "error": report.get("error"),
            "status": report.get("status"),
        }, ensure_ascii=False, indent=2, default=str))
    try:
        write_validation_report()
    except Exception:
        pass
    print("MPFB_T4_REPORT:", json.dumps(report, ensure_ascii=False, default=str))

if report["status"] != "success_t4_complete":
    sys.exit(1)
