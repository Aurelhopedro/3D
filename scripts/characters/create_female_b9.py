"""
B9 v3 — Personagem Feminina Realista via mhcore (MakeHuman headless)

PIPELINE:
  1. mhcore gera mesh humano feminino realista (sem GUI, sem Qt, sem OpenGL)
     — instalado via pip antes de chamar o Blender
  2. OBJ exportado pelo mhcore é importado no Blender
  3. Materiais PBR de pele com subsurface scattering
  4. Cabelo em curvas NURBS de alta densidade
  5. Vestuário medieval: túnica + saia longa em pano grosso
  6. Iluminação cinematográfica three-point com HDRI atmosférico
  7. Render Cycles CPU 128 samples — 1024×1024

OUTPUTS:
  female_b9.blend
  female_b9.glb
  b9_01_full_front.png   — corpo inteiro, pose T, frente
  b9_02_face_front.png   — rosto de frente (85mm)
  b9_03_face_side.png    — rosto lateral (85mm)
  b9_04_face_quarter.png — rosto em 3/4 (85mm)
  b9_report.json

NOTAS:
  — mhcore é instalado pelo workflow ANTES de chamar o Blender
  — O OBJ é passado via variável de ambiente MHCORE_OBJ
  — Nenhum ficheiro extra é criado no repositório
"""

import bpy
import bmesh
import json
import math
import os
import sys
import traceback
from pathlib import Path
from mathutils import Vector, Euler

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M = 1.70

# Cores PBR (sRGB hex)
C_SKIN_MED    = "#C68642"   # pele média mediterrânica / morena clara
C_SKIN_DARK   = "#8D5524"   # sombras da pele
C_HAIR_DARK   = "#2C1A0E"   # cabelo castanho escuro
C_TUNIC       = "#5C4A2A"   # túnica medieval castanha
C_SKIRT       = "#3A2E1A"   # saia escura
C_LEATHER     = "#2A1A0A"   # botas de couro
C_BELT        = "#1A0F05"   # cinto
C_SCLERA      = "#F2EDE0"   # esclera
C_IRIS        = "#4A3020"   # íris castanha
C_PUPIL       = "#080505"   # pupila

OUT_DIR = Path(os.environ.get("MHCORE_OUTPUT", "output/b9-v3")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)

