"""
B8 — Modelo Feminino Base (sem roupa).

Baseado integralmente no B7. Uma correccao adicional:

  CORRECCAO B8 — Olhos demasiado altos:
    No B7, head_center_z = body_min_z + body_h * 0.920 aponta para o TOPO
    da cabeca, nao para o centro geometrico. O fallback_eye_z era entao
    head_center_z + hr * 0.10, colocando os olhos acima das sobrancelhas.

    B8: os olhos sao posicionados a 85% da altura total do corpo (zona
    anatomica dos olhos), independentemente de head_center_z.
    fallback_eye_z = body_min_z + body_h * 0.850
    A banda de filtragem de vertices tambem e corrigida para esta zona.

    O relatorio regista agora as coordenadas reais dos olhos para
    validacao futura.

Tudo o resto e identico ao B7.
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

TARGET_HEIGHT_M    = 1.70
HEIGHT_TOLERANCE_M = 0.01

COLOR_SKIN   = "#A66A48"
COLOR_HAIR   = "#1A0D08"
COLOR_SHOE   = "#171717"
COLOR_SCLERA = "#F5F0E8"
COLOR_IRIS   = "#5C3A1E"
COLOR_PUPIL  = "#0A0808"

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-b8")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "b8",
    "status": "started",
    "components": {},
    "validations": {},
    "visual_limitations": [],
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS (idênticos ao B7)
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

def apply_all_transforms(obj):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

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
        "factor": factor,
        "h_final_m": max(vz2) - min(vz2),
        "body_min_z": min(vz2),
        "body_max_z": max(vz2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CABELO (idêntico ao B7 — correccao 3 mantida)
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head_center_z, head_radius, waist_z, shoulder_z, bust_z):
    hair_mat = make_material("B8_Hair", COLOR_HAIR, roughness=0.85)
    objects_created = []

    CAP_SEGS  = 24
    CAP_RINGS = 10
    cap_r = head_radius * 1.03

    cap_mesh = bpy.data.meshes.new("B8_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("B8_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    pole_z = head_center_z + cap_r
    pole = bm.verts.new((0.0, 0.0, pole_z))

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

        cdata = bpy.data.curves.new(name=f"B8_Strand_{i:02d}", type="CURVE")
        cdata.dimensions    = "3D"
        cdata.bevel_depth   = 0.006 + 0.003 * (i % 3)
        cdata.bevel_resolution = 2
        cdata.use_fill_caps = True

        sp = cdata.splines.new("NURBS")
        sp.points.add(STRAND_SEGS - 1)
        for pi_idx in range(STRAND_SEGS):
            t       = pi_idx / (STRAND_SEGS - 1)
            pz      = root_z + (tip_z - root_z) * t
            splay   = 1.0 + t * 0.35
            swing_y = swing_back * cap_r * t * 0.40
            px = root_x * splay
            py = root_y * splay + swing_y
            sp.points[pi_idx].co = (px, py, pz, 1.0)
        sp.use_endpoint_u = True
        sp.order_u = min(4, STRAND_SEGS)

        strand_obj = bpy.data.objects.new(f"B8_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# CORRECCAO B8 — OLHOS (posicao vertical corrigida)
# ─────────────────────────────────────────────────────────────────────────────
# No B7: eye_band centrada em head_center_z (topo da cabeca) → olhos altos.
# B8: zona dos olhos ancorada a 85% da altura total (proporcao anatomica).
# O relatorio regista as coordenadas reais para validacao.

def build_eyes(human, body_min_z, body_h, head_radius):
    sclera_mat = make_material("B8_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material("B8_Iris",   COLOR_IRIS,   roughness=0.15)
    pupil_mat  = make_material("B8_Pupil",  COLOR_PUPIL,  roughness=0.05)
    objects_created = []

    hr = head_radius

    # -- CORRECCAO B8: zona dos olhos a 85% da altura, nao ancorada ao topo --
    eye_center_z = body_min_z + body_h * 0.850
    eye_band_lo  = eye_center_z - hr * 0.18
    eye_band_hi  = eye_center_z + hr * 0.18

    verts_L, verts_R = [], []
    for v in human.data.vertices:
        wco = human.matrix_world @ v.co
        # Y negativo = frente no MPFB (correccao do B7 mantida)
        if eye_band_lo <= wco.z <= eye_band_hi and wco.y < -hr * 0.3:
            if wco.x > hr * 0.05:
                verts_L.append(wco)
            elif wco.x < -hr * 0.05:
                verts_R.append(wco)

    def centroid(vlist):
        if not vlist:
            return None
        cx = sum(v.x for v in vlist) / len(vlist)
        cy = sum(v.y for v in vlist) / len(vlist)
        cz = sum(v.z for v in vlist) / len(vlist)
        return Vector((cx, cy, cz))

    cL = centroid(verts_L)
    cR = centroid(verts_R)

    # fallback: proporcao anatomica directa
    fallback_eye_z = eye_center_z
    fallback_eye_y = -hr * 0.88
    fallback_eye_x =  hr * 0.38

    used_fallback_L = cL is None or len(verts_L) < 10
    used_fallback_R = cR is None or len(verts_R) < 10

    if used_fallback_L:
        cL = Vector(( fallback_eye_x, fallback_eye_y, fallback_eye_z))
    if used_fallback_R:
        cR = Vector((-fallback_eye_x, fallback_eye_y, fallback_eye_z))

    eye_coords = {}
    for side, center, used_fb in [("L", cL, used_fallback_L),
                                   ("R", cR, used_fallback_R)]:
        ex, ey, ez = center.x, center.y, center.z
        eye_coords[side] = {
            "x": round(ex, 4), "y": round(ey, 4), "z": round(ez, 4),
            "fallback_used": used_fb,
            "verts_found": len(verts_L) if side == "L" else len(verts_R),
        }

        sclera_r     = hr * 0.135
        iris_r       = sclera_r * 0.60
        pupil_r      = iris_r   * 0.50
        depth_offset = sclera_r * 0.82

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=20, ring_count=10, radius=sclera_r,
            location=(ex, ey - depth_offset * 0.1, ez)
        )
        sc = bpy.context.active_object
        sc.name = f"B8_Sclera_{side}"
        assign_material(sc, sclera_mat)

        bpy.ops.mesh.primitive_cylinder_add(
            vertices=24, radius=iris_r, depth=sclera_r * 0.12,
            location=(ex, ey - depth_offset * 0.78, ez)
        )
        ir = bpy.context.active_object
        ir.name = f"B8_Iris_{side}"
        assign_material(ir, iris_mat)

        bpy.ops.mesh.primitive_cylinder_add(
            vertices=20, radius=pupil_r, depth=sclera_r * 0.08,
            location=(ex, ey - depth_offset * 0.82, ez)
        )
        pu = bpy.context.active_object
        pu.name = f"B8_Pupil_{side}"
        assign_material(pu, pupil_mat)

        objects_created.extend([sc.name, ir.name, pu.name])

    return objects_created, eye_coords


# ─────────────────────────────────────────────────────────────────────────────
# CALÇADO (idêntico ao B7)
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z):
    shoe_mat = make_material("B8_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)
    SHOE_L   = 0.24
    SHOE_W   = 0.09
    SHOE_H   = 0.045
    SHOE_SEP = 0.10
    objects_created = []
    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx   = sign * SHOE_SEP / 2.0
        mesh_data = bpy.data.meshes.new(f"B8_Shoe_{side}_mesh")
        shoe_obj  = bpy.data.objects.new(f"B8_Shoe_{side}", mesh_data)
        bpy.context.collection.objects.link(shoe_obj)
        bm = bmesh.new()
        hw     = SHOE_W / 2.0
        hl     = SHOE_L / 2.0
        toe_hw = hw * 0.35
        bz     = body_min_z
        verts = [
            bm.verts.new((shoe_cx - hw,    -hl, bz)),
            bm.verts.new((shoe_cx + hw,    -hl, bz)),
            bm.verts.new((shoe_cx + toe_hw, hl, bz)),
            bm.verts.new((shoe_cx - toe_hw, hl, bz)),
            bm.verts.new((shoe_cx - hw,    -hl, bz + SHOE_H)),
            bm.verts.new((shoe_cx + hw,    -hl, bz + SHOE_H)),
            bm.verts.new((shoe_cx + toe_hw, hl, bz + SHOE_H * 0.70)),
            bm.verts.new((shoe_cx - toe_hw, hl, bz + SHOE_H * 0.70)),
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

    # ── MORPH FEMININO ────────────────────────────────────────────────────────
    keys = human.data.shape_keys.key_blocks
    local_z_before = evaluated_z_coords(human)

    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0

    bpy.context.view_layer.update()
    local_z_after = evaluated_z_coords(human)
    changed_v = sum(abs(a - b) > 1e-6 for a, b in zip(local_z_before, local_z_after))
    if changed_v == 0:
        raise RuntimeError("Morph feminino: nenhum vertice alterado")

    report["components"]["female_morph"] = {
        "changed_vertices": changed_v,
        "status": "confirmed_b7",
    }

    # ── ESCALA ────────────────────────────────────────────────────────────────
    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_height = hi["h_final_m"]
    body_min_z   = hi["body_min_z"]
    body_max_z   = hi["body_max_z"]

    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(
            f"Altura final ({world_height:.4f} m) fora da tolerancia")

    report["components"]["height"] = {
        "world_height_initial_m": hi["h_initial_m"],
        "scale_factor":           hi["factor"],
        "world_height_final_m":   world_height,
        "body_min_z":             body_min_z,
        "body_max_z":             body_max_z,
        "within_tolerance":       True,
        "method":                 "bmesh_vertex_scale",
    }

    # ── REFERÊNCIAS PROPORCIONAIS ─────────────────────────────────────────────
    body_h        = body_max_z - body_min_z
    waist_z       = body_min_z + body_h * 0.575
    bust_z        = body_min_z + body_h * 0.695
    shoulder_z    = body_min_z + body_h * 0.800
    head_center_z = body_min_z + body_h * 0.920  # topo da cabeca (para o cabelo)
    head_radius   = body_h * 0.075

    # ── PELE ──────────────────────────────────────────────────────────────────
    skin_mat = make_material("B8_Skin", COLOR_SKIN, roughness=0.52, subsurface=0.18)
    assign_material(human, skin_mat)
    report["components"]["skin"] = {"material": "B8_Skin", "subsurface": 0.18}

    # ── SEM VESTIDO ───────────────────────────────────────────────────────────
    report["components"]["dress"] = {
        "status": "omitted_by_design",
        "reason": "modelo base sem roupa",
    }

    # ── CABELO ────────────────────────────────────────────────────────────────
    hair_objects = build_hair(head_center_z, head_radius,
                              waist_z, shoulder_z, bust_z)
    report["components"]["hair"] = {
        "objects": hair_objects,
        "cap": "B8_HairCap",
        "strands": len(hair_objects) - 1,
        "color_hex": COLOR_HAIR,
    }

    # ── OLHOS (CORRECCAO B8) ──────────────────────────────────────────────────
    eye_objects, eye_coords = build_eyes(human, body_min_z, body_h, head_radius)
    report["components"]["eyes"] = {
        "objects": eye_objects,
        "positioning": "anatomical_85pct_height",
        "correction": "eye_zone_anchored_to_body_height_not_head_top",
        "coordinates": eye_coords,
        "eye_center_z_formula": "body_min_z + body_h * 0.850",
        "eye_center_z_value": round(body_min_z + body_h * 0.850, 4),
    }

    # ── CALÇADO ───────────────────────────────────────────────────────────────
    shoe_objects = build_shoes(body_min_z)
    report["components"]["shoes"] = {
        "objects": shoe_objects,
        "color_hex": COLOR_SHOE,
    }

    # ── VALIDAÇÕES ────────────────────────────────────────────────────────────
    expected = {
        "Human", "B8_HairCap",
        "B8_Sclera_L", "B8_Iris_L", "B8_Pupil_L",
        "B8_Sclera_R", "B8_Iris_R", "B8_Pupil_R",
        "B8_Shoe_L", "B8_Shoe_R",
    }
    for i in range(24):
        expected.add(f"B8_Strand_{i:02d}")

    scene_objects = {o.name for o in bpy.data.objects}
    missing = expected - scene_objects

    report["validations"]["scene_objects"] = {
        "expected_count": len(expected),
        "found_count":    len(scene_objects),
        "missing":        sorted(missing),
        "pass":           len(missing) == 0,
    }
    report["validations"]["world_height"] = {
        "value_m":     world_height,
        "target_m":    TARGET_HEIGHT_M,
        "tolerance_m": HEIGHT_TOLERANCE_M,
        "pass":        abs(world_height - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M,
    }

    mat_check = {}
    for oname in expected:
        obj = bpy.data.objects.get(oname)
        if obj and hasattr(obj.data, "materials"):
            mat_check[oname] = len(obj.data.materials) > 0
        elif obj:
            mat_check[oname] = True
        else:
            mat_check[oname] = False

    report["validations"]["materials"] = {
        "per_object": mat_check,
        "pass": all(mat_check.values()),
    }
    report["validations"]["overall_pass"] = all(
        v.get("pass", False) for v in report["validations"].values()
    )

    report["visual_limitations"] = [
        "Morph feminino experimental — nao utiliza presets oficiais do MPFB",
        "Cabelo em curvas NURBS com bevel — sem sistema de particulas",
        "Olhos por centroide de vertices com zona anatomica corrigida",
        "Calcado em prisma bmesh basico",
        "Nao ha rigging nem armature — pose T",
        "Sem vestuario — modelo base para fase posterior",
    ]

    # ── EXPORTAR ──────────────────────────────────────────────────────────────
    blend_path = out / "mpfb-female-b8.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-b8.glb"
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

    report["status"] = "success_b8_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"]  = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "mpfb-b8-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print("MPFB_B8_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_b8_complete":
    sys.exit(1)
