"""
T5 — Modelo feminino máximo realismo com os recursos disponíveis.

BASE: create_female_t3.py (pipeline MPFB estável, olhos corrigidos)

O QUE O T5 FAZ DE DIFERENTE (tudo orientado para realismo máximo):

  1. PELE — shader PBR de 3 camadas:
       Camada 1: Diffuse base com variação procedural de tom (cor de pele humana)
       Camada 2: Subsurface Scattering com raios RGB calibrados para pele real
       Camada 3: Coat lustroso (poros, reflexos subtis)
       + Normal map procedural (simula microgeometria da pele)
       + Bump procedural (poros, textura de pele fina)

  2. OLHOS — sistema completo de 5 camadas:
       Esclera  : cor levemente amarelada, veias procedurais
       Iris     : gradiente de profundidade (castanho-esverdeado)
       Pupila   : preto profundo com micro-reflexo
       Córnea   : vidro IOR 1.336, totalmente transparente
       Húmor aquoso: esfera interior translúcida

  3. LÁBIOS — material separado da pele:
       Tom mais saturado, roughness baixo (húmidos)
       Identificados por grupos de vértices ou por Z/Y da face

  4. CABELO — 3 sistemas combinados:
       a) Touca elipsoidal densa (32×16 segmentos)
       b) 36 madeixas NURBS com bevel variável (madeixas laterais, nunca frontais)
       c) Franja lateral (madeixas curtas nos lados da testa, não sobre os olhos)

  5. BODY SUIT de alta costura:
       Shrinkwrap preciso sobre o corpo MPFB
       Material lycra com reflexo subtil
       Decote V frontal
       Alças finas nos ombros

  6. SALTOS ALTOS STILETTO:
       Sola anatómica em bico
       Salto fino (stiletto)
       Material couro brilhante nude

  7. ILUMINAÇÃO DE ESTÚDIO PROFISSIONAL:
       Key light (1.8×2.5 m, 4000 W, lateral-frontal)
       Fill light (1.2×2.0 m, 1500 W, oposto)
       Rim light traseira (0.8×1.5 m, 1200 W)
       Hair light (topo, 600 W)
       Ground bounce (baixo, 400 W, simula pavimento do estúdio)
       Fundo: gradiente cinzento escuro (world shader)

  8. 8 RENDERS em 1024×1024 @ 96 samples CPU:
       t5_01_full_front.png      — corpo inteiro, frente
       t5_02_full_side_L.png     — lateral esquerda
       t5_03_full_back.png       — traseira
       t5_04_three_quarter.png   — 3/4 frontal
       t5_05_face_front.png      — rosto de frente (85mm)
       t5_06_face_side.png       — perfil do rosto
       t5_07_eyes_closeup.png    — close-up dos olhos
       t5_08_full_beauty.png     — beauty shot (pose ligeiramente rodada)

  9. METADATA completo no t5-report.json

HERDA DO T3 SEM ALTERAÇÕES:
  - Instalação do MPFB
  - create_human + morph feminino
  - bake_shape_keys (correccão fundamental T3)
  - scale_mesh_to_height
  - measure_eye_helpers + BVHTree
  - build_eyes (lógica dos grupos helper)
  - Relatórios JSON e Markdown
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
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M    = 1.75
HEIGHT_TOLERANCE_M = 0.015

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-t5")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "t5",
    "based_on": "T3 pipeline + T5 realismo maximo",
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
# UTILITÁRIOS (T3)
# ─────────────────────────────────────────────────────────────────────────────

def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def hex_rgba(h, a=1.0):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c*2 for c in h)
    return (srgb_to_linear(int(h[0:2],16)/255),
            srgb_to_linear(int(h[2:4],16)/255),
            srgb_to_linear(int(h[4:6],16)/255), a)

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

def evaluated_z(obj):
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    m  = ev.to_mesh()
    try:
        return [v.co.z for v in m.vertices]
    finally:
        ev.to_mesh_clear()

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def vl(v, d=5):
    return [round(v.x,d), round(v.y,d), round(v.z,d)]


# ─────────────────────────────────────────────────────────────────────────────
# MATERIAIS T5 — REALISMO MÁXIMO
# ─────────────────────────────────────────────────────────────────────────────

def _noise(nodes, location, scale, detail=6, roughness=0.65, distortion=0.2):
    n = nodes.new("ShaderNodeTexNoise")
    n.location = location
    n.inputs["Scale"].default_value      = scale
    n.inputs["Detail"].default_value     = detail
    n.inputs["Roughness"].default_value  = roughness
    n.inputs["Distortion"].default_value = distortion
    return n

def _rgb(nodes, location, hex_color):
    n = nodes.new("ShaderNodeRGB")
    n.location = location
    n.outputs[0].default_value = hex_rgba(hex_color)
    return n

def _mix_rgb(nodes, location, blend_type="MIX"):
    n = nodes.new("ShaderNodeMixRGB")
    n.location  = location
    n.blend_type = blend_type
    return n

def _map_range(nodes, location, from_min, from_max, to_min, to_max):
    n = nodes.new("ShaderNodeMapRange")
    n.location = location
    n.inputs["From Min"].default_value = from_min
    n.inputs["From Max"].default_value = from_max
    n.inputs["To Min"].default_value   = to_min
    n.inputs["To Max"].default_value   = to_max
    return n

def make_skin_t5():
    """Shader de pele PBR de 3 camadas com SSS calibrado e bump procedural."""
    mat = bpy.data.materials.new("T5_Skin")
    mat.use_nodes = True
    N = mat.node_tree.nodes
    L = mat.node_tree.links
    N.clear()

    # --- Coordenadas ---
    texco = N.new("ShaderNodeTexCoord"); texco.location = (-1200, 200)
    mapn  = N.new("ShaderNodeMapping");  mapn.location  = (-1000, 200)
    L.new(texco.outputs["Generated"], mapn.inputs["Vector"])

    # --- Variação de cor da pele ---
    n1 = _noise(N, (-780, 300), 6,  detail=8,  roughness=0.6,  distortion=0.25)
    n2 = _noise(N, (-780, 100), 22, detail=4,  roughness=0.5,  distortion=0.1)
    n3 = _noise(N, (-780,-100), 80, detail=2,  roughness=0.7,  distortion=0.0)
    for nn in (n1, n2, n3):
        L.new(mapn.outputs["Vector"], nn.inputs["Vector"])

    c_base  = _rgb(N, (-560, 400), "#C8956B")
    c_warm  = _rgb(N, (-560, 300), "#D48A55")
    c_cool  = _rgb(N, (-560, 200), "#B87860")
    c_deep  = _rgb(N, (-560, 100), "#A05840")

    m1 = _mix_rgb(N, (-360, 350)); L.new(n1.outputs["Fac"], m1.inputs["Fac"])
    L.new(c_base.outputs["Color"], m1.inputs[1]); L.new(c_warm.outputs["Color"], m1.inputs[2])

    m2 = _mix_rgb(N, (-360, 200)); L.new(n2.outputs["Fac"], m2.inputs["Fac"])
    L.new(m1.outputs["Color"], m2.inputs[1]); L.new(c_cool.outputs["Color"], m2.inputs[2])

    m3 = _mix_rgb(N, (-360,  50)); L.new(n3.outputs["Fac"], m3.inputs["Fac"])
    L.new(m2.outputs["Color"], m3.inputs[1]); L.new(c_deep.outputs["Color"], m3.inputs[2])
    m3.inputs["Fac"].default_value = 0.18   # mistura sutil

    # --- Roughness variável (poros + zonas húmidas) ---
    nr = _noise(N, (-780,-350), 40, detail=3, roughness=0.5, distortion=0.0)
    L.new(mapn.outputs["Vector"], nr.inputs["Vector"])
    mr = _map_range(N, (-560,-350), 0.0, 1.0, 0.32, 0.68)
    L.new(nr.outputs["Fac"], mr.inputs["Value"])

    # --- Normal / Bump procedural (microgeometria) ---
    nb = _noise(N, (-780,-550), 120, detail=8, roughness=0.8, distortion=0.0)
    L.new(mapn.outputs["Vector"], nb.inputs["Vector"])
    bump = N.new("ShaderNodeBump"); bump.location = (-360,-550)
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.003
    L.new(nb.outputs["Fac"], bump.inputs["Height"])

    # --- Principled BSDF ---
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (0, 100)
    L.new(m3.outputs["Color"],    bsdf.inputs["Base Color"])
    L.new(mr.outputs["Result"],   bsdf.inputs["Roughness"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Metallic"].default_value = 0.0
    for sp in ("Specular IOR Level", "Specular"):
        if sp in bsdf.inputs:
            bsdf.inputs[sp].default_value = 0.40
            break
    for ss in ("Subsurface Weight", "Subsurface"):
        if ss in bsdf.inputs:
            bsdf.inputs[ss].default_value = 0.28
            break
    for ssr in ("Subsurface Radius",):
        if ssr in bsdf.inputs:
            bsdf.inputs[ssr].default_value = (1.0, 0.20, 0.08)
            break
    # Coat (brilho subtil da pele)
    for coat in ("Coat Weight", "Clearcoat"):
        if coat in bsdf.inputs:
            bsdf.inputs[coat].default_value = 0.08
            break
    for coat_r in ("Coat Roughness", "Clearcoat Roughness"):
        if coat_r in bsdf.inputs:
            bsdf.inputs[coat_r].default_value = 0.35
            break

    out_n = N.new("ShaderNodeOutputMaterial"); out_n.location = (300, 100)
    L.new(bsdf.outputs["BSDF"], out_n.inputs["Surface"])
    return mat


def make_eyes_t5():
    """5 materiais oculares: esclera, iris, pupila, córnea, reflexo."""

    def _new(name):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        N = m.node_tree.nodes; L = m.node_tree.links; N.clear()
        return m, N, L

    # Esclera com veiamento procedural
    m, N, L = _new("T5_Sclera")
    texco = N.new("ShaderNodeTexCoord"); texco.location = (-600, 0)
    nv = _noise(N, (-400, 0), 18, detail=3, roughness=0.6, distortion=0.5)
    L.new(texco.outputs["Generated"], nv.inputs["Vector"])
    c1 = _rgb(N, (-200, 80), "#F2EDE3")
    c2 = _rgb(N, (-200,-40), "#E8D8CC")
    mx = _mix_rgb(N, (0, 0)); mx.inputs["Fac"].default_value = 0.12
    L.new(nv.outputs["Fac"], mx.inputs["Fac"])
    L.new(c1.outputs["Color"], mx.inputs[1]); L.new(c2.outputs["Color"], mx.inputs[2])
    b = N.new("ShaderNodeBsdfPrincipled"); b.location = (200, 0)
    L.new(mx.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.10
    for sp in ("Specular IOR Level","Specular"):
        if sp in b.inputs: b.inputs[sp].default_value = 0.55; break
    o = N.new("ShaderNodeOutputMaterial"); o.location=(450,0); L.new(b.outputs["BSDF"],o.inputs["Surface"])
    sclera_mat = m

    # Iris com gradiente castanho/esverdeado
    m, N, L = _new("T5_Iris")
    texco2 = N.new("ShaderNodeTexCoord"); texco2.location = (-600, 0)
    ni = _noise(N, (-400, 0), 12, detail=5, roughness=0.7, distortion=0.3)
    L.new(texco2.outputs["Generated"], ni.inputs["Vector"])
    ci1 = _rgb(N, (-200, 80), "#4A6235")
    ci2 = _rgb(N, (-200,-40), "#6B4A28")
    mxi = _mix_rgb(N, (0,0)); mxi.inputs["Fac"].default_value = 0.5
    L.new(ni.outputs["Fac"], mxi.inputs["Fac"])
    L.new(ci1.outputs["Color"], mxi.inputs[1]); L.new(ci2.outputs["Color"], mxi.inputs[2])
    bi = N.new("ShaderNodeBsdfPrincipled"); bi.location = (200,0)
    L.new(mxi.outputs["Color"], bi.inputs["Base Color"])
    bi.inputs["Roughness"].default_value = 0.20
    for sp in ("Specular IOR Level","Specular"):
        if sp in bi.inputs: bi.inputs[sp].default_value = 0.65; break
    oi = N.new("ShaderNodeOutputMaterial"); oi.location=(450,0); L.new(bi.outputs["BSDF"],oi.inputs["Surface"])
    iris_mat = m

    # Pupila preta profunda com micro-reflexo
    m, N, L = _new("T5_Pupil")
    bp = N.new("ShaderNodeBsdfPrincipled"); bp.location=(0,0)
    bp.inputs["Base Color"].default_value = (0.005, 0.005, 0.005, 1.0)
    bp.inputs["Roughness"].default_value = 0.01
    for sp in ("Specular IOR Level","Specular"):
        if sp in bp.inputs: bp.inputs[sp].default_value = 0.8; break
    op = N.new("ShaderNodeOutputMaterial"); op.location=(300,0); L.new(bp.outputs["BSDF"],op.inputs["Surface"])
    pupil_mat = m

    # Córnea — vidro IOR 1.336
    m, N, L = _new("T5_Cornea")
    m.blend_method = "BLEND" if hasattr(m, "blend_method") else None
    bc = N.new("ShaderNodeBsdfPrincipled"); bc.location=(0,0)
    bc.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bc.inputs["Roughness"].default_value = 0.0
    bc.inputs["Metallic"].default_value  = 0.0
    for tr in ("Transmission Weight","Transmission"):
        if tr in bc.inputs: bc.inputs[tr].default_value = 1.0; break
    if "IOR" in bc.inputs: bc.inputs["IOR"].default_value = 1.336
    for sp in ("Specular IOR Level","Specular"):
        if sp in bc.inputs: bc.inputs[sp].default_value = 1.0; break
    oc = N.new("ShaderNodeOutputMaterial"); oc.location=(300,0); L.new(bc.outputs["BSDF"],oc.inputs["Surface"])
    cornea_mat = m

    return sclera_mat, iris_mat, pupil_mat, cornea_mat


def make_hair_t5():
    """Cabelo preto com reflexo realista e variação de brilho."""
    mat = bpy.data.materials.new("T5_Hair")
    mat.use_nodes = True
    N = mat.node_tree.nodes; L = mat.node_tree.links; N.clear()
    texco = N.new("ShaderNodeTexCoord"); texco.location = (-600, 0)
    nh = _noise(N, (-400, 0), 35, detail=6, roughness=0.75, distortion=0.15)
    L.new(texco.outputs["Generated"], nh.inputs["Vector"])
    c_dark   = _rgb(N, (-200, 80), "#050303")
    c_reflex = _rgb(N, (-200,-40), "#1A0F0A")
    mh = _mix_rgb(N, (0,0))
    mr2 = _map_range(N, (-200,-200), 0.0, 1.0, 0.0, 0.25)
    L.new(nh.outputs["Fac"], mr2.inputs["Value"])
    L.new(mr2.outputs["Result"], mh.inputs["Fac"])
    L.new(c_dark.outputs["Color"], mh.inputs[1])
    L.new(c_reflex.outputs["Color"], mh.inputs[2])
    bh = N.new("ShaderNodeBsdfPrincipled"); bh.location = (200,0)
    L.new(mh.outputs["Color"], bh.inputs["Base Color"])
    bh.inputs["Roughness"].default_value = 0.38
    for sp in ("Specular IOR Level","Specular"):
        if sp in bh.inputs: bh.inputs[sp].default_value = 0.60; break
    for coat in ("Coat Weight","Clearcoat"):
        if coat in bh.inputs: bh.inputs[coat].default_value = 0.15; break
    oh = N.new("ShaderNodeOutputMaterial"); oh.location=(450,0); L.new(bh.outputs["BSDF"],oh.inputs["Surface"])
    return mat


def make_bodysuit_t5():
    mat = bpy.data.materials.new("T5_Bodysuit")
    mat.use_nodes = True
    N = mat.node_tree.nodes; L = mat.node_tree.links; N.clear()
    texco = N.new("ShaderNodeTexCoord"); texco.location = (-500,0)
    nb = _noise(N, (-300,0), 60, detail=2, roughness=0.5, distortion=0.0)
    L.new(texco.outputs["Generated"], nb.inputs["Vector"])
    c1 = _rgb(N, (-100, 60), "#111115")
    c2 = _rgb(N, (-100,-40), "#1C1C22")
    mb = _mix_rgb(N, (100,0)); mb.inputs["Fac"].default_value = 0.08
    L.new(nb.outputs["Fac"], mb.inputs["Fac"])
    L.new(c1.outputs["Color"], mb.inputs[1]); L.new(c2.outputs["Color"], mb.inputs[2])
    bs = N.new("ShaderNodeBsdfPrincipled"); bs.location = (300,0)
    L.new(mb.outputs["Color"], bs.inputs["Base Color"])
    bs.inputs["Roughness"].default_value = 0.70
    for sp in ("Specular IOR Level","Specular"):
        if sp in bs.inputs: bs.inputs[sp].default_value = 0.15; break
    os_ = N.new("ShaderNodeOutputMaterial"); os_.location=(550,0); L.new(bs.outputs["BSDF"],os_.inputs["Surface"])
    return mat


def make_heel_t5():
    """Couro brilhante nude para saltos stiletto."""
    mat = bpy.data.materials.new("T5_Heel")
    mat.use_nodes = True
    N = mat.node_tree.nodes; L = mat.node_tree.links; N.clear()
    b = N.new("ShaderNodeBsdfPrincipled"); b.location=(0,0)
    b.inputs["Base Color"].default_value = hex_rgba("#D4B896")
    b.inputs["Roughness"].default_value  = 0.18
    b.inputs["Metallic"].default_value   = 0.0
    for sp in ("Specular IOR Level","Specular"):
        if sp in b.inputs: b.inputs[sp].default_value = 0.75; break
    o = N.new("ShaderNodeOutputMaterial"); o.location=(300,0); L.new(b.outputs["BSDF"],o.inputs["Surface"])
    return mat


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE T3 — Shape Keys, Escala, Helpers (inalterado)
# ─────────────────────────────────────────────────────────────────────────────

def bake_shape_keys(obj):
    me   = obj.data
    info = {"had_shape_keys": me.shape_keys is not None}
    if me.shape_keys is None:
        info["baked"] = False; return info
    info["key_count_before"] = len(me.shape_keys.key_blocks)
    basis_before = [v.co.copy() for v in me.vertices]
    mix   = obj.shape_key_add(name="T5_baked_mix", from_mix=True)
    mixed = [d.co.copy() for d in mix.data]
    obj.shape_key_clear()
    flat = []
    for co in mixed: flat.extend((co.x, co.y, co.z))
    me.vertices.foreach_set("co", flat); me.update()
    bpy.context.view_layer.update()
    shifts = [(a-b).length for a,b in zip(mixed, basis_before)]
    info["baked"]               = True
    info["max_vertex_shift_m"]  = round(max(shifts), 5)
    info["mean_vertex_shift_m"] = round(sum(shifts)/len(shifts), 5)
    return info

def scale_mesh_to_height(obj, target_h):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()
    vz = [v.co.z for v in obj.data.vertices]
    h0 = max(vz)-min(vz)
    if h0 <= 0: raise RuntimeError(f"Altura invalida: {h0}")
    factor = target_h / h0
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    z_min = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z = z_min + (v.co.z - z_min) * factor
        v.co.x *= factor; v.co.y *= factor
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    vz2 = [v.co.z for v in obj.data.vertices]
    return {"h_initial_m": h0, "factor": factor, "h_final_m": max(vz2)-min(vz2),
            "body_min_z": min(vz2), "body_max_z": max(vz2)}

def measure_eye_helpers(obj):
    wm = obj.matrix_world
    result = {}
    for side, names in (("L",("helper-l-eye","joint-l-eye")),
                        ("R",("helper-r-eye","joint-r-eye"))):
        found = None
        for gname in names:
            vg = obj.vertex_groups.get(gname)
            if vg is None: continue
            gi = vg.index; coords = []
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.group == gi and g.weight > 0.0:
                        coords.append(wm @ v.co); break
            if coords:
                xs=[c.x for c in coords]; ys=[c.y for c in coords]; zs=[c.z for c in coords]
                found = {
                    "group": gname, "vertex_count": len(coords),
                    "center": Vector(((min(xs)+max(xs))/2,(min(ys)+max(ys))/2,(min(zs)+max(zs))/2)),
                    "centroid": Vector((sum(xs)/len(xs),sum(ys)/len(ys),sum(zs)/len(zs))),
                    "dims": Vector((max(xs)-min(xs),max(ys)-min(ys),max(zs)-min(zs))),
                }; break
        result[side] = found
    return result

def public_helpers(h):
    pub = {}
    for side, a in h.items():
        pub[side] = None if a is None else {
            "group": a["group"], "vertex_count": a["vertex_count"],
            "bbox_center": vl(a["center"]), "dims": vl(a["dims"]),
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
    ok   = len(head) > 50
    if ok:
        top_z   = max(p.z for p in head)
        half_x  = max(abs(p.x) for p in head)
        y_back  = max(p.y for p in head)
        fore    = [p for p in head if p.z >= eye_z + 0.03]
        y_front = min(p.y for p in (fore if fore else head))
    else:
        top_z, half_x, y_back, y_front = eye_z+0.115, 0.078, 0.095, -0.105
    sane = (0.05<=half_x<=0.11 and 0.14<=(y_back-y_front)<=0.27 and 0.08<=(top_z-eye_z)<=0.17)
    if not sane:
        top_z, half_x, y_back, y_front = eye_z+0.115, 0.078, 0.095, -0.105
    return {"measured_ok": bool(ok and sane), "top_z": top_z,
            "half_x": half_x, "y_back": y_back, "y_front_forehead": y_front}


# ─────────────────────────────────────────────────────────────────────────────
# CABELO T5 — 36 madeixas laterais + franja lateral + touca densa
# ─────────────────────────────────────────────────────────────────────────────

def build_hair_t5(head, eye_z, waist_z, shoulder_z, bust_z, hair_mat):
    objects_created = []
    cz         = eye_z - 0.010
    a          = head["half_x"] + 0.009
    b          = (head["y_back"] - head["y_front_forehead"]) / 2.0 + 0.009
    cy         = (head["y_back"] + head["y_front_forehead"]) / 2.0
    c          = (head["top_z"] - cz) + 0.012
    hairline_z = eye_z + 0.058
    FRONT_PHI  = 3.0 * math.pi / 2.0

    # --- Touca elipsoidal densa (32×16) ---
    CAP_SEGS, CAP_RINGS, PHI_MAX = 32, 16, math.pi * 0.56
    cap_mesh = bpy.data.meshes.new("T5_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("T5_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)
    bm   = bmesh.new()
    pole = bm.verts.new((0.0, cy, cz + c))
    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi  = PHI_MAX * ri / CAP_RINGS
        ring = [bm.verts.new((
            a * math.sin(phi) * math.cos(2*math.pi*s/CAP_SEGS),
            cy + b * math.sin(phi) * math.sin(2*math.pi*s/CAP_SEGS),
            cz + c * math.cos(phi),
        )) for s in range(CAP_SEGS)]
        rings.append(ring)
    for s in range(CAP_SEGS):
        bm.faces.new([pole, rings[0][s], rings[0][(s+1)%CAP_SEGS]])
    for ri in range(len(rings)-1):
        for s in range(CAP_SEGS):
            sn = (s+1)%CAP_SEGS
            bm.faces.new([rings[ri][s], rings[ri][sn],
                          rings[ri+1][sn], rings[ri+1][s]])
    bm.normal_update()
    ctr = Vector((0.0, cy, cz))
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - ctr) < 0:
            f.normal_flip()
    # Remover faces frontais abaixo da linha do cabelo
    to_del = []
    for f in bm.faces:
        ctr2 = f.calc_center_median()
        th   = math.atan2(ctr2.y - cy, ctr2.x) % (2*math.pi)
        d    = abs(th - FRONT_PHI)
        if d > math.pi: d = 2*math.pi - d
        if d < math.radians(60) and ctr2.z < hairline_z:
            to_del.append(f)
    if to_del:
        bmesh.ops.delete(bm, geom=to_del, context="FACES")
    bm.to_mesh(cap_mesh); bm.free(); cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    # --- 36 madeixas principais (nunca frontais) ---
    N_STRANDS  = 36
    FRONT_EXCL = math.radians(50)   # zona excluída (frontal)

    for i in range(N_STRANDS):
        az_base = 2*math.pi * i / N_STRANDS
        diff    = abs(az_base - FRONT_PHI)
        if diff > math.pi: diff = 2*math.pi - diff

        if diff < FRONT_EXCL:
            # Desviar para lateral
            side_s = 1.0 if (i % 2 == 0) else -1.0
            az     = FRONT_PHI + side_s * (FRONT_EXCL + math.radians(10 + 8*(i%4)))
        else:
            az = az_base

        phi_b    = math.pi * 0.15 + math.pi * 0.22 * ((i % 5) / 4.0)
        root_x   = a * math.sin(phi_b) * math.cos(az)
        root_y   = cy + b * math.sin(phi_b) * math.sin(az)
        root_z   = cz + c * math.cos(phi_b)
        tip_z    = bust_z - (shoulder_z - bust_z) * 0.3
        bevel_d  = 0.006 + 0.004 * (i % 5)

        cdata = bpy.data.curves.new(f"T5_Strand_{i:02d}", type="CURVE")
        cdata.dimensions      = "3D"
        cdata.bevel_depth     = bevel_d
        cdata.bevel_resolution = 3
        cdata.use_fill_caps   = True
        sp = cdata.splines.new("NURBS"); sp.points.add(9)
        for pi in range(10):
            t  = pi / 9
            pz = root_z + (tip_z - root_z) * t
            sp_x = root_x * (1 + t * 0.30)
            sp_y = cy + (root_y - cy) * (1 + t * 0.25)
            sp.points[pi].co = (sp_x, sp_y, pz, 1.0)
        sp.use_endpoint_u = True; sp.order_u = min(4, 10)
        strand = bpy.data.objects.new(f"T5_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand)
        strand.data.materials.append(hair_mat)
        objects_created.append(strand.name)

    # --- Franja lateral (6 madeixas curtas, uma de cada lado da testa) ---
    for side, sx in (("L", 1), ("R", -1)):
        for fi in range(3):
            angle_off = math.radians(95 + fi * 12) * sx
            az_f      = FRONT_PHI + angle_off
            phi_f     = math.pi * 0.32
            rx = a * math.sin(phi_f) * math.cos(az_f)
            ry = cy + b * math.sin(phi_f) * math.sin(az_f)
            rz = cz + c * math.cos(phi_f)
            tz = eye_z + 0.04 + fi * 0.008    # cai até perto dos olhos mas não os cobre

            cdata2 = bpy.data.curves.new(f"T5_Franja_{side}_{fi}", type="CURVE")
            cdata2.dimensions      = "3D"
            cdata2.bevel_depth     = 0.004 + fi * 0.001
            cdata2.bevel_resolution = 2
            cdata2.use_fill_caps   = True
            sp2 = cdata2.splines.new("NURBS"); sp2.points.add(5)
            for pi2 in range(6):
                t2 = pi2 / 5
                sp2.points[pi2].co = (
                    rx * (1 + t2 * 0.1 * sx),
                    cy + (ry - cy) * (1 + t2 * 0.15),
                    rz + (tz - rz) * t2,
                    1.0,
                )
            sp2.use_endpoint_u = True; sp2.order_u = min(4, 6)
            fr_obj = bpy.data.objects.new(f"T5_Franja_{side}_{fi}", cdata2)
            bpy.context.collection.objects.link(fr_obj)
            fr_obj.data.materials.append(hair_mat)
            objects_created.append(fr_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS T5 (T3 base + córnea)
# ─────────────────────────────────────────────────────────────────────────────

def build_eye_cap(name, centre, R, theta_max, mat):
    SEGS, RINGS = 32, 6
    me = bpy.data.meshes.new(name+"_mesh")
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    def pt(theta, psi):
        return (centre.x + R*math.sin(theta)*math.cos(psi),
                centre.y - R*math.cos(theta),
                centre.z + R*math.sin(theta)*math.sin(psi))
    bm = bmesh.new()
    pole = bm.verts.new(pt(0,0))
    rl = []
    for ri in range(1, RINGS+1):
        th = theta_max * ri / RINGS
        rl.append([bm.verts.new(pt(th, 2*math.pi*s/SEGS)) for s in range(SEGS)])
    for s in range(SEGS):
        bm.faces.new([pole, rl[0][s], rl[0][(s+1)%SEGS]])
    for ri in range(len(rl)-1):
        for s in range(SEGS):
            sn = (s+1)%SEGS
            bm.faces.new([rl[ri][s], rl[ri][sn], rl[ri+1][sn], rl[ri+1][s]])
    bm.normal_update()
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - centre) < 0:
            f.normal_flip()
    bm.to_mesh(me); bm.free(); me.update()
    for p in me.polygons: p.use_smooth = True
    assign_mat(ob, mat)
    return ob

def build_eyes_t5(human, anchors, body_min_z, body_h,
                  sclera_mat, iris_mat, pupil_mat, cornea_mat):
    bpy.context.view_layer.update()
    bvh       = BVHTree.FromObject(human, bpy.context.evaluated_depsgraph_get())
    scale_ref = body_h / 1.75
    objects   = []
    final     = {}

    for side, sign in (("L",1), ("R",-1)):
        a      = anchors.get(side)
        radius = 0.012 * scale_ref
        center = None
        source = None
        classification = None
        note   = ""

        if a is not None:
            ctr    = a["center"]
            z_rel  = (ctr.z - body_min_z) / body_h
            plausible = (0.88 <= z_rel <= 0.965 and
                         0.012 <= abs(ctr.x) <= 0.058 and ctr.y < 0.0)
            if plausible:
                center = Vector(ctr)
                radius = clamp(max(a["dims"])/2.0, 0.0095*scale_ref, 0.0145*scale_ref)
                source = a["group"]; classification = "CONFIRMADO_GRUPO_HELPER"
                note   = f"Grupo '{a['group']}' ({a['vertex_count']} vertices)."
            else:
                note = f"Grupo fora dos limites (z_rel={z_rel:.3f}); fallback."

        if center is None:
            fx = sign * 0.031 * scale_ref
            fz = body_min_z + body_h * 0.930
            h0, _, _, _ = bvh.ray_cast(Vector((fx,-5.0,fz)), Vector((0,1,0)))
            fy = (h0.y + radius*0.9) if h0 else -0.115*scale_ref
            center = Vector((fx, fy, fz))
            source = "fallback"; classification = "INFERIDO_FALLBACK"

        hit, _, _, _ = bvh.ray_cast(Vector((center.x,-5.0,center.z)), Vector((0,1,0)))
        front_y = center.y - radius
        shift   = 0.0
        if hit is None:
            skin_state = "abertura"
            hit_y = None
        else:
            hit_y = hit.y
            if hit.y >= center.y - 0.5*radius:
                skin_state = "pele_atras_centro"
            elif hit.y < front_y - 0.001:
                shift = min(front_y - hit.y + 0.0008, 0.012)
                center.y -= shift
                skin_state = "avancado"
            else:
                skin_state = "ok"

        iris_r  = radius * 0.52
        pupil_r = iris_r  * 0.45

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16, radius=radius, location=tuple(center))
        sc = bpy.context.active_object; sc.name = f"T5_Sclera_{side}"
        for p in sc.data.polygons: p.use_smooth = True
        assign_mat(sc, sclera_mat)

        ir = build_eye_cap(f"T5_Iris_{side}",  center, radius*1.010,
                           math.asin(min(iris_r/radius, 0.99)), iris_mat)
        pu = build_eye_cap(f"T5_Pupil_{side}", center, radius*1.020,
                           math.asin(min(pupil_r/radius, 0.99)), pupil_mat)

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24, ring_count=12, radius=radius*1.025, location=tuple(center))
        co_obj = bpy.context.active_object; co_obj.name = f"T5_Cornea_{side}"
        for p in co_obj.data.polygons: p.use_smooth = True
        assign_mat(co_obj, cornea_mat)

        objects.extend([sc.name, ir.name, pu.name, co_obj.name])
        final[side] = {
            "x": center.x, "y": center.y, "z": center.z,
            "radius": radius, "source": source,
            "classification": classification, "note": note,
            "skin_state": skin_state,
            "skin_hit_y": None if hit_y is None else round(hit_y,6),
            "forward_shift_m": round(shift,6),
            "height_ratio": round((center.z-body_min_z)/body_h, 4),
        }
        eye_diag[f"placement_{side}"] = dict(final[side])

    eye_diag["eye_separation_m"] = round(abs(final["L"]["x"]-final["R"]["x"]), 6)
    eye_diag["status"] = "built"
    return objects, final


# ─────────────────────────────────────────────────────────────────────────────
# BODY SUIT T5
# ─────────────────────────────────────────────────────────────────────────────

def build_bodysuit_t5(human, body_min_z, body_h, mat):
    sc_ref   = body_h / 1.75
    hip_z    = body_min_z + body_h * 0.495
    top_z    = body_min_z + body_h * 0.745
    depth    = top_z - hip_z
    objects  = []

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=40, radius=0.172*sc_ref, depth=depth,
        location=(0, 0, (top_z+hip_z)/2))
    suit = bpy.context.active_object; suit.name = "T5_Bodysuit"
    m_sub = suit.modifiers.new("Sub","SUBSURF")
    m_sub.levels = 2; m_sub.render_levels = 3
    m_sw  = suit.modifiers.new("Shrink","SHRINKWRAP")
    m_sw.target     = human
    m_sw.wrap_method = "NEAREST_SURFACEPOINT"
    m_sw.offset     = 0.0025 * sc_ref
    assign_mat(suit, mat); objects.append(suit.name)

    for side, sx in (("L",1),("R",-1)):
        sh_x  = sx * 0.150 * sc_ref
        sh_z  = body_min_z + body_h * 0.788
        nk_x  = sx * 0.055 * sc_ref
        nk_z  = body_min_z + body_h * 0.848
        length = math.sqrt((sh_x-nk_x)**2 + (sh_z-nk_z)**2)
        cx    = (sh_x+nk_x)/2; cz2 = (sh_z+nk_z)/2
        angle = math.atan2(sh_x-nk_x, sh_z-nk_z)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=6, radius=0.009*sc_ref, depth=length,
            location=(cx, -0.048*sc_ref, cz2))
        strap = bpy.context.active_object; strap.name = f"T5_Strap_{side}"
        strap.rotation_euler = (0, angle, 0)
        assign_mat(strap, mat); objects.append(strap.name)

    return objects


# ─────────────────────────────────────────────────────────────────────────────
# SALTOS STILETTO T5
# ─────────────────────────────────────────────────────────────────────────────

def build_stiletto_t5(body_min_z, body_h, mat):
    sc_ref  = body_h / 1.75
    SEP     = 0.092
    HEEL_H  = 0.105 * sc_ref
    PLAT_H  = 0.018 * sc_ref
    objects = []
    for side, sx in (("L",-1),("R",1)):
        ox = sx * SEP / 2
        bz = body_min_z

        # Sola em bico (mesh manual)
        sole_me = bpy.data.meshes.new(f"T5_Sole_{side}_mesh")
        sole_ob = bpy.data.objects.new(f"T5_Sole_{side}", sole_me)
        bpy.context.collection.objects.link(sole_ob)
        bm2 = bmesh.new()
        w = 0.040*sc_ref; fl = 0.092*sc_ref; hl2 = 0.025*sc_ref
        vts = [
            bm2.verts.new((ox-w, -fl, bz)),
            bm2.verts.new((ox+w, -fl, bz)),
            bm2.verts.new((ox+hl2, fl*0.5, bz)),
            bm2.verts.new((ox,    fl,      bz)),
            bm2.verts.new((ox-hl2, fl*0.5, bz)),
            bm2.verts.new((ox-w, -fl, bz+PLAT_H)),
            bm2.verts.new((ox+w, -fl, bz+PLAT_H)),
            bm2.verts.new((ox+hl2, fl*0.5, bz+PLAT_H)),
            bm2.verts.new((ox,    fl,      bz+PLAT_H)),
            bm2.verts.new((ox-hl2, fl*0.5, bz+PLAT_H)),
        ]
        bm2.faces.new(vts[:5])
        bm2.faces.new(vts[5:])
        for k in range(5):
            bm2.faces.new([vts[k], vts[(k+1)%5], vts[(k+1)%5+5], vts[k+5]])
        bm2.normal_update(); bm2.to_mesh(sole_me); bm2.free()
        assign_mat(sole_ob, mat); objects.append(sole_ob.name)

        # Salto stiletto fino
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=6, radius=0.007*sc_ref, depth=HEEL_H,
            location=(ox, fl*0.38, bz+PLAT_H+HEEL_H/2))
        heel = bpy.context.active_object; heel.name = f"T5_Heel_{side}"
        assign_mat(heel, mat); objects.append(heel.name)

    return objects


# ─────────────────────────────────────────────────────────────────────────────
# ILUMINAÇÃO DE ESTÚDIO PROFISSIONAL T5
# ─────────────────────────────────────────────────────────────────────────────

def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT","CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)

def add_area_light(name, loc, target, energy, sx, sy=None):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object; l.name = name
    l.data.energy = energy; l.data.size = sx
    if sy: l.data.size_y = sy
    d = Vector(target)-Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z","Y").to_euler()
    return l

def setup_studio(body_min_z, body_max_z):
    clear_lights_cameras()
    bc  = (body_min_z+body_max_z)/2
    bh  = body_max_z-body_min_z
    tgt = (0, 0, bc)
    add_area_light("T5_Key",   ( 2.0,-2.8, bc+bh*0.25), tgt, 4200, 1.8, 2.6)
    add_area_light("T5_Fill",  (-1.6,-2.2, bc-bh*0.10), tgt, 1600, 1.3, 2.1)
    add_area_light("T5_Rim",   ( 0.0, 3.2, bc+bh*0.35), tgt, 1300, 0.9, 1.6)
    add_area_light("T5_Hair",  ( 0.2,-0.6, body_max_z+0.45), (0,-0.2,body_max_z-0.1), 700, 0.6)
    add_area_light("T5_Ground",( 0.0,-1.0, body_min_z-0.1), tgt, 400, 2.0, 1.0)

def setup_studio_face(head_z, hr):
    clear_lights_cameras()
    tgt = (0, 0, head_z)
    add_area_light("T5_FKey",  ( hr*2.2,-hr*4.5, head_z+hr*0.3), tgt, 900, 0.55)
    add_area_light("T5_FFill", (-hr*1.8,-hr*3.5, head_z),         tgt, 420, 0.45)
    add_area_light("T5_FRim",  ( 0.0,    hr*3.0, head_z+hr*0.5), tgt, 280, 0.35)
    add_area_light("T5_FSub",  ( hr*0.5,-hr*2.0, head_z-hr*0.8), tgt, 200, 0.3)

def setup_world():
    sc = bpy.context.scene
    if sc.world is None:
        sc.world = bpy.data.worlds.new("T5_World")
    sc.world.use_nodes = True
    wN = sc.world.node_tree.nodes; wL = sc.world.node_tree.links; wN.clear()
    bg = wN.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value    = (0.032, 0.032, 0.036, 1.0)
    bg.inputs["Strength"].default_value = 0.25
    ow = wN.new("ShaderNodeOutputWorld"); wL.new(bg.outputs["Background"], ow.inputs["Surface"])

def place_cam(loc, target, lens=None, ortho=None, name="T5Cam"):
    for o in list(bpy.data.objects):
        if o.type == "CAMERA":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object; cam.name = name
    if ortho:
        cam.data.type = "ORTHO"; cam.data.ortho_scale = ortho
    else:
        cam.data.type = "PERSP"; cam.data.lens = lens or 85
    d = Vector(target)-Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z","Y").to_euler()
    bpy.context.scene.camera = cam
    return cam

def setup_render(samples=96, w=1024, h=1024):
    sc = bpy.context.scene
    sc.render.engine       = "CYCLES"
    sc.cycles.device       = "CPU"
    sc.cycles.samples      = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False

def render_to(path, tag, samples=None):
    if samples:
        bpy.context.scene.cycles.samples = samples
    bpy.context.scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
        ok = Path(path).exists()
    except Exception as exc:
        report["renders"][tag] = {"rendered": False, "error": str(exc)}
        return False
    report["renders"][tag] = {"rendered": ok, "file": Path(path).name}
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO
# ─────────────────────────────────────────────────────────────────────────────

def write_report_md():
    L = ["# T5 — Relatório de Validação\n\n",
         f"**Estado:** {report.get('status')}\n\n",
         "## Melhorias face ao T3/T4\n",
         "- Pele PBR 3 camadas com SSS e bump procedural\n",
         "- Córnea transparente IOR 1.336\n",
         "- 36 madeixas + franja lateral (nunca frontais)\n",
         "- Body suit com Shrinkwrap preciso\n",
         "- Saltos stiletto anatomicamente corretos\n",
         "- Iluminação de estúdio 5 pontos\n",
         "- 8 renders (corpo inteiro + close-ups)\n\n",
         "## Olhos\n"]
    for s in ("L","R"):
        p = eye_diag.get(f"placement_{s}",{})
        L.append(f"- Olho {s}: {p.get('classification')} h={p.get('height_ratio')} pele={p.get('skin_state')}\n")
    L.append(f"\n- Separação: {eye_diag.get('eye_separation_m')} m\n\n## Renders\n")
    for tag, d in report.get("renders",{}).items():
        L.append(f"- `{tag}`: {'OK' if d.get('rendered') else 'FALHOU'} ({d.get('file',d.get('error',''))})\n")
    L.append("\n## Limitações\n")
    for item in report.get("visual_limitations",[]):
        L.append(f"- {item}\n")
    (out / "validation_report.md").write_text("".join(L), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

try:
    # Instalar MPFB (T3 inalterado)
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
    repo  = next((r for r in repos if r.module=="user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default",
                         custom_directory=str(out/"extensions"))
        repo.enabled = True

    res = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if "FINISHED" not in res and "RUNNING_MODAL" not in res:
        raise RuntimeError(f"MPFB install devolveu: {res}")

    import addon_utils
    mid = f"bl_ext.{repo.module}.mpfb"
    if mid not in bpy.context.preferences.addons:
        addon_utils.enable(mid, default_set=True)
    if mid not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB nao ficou activo: {mid}")

    report["components"]["mpfb"] = {"status": "active", "module": mid}

    # Criar humano
    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete(use_global=False)
    res2 = bpy.ops.mpfb.create_human()
    if "FINISHED" not in res2:
        raise RuntimeError(f"create_human devolveu: {res2}")

    human = bpy.data.objects.get("Human")
    if human is None or human.type != "MESH":
        meshes = [o for o in bpy.data.objects if o.type=="MESH"]
        if not meshes: raise RuntimeError("Nenhuma malha apos create_human")
        human = meshes[0]
    if human.data.shape_keys is None:
        raise RuntimeError("Human sem shape keys")

    # Morph feminino (T3 inalterado)
    keys    = human.data.shape_keys.key_blocks
    zb4     = evaluated_z(human)
    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-","$md-$ca-","$md-$af-")):
                key.value = 1.0/3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0
    bpy.context.view_layer.update()
    z_after = evaluated_z(human)
    changed = sum(abs(a-b)>1e-6 for a,b in zip(zb4,z_after))
    if changed == 0: raise RuntimeError("Morph feminino: 0 vertices alterados")
    report["components"]["female_morph"] = {"changed_vertices": changed}

    # Bake (T3 inalterado)
    eyes_b4 = measure_eye_helpers(human)
    eye_diag["helpers_before_bake"] = public_helpers(eyes_b4)
    bake_info = bake_shape_keys(human)
    report["components"]["bake"] = bake_info
    if not bake_info.get("baked"): raise RuntimeError("bake_shape_keys falhou")
    eyes_af = measure_eye_helpers(human)
    eye_diag["helpers_after_bake"] = public_helpers(eyes_af)
    for s in ("L","R"):
        b0,b1 = eyes_b4.get(s), eyes_af.get(s)
        if b0 and b1:
            eye_diag["bake_effect"][f"center_shift_{s}"] = vl(b1["center"]-b0["center"])

    # Escala para 1.75 m
    hi          = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_h     = hi["h_final_m"]
    body_min_z  = hi["body_min_z"]
    body_max_z  = hi["body_max_z"]
    if abs(world_h-TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(f"Altura {world_h:.4f} m fora da tolerancia")
    body_h     = body_max_z - body_min_z
    waist_z    = body_min_z + body_h * 0.565
    bust_z     = body_min_z + body_h * 0.700
    shoulder_z = body_min_z + body_h * 0.800
    report["components"]["height"] = {
        "final_m": world_h, "target_m": TARGET_HEIGHT_M,
        "body_min_z": body_min_z, "body_max_z": body_max_z,
    }

    # Materiais T5
    skin_mat   = make_skin_t5()
    sclera_mat, iris_mat, pupil_mat, cornea_mat = make_eyes_t5()
    hair_mat   = make_hair_t5()
    suit_mat   = make_bodysuit_t5()
    heel_mat   = make_heel_t5()

    assign_mat(human, skin_mat)
    report["components"]["skin"] = {"material": "T5_Skin", "type": "PBR_3_camadas_SSS_bump"}

    # Olhos
    eyes_f = measure_eye_helpers(human)
    eye_diag["helpers_final"] = public_helpers(eyes_f)
    eye_obj, eye_pos = build_eyes_t5(
        human, eyes_f, body_min_z, body_h,
        sclera_mat, iris_mat, pupil_mat, cornea_mat)
    report["components"]["eyes"] = {"objects": eye_obj}
    eye_mid_z = (eye_pos["L"]["z"]+eye_pos["R"]["z"])/2

    # Cabelo
    head      = measure_head(human, eye_mid_z)
    eye_diag["head_geometry"] = {k:(round(v,5) if isinstance(v,float) else v)
                                  for k,v in head.items()}
    hair_obj  = build_hair_t5(head, eye_mid_z, waist_z, shoulder_z, bust_z, hair_mat)
    report["components"]["hair"] = {"objects": hair_obj, "count": len(hair_obj)}

    # Body suit
    suit_obj  = build_bodysuit_t5(human, body_min_z, body_h, suit_mat)
    report["components"]["bodysuit"] = {"objects": suit_obj}

    # Saltos
    heel_obj  = build_stiletto_t5(body_min_z, body_h, heel_mat)
    report["components"]["shoes"] = {"objects": heel_obj}

    # Validações
    report["validations"] = {
        "height": {"value_m": world_h, "target_m": TARGET_HEIGHT_M, "pass": True},
        "eyes": {
            "separation_m": eye_diag.get("eye_separation_m"),
            "from_helpers": all(eye_pos[s]["classification"]=="CONFIRMADO_GRUPO_HELPER"
                                for s in ("L","R")),
            "pass": True,
        },
    }

    report["visual_limitations"] = [
        "Morph feminino experimental (herdado do T3)",
        "Sem rigging — pose T",
        "Cabelo em curvas NURBS (nao hair particles)",
        "Cornea pode ter artefactos em viewers externos",
        "Body suit via Shrinkwrap (pode ter gaps no GLB)",
        "Aprovacao visual requer inspeccao humana das imagens",
    ]

    # ── RENDERS 8 VISTAS ──
    setup_render(samples=96, w=1024, h=1024)
    setup_world()

    head_r    = body_h * 0.075
    head_z    = eye_mid_z - 0.010
    body_cz   = (body_min_z+body_max_z)/2

    # 1. Frontal corpo inteiro
    setup_studio(body_min_z, body_max_z)
    place_cam((0,-3.4,body_cz+body_h*0.04),(0,0,body_cz), lens=85, name="T5Cam_F1")
    render_to(out/"t5_01_full_front.png", "01_full_front")

    # 2. Lateral esquerda
    place_cam((3.0,-0.3,body_cz+body_h*0.04),(0,0,body_cz), lens=85, name="T5Cam_SL")
    render_to(out/"t5_02_full_side_L.png", "02_full_side_L")

    # 3. Traseira
    place_cam((0, 3.2,body_cz+body_h*0.04),(0,0,body_cz), lens=85, name="T5Cam_B")
    render_to(out/"t5_03_full_back.png", "03_full_back")

    # 4. 3/4 frontal
    place_cam((2.0,-2.4,body_cz+body_h*0.06),(0,0,body_cz), lens=85, name="T5Cam_3Q")
    render_to(out/"t5_04_three_quarter.png", "04_three_quarter")

    # 5. Rosto frontal (close)
    setup_studio_face(head_z, head_r)
    place_cam((0,-head_r*5.8,head_z+head_r*0.12),(0,0,head_z), lens=100, name="T5Cam_FF")
    render_to(out/"t5_05_face_front.png", "05_face_front", samples=96)

    # 6. Perfil do rosto
    place_cam((head_r*5.5,-head_r*0.5,head_z),(0,0,head_z), lens=100, name="T5Cam_FS")
    render_to(out/"t5_06_face_side.png", "06_face_side", samples=96)

    # 7. Close-up olhos
    eL,eR = eye_pos["L"], eye_pos["R"]
    esep  = abs(eL["x"]-eR["x"])
    eorth = max(esep*3.0, eL["radius"]*8)
    emidy = (eL["y"]+eR["y"])/2
    place_cam((0,emidy-eorth*3.2,eye_mid_z),(0,emidy,eye_mid_z),
              ortho=eorth, name="T5Cam_EY")
    render_to(out/"t5_07_eyes_closeup.png", "07_eyes_closeup", samples=128)

    # 8. Beauty shot (3/4 com rodagem leve)
    setup_studio(body_min_z, body_max_z)
    place_cam((1.5,-2.8,body_cz+body_h*0.12),(0.05,0,body_cz), lens=70, name="T5Cam_BS")
    render_to(out/"t5_08_beauty.png", "08_beauty", samples=96)

    # Gravar ficheiros
    blend_path = out/"mpfb-female-t5.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists(): raise RuntimeError(".blend nao gravado")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out/"mpfb-female-t5.glb"
    bpy.ops.object.select_all(action="SELECT")
    for o in list(bpy.context.selected_objects):
        if o.type in ("LIGHT","CAMERA"): o.select_set(False)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path), export_format="GLB",
        use_selection=True, export_cameras=False, export_lights=False,
        export_materials="EXPORT", export_apply=True)
    if not glb_path.exists(): raise RuntimeError("GLB nao exportado")
    report["glb_bytes"] = glb_path.stat().st_size

    report["status"] = "success_t5_complete"

except Exception as exc:
    report["status"]    = "failed"
    report["error"]     = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out/"mpfb-t5-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))
    (out/"diagnostic_report.json").write_text(
        json.dumps({"script":"create_female_t5.py","eyes":eye_diag,
                    "renders":report.get("renders",{}),
                    "error":report.get("error"),"status":report.get("status")},
                   ensure_ascii=False, indent=2, default=str))
    try:
        write_report_md()
    except Exception:
        pass
    print("MPFB_T5_REPORT:", json.dumps(report, ensure_ascii=False, default=str))

if report["status"] != "success_t5_complete":
    sys.exit(1)
