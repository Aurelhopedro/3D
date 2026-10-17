"""
diagnose_mpfb_eyes.py — Investigação técnica da geometria ocular do MPFB.

OBJETIVO
--------
Determinar, de forma reproduzível e verificável, onde estão os olhos
na malha base do MPFB, sem recorrer a percentagens arbitrárias da altura.

ESTRATÉGIA (por ordem de prioridade)
--------------------------------------
1. Grupos de vértices "helper-l-eye" / "helper-r-eye" — geometry helpers
   nativos da malha HM08 do MPFB. Se existirem, os seus centróides são
   as coordenadas mais fiáveis possíveis.

2. Grupos de vértices "joint-l-eye" / "joint-r-eye" ou "eye.L" / "eye.R"
   — grupos de peso de osso; cobrem a região deformada pelo osso do olho.

3. Topologia facial directa: fatias Z na zona da cabeça, filtradas a Y
   negativo (face = Y-) com análise de simetria bilateral.
   NÃO usa a fatia com maior largura total — usa a que tem a menor
   variância lateral entre os dois lados (perfil de órbita ocular).

4. Ossos do armature (se o MPFB criar um): posição da cabeça dos ossos
   "eye.L" / "eye.R".

SAÍDAS
------
- JSON com todas as medições e o método usado
- 4 imagens de diagnóstico (frente, lado, 3/4, marcadores)
- Cabelo ocultado nas imagens se tiver a tapar o rosto

PRESERVAÇÃO
-----------
Não cria nem modifica os ficheiros B5, B7 ou B8.
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

out = Path(os.environ.get("MPFB_OUTPUT", "output/diagnose-eyes")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "script": "diagnose_mpfb_eyes",
    "version": "1.0",
    "blender": bpy.app.version_string,
    "status": "started",
    "mpfb": {},
    "scene_inventory": {},
    "body_geometry": {},
    "head_geometry": {},
    "vertex_groups": {},
    "eye_detection": {
        "method": None,
        "source": None,
        "left_eye": None,
        "right_eye": None,
        "confidence": "unverified",
        "requires_visual_validation": True,
    },
    "bone_eyes": {},
    "shape_keys": {},
    "mpfb_operators": [],
    "errors": [],
    "warnings": [],
}

def log(msg):
    print(f"[DIAG_EYES] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def verts_in_group(obj, group_name):
    """Devolve lista de Vector (world coords) dos vértices no grupo."""
    if group_name not in obj.vertex_groups:
        return []
    gidx = obj.vertex_groups[group_name].index
    wmat = obj.matrix_world
    result = []
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == gidx and g.weight > 0:
                result.append(wmat @ v.co)
                break
    return result


def centroid(vlist):
    if not vlist:
        return None
    n = len(vlist)
    return Vector((
        sum(v.x for v in vlist) / n,
        sum(v.y for v in vlist) / n,
        sum(v.z for v in vlist) / n,
    ))


def vec3_round(v, d=5):
    return [round(v.x, d), round(v.y, d), round(v.z, d)]


def setup_camera_and_lights(center, span):
    """Cria câmara ortográfica e duas luzes de área."""
    for obj in list(bpy.data.objects):
        if obj.name.startswith("DiagCam") or obj.name.startswith("DiagLight"):
            bpy.data.objects.remove(obj, do_unlink=True)

    bpy.ops.object.camera_add(location=(center.x, center.y - span * 2.8, center.z))
    cam = bpy.context.active_object
    cam.name = "DiagCam"
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = span * 1.6
    direction = Vector(center) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    for pos, energy in [
        ((center.x + span, center.y - span, center.z + span * 0.5), 800),
        ((center.x - span * 0.5, center.y - span * 0.5, center.z + span), 500),
    ]:
        bpy.ops.object.light_add(type="AREA", location=pos)
        light = bpy.context.active_object
        light.name = f"DiagLight_{pos[0]:.0f}"
        light.data.energy = energy
        light.data.size = span * 2
        direction = Vector(center) - light.location
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    return cam


def render_view(filename, cam_location, target, out_path, transparent=False):
    cam = bpy.context.scene.camera
    cam.location = cam_location
    direction = Vector(target) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 24
    sc.render.resolution_x = 512
    sc.render.resolution_y = 768
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = transparent
    sc.render.filepath = str(out_path / filename)

    bpy.ops.render.render(write_still=True)
    rendered = out_path / filename
    return rendered.exists()


def add_marker(location, name, color_hex="#FF0000", size=0.012):
    """Cria um marcador esférico temporário de diagnóstico."""
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=size, segments=10, ring_count=8, location=location
    )
    marker = bpy.context.active_object
    marker.name = name

    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    h = color_hex.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 2.0
    bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
    out_node = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    marker.data.materials.append(mat)
    return marker


# ─────────────────────────────────────────────────────────────────────────────
# INÍCIO
# ─────────────────────────────────────────────────────────────────────────────

try:

    # ── 1. INSTALAR MPFB (sequência idêntica à B7) ───────────────────────────
    log("A instalar MPFB...")
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
    log(f"Install: {install_result}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB não ficou activo: {module_id}")

    report["mpfb"]["module"] = module_id
    report["mpfb"]["status"] = "active"
    log(f"MPFB activo: {module_id}")

    # Tentar obter versão
    try:
        import mpfb
        report["mpfb"]["version"] = getattr(mpfb, "__version__", "unknown")
    except Exception:
        report["mpfb"]["version"] = "could_not_import_directly"

    # ── 2. INVENTÁRIO DOS OPERADORES MPFB ────────────────────────────────────
    log("A inventariar operadores MPFB...")
    mpfb_ops = []
    for mod_name in dir(bpy.ops):
        if "mpfb" in mod_name.lower():
            mod = getattr(bpy.ops, mod_name)
            for op_name in dir(mod):
                if not op_name.startswith("_"):
                    mpfb_ops.append(f"bpy.ops.{mod_name}.{op_name}()")
    report["mpfb_operators"] = sorted(mpfb_ops)
    eye_ops = [op for op in mpfb_ops if "eye" in op.lower()]
    report["mpfb"]["eye_related_operators"] = eye_ops
    log(f"Operadores MPFB: {len(mpfb_ops)} total, {len(eye_ops)} relacionados com olhos")
    for op in eye_ops:
        log(f"  {op}")

    # ── 3. LIMPAR CENA E CRIAR HUMANO ────────────────────────────────────────
    log("A limpar cena e criar humano...")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result = bpy.ops.mpfb.create_human()
    log(f"create_human: {result}")
    if "FINISHED" not in result:
        raise RuntimeError(f"mpfb.create_human devolveu: {result}")

    bpy.context.view_layer.update()

    # ── 4. INVENTÁRIO DA CENA ─────────────────────────────────────────────────
    log("A inventariar objectos da cena...")
    for obj in bpy.data.objects:
        info = {"type": obj.type, "location": [round(x, 4) for x in obj.location]}
        if obj.type == "MESH":
            verts = obj.data.vertices
            if verts:
                xs = [v.co.x for v in verts]
                ys = [v.co.y for v in verts]
                zs = [v.co.z for v in verts]
                info["vertex_count"] = len(verts)
                info["bbox"] = {
                    "x": [round(min(xs), 4), round(max(xs), 4)],
                    "y": [round(min(ys), 4), round(max(ys), 4)],
                    "z": [round(min(zs), 4), round(max(zs), 4)],
                }
                vg_names = [vg.name for vg in obj.vertex_groups]
                info["vertex_group_count"] = len(vg_names)
                info["vertex_groups_sample"] = vg_names[:30]
                info["eye_related_groups"] = [n for n in vg_names if "eye" in n.lower()]
                info["joint_groups"] = [n for n in vg_names if n.startswith("joint-")]
                info["helper_groups"] = [n for n in vg_names if n.startswith("helper-")]
                if obj.data.shape_keys:
                    info["shape_key_count"] = len(obj.data.shape_keys.key_blocks)
        elif obj.type == "ARMATURE":
            info["bones"] = [b.name for b in obj.data.bones]
            info["eye_bones"] = [b.name for b in obj.data.bones if "eye" in b.name.lower()]
        report["scene_inventory"][obj.name] = info
        log(f"  {obj.name}: {obj.type}")

    # ── 5. IDENTIFICAR MESH PRINCIPAL ─────────────────────────────────────────
    meshes = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True
    )
    if not meshes:
        raise RuntimeError("Nenhuma mesh encontrada após create_human")

    body = meshes[0][0]
    log(f"Mesh principal: {body.name} ({len(body.data.vertices)} vértices)")

    # ── 6. GEOMETRIA DO CORPO ─────────────────────────────────────────────────
    log("A medir geometria do corpo...")
    bpy.context.view_layer.update()
    verts_world = [body.matrix_world @ v.co for v in body.data.vertices]
    all_x = [v.x for v in verts_world]
    all_y = [v.y for v in verts_world]
    all_z = [v.z for v in verts_world]

    body_min_z = min(all_z)
    body_max_z = max(all_z)
    body_h     = body_max_z - body_min_z

    report["body_geometry"] = {
        "name":         body.name,
        "vertex_count": len(verts_world),
        "min_z":        round(body_min_z, 6),
        "max_z":        round(body_max_z, 6),
        "height":       round(body_h, 6),
        "width_x":      round(max(all_x) - min(all_x), 6),
        "depth_y":      round(max(all_y) - min(all_y), 6),
        "face_direction_y_negative_confirmed": min(all_y) < 0 and abs(min(all_y)) > abs(max(all_y)),
    }
    log(f"Corpo: altura={body_h:.4f}m  Z=[{body_min_z:.4f}, {body_max_z:.4f}]")
    log(f"Face aponta para Y-: {report['body_geometry']['face_direction_y_negative_confirmed']}")

    # ── 7. GEOMETRIA DA CABEÇA ─────────────────────────────────────────────────
    log("A analisar geometria da cabeça...")
    head_zone_lo = body_min_z + body_h * 0.80
    head_verts   = [v for v in verts_world if v.z >= head_zone_lo]

    hx = [v.x for v in head_verts]
    hy = [v.y for v in head_verts]
    hz = [v.z for v in head_verts]
    head_cx = (max(hx) + min(hx)) / 2 if hx else 0
    head_cy = (max(hy) + min(hy)) / 2 if hy else 0
    head_cz = (max(hz) + min(hz)) / 2 if hz else 0
    head_w  = (max(hx) - min(hx)) if hx else 0
    head_d  = (max(hy) - min(hy)) if hy else 0
    head_radius_est = (head_w + head_d) / 4

    report["head_geometry"] = {
        "zone_lo_z":        round(head_zone_lo, 4),
        "zone_hi_z":        round(body_max_z, 4),
        "vertex_count":     len(head_verts),
        "width_x":          round(head_w, 4),
        "depth_y":          round(head_d, 4),
        "center":           [round(head_cx, 4), round(head_cy, 4), round(head_cz, 4)],
        "radius_estimate":  round(head_radius_est, 4),
        "face_y_min":       round(min(hy), 4) if hy else None,
        "face_y_max":       round(max(hy), 4) if hy else None,
        "top_z":            round(max(hz), 4) if hz else None,
        "bottom_z":         round(min(hz), 4) if hz else None,
    }
    log(f"Cabeça: centro={[round(head_cx,3),round(head_cy,3),round(head_cz,3)]} raio≈{head_radius_est:.3f}")

    # ── 8. INVENTÁRIO DOS GRUPOS DE VÉRTICES ──────────────────────────────────
    log("A inventariar grupos de vértices relevantes...")
    all_groups = [vg.name for vg in body.vertex_groups]
    eye_groups   = [n for n in all_groups if "eye" in n.lower()]
    joint_groups = [n for n in all_groups if n.startswith("joint-")]
    helper_groups= [n for n in all_groups if n.startswith("helper-")]

    log(f"Grupos totais: {len(all_groups)}")
    log(f"Grupos 'eye': {eye_groups}")
    log(f"Grupos 'joint-': {joint_groups[:10]}")
    log(f"Grupos 'helper-': {helper_groups[:10]}")

    report["vertex_groups"] = {
        "total": len(all_groups),
        "all_names": all_groups,
        "eye_related": eye_groups,
        "joint_prefix": joint_groups,
        "helper_prefix": helper_groups,
    }

    # ── 9. DETECÇÃO DOS OLHOS — PRIORIDADE 1: helper-*-eye ───────────────────
    log("A tentar detecção por helper-*-eye...")
    eye_detected = False
    left_eye_pos = None
    right_eye_pos = None
    detection_method = None
    detection_source = None

    # Nomenclatura possível: helper-l-eye, helper-r-eye, Helper_l_eye, etc.
    candidate_left  = ["helper-l-eye", "helper-left-eye", "left-eye-helper",
                       "helperleye", "l_eye_helper", "l-eye"]
    candidate_right = ["helper-r-eye", "helper-right-eye", "right-eye-helper",
                       "helperreye", "r_eye_helper", "r-eye"]

    for gl in candidate_left:
        vl = verts_in_group(body, gl)
        if len(vl) >= 3:
            cL = centroid(vl)
            # procurar o par direito
            for gr in candidate_right:
                vr = verts_in_group(body, gr)
                if len(vr) >= 3:
                    cR = centroid(vr)
                    left_eye_pos  = cL
                    right_eye_pos = cR
                    detection_method = "helper_vertex_group_centroid"
                    detection_source = f"groups: {gl} / {gr}"
                    eye_detected = True
                    log(f"  SUCESSO via grupos {gl}/{gr}: E={vec3_round(cL)} D={vec3_round(cR)}")
                    break
        if eye_detected:
            break

    # ── 10. PRIORIDADE 2: joint-*-eye ─────────────────────────────────────────
    if not eye_detected:
        log("Não encontrado por helper-*-eye. A tentar joint-*-eye...")
        candidate_jl = ["joint-l-eye", "joint-left-eye", "joint-eye-l",
                        "eye.L", "joint-eye.L", "l-eye-joint"]
        candidate_jr = ["joint-r-eye", "joint-right-eye", "joint-eye-r",
                        "eye.R", "joint-eye.R", "r-eye-joint"]

        for gl in candidate_jl:
            vl = verts_in_group(body, gl)
            if len(vl) >= 3:
                cL = centroid(vl)
                for gr in candidate_jr:
                    vr = verts_in_group(body, gr)
                    if len(vr) >= 3:
                        cR = centroid(vr)
                        left_eye_pos  = cL
                        right_eye_pos = cR
                        detection_method = "joint_vertex_group_centroid"
                        detection_source = f"groups: {gl} / {gr}"
                        eye_detected = True
                        log(f"  SUCESSO via grupos {gl}/{gr}: E={vec3_round(cL)} D={vec3_round(cR)}")
                        break
            if eye_detected:
                break

    # ── 11. PRIORIDADE 3: VARREDURA TOPOLÓGICA COM ANÁLISE DE ÓRBITA ─────────
    if not eye_detected:
        log("Não encontrado por grupos de vértices. A tentar varredura topológica...")

        # Varrer fatias na zona 83%-97% da altura (zona facial provável)
        SLICE_H   = 0.008  # 8 mm por fatia
        scan_lo   = body_min_z + body_h * 0.83
        scan_hi   = body_min_z + body_h * 0.97
        face_y_threshold = min(all_y) * 0.35  # 35% do Y mais negativo

        slices_data = {}
        z = scan_lo
        while z < scan_hi:
            z_hi = z + SLICE_H
            sv = [v for v in verts_world if z <= v.z < z_hi]
            # filtrar vértices frontais (Y negativo)
            fv = [v for v in sv if v.y <= face_y_threshold]
            if len(fv) >= 6:
                left_verts  = [v for v in fv if v.x >  0.005]
                right_verts = [v for v in fv if v.x < -0.005]
                if len(left_verts) >= 3 and len(right_verts) >= 3:
                    cL = centroid(left_verts)
                    cR = centroid(right_verts)
                    # simetria: diferença das posições Z deve ser pequena
                    z_symmetry = abs(cL.z - cR.z)
                    # distancia interocular esperada: ~6-7% da altura
                    interocular = cL.x - cR.x
                    slices_data[round(z, 3)] = {
                        "left_centroid":  vec3_round(cL),
                        "right_centroid": vec3_round(cR),
                        "z_symmetry":     round(z_symmetry, 4),
                        "interocular_x":  round(interocular, 4),
                        "front_verts":    len(fv),
                    }
            z += SLICE_H

        # Escolher fatia com melhor simetria Z (olhos reais são simétricos)
        best_z_key = None
        best_z_symmetry = float("inf")
        for z_key, sd in slices_data.items():
            # Filtrar candidatos com separação interocular razoável (3%-10% da altura)
            io = sd["interocular_x"]
            if body_h * 0.03 <= io <= body_h * 0.12:
                if sd["z_symmetry"] < best_z_symmetry:
                    best_z_symmetry = sd["z_symmetry"]
                    best_z_key = z_key

        report["topological_scan"] = {
            "scan_range_z": [round(scan_lo, 4), round(scan_hi, 4)],
            "slices_with_data": len(slices_data),
            "best_z_key": best_z_key,
            "best_z_symmetry": round(best_z_symmetry, 4) if best_z_key else None,
            "all_slices": slices_data,
        }

        if best_z_key and best_z_key in slices_data:
            sd = slices_data[best_z_key]
            left_eye_pos  = Vector(sd["left_centroid"])
            right_eye_pos = Vector(sd["right_centroid"])
            detection_method = "topological_scan_best_z_symmetry"
            detection_source = f"slice_z={best_z_key} symmetry={best_z_symmetry:.4f}"
            eye_detected = True
            log(f"  SUCESSO via varredura topológica: z={best_z_key}")
            log(f"  Olho E: {sd['left_centroid']}")
            log(f"  Olho D: {sd['right_centroid']}")
        else:
            log("  Varredura topológica não encontrou candidatos válidos.")

    # ── 12. PRIORIDADE 4: OSSOS DO ARMATURE ──────────────────────────────────
    log("A procurar armature com ossos de olho...")
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    bone_eye_data = {}

    for arm in armatures:
        for bone in arm.data.bones:
            if "eye" in bone.name.lower():
                # posição da cabeça do osso em coordenadas globais
                head_world = arm.matrix_world @ bone.head_local
                bone_eye_data[bone.name] = {
                    "head_world": vec3_round(head_world),
                    "armature":   arm.name,
                }
                log(f"  Osso: {bone.name} → {vec3_round(head_world)}")

    report["bone_eyes"] = bone_eye_data

    if not eye_detected and bone_eye_data:
        # Tentar usar ossos eye.L / eye.R
        bl = bone_eye_data.get("eye.L") or bone_eye_data.get("Eye_L") or bone_eye_data.get("eye_L")
        br = bone_eye_data.get("eye.R") or bone_eye_data.get("Eye_R") or bone_eye_data.get("eye_R")
        if bl and br:
            left_eye_pos  = Vector(bl["head_world"])
            right_eye_pos = Vector(br["head_world"])
            detection_method = "armature_bone_head_position"
            detection_source = "bones eye.L / eye.R"
            eye_detected = True
            log(f"  SUCESSO via armature bones")

    # ── 13. SHAPE KEYS ────────────────────────────────────────────────────────
    log("A inventariar shape keys...")
    if body.data.shape_keys:
        keys = body.data.shape_keys.key_blocks
        report["shape_keys"] = {
            "count": len(keys),
            "names": [k.name for k in keys],
            "female_keys": [k.name for k in keys if "-$fe-" in k.name],
            "note": "shape keys de género não fornecem coordenadas oculares directamente",
        }
        log(f"Shape keys: {len(keys)}")
    else:
        report["shape_keys"] = {"count": 0}

    # ── 14. REGISTAR DETECÇÃO ─────────────────────────────────────────────────
    confidence = "unverified"
    if eye_detected:
        if detection_method == "helper_vertex_group_centroid":
            confidence = "high — native MPFB vertex group geometry"
        elif detection_method == "joint_vertex_group_centroid":
            confidence = "medium — bone weight vertex group"
        elif detection_method == "topological_scan_best_z_symmetry":
            confidence = "low — topological heuristic, requires visual validation"
        elif detection_method == "armature_bone_head_position":
            confidence = "medium — armature bone position"

    report["eye_detection"] = {
        "method":       detection_method,
        "source":       detection_source,
        "left_eye":     vec3_round(left_eye_pos)  if left_eye_pos  else None,
        "right_eye":    vec3_round(right_eye_pos) if right_eye_pos else None,
        "confidence":   confidence,
        "detected":     eye_detected,
        "requires_visual_validation": True,
    }

    log(f"Detecção: {detection_method} [{confidence}]")
    if left_eye_pos:
        log(f"  Olho E (MPFB 'esquerdo' = X+): {vec3_round(left_eye_pos)}")
    if right_eye_pos:
        log(f"  Olho D (MPFB 'direito' = X-):  {vec3_round(right_eye_pos)}")

    # ── 15. CONFIGURAR CENA PARA RENDER ──────────────────────────────────────
    log("A configurar cena para render de diagnóstico...")

    # Ocultar cabelo temporariamente (se existir)
    hair_objects_hidden = []
    for obj in bpy.data.objects:
        n = obj.name.lower()
        if ("hair" in n or "strand" in n or "cap" in n) and obj.name != body.name:
            obj.hide_render = True
            hair_objects_hidden.append(obj.name)

    if hair_objects_hidden:
        log(f"Cabelo ocultado para diagnóstico: {hair_objects_hidden}")
        report["hair_hidden_for_diagnosis"] = hair_objects_hidden

    # Centro da cena para posicionar câmara
    body_center = Vector((
        (max(all_x) + min(all_x)) / 2,
        (max(all_y) + min(all_y)) / 2,
        (body_min_z + body_max_z) / 2,
    ))
    span = body_h * 0.6

    # Centro da cabeça para views de close-up
    head_center = Vector((head_cx, head_cy, head_cz))
    head_span   = head_radius_est * 2.5

    cam = setup_camera_and_lights(body_center, span)
    sc  = bpy.context.scene
    sc.world = sc.world or bpy.data.worlds.new("World")
    sc.world.color = (0.1, 0.1, 0.1)

    # ── 16. ADICIONAR MARCADORES (se detecção foi feita) ─────────────────────
    markers = []
    if eye_detected and left_eye_pos and right_eye_pos:
        log("A adicionar marcadores oculares de diagnóstico...")
        mL = add_marker(
            (left_eye_pos.x, left_eye_pos.y, left_eye_pos.z),
            "DiagMarker_Eye_L", "#00FF88", size=body_h * 0.008
        )
        mR = add_marker(
            (right_eye_pos.x, right_eye_pos.y, right_eye_pos.z),
            "DiagMarker_Eye_R", "#FF4444", size=body_h * 0.008
        )
        markers = [mL, mR]

    # ── 17. RENDER — 4 IMAGENS DE DIAGNÓSTICO ────────────────────────────────
    log("A renderizar imagens de diagnóstico...")
    renders = {}
    distance = span * 1.8

    # Vista 1: Frontal do corpo completo
    ok = render_view(
        "diag_01_front_body.png",
        (body_center.x, body_center.y - distance, body_center.z),
        body_center, out
    )
    renders["01_front_body"] = {"rendered": ok, "file": "diag_01_front_body.png"}

    # Vista 2: Frontal close-up da cabeça
    cam.data.ortho_scale = head_span
    ok = render_view(
        "diag_02_head_front.png",
        (head_center.x, head_center.y - head_span * 2.5, head_center.z),
        head_center, out
    )
    renders["02_head_front"] = {"rendered": ok, "file": "diag_02_head_front.png"}
    cam.data.ortho_scale = span * 1.6  # restaurar

    # Vista 3: Lateral direita
    ok = render_view(
        "diag_03_side_right.png",
        (body_center.x + distance, body_center.y, body_center.z),
        body_center, out
    )
    renders["03_side_right"] = {"rendered": ok, "file": "diag_03_side_right.png"}

    # Vista 4: Três quartos frontal com marcadores
    ok = render_view(
        "diag_04_quarter_markers.png",
        (body_center.x + distance * 0.6, body_center.y - distance * 0.8, body_center.z + span * 0.1),
        body_center, out
    )
    renders["04_quarter_markers"] = {"rendered": ok, "file": "diag_04_quarter_markers.png"}

    report["renders"] = renders
    log(f"Renders: {renders}")

    # ── 18. REMOVER MARCADORES ────────────────────────────────────────────────
    for m in markers:
        if m.name in bpy.data.objects:
            bpy.data.objects.remove(m, do_unlink=True)

    # Restaurar cabelo
    for oname in hair_objects_hidden:
        obj = bpy.data.objects.get(oname)
        if obj:
            obj.hide_render = False

    # ── 19. SUMÁRIO DE RECOMENDAÇÃO PARA B9 ──────────────────────────────────
    if eye_detected and left_eye_pos and right_eye_pos:
        # calcular proporções em relação à altura do corpo
        eye_z_pct_L = (left_eye_pos.z - body_min_z) / body_h * 100
        eye_z_pct_R = (right_eye_pos.z - body_min_z) / body_h * 100
        eye_z_pct   = (eye_z_pct_L + eye_z_pct_R) / 2
        interocular = left_eye_pos.x - right_eye_pos.x

        report["b9_recommendation"] = {
            "eye_z_pct_body_height":  round(eye_z_pct, 2),
            "eye_z_absolute_left":    round(left_eye_pos.z, 5),
            "eye_z_absolute_right":   round(right_eye_pos.z, 5),
            "eye_y_left":             round(left_eye_pos.y, 5),
            "eye_y_right":            round(right_eye_pos.y, 5),
            "eye_x_left":             round(left_eye_pos.x, 5),
            "eye_x_right":            round(right_eye_pos.x, 5),
            "interocular_distance":   round(interocular, 5),
            "detection_method":       detection_method,
            "confidence":             confidence,
            "note": (
                "Se confidence='high', use eye_z_pct_body_height como referência VERIFICADA "
                "para a B9. Se 'low', validar visualmente as imagens de diagnóstico antes de usar."
            ),
        }
        log(f"B9: olhos a {eye_z_pct:.1f}% da altura (Z absoluto ~{left_eye_pos.z:.4f})")
    else:
        report["b9_recommendation"] = {
            "note": (
                "Detecção falhou. Não usar percentagem arbitrária. "
                "Verificar manualmente os logs e tentar com um rig aplicado."
            )
        }

    report["status"] = "success"

except Exception as exc:
    report["status"] = "failed"
    report["errors"].append(str(exc))
    report["traceback"] = traceback.format_exc()
    log(f"ERRO: {exc}")
    traceback.print_exc()

finally:
    report_path = out / "diagnose-eyes-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"Relatório gravado: {report_path}")
    print("DIAG_EYES_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success":
    sys.exit(1)
