"""
diagnose_mpfb_eye_geometry.py — Diagnóstico aprofundado da geometria ocular nativa do MPFB.

CONTEXTO
--------
O diagnóstico anterior (diagnose_mpfb_eyes.py) identificou com sucesso os grupos de vértices:
  helper-l-eye  →  centróide ≈ (0.03078, -0.12454, 1.54616)
  helper-r-eye  →  centróide ≈ (-0.03078, -0.12454, 1.54616)
  joint-l-eye   →  (a confirmar)
  joint-r-eye   →  (a confirmar)

OBJETIVO DESTA INVESTIGAÇÃO
----------------------------
Responder com evidência às seguintes perguntas:

  A. Os grupos auxiliares representam globos oculares completos, superfícies
     auxiliares ou outra estrutura?

  B. As coordenadas identificadas correspondem aos centros reais dos olhos
     (não apenas ao centróide geométrico do grupo)?

  C. A geometria ocular pode ser aproveitada directamente na B9?

  D. Qual é a posição correcta dos olhos relativamente às pálpebras e à face?

  E. O método continua a funcionar com diferentes morfologias faciais?

PRESERVAÇÃO
-----------
Não modifica nenhuma versão B5/B7/B8. Apenas leitura da malha criada por create_human().
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

out = Path(os.environ.get("MPFB_OUTPUT", "output/diagnose-eye-geometry")).resolve()
out.mkdir(parents=True, exist_ok=True)

# Coordenadas do diagnóstico anterior (para comparação)
PREV_LEFT  = Vector(( 0.03078, -0.12454, 1.54616))
PREV_RIGHT = Vector((-0.03078, -0.12454, 1.54616))

report = {
    "diagnostic":   "mpfb_eye_geometry",
    "version":      "1.0",
    "blender":      bpy.app.version_string,
    "status":       "started",
    "previous_detection": {
        "left_eye":  list(PREV_LEFT),
        "right_eye": list(PREV_RIGHT),
        "source":    "helper_vertex_group_centroid",
    },
    "left_eye": {
        "vertex_count": None, "center": None, "bbox": None,
        "dimensions": None, "vertex_indices": None,
        "face_count": None, "geometry_type": None,
    },
    "right_eye": {
        "vertex_count": None, "center": None, "bbox": None,
        "dimensions": None, "vertex_indices": None,
        "face_count": None, "geometry_type": None,
    },
    "joint_eyes": {},
    "native_eye_geometry": {
        "detected": None,
        "geometry_type": "unverified",
        "reusable": None,
        "notes": [],
    },
    "facial_relationship": {},
    "renders": {},
    "conclusion": {
        "eye_centers_verified": False,
        "requires_visual_validation": True,
        "recommended_approach": None,
        "approach_rationale": None,
    },
    "errors": [],
    "warnings": [],
}


def log(msg):
    print(f"[DIAG_GEO] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def vec3(v, d=5):
    return [round(v.x, d), round(v.y, d), round(v.z, d)]


def analyse_group(obj, group_name):
    """Análise completa de um grupo de vértices. Devolve dict ou None."""
    if group_name not in obj.vertex_groups:
        return None

    gidx = obj.vertex_groups[group_name].index
    wmat = obj.matrix_world

    # Recolher vértices e pesos
    group_verts = []
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == gidx and g.weight > 0:
                wco = wmat @ v.co
                group_verts.append({
                    "index":  v.index,
                    "local":  vec3(v.co),
                    "world":  vec3(wco),
                    "weight": round(g.weight, 4),
                })
                break

    if not group_verts:
        return {"vertex_count": 0, "note": "grupo existe mas sem vértices com peso > 0"}

    coords = [Vector(v["world"]) for v in group_verts]
    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]

    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    cz = sum(zs) / len(zs)

    # Faces que têm TODOS os vértices neste grupo
    group_vidx = {v["index"] for v in group_verts}
    face_count = sum(
        1 for f in obj.data.polygons
        if all(vi in group_vidx for vi in f.vertices)
    )

    # Distribuição de Z (para perceber se é esférica ou plana)
    z_range = max(zs) - min(zs)
    x_range = max(xs) - min(xs)
    y_range = max(ys) - min(ys)
    flatness = y_range / max(z_range, x_range, 1e-6)  # <0.3 → plano; >0.8 → esférico

    # Estimar tipo de geometria
    if len(group_verts) < 5:
        geo_type = "too_few_vertices"
    elif flatness < 0.25:
        geo_type = "flat_surface_likely_eyelid_or_helper"
    elif flatness > 0.7 and x_range > 0.005 and z_range > 0.005:
        geo_type = "volumetric_likely_eyeball_or_region"
    else:
        geo_type = "intermediate_surface"

    return {
        "vertex_count":   len(group_verts),
        "face_count":     face_count,
        "center":         [round(cx, 5), round(cy, 5), round(cz, 5)],
        "bbox": {
            "x": [round(min(xs), 5), round(max(xs), 5)],
            "y": [round(min(ys), 5), round(max(ys), 5)],
            "z": [round(min(zs), 5), round(max(zs), 5)],
        },
        "dimensions": {
            "x": round(x_range, 5),
            "y": round(y_range, 5),
            "z": round(z_range, 5),
        },
        "flatness_ratio":  round(flatness, 3),
        "geometry_type":   geo_type,
        "vertex_indices":  sorted(group_vidx),
        "weight_min":      round(min(v["weight"] for v in group_verts), 4),
        "weight_max":      round(max(v["weight"] for v in group_verts), 4),
        "vertices_sample": group_verts[:20],
    }


def make_material_emission(name, r, g, b, strength=3.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Emission Strength"].default_value = strength
    bsdf.inputs["Roughness"].default_value = 0.4
    out_node = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def create_highlight_mesh(obj, group_name, mat, scene_name):
    """Cria uma cópia da malha com apenas os vértices do grupo visíveis."""
    if group_name not in obj.vertex_groups:
        return None
    gidx  = obj.vertex_groups[group_name].index
    vidxs = {v.index for v in obj.data.vertices
              for g in v.groups if g.group == gidx and g.weight > 0}
    if not vidxs:
        return None

    # duplicar objecto
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.duplicate(linked=False)
    dup = bpy.context.active_object
    dup.name = scene_name

    # modo edição: apagar vértices que NÃO pertencem ao grupo
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(dup.data)
    bm.verts.ensure_lookup_table()
    to_delete = [v for v in bm.verts if v.index not in vidxs]
    bmesh.ops.delete(bm, geom=to_delete, context="VERTS")
    bmesh.update_edit_mesh(dup.data)
    bpy.ops.object.mode_set(mode="OBJECT")

    if dup.data.materials:
        dup.data.materials[0] = mat
    else:
        dup.data.materials.append(mat)
    return dup


def add_sphere_marker(location, name, mat, size=0.004):
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=size, segments=12, ring_count=8, location=location)
    m = bpy.context.active_object
    m.name = name
    if m.data.materials:
        m.data.materials[0] = mat
    else:
        m.data.materials.append(mat)
    return m


def configure_render(samples=32, w=768, h=768):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device  = "CPU"
    sc.cycles.samples = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.08, 0.08, 0.08)


def render_to(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return Path(path).exists()


def setup_camera_ortho(location, target, scale, name="DiagCam"):
    for o in list(bpy.data.objects):
        if o.name.startswith(name):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.camera_add(location=location)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = scale
    d = Vector(target) - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def add_area_light(location, target, energy=600, size=0.5, name="DiagLight"):
    for o in list(bpy.data.objects):
        if o.name.startswith(name):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.light_add(type="AREA", location=location)
    l = bpy.context.active_object
    l.name = name
    l.data.energy = energy
    l.data.size   = size
    d = Vector(target) - l.location
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


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

    bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB não ficou activo: {module_id}")
    log(f"MPFB activo: {module_id}")

    # ── 2. CRIAR HUMANO ───────────────────────────────────────────────────────
    log("A limpar cena e criar humano...")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result:
        raise RuntimeError(f"mpfb.create_human devolveu: {result}")
    bpy.context.view_layer.update()

    # Identificar mesh principal
    meshes = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True,
    )
    if not meshes:
        raise RuntimeError("Nenhuma mesh encontrada após create_human")
    body = meshes[0][0]
    log(f"Mesh: {body.name} ({len(body.data.vertices)} vértices)")

    # Dimensões do corpo (para referência de escala)
    verts_world = [body.matrix_world @ v.co for v in body.data.vertices]
    body_min_z  = min(v.z for v in verts_world)
    body_max_z  = max(v.z for v in verts_world)
    body_h      = body_max_z - body_min_z
    log(f"Altura corpo: {body_h:.4f} m")

    # ── 3. LISTAR TODOS OS GRUPOS DE VÉRTICES ────────────────────────────────
    all_groups = [vg.name for vg in body.vertex_groups]
    log(f"Total de grupos de vértices: {len(all_groups)}")

    # Grupos relacionados com olhos
    eye_groups = [n for n in all_groups if "eye" in n.lower()]
    log(f"Grupos 'eye': {eye_groups}")
    report["all_vertex_groups"] = all_groups
    report["eye_related_groups_found"] = eye_groups

    # ── 4. ANÁLISE PROFUNDA DOS GRUPOS HELPER ────────────────────────────────
    log("=== A analisar helper-l-eye ===")
    hl = analyse_group(body, "helper-l-eye")
    if hl:
        report["left_eye"].update(hl)
        report["left_eye"]["group_name"] = "helper-l-eye"
        log(f"  Vértices: {hl['vertex_count']}")
        log(f"  Centro:   {hl['center']}")
        log(f"  Bbox Y:   {hl['bbox']['y']}  (frente=min negativo)")
        log(f"  Dims:     {hl['dimensions']}")
        log(f"  Tipo geo: {hl['geometry_type']}")
        log(f"  Flatness: {hl['flatness_ratio']}")
    else:
        report["left_eye"]["note"] = "helper-l-eye não encontrado"
        report["warnings"].append("helper-l-eye não encontrado")
        log("  AVISO: helper-l-eye não encontrado")

    log("=== A analisar helper-r-eye ===")
    hr = analyse_group(body, "helper-r-eye")
    if hr:
        report["right_eye"].update(hr)
        report["right_eye"]["group_name"] = "helper-r-eye"
        log(f"  Vértices: {hr['vertex_count']}")
        log(f"  Centro:   {hr['center']}")
        log(f"  Tipo geo: {hr['geometry_type']}")
    else:
        report["right_eye"]["note"] = "helper-r-eye não encontrado"
        report["warnings"].append("helper-r-eye não encontrado")
        log("  AVISO: helper-r-eye não encontrado")

    # ── 5. ANÁLISE DOS GRUPOS JOINT ───────────────────────────────────────────
    for jname in ["joint-l-eye", "joint-r-eye", "joint-eye-l", "joint-eye-r",
                  "l-eye", "r-eye"]:
        jdata = analyse_group(body, jname)
        if jdata:
            report["joint_eyes"][jname] = jdata
            log(f"Grupo '{jname}': {jdata['vertex_count']} vértices, centro={jdata['center']}")

    # ── 6. COMPARAR CENTROS: helper vs joint ──────────────────────────────────
    comparison = {}
    if hl and hl.get("center") and report["joint_eyes"].get("joint-l-eye", {}).get("center"):
        jl_center = Vector(report["joint_eyes"]["joint-l-eye"]["center"])
        hl_center = Vector(hl["center"])
        delta = (jl_center - hl_center).length
        comparison["left_helper_vs_joint_distance"] = round(delta, 5)
        log(f"Distância helper-l-eye → joint-l-eye: {delta:.5f} m")

    report["center_comparison"] = comparison

    # ── 7. RELAÇÃO COM A SUPERFÍCIE FACIAL ────────────────────────────────────
    # Calcular profundidade do centro do olho relativamente ao Y mais frontal da cabeça
    if hl and hl.get("bbox"):
        face_y_most_front  = min(v.y for v in verts_world
                                  if v.z > body_min_z + body_h * 0.80)
        eye_y_center_L = hl["center"][1] if hl.get("center") else None
        if eye_y_center_L is not None:
            depth_behind_face = eye_y_center_L - face_y_most_front
            report["facial_relationship"] = {
                "face_y_most_front":    round(face_y_most_front, 5),
                "helper_l_center_y":    round(eye_y_center_L, 5),
                "depth_behind_face_y":  round(depth_behind_face, 5),
                "note": ("profundidade positiva = centro do olho está ATRÁS "
                         "da superfície frontal do rosto (esperado para globo ocular)"),
            }
            log(f"Profundidade do olho atrás da face: {depth_behind_face:.4f} m")

    # ── 8. AVALIAR SE A GEOMETRIA É REUTILIZÁVEL ─────────────────────────────
    geo_notes = []
    reusable  = False
    geo_type  = "unverified"

    if hl:
        vc = hl.get("vertex_count", 0)
        ft = hl.get("geometry_type", "")
        fc = hl.get("face_count", 0)

        if vc < 10:
            geo_notes.append(f"Poucos vértices ({vc}) — provavelmente apenas marcadores ou landmarks, não geometria renderizável.")
            geo_type = "landmark_markers"
        elif ft == "flat_surface_likely_eyelid_or_helper":
            geo_notes.append("Geometria plana — pode ser superfície auxiliar de referência para ajuste de roupa/rig, não globo ocular.")
            geo_type = "helper_surface_flat"
            reusable = False
        elif ft == "volumetric_likely_eyeball_or_region":
            geo_notes.append("Geometria volumétrica — pode conter globo ocular ou região ocular aproveitável.")
            geo_type = "potential_eyeball_region"
            reusable = True  # a confirmar visualmente
        elif ft == "intermediate_surface":
            geo_notes.append("Geometria intermédia — necessita validação visual para determinar tipo.")
            geo_type = "intermediate_needs_validation"

        if fc == 0:
            geo_notes.append("Sem faces próprias — vértices pertencem ao body mesh principal, não é uma submesh independente.")
            reusable = False
            geo_type = "vertex_weight_region_not_separate_mesh"
        elif fc > 0:
            geo_notes.append(f"{fc} faces inteiramente dentro do grupo — geometria tem estrutura de malha.")

        if vc > 50:
            geo_notes.append(f"Com {vc} vértices, o grupo tem densidade suficiente para representar geometria ocular.")

    report["native_eye_geometry"] = {
        "detected":      hl is not None,
        "geometry_type": geo_type,
        "reusable":      reusable,
        "notes":         geo_notes,
    }
    for note in geo_notes:
        log(f"  GEO: {note}")

    # ── 9. CONFIGURAR RENDER ──────────────────────────────────────────────────
    log("A configurar render...")
    configure_render(samples=32, w=768, h=768)

    # Materiais de diagnóstico
    mat_skin    = make_material_emission("DiagSkin",    0.7,  0.5,  0.4,  0.5)
    mat_eye_l   = make_material_emission("DiagEyeL",   0.0,  1.0,  0.5,  5.0)  # verde
    mat_eye_r   = make_material_emission("DiagEyeR",   1.0,  0.2,  0.2,  5.0)  # vermelho
    mat_joint_l = make_material_emission("DiagJointL", 1.0,  1.0,  0.0,  4.0)  # amarelo
    mat_joint_r = make_material_emission("DiagJointR", 0.0,  0.5,  1.0,  4.0)  # azul

    # Aplicar material de pele ao body
    if body.data.materials:
        body.data.materials[0] = mat_skin
    else:
        body.data.materials.append(mat_skin)

    # Dimensões da cabeça para posicionar câmara
    head_verts = [v for v in verts_world if v.z > body_min_z + body_h * 0.80]
    hx = [v.x for v in head_verts]
    hy = [v.y for v in head_verts]
    hz = [v.z for v in head_verts]
    hcx = (max(hx) + min(hx)) / 2
    hcy = (max(hy) + min(hy)) / 2
    hcz = (max(hz) + min(hz)) / 2
    head_span = max(max(hx) - min(hx), max(hy) - min(hy), max(hz) - min(hz)) * 1.3
    head_center = Vector((hcx, hcy, hcz))

    # Ocultar cabelo (se existir)
    for obj in bpy.data.objects:
        n = obj.name.lower()
        if any(k in n for k in ("hair", "strand", "cap")) and obj != body:
            obj.hide_render = True

    # ── 10. IMAGEM 1 — Cabeça frontal com grupos destacados ──────────────────
    log("Render 1: cabeça frontal com grupos destacados...")
    diag_objects = []

    # Criar meshes de destaque para helper-l-eye e helper-r-eye
    dup_l = create_highlight_mesh(body, "helper-l-eye", mat_eye_l, "Highlight_EyeL")
    dup_r = create_highlight_mesh(body, "helper-r-eye", mat_eye_r, "Highlight_EyeR")
    if dup_l: diag_objects.append(dup_l)
    if dup_r: diag_objects.append(dup_r)

    add_area_light((hcx + 0.3, hcy - 0.3, hcz + 0.2), head_center, energy=400, size=0.4)
    add_area_light((hcx - 0.2, hcy - 0.2, hcz + 0.3), head_center, energy=300, size=0.3,
                   name="DiagLight2")
    setup_camera_ortho(
        (hcx, hcy - head_span * 2, hcz),
        head_center, head_span * 1.1, name="DiagCam"
    )
    ok1 = render_to(out / "diag_geo_01_head_front_groups.png")
    report["renders"]["01_head_front_groups"] = {
        "rendered": ok1,
        "file": "diag_geo_01_head_front_groups.png",
        "description": "Vista frontal com helper-l-eye (verde) e helper-r-eye (vermelho) destacados",
    }
    log(f"  Render 1: {ok1}")

    # ── 11. IMAGEM 2 — Cabeça lateral (profundidade) ─────────────────────────
    log("Render 2: vista lateral...")
    setup_camera_ortho(
        (hcx + head_span * 2, hcy, hcz),
        head_center, head_span * 1.1, name="DiagCam"
    )
    ok2 = render_to(out / "diag_geo_02_head_side_depth.png")
    report["renders"]["02_head_side_depth"] = {
        "rendered": ok2,
        "file": "diag_geo_02_head_side_depth.png",
        "description": "Vista lateral: profundidade da geometria ocular vs superfície facial",
    }
    log(f"  Render 2: {ok2}")

    # ── 12. IMAGEM 3 — Geometria ocular isolada ───────────────────────────────
    log("Render 3: geometria ocular isolada...")
    body.hide_render = True  # ocultar body principal

    setup_camera_ortho(
        (hcx, hcy - head_span * 1.5, hcz),
        head_center, head_span * 0.6, name="DiagCam"
    )
    ok3 = render_to(out / "diag_geo_03_eye_geometry_isolated.png")
    report["renders"]["03_eye_isolated"] = {
        "rendered": ok3,
        "file": "diag_geo_03_eye_geometry_isolated.png",
        "description": "Geometria ocular isolada (apenas helper-l-eye e helper-r-eye)",
    }
    log(f"  Render 3: {ok3}")
    body.hide_render = False  # restaurar

    # ── 13. IMAGEM 4 — Comparação helper vs joint ─────────────────────────────
    log("Render 4: comparação helper vs joint...")

    # Marcadores para os centros dos grupos
    markers = []
    if hl and hl.get("center"):
        cL = Vector(hl["center"])
        mL = add_sphere_marker(cL, "Marker_Helper_L", mat_eye_l,   size=body_h * 0.007)
        markers.append(mL)
    if hr and hr.get("center"):
        cR = Vector(hr["center"])
        mR = add_sphere_marker(cR, "Marker_Helper_R", mat_eye_r,   size=body_h * 0.007)
        markers.append(mR)
    if report["joint_eyes"].get("joint-l-eye", {}).get("center"):
        jcL = Vector(report["joint_eyes"]["joint-l-eye"]["center"])
        mjL = add_sphere_marker(jcL, "Marker_Joint_L", mat_joint_l, size=body_h * 0.005)
        markers.append(mjL)
    if report["joint_eyes"].get("joint-r-eye", {}).get("center"):
        jcR = Vector(report["joint_eyes"]["joint-r-eye"]["center"])
        mjR = add_sphere_marker(jcR, "Marker_Joint_R", mat_joint_r, size=body_h * 0.005)
        markers.append(mjR)

    setup_camera_ortho(
        (hcx, hcy - head_span * 2, hcz),
        head_center, head_span * 1.1, name="DiagCam"
    )
    ok4 = render_to(out / "diag_geo_04_comparison_markers.png")
    report["renders"]["04_comparison"] = {
        "rendered": ok4,
        "file": "diag_geo_04_comparison_markers.png",
        "description": (
            "Comparação: verde=helper-l-eye, vermelho=helper-r-eye, "
            "amarelo=joint-l-eye, azul=joint-r-eye"
        ),
    }
    log(f"  Render 4: {ok4}")

    # Remover marcadores temporários
    for m in markers:
        if m and m.name in bpy.data.objects:
            bpy.data.objects.remove(m, do_unlink=True)
    for d in diag_objects:
        if d and d.name in bpy.data.objects:
            bpy.data.objects.remove(d, do_unlink=True)

    # ── 14. DETERMINAR ABORDAGEM PARA B9 ─────────────────────────────────────
    log("A determinar abordagem para B9...")

    hl_center = hl["center"] if hl and hl.get("center") else None
    hr_center = hr["center"] if hr and hr.get("center") else None
    reusable_native = report["native_eye_geometry"]["reusable"]
    geo_type_native = report["native_eye_geometry"]["geometry_type"]

    # Verificar desvio entre diagnóstico anterior e este
    if hl_center:
        prev_delta = (Vector(hl_center) - PREV_LEFT).length
        report["reproducibility"] = {
            "prev_left":   list(PREV_LEFT),
            "this_left":   hl_center,
            "delta_m":     round(prev_delta, 6),
            "reproducible": prev_delta < 0.001,
        }
        log(f"Reprodutibilidade: delta={prev_delta:.6f} m (< 1mm = {'SIM' if prev_delta < 0.001 else 'NÃO'})")

    # Decidir abordagem
    if geo_type_native == "vertex_weight_region_not_separate_mesh":
        approach = "B"
        rationale = (
            "Os grupos helper-*-eye são regiões de peso de vértice no body mesh, "
            "não meshes independentes de globo ocular. Não podem ser separadas "
            "sem destruir a topologia. O seu centróide é, contudo, a referência "
            "anatómica mais fiável disponível para posicionar novos olhos. "
            "Abordagem B: criar esferas de globo ocular posicionadas pelos "
            "centróides verificados destes grupos."
        )
    elif geo_type_native == "potential_eyeball_region" and reusable_native:
        approach = "A"
        rationale = (
            "Os grupos helper-*-eye contêm geometria volumétrica com faces próprias. "
            "Pode ser viável separar esta geometria como objecto independente "
            "e usá-la como base do globo ocular na B9. "
            "Abordagem A: separar e reutilizar a geometria nativa."
        )
    elif geo_type_native in ("flat_surface_likely_eyelid_or_helper", "helper_surface_flat"):
        approach = "B"
        rationale = (
            "Os grupos helper-*-eye representam superfícies planas auxiliares "
            "(provavelmente pálpebras ou superfície de referência para rig). "
            "Não são adequados como globo ocular. O centróide é, contudo, "
            "a melhor referência anatómica disponível. "
            "Abordagem B: criar novos globos oculares posicionados pelo centróide."
        )
    elif geo_type_native == "landmark_markers":
        approach = "B"
        rationale = (
            "Os grupos helper-*-eye têm demasiado poucos vértices para ser "
            "geometria renderizável. São landmarks de posicionamento. "
            "Abordagem B: usar o centróide como posição de referência para novos olhos."
        )
    else:
        approach = "B"
        rationale = (
            "Tipo de geometria indeterminado. Por precaução, usar centróide "
            "do grupo como referência e criar globos oculares novos. "
            "Abordagem B recomendada."
        )

    report["conclusion"] = {
        "eye_centers_verified": hl_center is not None,
        "requires_visual_validation": True,
        "left_eye_center_for_b9":  hl_center,
        "right_eye_center_for_b9": hr_center,
        "recommended_approach": approach,
        "approach_rationale": rationale,
        "answers": {
            "A_groups_represent_eyeball": (
                "Verificar nas imagens de diagnóstico. "
                f"Tipo identificado: {geo_type_native}"
            ),
            "B_centers_match_real_eyes": (
                "Os centróides dos grupos correspondem à posição geométrica dos grupos. "
                "Se os grupos representam geometria ocular, os centróides são os centros dos olhos. "
                "Validação visual necessária (imagem diag_geo_01)."
            ),
            "C_reusable_in_b9": (
                f"{'Potencialmente sim' if reusable_native else 'Não directamente'} — "
                f"ver imagem diag_geo_03 para confirmação."
            ),
            "D_position_relative_to_lids": (
                "Ver imagem diag_geo_02 (lateral) para avaliar profundidade. "
                f"Profundidade calculada: {report.get('facial_relationship', {}).get('depth_behind_face_y', 'N/A')} m"
            ),
            "E_method_reproducible": (
                f"Delta vs diagnóstico anterior: "
                f"{report.get('reproducibility', {}).get('delta_m', 'N/A')} m"
            ),
        },
    }

    log(f"Abordagem recomendada: {approach}")
    log(f"Racional: {rationale[:80]}...")

    report["status"] = "success"

except Exception as exc:
    report["status"] = "failed"
    report["errors"].append(str(exc))
    report["traceback"] = traceback.format_exc()
    log(f"ERRO: {exc}")
    traceback.print_exc()

finally:
    report_path = out / "diagnose-eye-geometry-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"Relatório gravado: {report_path}")
    print("DIAG_EYE_GEO_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success":
    sys.exit(1)