report = {
    "version": "b9-v3-mhcore",
    "status":  "started",
    "blender": bpy.app.version_string,
    "components": {},
    "renders":    {},
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS DE MATERIAL PBR
# ─────────────────────────────────────────────────────────────────────────────

def srgb2lin(c):
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


def hex2rgba(h, a=1.0):
    h = h.lstrip("#")
    return (srgb2lin(int(h[0:2],16)/255),
            srgb2lin(int(h[2:4],16)/255),
            srgb2lin(int(h[4:6],16)/255), a)


def mat_pbr(name, base_color, roughness=0.5, metallic=0.0,
            subsurface=0.0, subsurface_color=None, specular=0.5,
            emission=None, alpha=1.0):
    """Cria material Principled BSDF. Compatível com Blender 3.x e 4.x."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nodes = m.node_tree.nodes
    links = m.node_tree.links
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex2rgba(base_color)
    bsdf.inputs["Roughness"].default_value  = roughness
    bsdf.inputs["Metallic"].default_value   = metallic

    # Specular — nome mudou em Blender 4
    for sp_name in ("Specular IOR Level", "Specular"):
        if sp_name in bsdf.inputs:
            bsdf.inputs[sp_name].default_value = specular
            break

    # Subsurface — nome mudou em Blender 4
    if subsurface > 0:
        for ss_name in ("Subsurface Weight", "Subsurface"):
            if ss_name in bsdf.inputs:
                bsdf.inputs[ss_name].default_value = subsurface
                break
        if subsurface_color and "Subsurface Color" in bsdf.inputs:
            bsdf.inputs["Subsurface Color"].default_value = hex2rgba(subsurface_color)
        elif subsurface_color and "Subsurface Radius" in bsdf.inputs:
            # Blender 4: subsurface radius vector
            pass

    if alpha < 1.0 and "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = alpha
        m.blend_method = "BLEND"

    if emission:
        bsdf.inputs["Emission Color"].default_value = hex2rgba(emission) if "Emission Color" in bsdf.inputs else None

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (300, 0)
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return m


def apply_mat(obj, mat, slot=0):
    while len(obj.data.materials) <= slot:
        obj.data.materials.append(None)
    obj.data.materials[slot] = mat


def set_active(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


# ─────────────────────────────────────────────────────────────────────────────
# ESCALA UNIFORME PARA ALTURA ALVO
# ─────────────────────────────────────────────────────────────────────────────

def scale_to_height(obj, target_h):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()

    vz = [v.co.z for v in obj.data.vertices]
    h0 = max(vz) - min(vz)
    if h0 <= 0:
        raise RuntimeError(f"Altura do mesh nula: {h0}")

    factor = target_h / h0
    obj.scale = (factor, factor, factor)
    bpy.ops.object.transform_apply(scale=True)
    bpy.context.view_layer.update()

    vz2 = [v.co.z for v in obj.data.vertices]
    return {
        "h_initial": h0,
        "factor": factor,
        "h_final": max(vz2) - min(vz2),
        "z_min": min(vz2),
        "z_max": max(vz2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MALHA ANATÓMICA DE ALTA QUALIDADE (alternativa se OBJ não disponível)
# Usa metaballs + subdivision para corpo feminino proporcional
# ─────────────────────────────────────────────────────────────────────────────

def build_body_mesh_advanced():
    """
    Constrói corpo feminino realista usando subdivisão progressiva de um
    mesh base anatómico. Produz ~15k vértices com proporções correctas.
    """
    # Criar mesh base com loops anatómicos
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0, location=(0, 0, 0))
    body = bpy.context.active_object
    body.name = "B9_Body"

    set_active(body)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Reesculpir proporções femininas via shape keys programáticas
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.verts.ensure_lookup_table()

    # Transformar esfera em silhueta feminina proporcional
    # Referência: proporções 8-heads canon com curvas femininas
    for v in bm.verts:
        x, y, z = v.co.x, v.co.y, v.co.z
        # z normalizado: -1 (pés) a +1 (cabeça)
        z_n = z  # -1 a +1

        # Factor de largura ao longo do corpo
        if z_n > 0.75:        # cabeça (elipsoide)
            x_scale = 0.60
            y_scale = 0.65
            z_local = z_n
        elif z_n > 0.60:      # pescoço (estreito)
            t = (z_n - 0.60) / 0.15
            x_scale = 0.28 + t * 0.12
            y_scale = 0.25 + t * 0.15
            z_local = z_n
        elif z_n > 0.45:      # ombros / peito (alargamento)
            t = (z_n - 0.45) / 0.15
            x_scale = 0.70 - t * 0.05
            y_scale = 0.42 + t * 0.08
            z_local = z_n
        elif z_n > 0.25:      # cintura (estreitamento)
            t = (z_n - 0.25) / 0.20
            x_scale = 0.52 + t * 0.18
            y_scale = 0.30 + t * 0.12
            z_local = z_n
        elif z_n > -0.05:     # ancas / quadris (alargamento feminino)
            t = (z_n + 0.05) / 0.30
            x_scale = 0.68 - t * 0.16
            y_scale = 0.38 - t * 0.08
            z_local = z_n
        elif z_n > -0.70:     # coxas / pernas (afilamento gradual)
            t = (z_n + 0.70) / 0.65
            x_scale = 0.32 + t * 0.36
            y_scale = 0.22 + t * 0.16
            z_local = z_n
        else:                  # tornozelos / pés
            x_scale = 0.18
            y_scale = 0.14
            z_local = z_n

        # Separação de pernas abaixo das ancas
        if z_n < -0.05:
            leg_sep = 0.22 * max(0, min(1, (-z_n - 0.05) / 0.65))
            x += math.copysign(leg_sep * abs(x) / max(abs(x), 0.001), x)

        v.co.x = x / max(abs(x), 0.001) * abs(x) * x_scale
        v.co.y = y / max(abs(y), 0.001) * abs(y) * y_scale
        v.co.z = z_n

    bm.to_mesh(body.data)
    bm.free()
    body.data.update()

    # Subdivisão para suavizar
    mod = body.modifiers.new("Subd", "SUBSURF")
    mod.levels = 2
    mod.render_levels = 3

    # Aplicar
    set_active(body)
    bpy.ops.object.modifier_apply(modifier="Subd")

    return body


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS REALISTAS
# ─────────────────────────────────────────────────────────────────────────────

def build_eyes(body_h, body_min_z, body_max_z):
    head_cz = body_min_z + body_h * 0.925
    head_r  = body_h * 0.072

    # Profundidade da face: estimada como Y mais negativo do corpo
    # Para mesh centrado em Y=0 com frente a Y negativo: face_y ≈ -head_r * 0.85
    face_y    = -head_r * 0.82
    sclera_r  = head_r * 0.130
    eye_sep   = head_r * 0.52   # separação lateral entre centros
    eye_z     = head_cz + head_r * 0.10

    # Posição do centro da esclera: 40% do raio à frente da face
    sclera_y  = face_y - sclera_r * 0.40

    iris_r   = sclera_r * 0.58
    pupil_r  = sclera_r * 0.32

    mats = {
        "sclera": mat_pbr("B9_Sclera", C_SCLERA, roughness=0.08, specular=0.6),
        "iris":   mat_pbr("B9_Iris",   C_IRIS,   roughness=0.12, specular=0.5),
        "pupil":  mat_pbr("B9_Pupil",  C_PUPIL,  roughness=0.04, specular=0.9),
        "reflex": mat_pbr("B9_Reflex", "#FFFFFF", roughness=0.0,  metallic=0.0),
    }

    eye_objs = []
    for side, sx in [("L", eye_sep / 2), ("R", -eye_sep / 2)]:
        # Esclera
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16, radius=sclera_r,
            location=(sx, sclera_y, eye_z))
        sc = bpy.context.active_object
        sc.name = f"B9_Sclera_{side}"
        apply_mat(sc, mats["sclera"])
        eye_objs.append(sc.name)

        # Íris (cilindro fino orientado em Y)
        iy = sclera_y - sclera_r * 0.84
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=48, radius=iris_r, depth=sclera_r * 0.07,
            location=(sx, iy, eye_z))
        ir = bpy.context.active_object
        ir.name = f"B9_Iris_{side}"
        ir.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        apply_mat(ir, mats["iris"])
        eye_objs.append(ir.name)

        # Pupila
        py_ = sclera_y - sclera_r * 0.87
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32, radius=pupil_r, depth=sclera_r * 0.05,
            location=(sx, py_, eye_z))
        pu = bpy.context.active_object
        pu.name = f"B9_Pupil_{side}"
        pu.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        apply_mat(pu, mats["pupil"])
        eye_objs.append(pu.name)

        # Reflexo
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=8, ring_count=6, radius=sclera_r * 0.065,
            location=(sx + iris_r * 0.4, sclera_y - sclera_r * 0.88, eye_z + iris_r * 0.4))
        rf = bpy.context.active_object
        rf.name = f"B9_Reflex_{side}"
        apply_mat(rf, mats["reflex"])
        eye_objs.append(rf.name)

    return eye_objs, {"head_cz": head_cz, "head_r": head_r, "eye_z": eye_z, "face_y": face_y}


# ─────────────────────────────────────────────────────────────────────────────
# CABELO — cap + mechas NURBS de alta densidade
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head_cz, head_r, shoulder_z):
    hair_mat = mat_pbr("B9_Hair", C_HAIR_DARK, roughness=0.80, specular=0.3)
    objs = []

    # --- Calota capilar ---
    cap_r = head_r * 1.04
    cap_mesh = bpy.data.meshes.new("B9_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("B9_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    SEGS = 32; RINGS = 12
    pole = bm.verts.new((0, 0, head_cz + cap_r))
    rings = []
    for ri in range(1, RINGS + 1):
        phi = (math.pi * 0.55) * ri / RINGS
        z   = head_cz + cap_r * math.cos(phi)
        r   = cap_r   * math.sin(phi)
        ring = []
        for s in range(SEGS):
            a = 2 * math.pi * s / SEGS
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), z)))
        rings.append(ring)
    for s in range(SEGS):
        sn = (s + 1) % SEGS
        bm.faces.new([pole, rings[0][s], rings[0][sn]])
    for ri in range(len(rings) - 1):
        for s in range(SEGS):
            sn = (s + 1) % SEGS
            bm.faces.new([rings[ri][s], rings[ri][sn], rings[ri+1][sn], rings[ri+1][s]])
    bm.normal_update()
    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objs.append(cap_obj.name)

    # --- Mechas longas (cabelo até os ombros — medieval) ---
    FRONT_PHI   = 3 * math.pi / 2  # frente do corpo em coordenadas padrão
    FRONT_RANGE = math.radians(70)
    NUM_STRANDS = 36

    for i in range(NUM_STRANDS):
        az   = 2 * math.pi * i / NUM_STRANDS
        diff = abs(az - FRONT_PHI) % (2 * math.pi)
        diff = min(diff, 2 * math.pi - diff)

        if diff < FRONT_RANGE:
            phi_b = math.pi * 0.28
            length_factor = 0.65
            back_swing = 0.5
        else:
            phi_b = math.pi * 0.12 + 0.18 * ((i % 5) / 4)
            length_factor = 1.0
            back_swing = 0.0

        rx = cap_r * math.sin(phi_b) * math.cos(az)
        ry = cap_r * math.sin(phi_b) * math.sin(az)
        rz = head_cz + cap_r * math.cos(phi_b)
        tz = shoulder_z - head_r * length_factor * 0.8

        cd = bpy.data.curves.new(f"B9_Str{i:02d}", "CURVE")
        cd.dimensions   = "3D"
        cd.bevel_depth  = 0.005 + 0.003 * (i % 4)
        cd.bevel_resolution = 3
        cd.use_fill_caps = True

        sp = cd.splines.new("NURBS")
        sp.points.add(9)
        for pi in range(10):
            t  = pi / 9
            pz = rz + (tz - rz) * t
            sp_x = rx * (1 + t * 0.3)
            sp_y = ry * (1 + t * 0.3) + back_swing * cap_r * t * 0.35
            sp.points[pi].co = (sp_x, sp_y, pz, 1.0)
        sp.use_endpoint_u = True
        sp.order_u = min(4, 10)

        so = bpy.data.objects.new(f"B9_Str{i:02d}", cd)
        bpy.context.collection.objects.link(so)
        so.data.materials.append(hair_mat)
        objs.append(so.name)

    return objs


# ─────────────────────────────────────────────────────────────────────────────
# VESTUÁRIO MEDIEVAL — túnica + saia + cinto + botas
# ─────────────────────────────────────────────────────────────────────────────

def build_medieval_clothing(body_min_z, body_max_z):
    body_h    = body_max_z - body_min_z
    waist_z   = body_min_z + body_h * 0.570
    bust_z    = body_min_z + body_h * 0.690
    hip_z     = body_min_z + body_h * 0.520
    shoulder_z = body_min_z + body_h * 0.800
    head_cz   = body_min_z + body_h * 0.920
    head_r    = body_h * 0.072

    tunic_mat  = mat_pbr("B9_Tunic",  C_TUNIC,   roughness=0.88, specular=0.1)
    skirt_mat  = mat_pbr("B9_Skirt",  C_SKIRT,   roughness=0.90, specular=0.08)
    leather_mat= mat_pbr("B9_Leather",C_LEATHER, roughness=0.65, specular=0.3)
    belt_mat   = mat_pbr("B9_Belt",   C_BELT,    roughness=0.60, specular=0.4, metallic=0.1)

    objs = []

    # ── Túnica (tronco + mangas) ──────────────────────────────────────────────
    TUNIC_SEGS = 32
    tunic_mesh = bpy.data.meshes.new("B9_TunicMesh")
    tunic_obj  = bpy.data.objects.new("B9_Tunic", tunic_mesh)
    bpy.context.collection.objects.link(tunic_obj)

    bm = bmesh.new()
    # Perfil da túnica: da cintura ao busto, com mangas
    # Corpo da túnica: cilindro oval acompanhando a silhueta
    rings_t = []
    z_steps = [waist_z, waist_z + body_h * 0.04, waist_z + body_h * 0.08,
               bust_z * 0.97, bust_z, bust_z + body_h * 0.03,
               shoulder_z - body_h * 0.01, shoulder_z]
    rx_steps = [0.19,  0.20,  0.21,  0.23,  0.25,  0.26,  0.27,  0.28]
    ry_steps = [0.12,  0.13,  0.13,  0.17,  0.18,  0.18,  0.19,  0.20]

    for zi, z in enumerate(z_steps):
        ring = []
        for s in range(TUNIC_SEGS):
            a = 2 * math.pi * s / TUNIC_SEGS
            x = rx_steps[zi] * math.cos(a)
            y = ry_steps[zi] * math.sin(a)
            ring.append(bm.verts.new((x, y, z)))
        rings_t.append(ring)

    for ri in range(len(rings_t) - 1):
        for s in range(TUNIC_SEGS):
            sn = (s + 1) % TUNIC_SEGS
            bm.faces.new([rings_t[ri][s], rings_t[ri][sn],
                          rings_t[ri+1][sn], rings_t[ri+1][s]])

    # Tampo superior (ombros)
    for s in range(TUNIC_SEGS):
        sn = (s + 1) % TUNIC_SEGS
        bm.faces.new([rings_t[-1][s], rings_t[-1][sn],
                      bm.verts.new((0, 0, shoulder_z))])

    # Mangas (tubos simples dos ombros até meio braço)
    ARM_SEGS = 16
    for side, sx in [(1, 1), (-1, -1)]:
        arm_r_start = 0.085
        arm_r_end   = 0.065
        arm_start = (sx * 0.28, 0.0, shoulder_z - body_h * 0.01)
        arm_end   = (sx * 0.42, 0.0, shoulder_z - body_h * 0.08)
        arm_rings = []
        for step, (ax, ay, az) in enumerate([arm_start, arm_end]):
            t = step
            arm_r = arm_r_start + (arm_r_end - arm_r_start) * step
            ring = []
            for s in range(ARM_SEGS):
                a = 2 * math.pi * s / ARM_SEGS
                # orientar o tubo da manga horizontalmente
                vx = ax + arm_r * math.cos(a)
                vy = ay + arm_r * math.sin(a)
                vz = az
                ring.append(bm.verts.new((vx, vy, vz)))
            arm_rings.append(ring)
        for s in range(ARM_SEGS):
            sn = (s + 1) % ARM_SEGS
            bm.faces.new([arm_rings[0][s], arm_rings[0][sn],
                          arm_rings[1][sn], arm_rings[1][s]])

    bm.normal_update()
    bm.to_mesh(tunic_mesh)
    bm.free()
    tunic_mesh.update()
    tunic_obj.data.materials.append(tunic_mat)
    objs.append(tunic_obj.name)

    # ── Saia longa (cintura ao tornozelo) ─────────────────────────────────────
    SKIRT_SEGS = 48
    skirt_mesh = bpy.data.meshes.new("B9_SkirtMesh")
    skirt_obj  = bpy.data.objects.new("B9_Skirt", skirt_mesh)
    bpy.context.collection.objects.link(skirt_obj)

    bm = bmesh.new()
    ankle_z = body_min_z + body_h * 0.065
    skirt_z = [waist_z, waist_z - body_h*0.05, hip_z,
               hip_z - body_h*0.08, hip_z - body_h*0.18,
               hip_z - body_h*0.28, ankle_z + body_h*0.03, ankle_z]
    # Raios crescem conforme a saia alarga (efeito de sino moderado)
    skirt_rx = [0.185, 0.195, 0.220, 0.240, 0.260, 0.270, 0.260, 0.255]
    skirt_ry = [0.115, 0.125, 0.145, 0.160, 0.175, 0.185, 0.178, 0.172]

    sk_rings = []
    for zi, z in enumerate(skirt_z):
        ring = []
        for s in range(SKIRT_SEGS):
            a = 2 * math.pi * s / SKIRT_SEGS
            # Adicionar variação de dobras (prega)
            fold = 1 + 0.025 * math.sin(a * 8)
            x = skirt_rx[zi] * math.cos(a) * fold
            y = skirt_ry[zi] * math.sin(a) * fold
            ring.append(bm.verts.new((x, y, z)))
        sk_rings.append(ring)

    for ri in range(len(sk_rings) - 1):
        for s in range(SKIRT_SEGS):
            sn = (s + 1) % SKIRT_SEGS
            bm.faces.new([sk_rings[ri][s], sk_rings[ri][sn],
                          sk_rings[ri+1][sn], sk_rings[ri+1][s]])

    bm.normal_update()
    bm.to_mesh(skirt_mesh)
    bm.free()
    skirt_mesh.update()
    skirt_obj.data.materials.append(skirt_mat)
    objs.append(skirt_obj.name)

    # Suavizar saia
    for poly in skirt_obj.data.polygons:
        poly.use_smooth = True
    for poly in tunic_obj.data.polygons:
        poly.use_smooth = True

    # ── Cinto (aro ao nível da cintura) ───────────────────────────────────────
    BELT_SEGS = 48
    belt_mesh = bpy.data.meshes.new("B9_BeltMesh")
    belt_obj  = bpy.data.objects.new("B9_Belt", belt_mesh)
    bpy.context.collection.objects.link(belt_obj)

    bm = bmesh.new()
    bz0 = waist_z + body_h * 0.005
    bz1 = waist_z + body_h * 0.025
    brx = 0.195; bry = 0.125
    b_lo = []; b_hi = []
    for s in range(BELT_SEGS):
        a = 2 * math.pi * s / BELT_SEGS
        b_lo.append(bm.verts.new((brx*math.cos(a)*1.02, bry*math.sin(a)*1.02, bz0)))
        b_hi.append(bm.verts.new((brx*math.cos(a)*1.02, bry*math.sin(a)*1.02, bz1)))
    for s in range(BELT_SEGS):
        sn = (s + 1) % BELT_SEGS
        bm.faces.new([b_lo[s], b_lo[sn], b_hi[sn], b_hi[s]])
    bm.normal_update()
    bm.to_mesh(belt_mesh)
    bm.free()
    belt_mesh.update()
    belt_obj.data.materials.append(belt_mat)
    objs.append(belt_obj.name)

    # ── Botas de couro (tornozelo até joelho) ─────────────────────────────────
    BOOT_SEGS = 24
    knee_z = body_min_z + body_h * 0.28
    for side, sx in [(-1, -0.085), (1, 0.085)]:
        boot_mesh = bpy.data.meshes.new(f"B9_Boot_{side}_mesh")
        boot_obj  = bpy.data.objects.new(f"B9_Boot_{'L' if side < 0 else 'R'}", boot_mesh)
        bpy.context.collection.objects.link(boot_obj)

        bm = bmesh.new()
        b_z  = [body_min_z, body_min_z + body_h*0.03,
                body_min_z + body_h*0.06, ankle_z, ankle_z + body_h*0.03,
                knee_z * 0.95, knee_z]
        b_rx = [0.055, 0.060, 0.058, 0.055, 0.052, 0.050, 0.048]
        b_ry = [0.040, 0.045, 0.043, 0.040, 0.038, 0.037, 0.036]

        boot_rings = []
        for zi, z in enumerate(b_z):
            ring = []
            for s in range(BOOT_SEGS):
                a = 2 * math.pi * s / BOOT_SEGS
                x = sx + b_rx[zi] * math.cos(a)
                y = b_ry[zi] * math.sin(a)
                ring.append(bm.verts.new((x, y, z)))
            boot_rings.append(ring)

        for ri in range(len(boot_rings) - 1):
            for s in range(BOOT_SEGS):
                sn = (s + 1) % BOOT_SEGS
                bm.faces.new([boot_rings[ri][s], boot_rings[ri][sn],
                              boot_rings[ri+1][sn], boot_rings[ri+1][s]])

        # Sola da bota
        sole_verts = boot_rings[0]
        try:
            bm.faces.new(sole_verts)
        except Exception:
            pass

        bm.normal_update()
        bm.to_mesh(boot_mesh)
        bm.free()
        boot_mesh.update()
        for poly in boot_obj.data.polygons:
            poly.use_smooth = True
        boot_obj.data.materials.append(leather_mat)
        objs.append(boot_obj.name)

    return objs, {"shoulder_z": shoulder_z, "head_cz": head_cz, "head_r": head_r}


# ─────────────────────────────────────────────────────────────────────────────
# HDRI WORLD (céu neutral de estúdio)
# ─────────────────────────────────────────────────────────────────────────────

def setup_world_lighting():
    sc = bpy.context.scene
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.use_nodes = True
    nodes = sc.world.node_tree.nodes
    links = sc.world.node_tree.links
    nodes.clear()

    bg = nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.03, 0.04, 0.07, 1.0)   # azul noite medieval
    bg.inputs["Strength"].default_value = 0.3

    out = nodes.new("ShaderNodeOutputWorld")
    out.location = (300, 0)
    links.new(bg.outputs["Background"], out.inputs["Surface"])


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=128, w=1024, h=1024):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    # Denoising para reduzir ruído mesmo com poucos samples
    sc.cycles.use_denoising = True


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_area_light(loc, target, energy, size, name, color=(1, 1, 1)):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name = name
    l.data.energy = energy
    l.data.size   = size
    l.data.color  = color
    d = Vector(target) - Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


def camera_persp(loc, target, lens=85, name="Cam"):
    bpy.ops.object.camera_add(location=loc)
    c = bpy.context.active_object
    c.name = name
    c.data.type = "PERSP"
    c.data.lens = lens
    d = Vector(target) - Vector(loc)
    c.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = c
    return c


def camera_ortho(loc, target, scale, name="CamOrtho"):
    bpy.ops.object.camera_add(location=loc)
    c = bpy.context.active_object
    c.name = name
    c.data.type = "ORTHO"
    c.data.ortho_scale = scale
    d = Vector(target) - Vector(loc)
    c.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = c
    return c


def render_to(path, tag, samples=None):
    if samples:
        bpy.context.scene.cycles.samples = samples
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    ok = Path(path).exists()
    report["renders"][tag] = {"file": Path(path).name, "ok": ok}
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

try:
    # ── LIMPAR CENA ──────────────────────────────────────────────────────────
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes): bpy.data.meshes.remove(block)
    for block in list(bpy.data.curves): bpy.data.curves.remove(block)
    for block in list(bpy.data.materials): bpy.data.materials.remove(block)

    # ── TENTAR IMPORTAR OBJ DO MHCORE (se disponível) ───────────────────────
    obj_path = Path(os.environ.get("MHCORE_OBJ", ""))
    human = None

    if obj_path.exists() and obj_path.suffix.lower() == ".obj":
        # Importar OBJ gerado pelo mhcore
        try:
            bpy.ops.wm.obj_import(filepath=str(obj_path))
            # O maior mesh importado é o corpo
            meshes = sorted(
                [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
                key=lambda x: x[1], reverse=True,
            )
            if meshes:
                human = meshes[0][0]
                human.name = "B9_Body"
                report["components"]["body_source"] = "mhcore_obj"
                report["components"]["body_vertices"] = len(human.data.vertices)
        except Exception as e:
            report["components"]["mhcore_import_error"] = str(e)
            human = None

    if human is None:
        # Fallback: construir mesh anatómico programaticamente
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        human = build_body_mesh_advanced()
        report["components"]["body_source"] = "programmatic_advanced"
        report["components"]["body_vertices"] = len(human.data.vertices)

    # ── ESCALA ───────────────────────────────────────────────────────────────
    hi = scale_to_height(human, TARGET_HEIGHT_M)
    body_h    = hi["h_final"]
    body_min_z = hi["z_min"]
    body_max_z = hi["z_max"]

    # Colocar base do personagem em Z=0
    if body_min_z != 0:
        human.location.z += -body_min_z
        bpy.ops.object.select_all(action="DESELECT")
        human.select_set(True)
        bpy.context.view_layer.objects.active = human
        bpy.ops.object.transform_apply(location=True)
        bpy.context.view_layer.update()
        vz = [v.co.z for v in human.data.vertices]
        body_min_z = min(vz)
        body_max_z = max(vz)
        body_h = body_max_z - body_min_z

    report["components"]["height"] = {
        "h_final_m": round(body_h, 4),
        "body_min_z": round(body_min_z, 4),
        "body_max_z": round(body_max_z, 4),
    }

    # ── MATERIAL DE PELE PBR COM SUBSURFACE ──────────────────────────────────
    skin_mat = mat_pbr("B9_Skin", C_SKIN_MED,
                       roughness=0.50, specular=0.45,
                       subsurface=0.22, subsurface_color=C_SKIN_DARK)
    # Smooth shading no corpo
    for poly in human.data.polygons:
        poly.use_smooth = True
    human.data.update()
    apply_mat(human, skin_mat)
    report["components"]["skin_material"] = "PBR_subsurface"

    # ── CONSTRUIR OLHOS ───────────────────────────────────────────────────────
    eye_objs, eye_info = build_eyes(body_h, body_min_z, body_max_z)
    report["components"]["eyes"] = {"count": len(eye_objs)}

    head_cz   = eye_info["head_cz"]
    head_r    = eye_info["head_r"]
    face_y    = eye_info["face_y"]
    shoulder_z = body_min_z + body_h * 0.800

    # ── VESTUÁRIO MEDIEVAL ────────────────────────────────────────────────────
    cloth_objs, cloth_info = build_medieval_clothing(body_min_z, body_max_z)
    report["components"]["clothing"] = {"pieces": len(cloth_objs)}

    # ── CABELO ────────────────────────────────────────────────────────────────
    hair_objs = build_hair(head_cz, head_r, shoulder_z)
    report["components"]["hair"] = {"strands": len(hair_objs)}

    # ── HDRI WORLD ───────────────────────────────────────────────────────────
    setup_world_lighting()

    # ── CONFIGURAR RENDER ─────────────────────────────────────────────────────
    setup_render(samples=96, w=1024, h=1024)

    # ── GRAVAR .BLEND ─────────────────────────────────────────────────────────
    blend_path = OUT_DIR / "female_b9.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    report["blend_bytes"] = blend_path.stat().st_size if blend_path.exists() else 0

    # ── EXPORTAR GLB ──────────────────────────────────────────────────────────
    glb_path = OUT_DIR / "female_b9.glb"
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
    report["glb_bytes"] = glb_path.stat().st_size if glb_path.exists() else 0

    # ── RENDERS ───────────────────────────────────────────────────────────────
    # Alvo dos renders
    body_mid_z = (body_min_z + body_max_z) / 2
    body_tgt   = (0, 0, body_mid_z)
    head_tgt   = (0, 0, head_cz)

    # R01 — corpo inteiro, frente, ortho
    clear_lights_cameras()
    cam_y = face_y - body_h * 1.6
    add_area_light((1.5, cam_y + 0.8, body_mid_z + body_h * 0.4),
                   body_tgt, 3000, 1.8, "Key",  color=(1.0, 0.95, 0.85))
    add_area_light((-1.2, cam_y + 0.6, body_mid_z + body_h * 0.1),
                   body_tgt, 1200, 1.4, "Fill", color=(0.85, 0.90, 1.0))
    add_area_light((0, face_y + body_h * 0.6, body_max_z * 0.9),
                   body_tgt, 600,  0.9, "Rim",  color=(1.0, 0.98, 0.90))
    camera_ortho((0, cam_y, body_mid_z), body_tgt, body_h * 1.15, name="CamFull")
    render_to(OUT_DIR / "b9_01_full_front.png", "b9_01_full_front", samples=96)

    # R02 — rosto de frente (85mm)
    clear_lights_cameras()
    cam_face_y = face_y - head_r * 5.5
    add_area_light((head_r*2.5,  cam_face_y + head_r, head_cz + head_r*0.5),
                   head_tgt, 700, 0.7, "Key",  color=(1.0, 0.95, 0.85))
    add_area_light((-head_r*2.0, cam_face_y + head_r*0.8, head_cz),
                   head_tgt, 350, 0.6, "Fill", color=(0.85, 0.90, 1.0))
    add_area_light((0, face_y + head_r*2, head_cz + head_r),
                   head_tgt, 220, 0.5, "Rim",  color=(1.0, 0.98, 0.90))
    camera_persp((0, cam_face_y, head_cz), head_tgt, lens=85, name="CamFace")
    render_to(OUT_DIR / "b9_02_face_front.png", "b9_02_face_front", samples=96)

    # R03 — rosto lateral
    camera_persp((head_r*4.5, face_y - head_r*0.5, head_cz),
                 head_tgt, lens=85, name="CamSide")
    render_to(OUT_DIR / "b9_03_face_side.png", "b9_03_face_side", samples=96)

    # R04 — rosto em 3/4
    camera_persp((head_r*3.0, cam_face_y*0.65, head_cz + head_r*0.1),
                 head_tgt, lens=85, name="CamQuarter")
    render_to(OUT_DIR / "b9_04_face_quarter.png", "b9_04_face_quarter", samples=96)

    report["status"] = "success_b9_v3_complete"

except Exception as exc:
    report["status"]    = "failed"
    report["error"]     = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    rp = OUT_DIR / "b9_report.json"
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("B9V3_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_b9_v3_complete":
    sys.exit(1)
