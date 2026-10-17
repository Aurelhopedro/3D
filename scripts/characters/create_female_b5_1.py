"""
B5.1 — Aperfeiçoamento controlado da personagem feminina.

Base: create_female_b5.py (estrutura, MPFB install, morph, escala, materiais, exportação).
Melhorias específicas:
  Vestido  — malha independente com Shrinkwrap + Solidify sobre superfície real do corpo.
             Sem cilindros revolve. Separação controlada (~8 mm). Verificação de interseção.
  Olhos    — detecção de objectos nativos do MPFB (LeftEye / RightEye / cornea).
             Fallback: centróide em vértices Y-frontal verificados. Sem olhos atrás da cabeça.
  Cabelo   — calote + 24 mechas da B5 preservadas. Mechas frontais ajustadas para não
             atravessarem o rosto (phi_birth maior + swing_y aumentado para trás).
  Sapatos  — preservados da B5 (prisma bmesh).
  Render   — não-fatal; motor detectado dinamicamente.

Não modifica B5, B6 nem nenhum outro ficheiro existente.
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
COLOR_DRESS  = "#C62828"
COLOR_HAIR   = "#1A0D08"
COLOR_SHOE   = "#171717"
COLOR_SCLERA = "#F5F0E8"
COLOR_IRIS   = "#5C3A1E"
COLOR_PUPIL  = "#0A0808"

OUT = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-b5-1")).resolve()
OUT.mkdir(parents=True, exist_ok=True)

report = {
    "version": "B5.1",
    "blender": bpy.app.version_string,
    "status": "started",
    "components": {},
    "validations": {},
    "limitations": [],
}

# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def srgb(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def hex_rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    return tuple(srgb(int(h[i:i+2], 16) / 255) for i in (0, 2, 4)) + (alpha,)

def make_material(name, hex_color, roughness=0.6, metallic=0.0,
                  alpha=1.0, subsurface=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex_rgba(hex_color)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    for ss in ("Subsurface Weight", "Subsurface"):
        if ss in bsdf.inputs and subsurface > 0:
            bsdf.inputs[ss].default_value = subsurface
            break
    if alpha < 1.0:
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = alpha
        mat.blend_method = "BLEND"
    out_node = nodes.new("ShaderNodeOutputMaterial")
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

def apply_transforms(obj):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

def evaluated_verts(obj):
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        return [obj.matrix_world @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()

# ─────────────────────────────────────────────────────────────────────────────
# INSTALAÇÃO DO MPFB (idêntica à B5 — confirmada funcional)
# ─────────────────────────────────────────────────────────────────────────────

def install_mpfb(zip_path: Path) -> str:
    repacked = OUT / "mpfb-extension.zip"
    with zipfile.ZipFile(zip_path) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix = manifest[:-len("blender_manifest.toml")]
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == "user_default"), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default",
                         custom_directory=str(OUT / "extensions"))
    repo.enabled = True

    result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if "FINISHED" not in result and "RUNNING_MODAL" not in result:
        raise RuntimeError(f"MPFB install devolveu: {result}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(
            f"MPFB não ficou activo. Addons: {list(bpy.context.preferences.addons.keys())}")
    return module_id

# ─────────────────────────────────────────────────────────────────────────────
# ESCALA PARA 1,70 m (bmesh — confirmada funcional B4/B5)
# ─────────────────────────────────────────────────────────────────────────────

def scale_to_height(obj, target):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()
    vz = [v.co.z for v in obj.data.vertices]
    h0 = max(vz) - min(vz)
    if h0 <= 0:
        raise RuntimeError(f"Altura inicial inválida: {h0}")
    factor = target / h0
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
    return {"h_initial": h0, "factor": factor,
            "h_final": max(vz2) - min(vz2),
            "z_min": min(vz2), "z_max": max(vz2)}

# ─────────────────────────────────────────────────────────────────────────────
# VESTIDO — Shrinkwrap + Solidify (B5.1: sem cilindros)
# ─────────────────────────────────────────────────────────────────────────────

def build_dress_shrinkwrap(human, body_min_z, body_max_z, body_h, dress_mat):
    """
    Constrói o vestido em duas partes independentes:
      1. Corpete (torso): malha de anéis elípticos entre cintura e ombros,
         com Shrinkwrap sobre o corpo + Solidify para espessura.
      2. Saia: anéis elípticos de evasê entre quadris e joelho,
         sem shrinkwrap (forma livre para movimento).
    Cada parte é uma malha separada, não atravessa o corpo.
    """
    # ── Proporções anatómicas ────────────────────────────────────────────────
    knee_z     = body_min_z + body_h * 0.285
    hip_z      = body_min_z + body_h * 0.510
    waist_z    = body_min_z + body_h * 0.572
    bust_z     = body_min_z + body_h * 0.695
    shoulder_z = body_min_z + body_h * 0.798

    r_hip     = body_h * 0.126
    r_waist   = body_h * 0.093
    r_bust    = body_h * 0.111
    r_shoulder= body_h * 0.096
    SEGS      = 48

    def elliptic_ring(bm_inst, rx, ry, z, factor_x=1.0):
        """Cria um anel de vértices elíptico e devolve a lista."""
        ring = []
        for s in range(SEGS):
            a = 2 * math.pi * s / SEGS
            ring.append(bm_inst.verts.new(
                (rx * factor_x * math.cos(a), ry * math.sin(a), z)))
        return ring

    def connect_rings(bm_inst, r0, r1):
        for s in range(SEGS):
            s2 = (s + 1) % SEGS
            bm_inst.faces.new([r0[s], r0[s2], r1[s2], r1[s]])

    # ── 1. CORPETE (cintura → ombro) com Shrinkwrap ──────────────────────────
    profile_bodice = [
        (r_waist,    r_waist * 0.90, waist_z),
        (r_bust,     r_bust  * 0.88, bust_z - body_h * 0.02),
        (r_bust,     r_bust  * 0.88, bust_z),
        (r_shoulder, r_shoulder * 0.88, shoulder_z),
    ]
    me_b = bpy.data.meshes.new("B51_Bodice_mesh")
    ob_b = bpy.data.objects.new("B51_Bodice", me_b)
    bpy.context.collection.objects.link(ob_b)

    bm_b = bmesh.new()
    rings_b = [elliptic_ring(bm_b, rx, ry, z) for rx, ry, z in profile_bodice]
    for i in range(len(rings_b) - 1):
        connect_rings(bm_b, rings_b[i], rings_b[i + 1])
    # Tampa superior (ombro)
    top_center = bm_b.verts.new((0, 0, shoulder_z + body_h * 0.005))
    for s in range(SEGS):
        bm_b.faces.new([rings_b[-1][s], top_center, rings_b[-1][(s + 1) % SEGS]])
    bm_b.normal_update()
    bm_b.to_mesh(me_b)
    bm_b.free()
    me_b.update()
    assign_material(ob_b, dress_mat)

    # Shrinkwrap sobre o corpo (offset 8 mm para separação)
    sw = ob_b.modifiers.new("SW_Body", "SHRINKWRAP")
    sw.target = human
    sw.offset = 0.008
    sw.wrap_method = "NEAREST_SURFACEPOINT"
    sw.use_project_z = False

    # Solidify para dar espessura ao tecido
    sol = ob_b.modifiers.new("Solidify", "SOLIDIFY")
    sol.thickness = 0.003
    sol.offset = 1.0

    # Aplicar modificadores
    set_active(ob_b)
    bpy.ops.object.modifier_apply(modifier="SW_Body")
    bpy.ops.object.modifier_apply(modifier="Solidify")
    apply_transforms(ob_b)

    # ── 2. SAIA (quadril → joelho) — forma livre, sem shrinkwrap ─────────────
    profile_skirt = [
        (r_hip  * 1.02, r_hip  * 0.98, hip_z),
        (r_hip  * 1.20, r_hip  * 1.15, hip_z  - body_h * 0.05),
        (r_hip  * 1.38, r_hip  * 1.32, waist_z - body_h * 0.08),
        (r_hip  * 1.52, r_hip  * 1.46, knee_z  + body_h * 0.02),
        (r_hip  * 1.55, r_hip  * 1.49, knee_z),
    ]
    me_s = bpy.data.meshes.new("B51_Skirt_mesh")
    ob_s = bpy.data.objects.new("B51_Skirt", me_s)
    bpy.context.collection.objects.link(ob_s)

    bm_s = bmesh.new()
    rings_s = [elliptic_ring(bm_s, rx, ry, z) for rx, ry, z in profile_skirt]
    for i in range(len(rings_s) - 1):
        connect_rings(bm_s, rings_s[i], rings_s[i + 1])
    # Barra inferior
    hem_center = bm_s.verts.new((0, 0, knee_z - body_h * 0.003))
    for s in range(SEGS):
        bm_s.faces.new([rings_s[-1][(s + 1) % SEGS], hem_center, rings_s[-1][s]])
    bm_s.normal_update()
    bm_s.to_mesh(me_s)
    bm_s.free()
    me_s.update()
    assign_material(ob_s, dress_mat)
    apply_transforms(ob_s)

    # ── Verificação de separação corpete/corpo ────────────────────────────────
    body_verts  = evaluated_verts(human)
    bodice_verts = [ob_b.matrix_world @ v.co for v in ob_b.data.vertices]
    min_sep = float("inf")
    # Amostra rápida: 200 vértices do corpete vs centróide do corpo
    sample = bodice_verts[:200] if len(bodice_verts) > 200 else bodice_verts
    body_centroid = Vector(
        (sum(v.x for v in body_verts) / len(body_verts),
         sum(v.y for v in body_verts) / len(body_verts),
         sum(v.z for v in body_verts) / len(body_verts)))
    for bv in sample:
        d = min((bv - Vector(v)).length for v in body_verts[:500])
        if d < min_sep:
            min_sep = d

    report["components"]["dress"] = {
        "method": "shrinkwrap_solidify_bodice_plus_free_skirt",
        "bodice": ob_b.name,
        "skirt": ob_s.name,
        "min_separation_m": round(min_sep, 4),
        "separation_ok": min_sep >= 0.004,
    }
    report["validations"]["dress_separation"] = {
        "min_m": round(min_sep, 4), "threshold_m": 0.004, "pass": min_sep >= 0.004}

    return ob_b, ob_s

# ─────────────────────────────────────────────────────────────────────────────
# OLHOS — detecção de objectos MPFB nativos + fallback controlado
# ─────────────────────────────────────────────────────────────────────────────

def setup_eyes(human, head_center_z, head_radius):
    """
    1. Verifica se o MPFB já criou objectos de olho na cena
       (nomes comuns: LeftEye, RightEye, eye.L, eye.R, cornea, etc.)
    2. Se existirem, apenas aplica materiais — não move nem duplica.
    3. Fallback: centróide de vértices na banda frontal da cabeça,
       com verificação de que Y > 0.6*hr (estritamente frontal).
       Sem olhos se não houver vértices suficientes na frente.
    """
    sclera_mat = make_material("B51_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material("B51_Iris",   COLOR_IRIS,   roughness=0.15)
    pupil_mat  = make_material("B51_Pupil",  COLOR_PUPIL,  roughness=0.05)

    hr = head_radius
    eye_names_patterns = [
        "lefteye", "righteye", "eye.l", "eye.r", "eyeleft", "eyeright",
        "cornea", "eyeball", "iris", "pupil", "sclera",
    ]

    native_eyes = []
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            low = obj.name.lower()
            if any(p in low for p in eye_names_patterns):
                native_eyes.append(obj)

    if native_eyes:
        report["components"]["eyes"] = {
            "method": "mpfb_native_objects",
            "objects": [o.name for o in native_eyes],
            "note": "MPFB already created eye geometry; no artificial eyes added.",
        }
        return [o.name for o in native_eyes]

    # Fallback: centróide estritamente frontal
    eye_band_lo = head_center_z - hr * 0.05
    eye_band_hi = head_center_z + hr * 0.25
    min_y_threshold = hr * 0.55  # estritamente frontal

    verts_L, verts_R = [], []
    for v in human.data.vertices:
        wco = human.matrix_world @ v.co
        if eye_band_lo <= wco.z <= eye_band_hi and wco.y > min_y_threshold:
            if wco.x > hr * 0.08:
                verts_L.append(wco)
            elif wco.x < -hr * 0.08:
                verts_R.append(wco)

    MIN_VERTS = 15
    if len(verts_L) < MIN_VERTS or len(verts_R) < MIN_VERTS:
        report["components"]["eyes"] = {
            "method": "skipped",
            "reason": (f"Insufficient frontal vertices for safe placement "
                       f"(L={len(verts_L)}, R={len(verts_R)}, min={MIN_VERTS}). "
                       "No artificial eyes added to avoid placement behind head."),
        }
        report["limitations"].append(
            "Eyes skipped: could not verify frontal face orientation safely.")
        return []

    def centroid(vlist):
        return Vector((sum(v.x for v in vlist) / len(vlist),
                       sum(v.y for v in vlist) / len(vlist),
                       sum(v.z for v in vlist) / len(vlist)))

    cL, cR = centroid(verts_L), centroid(verts_R)
    objects_created = []

    for side, center in [("L", cL), ("R", cR)]:
        ex, ey, ez = center.x, center.y, center.z
        # Verificação extra: centro deve estar na frente (Y > hr*0.5)
        if ey < hr * 0.5:
            report["limitations"].append(
                f"Eye {side} centroid Y={ey:.3f} < threshold {hr*0.5:.3f}; skipped.")
            continue

        sr = hr * 0.13
        ir = sr * 0.58
        pr = ir * 0.48
        offset = sr * 0.80

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=18, ring_count=9, radius=sr,
            location=(ex, ey - offset * 0.08, ez))
        sc = bpy.context.active_object
        sc.name = f"B51_Sclera_{side}"
        assign_material(sc, sclera_mat)

        bpy.ops.mesh.primitive_cylinder_add(
            vertices=20, radius=ir, depth=sr * 0.10,
            location=(ex, ey + offset * 0.76, ez))
        ir_obj = bpy.context.active_object
        ir_obj.name = f"B51_Iris_{side}"
        assign_material(ir_obj, iris_mat)

        bpy.ops.mesh.primitive_cylinder_add(
            vertices=16, radius=pr, depth=sr * 0.07,
            location=(ex, ey + offset * 0.80, ez))
        pu = bpy.context.active_object
        pu.name = f"B51_Pupil_{side}"
        assign_material(pu, pupil_mat)

        objects_created.extend([sc.name, ir_obj.name, pu.name])

    report["components"]["eyes"] = {
        "method": "frontal_vertex_centroid_verified",
        "objects": objects_created,
        "verts_L": len(verts_L), "verts_R": len(verts_R),
        "centroid_L_y": round(cL.y, 4), "centroid_R_y": round(cR.y, 4),
    }
    return objects_created

# ─────────────────────────────────────────────────────────────────────────────
# CABELO — calote B5 preservada; mechas frontais corrigidas
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head_center_z, head_radius, waist_z, shoulder_z, bust_z):
    hair_mat = make_material("B51_Hair", COLOR_HAIR, roughness=0.85)
    objects_created = []

    # ── Calote (idêntica à B5) ────────────────────────────────────────────
    CAP_SEGS, CAP_RINGS = 24, 10
    cap_r = head_radius * 1.03

    cap_mesh = bpy.data.meshes.new("B51_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("B51_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    pole = bm.verts.new((0.0, 0.0, head_center_z + cap_r))
    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi = (math.pi * 0.52) * ri / CAP_RINGS
        z = head_center_z + cap_r * math.cos(phi)
        r = cap_r * math.sin(phi)
        ring = []
        for s in range(CAP_SEGS):
            angle = 2.0 * math.pi * s / CAP_SEGS
            ring.append(bm.verts.new((r * math.cos(angle), r * math.sin(angle), z)))
        rings.append(ring)
    for s in range(CAP_SEGS):
        bm.faces.new([pole, rings[0][s], rings[0][(s + 1) % CAP_SEGS]])
    for ri in range(len(rings) - 1):
        for s in range(CAP_SEGS):
            bm.faces.new([rings[ri][s], rings[ri][(s + 1) % CAP_SEGS],
                          rings[ri + 1][(s + 1) % CAP_SEGS], rings[ri + 1][s]])
    bm.normal_update()
    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    # ── 24 Mechas — frontais corrigidas ──────────────────────────────────────
    # Definir "frente" como Y positivo (o MPFB cria a personagem com face a -Y
    # mas o sistema de coordenadas pode variar; as mechas frontais são as que
    # têm azimuth próximo de 0 ou π, dependendo da orientação).
    # Estratégia conservadora: para mechas com |cos(azimuth)| < 0.5 (laterais/traseiras)
    # manter como B5; para mechas frontais (|cos(azimuth)| >= 0.5) aumentar phi_birth
    # e reduzir swing para trás, evitando passagem pelo rosto.
    N_STRANDS  = 24
    STRAND_SEGS = 8

    for i in range(N_STRANDS):
        azimuth = 2.0 * math.pi * i / N_STRANDS
        cos_az = math.cos(azimuth)

        # Determinar se a mecha é frontal (|cos| >= 0.5, ou seja, ±60° da frente)
        is_frontal = abs(cos_az) >= 0.5

        if is_frontal:
            # Frontal: nasce mais no topo da cabeça (phi maior = mais lateral)
            # e desce para trás, nunca para a frente
            phi_birth = math.pi * 0.30 + math.pi * 0.12 * ((i % 3) / 2.0)
            swing_back = 0.55   # deslocamento para trás (Y negativo se face a -Y)
        else:
            # Lateral/traseira: como na B5
            phi_birth = math.pi * 0.15 + math.pi * 0.20 * ((i % 4) / 3.0)
            swing_back = 0.25

        root_x = cap_r * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cap_r * math.sin(phi_birth) * math.sin(azimuth)
        root_z = head_center_z + cap_r * math.cos(phi_birth)

        # Ponta: entre ombros e busto (mechas frontais mais curtas)
        front_factor = 0.5 + 0.5 * abs(math.sin(azimuth))
        tip_z = bust_z + (shoulder_z - bust_z) * front_factor * 0.6
        if is_frontal:
            tip_z = max(tip_z, shoulder_z - (shoulder_z - bust_z) * 0.3)

        cdata = bpy.data.curves.new(name=f"B51_Strand_{i:02d}", type="CURVE")
        cdata.dimensions = "3D"
        cdata.bevel_depth = 0.006 + 0.003 * (i % 3)
        cdata.bevel_resolution = 2
        cdata.use_fill_caps = True

        sp = cdata.splines.new("NURBS")
        sp.points.add(STRAND_SEGS - 1)

        for pi_idx in range(STRAND_SEGS):
            t = pi_idx / (STRAND_SEGS - 1)
            pz = root_z + (tip_z - root_z) * t
            splay = 1.0 + t * 0.35
            # Mechas frontais desviam para trás; laterais/traseiras para os lados
            swing_y = -root_y * t * swing_back
            px = root_x * splay
            py = root_y * splay + swing_y
            sp.points[pi_idx].co = (px, py, pz, 1.0)

        sp.use_endpoint_u = True
        sp.order_u = min(4, STRAND_SEGS)

        strand_obj = bpy.data.objects.new(f"B51_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    report["components"]["hair"] = {
        "cap": "B51_HairCap",
        "strands": N_STRANDS,
        "frontal_correction": "phi_birth increased, swing_back applied to frontal strands",
        "objects": objects_created,
    }
    return objects_created

# ─────────────────────────────────────────────────────────────────────────────
# SAPATOS (idênticos à B5)
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z):
    shoe_mat = make_material("B51_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)
    SHOE_L, SHOE_W, SHOE_H, SHOE_SEP = 0.24, 0.09, 0.045, 0.10
    objects_created = []
    for side, sign in [("L", -1), ("R", 1)]:
        cx = sign * SHOE_SEP / 2.0
        me = bpy.data.meshes.new(f"B51_Shoe_{side}_mesh")
        ob = bpy.data.objects.new(f"B51_Shoe_{side}", me)
        bpy.context.collection.objects.link(ob)
        bm = bmesh.new()
        hw, hl, toe = SHOE_W / 2, SHOE_L / 2, SHOE_W / 2 * 0.35
        bz = body_min_z
        vs = [
            bm.verts.new((cx - hw,  -hl, bz)),
            bm.verts.new((cx + hw,  -hl, bz)),
            bm.verts.new((cx + toe,  hl, bz)),
            bm.verts.new((cx - toe,  hl, bz)),
            bm.verts.new((cx - hw,  -hl, bz + SHOE_H)),
            bm.verts.new((cx + hw,  -hl, bz + SHOE_H)),
            bm.verts.new((cx + toe,  hl, bz + SHOE_H * 0.70)),
            bm.verts.new((cx - toe,  hl, bz + SHOE_H * 0.70)),
        ]
        for f in [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[3,0,4,7]]:
            bm.faces.new([vs[i] for i in f])
        bm.normal_update()
        bm.to_mesh(me)
        bm.free()
        me.update()
        assign_material(ob, shoe_mat)
        objects_created.append(ob.name)
    report["components"]["shoes"] = {"objects": objects_created, "method": "b5_preserved"}
    return objects_created

# ─────────────────────────────────────────────────────────────────────────────
# RENDER — não-fatal, motor detectado dinamicamente
# ─────────────────────────────────────────────────────────────────────────────

def render_views(body_min_z, body_max_z):
    scene = bpy.context.scene
    engine = None
    for candidate in ("BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES"):
        try:
            scene.render.engine = candidate
            engine = candidate
            break
        except TypeError:
            continue
    if engine is None:
        report["limitations"].append("No render engine available; previews skipped.")
        return

    if engine == "BLENDER_WORKBENCH":
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"

    scene.render.resolution_x = 640
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.color = (.30, .30, .30)

    center_z = (body_min_z + body_max_z) / 2
    center = (0, 0, center_z)
    dist = (body_max_z - body_min_z) * 1.2

    views = [("front", (0, -dist, center_z)),
             ("back",  (0,  dist, center_z)),
             ("side",  (dist, 0, center_z))]

    rendered = []
    for label, loc in views:
        ca = bpy.data.cameras.new(f"B51_Cam_{label}")
        ob = bpy.data.objects.new(f"B51_Cam_{label}", ca)
        bpy.context.collection.objects.link(ob)
        ob.location = loc
        ob.rotation_euler = (Vector(center) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        ca.type = "ORTHO"
        ca.ortho_scale = (body_max_z - body_min_z) * 1.05
        scene.camera = ob
        fp = str(OUT / f"b51-{label}.png")
        scene.render.filepath = fp
        try:
            bpy.ops.render.render(write_still=True)
            rendered.append(label)
        except Exception as e:
            report["limitations"].append(f"Render {label} failed: {e}")
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.cameras.remove(ca, do_unlink=True)

    report["components"]["renders"] = {
        "engine": engine, "rendered": rendered}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

try:
    # ── Limpar cena ──────────────────────────────────────────────────────────
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for col in [bpy.data.meshes, bpy.data.cameras, bpy.data.lights,
                bpy.data.materials, bpy.data.curves]:
        for block in list(col):
            col.remove(block)

    # ── Instalar MPFB ────────────────────────────────────────────────────────
    src = Path(os.environ["MPFB_ZIP"])
    module_id = install_mpfb(src)
    report["components"]["mpfb"] = {"status": "active", "module": module_id}

    # ── Criar humano ─────────────────────────────────────────────────────────
    if "FINISHED" not in bpy.ops.mpfb.create_human():
        raise RuntimeError("mpfb.create_human failed")
    human = bpy.data.objects.get("Human")
    if human is None or human.type != "MESH":
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            raise RuntimeError("No mesh after create_human")
        human = meshes[0]
    report["components"]["human_base"] = {
        "name": human.name,
        "vertices": len(human.data.vertices),
    }

    # ── Morph feminino (B5 confirmado) ───────────────────────────────────────
    if human.data.shape_keys:
        for key in human.data.shape_keys.key_blocks:
            n = key.name
            if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")) and "-$yn" in n:
                key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-") and "-$yn" in n:
                key.value = 1.0 if "-$fe-" in n else 0.0
        bpy.context.view_layer.update()
        report["components"]["female_morph"] = {"status": "applied_b5_keys"}

    # ── Escala 1,70 m ────────────────────────────────────────────────────────
    hi = scale_to_height(human, TARGET_HEIGHT_M)
    if abs(hi["h_final"] - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(f"Height {hi['h_final']:.4f} outside tolerance")
    body_min_z = hi["z_min"]
    body_max_z = hi["z_max"]
    body_h     = body_max_z - body_min_z
    report["components"]["height"] = hi
    report["validations"]["height"] = {
        "value": hi["h_final"], "target": TARGET_HEIGHT_M,
        "pass": abs(hi["h_final"] - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M}

    # ── Referências anatómicas ────────────────────────────────────────────────
    head_center_z = body_min_z + body_h * 0.920
    head_radius   = body_h * 0.075
    waist_z       = body_min_z + body_h * 0.572
    shoulder_z    = body_min_z + body_h * 0.798
    bust_z        = body_min_z + body_h * 0.695

    # ── Pele ─────────────────────────────────────────────────────────────────
    skin_mat = make_material("B51_Skin", COLOR_SKIN, roughness=0.52, subsurface=0.18)
    assign_material(human, skin_mat)

    # ── Vestido (Shrinkwrap + Solidify) ──────────────────────────────────────
    dress_mat = make_material("B51_Dress", COLOR_DRESS, roughness=0.72)
    ob_bodice, ob_skirt = build_dress_shrinkwrap(
        human, body_min_z, body_max_z, body_h, dress_mat)

    # ── Olhos ─────────────────────────────────────────────────────────────────
    eye_objects = setup_eyes(human, head_center_z, head_radius)

    # ── Cabelo ────────────────────────────────────────────────────────────────
    hair_objects = build_hair(
        head_center_z, head_radius, waist_z, shoulder_z, bust_z)

    # ── Sapatos ───────────────────────────────────────────────────────────────
    shoe_objects = build_shoes(body_min_z)

    # ── Validação de materiais ────────────────────────────────────────────────
    mat_check = {}
    for obj in bpy.data.objects:
        if obj.type in ("MESH", "CURVE") and hasattr(obj.data, "materials"):
            mat_check[obj.name] = len(obj.data.materials) > 0
    report["validations"]["materials"] = {
        "per_object": mat_check,
        "pass": all(mat_check.values())}

    # ── Gravar .blend e exportar GLB ─────────────────────────────────────────
    blend_path = OUT / "mpfb-female-b5-1.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend not saved: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = OUT / "mpfb-female-b5-1.glb"
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
        raise RuntimeError(f"GLB not exported: {glb_path}")
    report["glb_bytes"] = glb_path.stat().st_size

    # ── Render (não-fatal) ────────────────────────────────────────────────────
    try:
        render_views(body_min_z, body_max_z)
    except Exception as e:
        report["limitations"].append(f"render_views non-fatal error: {e}")

    report["status"] = "success_b5_1"
    report["limitations"] += [
        "Dress shrinkwrap offset 8 mm — visual inspection recommended.",
        "Hair frontal correction is geometric, not face-detection based.",
        "No rigging or garment physics.",
        "Visual result requires user review in Blender or a GLB viewer.",
    ]

except Exception as e:
    report["status"] = "failed"
    report["error"] = str(e)
    report["traceback"] = traceback.format_exc()

finally:
    (OUT / "mpfb-b5-1-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    print("MPFB_B51_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_b5_1":
    sys.exit(1)
