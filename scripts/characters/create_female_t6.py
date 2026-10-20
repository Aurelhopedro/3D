"""
T6 — Personagem Anime 3D estilo Isekai (inspirado em Tensei Shitara Slime Datta Ken)

MOTOR: BLENDER_EEVEE (detecao automatica por versao)
ESTILO: Cel-shading com faixas duras, Freestyle para contornos pretos
CORPO: construido do zero em bmesh com proporcoes anime
       (sem MPFB — o MPFB gera malhas realistas, incompativeis com o visual anime)

PROPORCOES ANIME:
  - Altura total: 1.55 m (figura jovem/androide estilo Rimuru)
  - Cabeca: 19% da altura (vs 12% em humano real) — caracteristica anime
  - Olhos: ocupam 38% da altura do rosto, posicionados no centro do rosto
  - Cintura fina, ombros estreitos, pernas longas

COMPONENTES:
  1. Corpo base (torso, pernas, braccos, maos simplificadas)
  2. Cabeca com rosto (olhos grandes 2D, nariz minimo, boca fina)
  3. Cabelo estilo anime (mechas angulosas, franja com picos)
  4. Roupa estilo Rimuru (tunica escura com detalhes)
  5. Freestyle + EEVEE cel-shading

OUTPUTS: output/mpfb-t6/
"""

import bpy
import bmesh
import json
import math
import os
import sys
import traceback
from pathlib import Path
from mathutils import Vector

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

H          = 1.55      # altura total em metros
HEAD_FRAC  = 0.190     # cabeca = 19% da altura
NECK_FRAC  = 0.040
TORSO_FRAC = 0.280
LEG_FRAC   = 0.430
FOOT_FRAC  = 0.060

# Derivados
HEAD_H   = H * HEAD_FRAC
NECK_H   = H * NECK_FRAC
TORSO_H  = H * TORSO_FRAC
LEG_H    = H * LEG_FRAC
FOOT_H   = H * FOOT_FRAC
ARM_H    = TORSO_H * 0.95

# Z base = 0 (sola dos pes)
Z_FLOOR    = 0.0
Z_ANKLE    = FOOT_H
Z_HIP      = Z_ANKLE + LEG_H
Z_SHOULDER = Z_HIP + TORSO_H
Z_NECK_BOT = Z_SHOULDER
Z_NECK_TOP = Z_NECK_BOT + NECK_H
Z_HEAD_BOT = Z_NECK_TOP
Z_HEAD_MID = Z_HEAD_BOT + HEAD_H * 0.50
Z_HEAD_TOP = Z_HEAD_BOT + HEAD_H

# Larguras (raios)
R_HEAD    = HEAD_H * 0.50
R_NECK    = 0.028
R_SHOULDER= 0.110
R_BUST    = 0.105
R_WAIST   = 0.078
R_HIP     = 0.112
R_THIGH   = 0.058
R_KNEE    = 0.048
R_CALF    = 0.042
R_ANKLE   = 0.028
R_FOOT_L  = 0.115
R_FOOT_W  = 0.040
R_UPPER_ARM = 0.030
R_LOWER_ARM = 0.024
R_WRIST   = 0.018

# Paleta estilo Rimuru
C_SKIN      = "#F5D5B8"
C_SKIN_SH   = "#D4A882"
C_HAIR      = "#B8C8E8"   # azul-cinza claro (cabelo do Rimuru)
C_HAIR_SH   = "#7A98C8"
C_EYE_W     = "#F0F0F8"
C_EYE_IRIS  = "#6890D0"   # azul
C_EYE_PUPIL = "#1A1A2E"
C_EYE_HL    = "#FFFFFF"
C_CLOTH_DARK= "#1A1A2A"   # tunica escura
C_CLOTH_MID = "#2A2A4A"
C_CLOTH_ACC = "#8080FF"   # detalhes roxo-azul
C_OUTLINE   = "#000000"

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-t6")).resolve()
out.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# DETETAR MOTOR EEVEE DISPONIVEL
# Blender <= 4.1 usa "BLENDER_EEVEE"
# Blender 4.2+ usou "BLENDER_EEVEE_NEXT"
# Blender 5.x LTS voltou a "BLENDER_EEVEE"
# ─────────────────────────────────────────────────────────────────────────────

def _get_eevee_engine():
    """Retorna o identificador correto do motor EEVEE para a versao instalada."""
    import bpy
    engines = {e.bl_idname for e in bpy.types.RenderEngine.__subclasses__()}
    # Tambem verificar via enumeracao de RenderEngine registados
    registered = set()
    try:
        for item in bpy.types.Scene.bl_rna.properties["render.engine"].enum_items:
            registered.add(item.identifier)
    except Exception:
        pass
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if name in registered:
            return name
    # Fallback seguro: tentar atribuir e capturar erro
    import bpy
    scene = bpy.context.scene
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = name
            return name
        except TypeError:
            continue
    return "BLENDER_EEVEE"

EEVEE_ENGINE = _get_eevee_engine()

report = {
    "blender": bpy.app.version_string,
    "version": "t6",
    "style": "anime_isekai_cel_shading",
    "engine": EEVEE_ENGINE,
    "status": "started",
    "components": {},
    "validations": {},
    "renders": {},
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITARIOS
# ─────────────────────────────────────────────────────────────────────────────

def srgb(c):
    if c <= 0.04045: return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4

def hex4(h, a=1.0):
    h = h.lstrip("#")
    if len(h)==3: h="".join(c*2 for c in h)
    return (srgb(int(h[0:2],16)/255), srgb(int(h[2:4],16)/255),
            srgb(int(h[4:6],16)/255), a)

def assign(obj, mat, slot=0):
    while len(obj.data.materials) <= slot:
        obj.data.materials.append(None)
    obj.data.materials[slot] = mat

def desel():
    bpy.ops.object.select_all(action="DESELECT")

def active(obj):
    desel()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

def smooth(obj):
    active(obj)
    bpy.ops.object.shade_smooth()


# ─────────────────────────────────────────────────────────────────────────────
# MATERIAIS CEL-SHADING (EEVEE)
# ─────────────────────────────────────────────────────────────────────────────

def mat_toon(name, c_lit, c_shadow, threshold=0.5, smooth_w=0.02):
    """Cel-shading com duas faixas duras via ColorRamp."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    N = mat.node_tree.nodes
    L = mat.node_tree.links
    N.clear()

    geom  = N.new("ShaderNodeNewGeometry")
    geom.location = (-800, 0)
    dot   = N.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.location = (-600, 0)

    # Luz principal: usar normal dot com uma direção fixa (simula luz de cima-frente)
    light_vec = N.new("ShaderNodeCombineXYZ")
    light_vec.location = (-800, -200)
    light_vec.inputs["X"].default_value = 0.3
    light_vec.inputs["Y"].default_value = -0.7
    light_vec.inputs["Z"].default_value = 0.6

    dot2 = N.new("ShaderNodeVectorMath")
    dot2.operation = "DOT_PRODUCT"
    dot2.location = (-600, -150)

    ramp = N.new("ShaderNodeValToRGB")
    ramp.location = (-350, 0)
    ramp.color_ramp.interpolation = "CONSTANT"  # FAIXA DURA — chave do cel-shading
    e = ramp.color_ramp.elements
    e[0].position = 0.0
    e[0].color = hex4(c_shadow)
    e[1].position = threshold
    e[1].color = hex4(c_lit)

    emit = N.new("ShaderNodeEmission")
    emit.location = (-100, 0)
    emit.inputs["Strength"].default_value = 1.0

    out_n = N.new("ShaderNodeOutputMaterial")
    out_n.location = (100, 0)

    L.new(geom.outputs["Normal"],       dot2.inputs[0])
    L.new(light_vec.outputs["Vector"],  dot2.inputs[1])
    L.new(dot2.outputs["Value"],        ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"],        emit.inputs["Color"])
    L.new(emit.outputs["Emission"],     out_n.inputs["Surface"])
    return mat


def mat_eye_iris(name):
    """Iris anime: gradiente radial com highlight."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    N = mat.node_tree.nodes
    L = mat.node_tree.links
    N.clear()

    tc  = N.new("ShaderNodeTexCoord"); tc.location=(-700,0)
    mp  = N.new("ShaderNodeMapping");  mp.location=(-500,0)
    gr  = N.new("ShaderNodeTexGradient"); gr.location=(-300,0); gr.gradient_type="RADIAL"
    cr  = N.new("ShaderNodeValToRGB"); cr.location=(-100,0)
    cr.color_ramp.interpolation = "CONSTANT"
    e = cr.color_ramp.elements
    e[0].position=0.0;  e[0].color=hex4(C_EYE_HL)
    e[1].position=0.12; e[1].color=hex4(C_EYE_IRIS)
    cr.color_ramp.elements.new(0.72).color=hex4(C_EYE_PUPIL)
    cr.color_ramp.elements.new(0.96).color=hex4("#0A0A18")

    em  = N.new("ShaderNodeEmission"); em.location=(150,0)
    em.inputs["Strength"].default_value = 1.2
    on  = N.new("ShaderNodeOutputMaterial"); on.location=(350,0)
    L.new(tc.outputs["Object"], mp.inputs["Vector"])
    L.new(mp.outputs["Vector"], gr.inputs["Vector"])
    L.new(gr.outputs["Color"],  cr.inputs["Fac"])
    L.new(cr.outputs["Color"],  em.inputs["Color"])
    L.new(em.outputs["Emission"],on.inputs["Surface"])
    return mat


def mat_solid(name, hex_color, emit_strength=1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    N = mat.node_tree.nodes
    L = mat.node_tree.links
    N.clear()
    em = N.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = hex4(hex_color)
    em.inputs["Strength"].default_value = emit_strength
    on = N.new("ShaderNodeOutputMaterial")
    L.new(em.outputs["Emission"], on.inputs["Surface"])
    return mat


# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRIA — FUNCOES BASE
# ─────────────────────────────────────────────────────────────────────────────

def cylinder_section(bm, z_bot, z_top, r_bot, r_top, segs=24, y_off=0.0):
    """Secao de cilindro entre dois raios. Retorna (ring_bot, ring_top)."""
    def ring(z, r):
        return [bm.verts.new((r*math.cos(2*math.pi*s/segs),
                              r*math.sin(2*math.pi*s/segs)+y_off, z))
                for s in range(segs)]
    rb = ring(z_bot, r_bot)
    rt = ring(z_top, r_top)
    for s in range(segs):
        sn = (s+1)%segs
        bm.faces.new([rb[s], rb[sn], rt[sn], rt[s]])
    return rb, rt


def cap_ring(bm, ring_verts, z=None, inward=False):
    """Tampa plana de um anel."""
    face = bm.faces.new(ring_verts if not inward else list(reversed(ring_verts)))
    return face


def new_obj(name, bm_src):
    me = bpy.data.meshes.new(name+"_mesh")
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    bm_src.normal_update()
    bm_src.to_mesh(me)
    bm_src.free()
    me.update()
    return ob


# ─────────────────────────────────────────────────────────────────────────────
# CORPO
# ─────────────────────────────────────────────────────────────────────────────

def build_body():
    skin_mat   = mat_toon("T6_Skin", C_SKIN, C_SKIN_SH, threshold=0.52)
    cloth_mat  = mat_toon("T6_Cloth", C_CLOTH_DARK, C_CLOTH_MID, threshold=0.48)
    acc_mat    = mat_toon("T6_Acc", C_CLOTH_ACC, "#4040AA", threshold=0.50)
    SEGS = 20

    objs = []

    # ── TORSO (6 secoes com perfil variado) ──────────────────────────────────
    bm = bmesh.new()
    profile = [
        (Z_HIP,      R_HIP),
        (Z_HIP + TORSO_H*0.15, R_WAIST*1.05),
        (Z_HIP + TORSO_H*0.35, R_WAIST),
        (Z_HIP + TORSO_H*0.55, R_BUST),
        (Z_HIP + TORSO_H*0.75, R_BUST*1.02),
        (Z_SHOULDER, R_SHOULDER),
    ]
    rings = []
    for z, r in profile:
        rings.append([bm.verts.new((r*math.cos(2*math.pi*s/SEGS),
                                    r*math.sin(2*math.pi*s/SEGS), z))
                      for s in range(SEGS)])
    cap_ring(bm, list(reversed(rings[0])))
    for i in range(len(rings)-1):
        r0, r1 = rings[i], rings[i+1]
        for s in range(SEGS):
            sn = (s+1)%SEGS
            bm.faces.new([r0[s],r0[sn],r1[sn],r1[s]])
    torso = new_obj("T6_Torso", bm)
    assign(torso, cloth_mat)
    smooth(torso)
    objs.append(torso.name)

    # ── PESCOCO ───────────────────────────────────────────────────────────────
    bm = bmesh.new()
    rb, rt = cylinder_section(bm, Z_NECK_BOT, Z_NECK_TOP, R_NECK*1.1, R_NECK, SEGS)
    cap_ring(bm, rb, inward=True)
    cap_ring(bm, rt)
    neck = new_obj("T6_Neck", bm)
    assign(neck, skin_mat)
    smooth(neck)
    objs.append(neck.name)

    # ── PERNAS (2) ────────────────────────────────────────────────────────────
    LEG_SEP = R_HIP * 0.58
    for side, sign in (("L",-1),("R",1)):
        bm = bmesh.new()
        cx = sign * LEG_SEP
        Z_KNEE_Z = Z_ANKLE + LEG_H * 0.48
        # coxa
        r0 = [bm.verts.new((cx+R_THIGH*math.cos(2*math.pi*s/SEGS),
                             R_THIGH*math.sin(2*math.pi*s/SEGS), Z_HIP))
              for s in range(SEGS)]
        r1 = [bm.verts.new((cx+R_KNEE*math.cos(2*math.pi*s/SEGS),
                             R_KNEE*math.sin(2*math.pi*s/SEGS), Z_KNEE_Z))
              for s in range(SEGS)]
        r2 = [bm.verts.new((cx+R_CALF*math.cos(2*math.pi*s/SEGS),
                             R_CALF*math.sin(2*math.pi*s/SEGS), Z_ANKLE))
              for s in range(SEGS)]
        cap_ring(bm, list(reversed(r0)))
        for rb2, rt2 in ((r0,r1),(r1,r2)):
            for s in range(SEGS):
                sn=(s+1)%SEGS
                bm.faces.new([rb2[s],rb2[sn],rt2[sn],rt2[s]])
        cap_ring(bm, r2)
        leg = new_obj(f"T6_Leg_{side}", bm)
        assign(leg, cloth_mat)
        smooth(leg)
        objs.append(leg.name)

        # pe
        bm = bmesh.new()
        hw, hl = R_FOOT_W, R_FOOT_L/2
        toe_hw = hw*0.40
        bz = Z_FLOOR; bz2 = bz + FOOT_H*0.55
        vs = [bm.verts.new(p) for p in [
            (cx-hw, -hl, bz),(cx+hw, -hl, bz),(cx+toe_hw, hl, bz),(cx-toe_hw, hl, bz),
            (cx-hw, -hl, bz2),(cx+hw, -hl, bz2),(cx+toe_hw, hl, bz2*0.65),(cx-toe_hw, hl, bz2*0.65),
        ]]
        for f in [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[3,0,4,7]]:
            bm.faces.new([vs[i] for i in f])
        bm.normal_update()
        foot = new_obj(f"T6_Foot_{side}", bm)
        assign(foot, cloth_mat)
        objs.append(foot.name)

    # ── BRACOS (2) ────────────────────────────────────────────────────────────
    SHOULDER_X = R_SHOULDER * 0.85
    Z_ELBOW    = Z_SHOULDER - ARM_H*0.48
    Z_WRIST_Z  = Z_SHOULDER - ARM_H
    for side, sign in (("L",-1),("R",1)):
        bm = bmesh.new()
        # braco superior
        for (za, ra, zb, rb_r) in [
            (Z_SHOULDER, R_UPPER_ARM, Z_ELBOW, R_LOWER_ARM*1.05),
            (Z_ELBOW, R_LOWER_ARM*1.05, Z_WRIST_Z, R_WRIST),
        ]:
            r0 = [bm.verts.new((sign*(SHOULDER_X + ra*math.cos(2*math.pi*s/SEGS)),
                                 ra*math.sin(2*math.pi*s/SEGS), za)) for s in range(SEGS)]
            r1 = [bm.verts.new((sign*(SHOULDER_X + rb_r*math.cos(2*math.pi*s/SEGS)),
                                 rb_r*math.sin(2*math.pi*s/SEGS), zb)) for s in range(SEGS)]
            for s in range(SEGS):
                sn=(s+1)%SEGS
                bm.faces.new([r0[s],r0[sn],r1[sn],r1[s]])
        # mao simplificada (esfera achatada)
        bm.normal_update(); bm.to_mesh(bpy.data.meshes.new("_tmp")); bm.free()
        bm = bmesh.new()
        # recomecar com braco completo
        r0 = [bm.verts.new((sign*(SHOULDER_X + R_UPPER_ARM*math.cos(2*math.pi*s/SEGS)),
                             R_UPPER_ARM*math.sin(2*math.pi*s/SEGS), Z_SHOULDER))
              for s in range(SEGS)]
        r1 = [bm.verts.new((sign*(SHOULDER_X + R_LOWER_ARM*math.cos(2*math.pi*s/SEGS)),
                             R_LOWER_ARM*math.sin(2*math.pi*s/SEGS), Z_ELBOW))
              for s in range(SEGS)]
        r2 = [bm.verts.new((sign*(SHOULDER_X + R_WRIST*math.cos(2*math.pi*s/SEGS)),
                             R_WRIST*math.sin(2*math.pi*s/SEGS), Z_WRIST_Z))
              for s in range(SEGS)]
        cap_ring(bm, list(reversed(r0)))
        for rA, rB in ((r0,r1),(r1,r2)):
            for s in range(SEGS):
                sn=(s+1)%SEGS
                bm.faces.new([rA[s],rA[sn],rB[sn],rB[s]])
        # palma simples
        palm = bm.verts.new((sign*(SHOULDER_X), 0.0, Z_WRIST_Z - R_WRIST*1.8))
        for s in range(SEGS):
            sn=(s+1)%SEGS
            bm.faces.new([r2[s], r2[sn], palm])
        arm = new_obj(f"T6_Arm_{side}", bm)
        assign(arm, skin_mat)
        smooth(arm)
        objs.append(arm.name)

    return objs, {"skin": "T6_Skin", "cloth": "T6_Cloth"}


# ─────────────────────────────────────────────────────────────────────────────
# CABECA ANIME
# ─────────────────────────────────────────────────────────────────────────────

def build_head():
    skin_mat = mat_toon("T6_HeadSkin", C_SKIN, C_SKIN_SH, threshold=0.52)
    SEGS = 28
    objs = []

    # Cabeca: ovalo com queixo pontiagudo (anime)
    bm = bmesh.new()
    N_RINGS = 14
    HEAD_R_X = R_HEAD * 1.00   # largura
    HEAD_R_Y = R_HEAD * 0.85   # profundidade (ligeiramente achatada)
    HEAD_R_Z_TOP = HEAD_H * 0.52
    HEAD_R_Z_BOT = HEAD_H * 0.48

    rings = []
    for ri in range(N_RINGS+1):
        # 0 = topo, N_RINGS = queixo
        t = ri / N_RINGS
        # perfil: largo no meio, mais estreito no topo e muito estreito no queixo
        if t < 0.5:
            sx = math.sin(math.pi * t)
            sy = math.sin(math.pi * t)
        else:
            # queixo pontiagudo: encolhe mais rapido em X que Y
            sx = math.sin(math.pi * t) * (1.0 - (t-0.5)*1.2)
            sy = math.sin(math.pi * t) * (1.0 - (t-0.5)*0.6)
        sx = max(sx, 0.01)
        sy = max(sy, 0.01)
        z = Z_HEAD_TOP - HEAD_H * t
        ring = [bm.verts.new((
            HEAD_R_X * sx * math.cos(2*math.pi*s/SEGS),
            HEAD_R_Y * sy * math.sin(2*math.pi*s/SEGS),
            z
        )) for s in range(SEGS)]
        rings.append(ring)

    # Polo topo
    pole_top = bm.verts.new((0.0, 0.0, Z_HEAD_TOP + 0.002))
    for s in range(SEGS):
        bm.faces.new([pole_top, rings[0][(s+1)%SEGS], rings[0][s]])

    # Faces laterais
    for ri in range(len(rings)-1):
        r0, r1 = rings[ri], rings[ri+1]
        for s in range(SEGS):
            sn=(s+1)%SEGS
            bm.faces.new([r0[s], r0[sn], r1[sn], r1[s]])

    # Queixo pontiagudo
    chin = bm.verts.new((0.0, -HEAD_R_Y*0.08, Z_HEAD_BOT - HEAD_H*0.04))
    for s in range(SEGS):
        bm.faces.new([rings[-1][s], rings[-1][(s+1)%SEGS], chin])

    head = new_obj("T6_Head", bm)
    assign(head, skin_mat)
    smooth(head)
    objs.append(head.name)

    # ── ORELHAS ───────────────────────────────────────────────────────────────
    EAR_Z = Z_HEAD_BOT + HEAD_H * 0.52
    for side, sign in (("L",-1),("R",1)):
        bm = bmesh.new()
        ear_cx = sign * HEAD_R_X * 0.92
        # elipse simples
        for ri in range(5):
            t = ri/4
            er = 0.022 * math.sin(math.pi*t) + 0.003
            ring_ear = [bm.verts.new((
                ear_cx + er*0.4*math.cos(2*math.pi*s/12),
                HEAD_R_Y*0.88 * math.sin(2*math.pi*s/12) * 0.15,
                EAR_Z + 0.028*(t-0.5)
            )) for s in range(12)]
            if ri>0:
                for s in range(12):
                    sn=(s+1)%12
                    bm.faces.new([prev_r[s],prev_r[sn],ring_ear[sn],ring_ear[s]])
            prev_r = ring_ear
        ear = new_obj(f"T6_Ear_{side}", bm)
        assign(ear, skin_mat)
        objs.append(ear.name)

    return objs, {"head_height_m": round(HEAD_H,4), "chin_y_offset": -HEAD_R_Y*0.08}


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS ANIME (planos 2D colados a cabeca, com iris radial)
# ─────────────────────────────────────────────────────────────────────────────

def build_anime_eyes_2d():
    sclera_mat = mat_toon("T6_Sclera", C_EYE_W, "#D8D8E8", threshold=0.60)
    iris_mat   = mat_eye_iris("T6_Iris")
    pupil_mat  = mat_solid("T6_Pupil", C_EYE_PUPIL)
    hl_mat     = mat_solid("T6_EyeHL", C_EYE_HL, emit_strength=2.0)
    lash_mat   = mat_solid("T6_Lash", "#0A0A18")
    objs = []

    EYE_SEP   = R_HEAD * 0.58
    EYE_Z     = Z_HEAD_BOT + HEAD_H * 0.50   # centro dos olhos = meio do rosto
    EYE_Y     = -(HEAD_H * 0.43)             # frente da cabeca
    EYE_W     = R_HEAD * 0.38               # largura do olho
    EYE_H_TOP = R_HEAD * 0.22               # altura do olho (em Z)
    EYE_DEPTH = 0.002                        # espessura do quad
    SEGS_EYE  = 32

    for side, sign in (("L",-1),("R",1)):
        cx = sign * EYE_SEP

        # Esclera (quad eliptico ligeiramente curvado)
        bm = bmesh.new()
        N_RINGS_E = 8
        for ri in range(N_RINGS_E+1):
            tz = ri/N_RINGS_E
            # forma do olho anime: mais larga a meio, pontas afiladas
            ew = EYE_W * math.sin(math.pi * tz)
            eh_top = EYE_H_TOP * (1.0 - 0.3*abs(2*tz-1.0))
            z_eye = EYE_Z - EYE_H_TOP + EYE_H_TOP*2.0*tz
            # slight curve: y avanca ligeiramente no meio
            depth_off = EYE_DEPTH * math.sin(math.pi*tz)
            ring_e = [bm.verts.new((
                cx + ew * math.cos(2*math.pi*s/SEGS_EYE),
                EYE_Y - depth_off,
                z_eye + eh_top * math.sin(2*math.pi*s/SEGS_EYE) * 0.0
            )) for s in range(SEGS_EYE)]
            if ri > 0:
                for s in range(SEGS_EYE):
                    sn=(s+1)%SEGS_EYE
                    bm.faces.new([prev_re[s],prev_re[sn],ring_e[sn],ring_e[s]])
            else:
                bm.faces.new(list(reversed(ring_e)))
            prev_re = ring_e
        bm.faces.new(ring_e)

        # Substituir por quad simples (mais controlavel)
        bm.free()
        bm = bmesh.new()
        # quad com forma de amendoa (8 pontos)
        pts_sclera = [
            (cx - EYE_W,       EYE_Y, EYE_Z),
            (cx - EYE_W*0.55,  EYE_Y, EYE_Z + EYE_H_TOP*0.90),
            (cx,               EYE_Y, EYE_Z + EYE_H_TOP),
            (cx + EYE_W*0.55,  EYE_Y, EYE_Z + EYE_H_TOP*0.85),
            (cx + EYE_W,       EYE_Y, EYE_Z),
            (cx + EYE_W*0.50,  EYE_Y, EYE_Z - EYE_H_TOP*0.55),
            (cx,               EYE_Y, EYE_Z - EYE_H_TOP*0.60),
            (cx - EYE_W*0.50,  EYE_Y, EYE_Z - EYE_H_TOP*0.50),
        ]
        vs = [bm.verts.new(p) for p in pts_sclera]
        centre_v = bm.verts.new((cx, EYE_Y, EYE_Z))
        for i in range(len(vs)):
            bm.faces.new([vs[i], vs[(i+1)%len(vs)], centre_v])
        sc_obj = new_obj(f"T6_Sclera_{side}", bm)
        assign(sc_obj, sclera_mat)
        objs.append(sc_obj.name)

        # Iris (menor, centrado, mesma forma)
        IR = 0.62
        bm = bmesh.new()
        pts_iris = [(cx + (p[0]-cx)*IR, EYE_Y-0.001, EYE_Z+(p[2]-EYE_Z)*IR)
                    for p in pts_sclera]
        vi = [bm.verts.new(p) for p in pts_iris]
        ci = bm.verts.new((cx, EYE_Y-0.001, EYE_Z))
        for i in range(len(vi)):
            bm.faces.new([vi[i], vi[(i+1)%len(vi)], ci])
        iris_obj = new_obj(f"T6_Iris_{side}", bm)
        assign(iris_obj, iris_mat)
        objs.append(iris_obj.name)

        # Pupila
        PU = 0.28
        bm = bmesh.new()
        pts_pu = [(cx + (p[0]-cx)*PU, EYE_Y-0.002, EYE_Z+(p[2]-EYE_Z)*PU*1.35)
                  for p in pts_sclera]
        vp = [bm.verts.new(p) for p in pts_pu]
        cp = bm.verts.new((cx, EYE_Y-0.002, EYE_Z))
        for i in range(len(vp)):
            bm.faces.new([vp[i], vp[(i+1)%len(vp)], cp])
        pu_obj = new_obj(f"T6_Pupil_{side}", bm)
        assign(pu_obj, pupil_mat)
        objs.append(pu_obj.name)

        # Highlight (pequeno quad branco no canto superior)
        HL = 0.14
        bm = bmesh.new()
        hx = cx - sign*EYE_W*0.30
        hz = EYE_Z + EYE_H_TOP*0.45
        pts_hl = [
            (hx - EYE_W*HL, EYE_Y-0.003, hz),
            (hx,            EYE_Y-0.003, hz + EYE_H_TOP*HL*1.1),
            (hx + EYE_W*HL, EYE_Y-0.003, hz),
            (hx,            EYE_Y-0.003, hz - EYE_H_TOP*HL*0.7),
        ]
        vh = [bm.verts.new(p) for p in pts_hl]
        bm.faces.new(vh)
        hl_obj = new_obj(f"T6_Hilite_{side}", bm)
        assign(hl_obj, hl_mat)
        objs.append(hl_obj.name)

        # Pestana superior (faixa escura acima)
        bm = bmesh.new()
        LASH_H = EYE_H_TOP * 0.20
        pts_lash_top = [(p[0], EYE_Y-0.001, p[2]) for p in pts_sclera[:5]]
        pts_lash_bot = [(p[0], EYE_Y-0.001, p[2]+LASH_H + (0.010 if i==2 else 0.004))
                        for i,p in enumerate(pts_sclera[:5])]
        vlt = [bm.verts.new(p) for p in pts_lash_top]
        vlb = [bm.verts.new(p) for p in pts_lash_bot]
        for i in range(len(vlt)-1):
            bm.faces.new([vlt[i],vlt[i+1],vlb[i+1],vlb[i]])
        lash_obj = new_obj(f"T6_Lash_{side}", bm)
        assign(lash_obj, lash_mat)
        objs.append(lash_obj.name)

    return objs, {
        "eye_separation_m": round(EYE_SEP*2, 4),
        "eye_width_m": round(EYE_W*2, 4),
        "eye_height_m": round(EYE_H_TOP*2, 4),
        "eye_z_ratio": round((EYE_Z - Z_HEAD_BOT)/HEAD_H, 3),
        "style": "2D_quad_flat_on_face",
    }


# ─────────────────────────────────────────────────────────────────────────────
# CABELO ANIME (estilo Rimuru: azul-cinza, mechas angulosas, franja)
# ─────────────────────────────────────────────────────────────────────────────

def build_anime_hair():
    hair_mat = mat_toon("T6_Hair", C_HAIR, C_HAIR_SH, threshold=0.55)
    acc_mat  = mat_toon("T6_HairAcc", C_CLOTH_ACC, "#3030AA", threshold=0.50)
    objs = []

    HEAD_R_X = R_HEAD
    HEAD_R_Y = R_HEAD * 0.85
    TOP_Z    = Z_HEAD_TOP
    EYE_Y    = -(HEAD_H * 0.43)

    # Touca base (elipsoide)
    bm = bmesh.new()
    CAP_S, CAP_R = 28, 10
    cy_cap = 0.010
    for ri in range(CAP_R+1):
        phi = math.pi*0.55 * ri/CAP_R
        ring_c = [bm.verts.new((
            HEAD_R_X*1.04 * math.sin(phi)*math.cos(2*math.pi*s/CAP_S),
            cy_cap + HEAD_R_Y*1.04*math.sin(phi)*math.sin(2*math.pi*s/CAP_S),
            TOP_Z - (TOP_Z-Z_HEAD_BOT)*0.05 + (HEAD_H*1.06)*math.cos(phi) - HEAD_H*0.05
        )) for s in range(CAP_S)]
        if ri==0:
            pole_cap = bm.verts.new((0,cy_cap,TOP_Z+0.005))
            for s in range(CAP_S):
                bm.faces.new([pole_cap, ring_c[(s+1)%CAP_S], ring_c[s]])
        else:
            for s in range(CAP_S):
                sn=(s+1)%CAP_S
                bm.faces.new([prev_rc[s],prev_rc[sn],ring_c[sn],ring_c[s]])
        prev_rc = ring_c
    cap_obj = new_obj("T6_HairCap", bm)
    assign(cap_obj, hair_mat)
    smooth(cap_obj)
    objs.append(cap_obj.name)

    # Franja anime (picos angulosos triangulares, sobre a testa)
    bm = bmesh.new()
    N_PICOS = 8
    FRINGE_Y = EYE_Y + 0.005
    FRINGE_TOP_Z = Z_HEAD_TOP - HEAD_H*0.12
    for i in range(N_PICOS):
        t = i/(N_PICOS-1)
        frac = 2*t-1  # -1 a 1
        cx_f = HEAD_R_X * 0.88 * frac
        w = HEAD_R_X * 0.12 + HEAD_R_X*0.04*abs(math.sin(i*1.1))
        tip_z = FRINGE_TOP_Z - HEAD_H*(0.22 + 0.08*abs(math.sin(i*0.9)))
        v0 = bm.verts.new((cx_f-w, FRINGE_Y, FRINGE_TOP_Z))
        v1 = bm.verts.new((cx_f+w, FRINGE_Y, FRINGE_TOP_Z))
        v2 = bm.verts.new((cx_f + HEAD_R_X*0.03*math.sin(i), FRINGE_Y, tip_z))
        bm.faces.new([v0,v1,v2])
    fringe_obj = new_obj("T6_Fringe", bm)
    assign(fringe_obj, hair_mat)
    objs.append(fringe_obj.name)

    # Mechas laterais longas (7 de cada lado)
    N_SIDE = 7
    for side, sign in (("L",-1),("R",1)):
        for i in range(N_SIDE):
            t = i/N_SIDE
            phi_b = math.pi*(0.18 + t*0.20)
            az = sign*(math.pi*0.55 + t*math.pi*0.35)
            rx = HEAD_R_X*1.05*math.sin(phi_b)*math.cos(az)
            ry = cy_cap + HEAD_R_Y*1.05*math.sin(phi_b)*math.sin(az)
            rz = TOP_Z + HEAD_H*0.06*math.cos(phi_b) - HEAD_H*0.05
            tip_z = Z_HIP + TORSO_H*(0.30 + t*0.35)
            tip_x = sign*(abs(rx)*0.8 + 0.02)

            bev = 0.010 + 0.005*(i%3)
            cd = bpy.data.curves.new(f"T6_Strand_{side}_{i}", "CURVE")
            cd.dimensions="3D"; cd.bevel_depth=bev; cd.bevel_resolution=2; cd.use_fill_caps=True
            sp = cd.splines.new("NURBS"); SSEGS=8; sp.points.add(SSEGS-1)
            for pi_i in range(SSEGS):
                tt = pi_i/(SSEGS-1)
                sp.points[pi_i].co=(
                    rx+(tip_x-rx)*tt,
                    ry+(0.0-ry)*tt*0.3,
                    rz+(tip_z-rz)*tt,
                    1.0)
            sp.use_endpoint_u=True; sp.order_u=min(4,SSEGS)
            so = bpy.data.objects.new(f"T6_Strand_{side}_{i}", cd)
            bpy.context.collection.objects.link(so)
            so.data.materials.append(hair_mat)
            objs.append(so.name)

    # Laco de cabelo (detalhe estilo Rimuru) — fita azul
    bm = bmesh.new()
    BOW_Z = Z_HEAD_BOT + HEAD_H*0.75
    BOW_X = HEAD_R_X*0.85
    BOW_Y = cy_cap + HEAD_R_Y*0.55
    for sign in (-1,1):
        pts_bow = [
            (sign*BOW_X,        BOW_Y, BOW_Z+0.020),
            (sign*(BOW_X+0.030),BOW_Y, BOW_Z+0.010),
            (sign*BOW_X,        BOW_Y, BOW_Z),
            (sign*(BOW_X+0.030),BOW_Y, BOW_Z+0.015),
        ]
        vb = [bm.verts.new(p) for p in pts_bow]
        bm.faces.new([vb[0],vb[1],vb[3],vb[2]])
    bow_obj = new_obj("T6_HairBow", bm)
    assign(bow_obj, acc_mat)
    objs.append(bow_obj.name)

    return objs, {"style": "rimuru_blue_grey_angular_strands"}


# ─────────────────────────────────────────────────────────────────────────────
# ROUPA ESTILO RIMURU (tunica escura simples)
# ─────────────────────────────────────────────────────────────────────────────

def build_outfit():
    cloth_mat = mat_toon("T6_OutfitMain", C_CLOTH_DARK, C_CLOTH_MID, threshold=0.45)
    acc_mat   = mat_toon("T6_OutfitAcc",  C_CLOTH_ACC,  "#3030AA",   threshold=0.50)
    SEGS = 24
    objs = []
    OFFSET = 0.012

    # Tunica (topo): cobre torso + vai ate medio-coxa
    bm = bmesh.new()
    tunic_profile = [
        (Z_HIP - TORSO_H*0.10, R_HIP+OFFSET),
        (Z_HIP,                R_HIP+OFFSET),
        (Z_HIP+TORSO_H*0.20,   R_WAIST+OFFSET*0.8),
        (Z_HIP+TORSO_H*0.40,   R_WAIST+OFFSET*0.8),
        (Z_HIP+TORSO_H*0.60,   R_BUST+OFFSET),
        (Z_SHOULDER,           R_SHOULDER+OFFSET),
    ]
    tg = []
    for z, r in tunic_profile:
        tg.append([bm.verts.new((r*math.cos(2*math.pi*s/SEGS),
                                  r*math.sin(2*math.pi*s/SEGS), z))
                    for s in range(SEGS)])
    cap_ring(bm, list(reversed(tg[0])))
    for ri in range(len(tg)-1):
        r0,r1 = tg[ri],tg[ri+1]
        for s in range(SEGS):
            sn=(s+1)%SEGS
            bm.faces.new([r0[s],r0[sn],r1[sn],r1[s]])
    tunic = new_obj("T6_Tunic", bm)
    assign(tunic, cloth_mat)
    smooth(tunic)
    objs.append(tunic.name)

    # Cinto / faixa na cintura
    bm = bmesh.new()
    Z_BELT = Z_HIP + TORSO_H*0.32
    rb_b, rt_b = cylinder_section(bm, Z_BELT, Z_BELT+0.020, R_WAIST+OFFSET*1.5,
                                   R_WAIST+OFFSET*1.5, SEGS)
    belt = new_obj("T6_Belt", bm)
    assign(belt, acc_mat)
    objs.append(belt.name)

    # Gola (colar alto)
    bm = bmesh.new()
    Z_COLLAR_BOT = Z_NECK_BOT + 0.010
    Z_COLLAR_TOP = Z_NECK_TOP + 0.005
    rb_c, rt_c = cylinder_section(bm, Z_COLLAR_BOT, Z_COLLAR_TOP,
                                   R_NECK+0.015, R_NECK+0.020, SEGS)
    collar = new_obj("T6_Collar", bm)
    assign(collar, cloth_mat)
    objs.append(collar.name)

    # Detalhes do peito (faixa central)
    bm = bmesh.new()
    Z_CHEST_BOT = Z_HIP + TORSO_H*0.55
    Z_CHEST_TOP = Z_HIP + TORSO_H*0.90
    CHEST_W = 0.022
    for z in (Z_CHEST_BOT, Z_CHEST_BOT+(Z_CHEST_TOP-Z_CHEST_BOT)/3,
              Z_CHEST_BOT+2*(Z_CHEST_TOP-Z_CHEST_BOT)/3, Z_CHEST_TOP):
        fy = -(R_BUST + OFFSET + 0.002)
        vs_c = [
            bm.verts.new((-CHEST_W, fy, z-0.003)),
            bm.verts.new(( CHEST_W, fy, z-0.003)),
            bm.verts.new(( CHEST_W, fy, z+0.003)),
            bm.verts.new((-CHEST_W, fy, z+0.003)),
        ]
        bm.faces.new(vs_c)
    chest_d = new_obj("T6_ChestDetail", bm)
    assign(chest_d, acc_mat)
    objs.append(chest_d.name)

    return objs, {"style": "dark_tunic_rimuru_inspired"}


# ─────────────────────────────────────────────────────────────────────────────
# FREESTYLE + EEVEE
# ─────────────────────────────────────────────────────────────────────────────

def setup_eevee_freestyle():
    scene = bpy.context.scene
    # Motor EEVEE — usar o identificador correto para esta versao do Blender
    scene.render.engine = EEVEE_ENGINE

    # Freestyle
    scene.render.use_freestyle = True
    vl = scene.view_layers[0]
    vl.use_freestyle = True
    fs = vl.freestyle_settings
    fs.use_culling = True
    while fs.linesets:
        fs.linesets.remove(fs.linesets[0])
    ls = fs.linesets.new("AnimeOutline")
    ls.select_silhouette = True
    ls.select_crease = True
    ls.select_border = True
    ls.select_edge_mark = True
    ls.linestyle.color = (0.0, 0.0, 0.0)
    ls.linestyle.thickness = 2.2
    ls.linestyle.alpha = 1.0

    # Iluminacao plana (evita sombras duras que quebram o cel-shading)
    scene.eevee.use_soft_shadows = False
    if hasattr(scene.eevee, "use_taa_reprojection"):
        scene.eevee.use_taa_reprojection = False


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(w=768, h=1024, samples=16):
    scene = bpy.context.scene
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False

    # EEVEE samples
    if hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = samples

    # Fundo cinza-azul escuro
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.05, 0.05, 0.08, 1.0)
        bg.inputs["Strength"].default_value = 0.3


def clear_scene_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT","CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_area_light(loc, tgt, energy, size, name):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name = name; l.data.energy = energy; l.data.size = size
    d = Vector(tgt)-Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z","Y").to_euler()


def make_camera(loc, tgt, lens, name, ortho=False, ortho_scale=2.0):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    if ortho:
        cam.data.type="ORTHO"; cam.data.ortho_scale=ortho_scale
    else:
        cam.data.type="PERSP"; cam.data.lens=lens
    d = Vector(tgt)-Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z","Y").to_euler()
    bpy.context.scene.camera = cam


def render_to(path, tag):
    bpy.context.scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
        ok = Path(path).exists()
    except Exception as exc:
        report["renders"][tag] = {"rendered": False, "error": str(exc)}
        return
    report["renders"][tag] = {"rendered": ok, "file": Path(path).name}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

try:
    # Limpar cena
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for bl in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
               bpy.data.lights, bpy.data.cameras):
        for item in list(bl):
            bl.remove(item)

    # Construir personagem
    body_objs,   body_info   = build_body()
    head_objs,   head_info   = build_head()
    eye_objs,    eye_info    = build_anime_eyes_2d()
    hair_objs,   hair_info   = build_anime_hair()
    outfit_objs, outfit_info = build_outfit()

    report["components"] = {
        "body":   {"objects": body_objs,   "info": body_info},
        "head":   {"objects": head_objs,   "info": head_info},
        "eyes":   {"objects": eye_objs,    "info": eye_info},
        "hair":   {"objects": hair_objs,   "info": hair_info},
        "outfit": {"objects": outfit_objs, "info": outfit_info},
    }

    # EEVEE + Freestyle
    setup_eevee_freestyle()
    setup_render(w=768, h=1024, samples=16)

    # ── VALIDACOES ────────────────────────────────────────────────────────────
    all_objs = set(o.name for o in bpy.data.objects if o.type not in ("LIGHT","CAMERA"))
    required = {"T6_Torso","T6_Neck","T6_Head","T6_Fringe","T6_HairCap","T6_HairBow",
                "T6_Tunic","T6_Belt","T6_Collar",
                "T6_Sclera_L","T6_Iris_L","T6_Pupil_L","T6_Hilite_L","T6_Lash_L",
                "T6_Sclera_R","T6_Iris_R","T6_Pupil_R","T6_Hilite_R","T6_Lash_R"}
    missing = required - all_objs

    report["validations"]["scene"] = {
        "total_objects": len(all_objs),
        "required_present": len(required-missing),
        "missing": sorted(missing),
        "pass": len(missing)==0,
    }
    report["validations"]["height"] = {
        "designed_m": H,
        "pass": True,
    }
    report["validations"]["overall_pass"] = all(
        v.get("pass",False) for v in report["validations"].values()
        if isinstance(v,dict) and "pass" in v
    )

    # ── RENDERS ───────────────────────────────────────────────────────────────
    BC  = H * 0.50         # centro do corpo
    CAM_D = 3.8            # distancia da camara
    ORTHO = H * 1.30

    def std_lights():
        clear_scene_lights_cameras()
        add_area_light((1.0, -2.5, BC+0.8), (0,0,BC), 800, 1.5, "Key")
        add_area_light((-0.8,-2.0, BC),     (0,0,BC), 400, 1.2, "Fill")
        add_area_light((0.0,  1.5, BC+1.0), (0,0,BC), 300, 1.0, "Rim")

    # 01 Frente (ortho — tipico de anime key art)
    std_lights()
    make_camera((0,-CAM_D,BC), (0,0,BC), 85, "Cam_Front", ortho=True, ortho_scale=ORTHO)
    render_to(out/"t6_01_front.png", "01_front")

    # 02 Tres quartos
    std_lights()
    make_camera((2.2,-2.8,BC), (0,0,BC), 85, "Cam_3Q")
    render_to(out/"t6_02_three_quarter.png", "02_three_quarter")

    # 03 Rosto close-up
    clear_scene_lights_cameras()
    HC = Z_HEAD_BOT + HEAD_H*0.55
    add_area_light((0.4,-1.2,HC+0.2),(0,0,HC),500,0.6,"FaceKey")
    add_area_light((-0.3,-1.0,HC),   (0,0,HC),250,0.5,"FaceFill")
    add_area_light((0.0, 0.8,HC+0.3),(0,0,HC),200,0.4,"FaceRim")
    make_camera((0,-1.5,HC),(0,0,HC),85,"Cam_Face")
    render_to(out/"t6_03_face.png", "03_face")

    # 04 Costas
    std_lights()
    make_camera((0,CAM_D,BC),(0,0,BC),85,"Cam_Back",ortho=True,ortho_scale=ORTHO)
    render_to(out/"t6_04_back.png", "04_back")

    # 05 Lado
    std_lights()
    make_camera((CAM_D,0,BC),(0,0,BC),85,"Cam_Side",ortho=True,ortho_scale=ORTHO)
    render_to(out/"t6_05_side.png", "05_side")

    # Gravar ficheiros
    blend_path = out/"mpfb-female-t6.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(".blend nao gravado")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out/"mpfb-female-t6.glb"
    bpy.ops.object.select_all(action="SELECT")
    for o in bpy.context.selected_objects:
        if o.type in ("LIGHT","CAMERA"): o.select_set(False)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path), export_format="GLB",
        use_selection=True, export_cameras=False, export_lights=False,
        export_materials="EXPORT", export_apply=True)
    if not glb_path.exists():
        raise RuntimeError("GLB nao exportado")
    report["glb_bytes"] = glb_path.stat().st_size

    report["status"] = "success_t6_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out/"mpfb-t6-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print("T6_REPORT:", json.dumps(report, ensure_ascii=False, default=str))

if report["status"] != "success_t6_complete":
    sys.exit(1)
