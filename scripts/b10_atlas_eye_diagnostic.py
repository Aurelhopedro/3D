"""
B10 — Atlas Eye Diagnostic
===========================
Diagnóstico ocular baseado nos dados do Mesh Atlas (Etapa 1).

ORIGEM DOS DADOS
  Os grupos oculares foram identificados no inventário do Atlas:
    helper-l-eye      72 vértices
    helper-r-eye      72 vértices
    joint-l-eye        8 vértices
    joint-r-eye        8 vértices
    joint-l-eye-target 8 vértices
    joint-r-eye-target 8 vértices
    joint-l-upperlid   8 vértices
    joint-r-upperlid   8 vértices
    joint-l-lowerlid   8 vértices
    joint-r-lowerlid   8 vértices

COORDENADAS DE REFERÊNCIA HISTÓRICAS (para comparação — NÃO para posicionamento)
  REFERENCE_LEFT_EYE    = (0.03077501, -0.124534965, 1.546157895)
  REFERENCE_LEFT_TARGET = (0.03173501, -0.16181498,  1.54723787)
  REFERENCE_RIGHT_EYE   = (-0.03077501, -0.124534965, 1.546157895)
  REFERENCE_RIGHT_TARGET= (-0.03173501, -0.16181498,  1.54723787)

PRINCÍPIOS
  1. Todos os centros são RECALCULADOS da malha feminina em uso.
  2. Se um grupo estiver ausente → erro explícito, não fallback silencioso.
  3. Os olhos temporários são diagnóstico, não produto final.
  4. Sucesso técnico ≠ validação anatómica.

PRESERVAÇÃO
  Não modifica nenhum ficheiro B5–B9 Rev2.
  Script independente em scripts/b10_atlas_eye_diagnostic.py
"""

import bpy
import bmesh
import json
import math
import os
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from mathutils import Vector, Matrix

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

OUT = Path(os.environ.get("MPFB_OUTPUT", "output/b10-atlas-eye-diagnostic")).resolve()
OUT_RENDERS = OUT / "renders"
OUT.mkdir(parents=True, exist_ok=True)
OUT_RENDERS.mkdir(parents=True, exist_ok=True)

# Referências históricas — APENAS para comparação
REFERENCE = {
    "left_eye":    Vector(( 0.03077501, -0.124534965, 1.546157895)),
    "left_target": Vector(( 0.03173501, -0.16181498,  1.54723787)),
    "right_eye":   Vector((-0.03077501, -0.124534965, 1.546157895)),
    "right_target":Vector((-0.03173501, -0.16181498,  1.54723787)),
}

# Grupos oculares esperados e os seus papéis
EYE_GROUPS = {
    "helper-l-eye":       {"side": "L", "role": "helper",   "expected_verts": 72},
    "helper-r-eye":       {"side": "R", "role": "helper",   "expected_verts": 72},
    "joint-l-eye":        {"side": "L", "role": "joint",    "expected_verts": 8},
    "joint-r-eye":        {"side": "R", "role": "joint",    "expected_verts": 8},
    "joint-l-eye-target": {"side": "L", "role": "target",   "expected_verts": 8},
    "joint-r-eye-target": {"side": "R", "role": "target",   "expected_verts": 8},
    "joint-l-upperlid":   {"side": "L", "role": "upperlid", "expected_verts": 8},
    "joint-r-upperlid":   {"side": "R", "role": "upperlid", "expected_verts": 8},
    "joint-l-lowerlid":   {"side": "L", "role": "lowerlid", "expected_verts": 8},
    "joint-r-lowerlid":   {"side": "R", "role": "lowerlid", "expected_verts": 8},
}

# Série de raios de diagnóstico (parâmetro configurável)
DIAGNOSTIC_RADII = [0.011, 0.013, 0.015, 0.017]

# Deslocamentos ao longo do eixo joint → target para testar posicionamento
DIAGNOSTIC_OFFSETS = [-0.005, 0.0, 0.005, 0.010]

TARGET_VERTS = 19158   # esperado para a malha feminina MPFB

report = {
    "b10_version":        "1.0",
    "generated_at":       datetime.now(timezone.utc).isoformat(),
    "blender_version":    bpy.app.version_string,
    "status":             "started",
    "mpfb_asset_research": {},
    "human": {},
    "female_morph": {},
    "eye_groups": {},
    "measurements": {},
    "reference_comparison": {},
    "diagnostic_variants": [],
    "renders": {},
    "validations": {},
    "errors": [],
    "limitations": [],
}


def log(msg):
    print(f"[B10] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def v3(v, d=6):
    return [round(float(v[0]), d), round(float(v[1]), d), round(float(v[2]), d)]


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(h, a=1.0):
    h = h.lstrip("#")
    return tuple(srgb_to_linear(int(h[i:i+2], 16) / 255) for i in (0, 2, 4)) + (a,)


def make_mat(name, color_hex, roughness=0.5, emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value    = hex_rgba(color_hex)
    b.inputs["Roughness"].default_value     = roughness
    if emission_strength > 0:
        b.inputs["Emission Color"].default_value    = hex_rgba(color_hex)
        b.inputs["Emission Strength"].default_value = emission_strength
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(b.outputs["BSDF"], o.inputs["Surface"])
    return mat


def assign_mat(obj, mat, slot=0):
    while len(obj.data.materials) <= slot:
        obj.data.materials.append(None)
    obj.data.materials[slot] = mat


def deselect():
    bpy.ops.object.select_all(action="DESELECT")


def set_active(obj):
    deselect()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def evaluated_world_coords(obj):
    """Coordenadas mundiais avaliadas (shape keys + modificadores) via depsgraph."""
    bpy.context.view_layer.update()
    dep = bpy.context.evaluated_depsgraph_get()
    ev  = obj.evaluated_get(dep)
    m   = ev.to_mesh()
    wm  = obj.matrix_world
    coords = [wm @ v.co for v in m.vertices]
    ev.to_mesh_clear()
    return coords


# ─────────────────────────────────────────────────────────────────────────────
# CENTRÓIDES A PARTIR DE COORDENADAS AVALIADAS
# ─────────────────────────────────────────────────────────────────────────────

def group_centroid_evaluated(obj, group_name, eval_coords):
    """
    Calcula centróide usando coordenadas mundiais avaliadas (shape keys activas).
    Devolve dict com centróide, bbox, contagens — ou dict de erro se grupo ausente.
    """
    if group_name not in obj.vertex_groups:
        return {
            "status": "unavailable",
            "error":  f"Grupo '{group_name}' não encontrado na malha.",
            "centroid": None,
        }

    gidx = obj.vertex_groups[group_name].index
    data = []
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == gidx and g.weight > 0:
                wco = eval_coords[v.index]
                data.append({
                    "vi": v.index,
                    "w":  round(g.weight, 6),
                    "co": v3(wco),
                })
                break

    if not data:
        return {
            "status":   "unavailable",
            "error":    f"Grupo '{group_name}' existe mas sem vértices com peso > 0.",
            "centroid": None,
        }

    coords = [Vector(d["co"]) for d in data]
    cx = sum(c.x for c in coords) / len(coords)
    cy = sum(c.y for c in coords) / len(coords)
    cz = sum(c.z for c in coords) / len(coords)
    cen = Vector((cx, cy, cz))

    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]

    return {
        "status":       "complete",
        "group_name":   group_name,
        "vertex_count": len(data),
        "centroid":     v3(cen),
        "bbox": {
            "x": [round(min(xs), 6), round(max(xs), 6)],
            "y": [round(min(ys), 6), round(max(ys), 6)],
            "z": [round(min(zs), 6), round(max(zs), 6)],
        },
        "dimensions": {
            "x": round(max(xs) - min(xs), 6),
            "y": round(max(ys) - min(ys), 6),
            "z": round(max(zs) - min(zs), 6),
        },
        "coord_state": "world_eval",
        "vertices_sample": data[:10],
    }


# ─────────────────────────────────────────────────────────────────────────────
# MEDIÇÕES GEOMÉTRICAS
# ─────────────────────────────────────────────────────────────────────────────

def compute_measurements(groups_data, eval_coords, obj):
    m = {}

    def cen(name):
        d = groups_data.get(name, {})
        c = d.get("centroid")
        return Vector(c) if c else None

    jl  = cen("joint-l-eye")
    jr  = cen("joint-r-eye")
    tl  = cen("joint-l-eye-target")
    tr  = cen("joint-r-eye-target")
    hl  = cen("helper-l-eye")
    hr  = cen("helper-r-eye")
    ull = cen("joint-l-upperlid")
    ulr = cen("joint-r-upperlid")
    lll = cen("joint-l-lowerlid")
    llr = cen("joint-r-lowerlid")

    def dist(a, b, key):
        if a and b:
            m[key] = round((b - a).length, 7)
        else:
            m[key] = {"status": "unavailable", "reason": "centroid_missing"}

    # Distância entre joints oculares
    dist(jl, jr, "interocular_distance_joint")

    # Distância joint → target (define orientação do olho)
    dist(jl, tl, "joint_to_target_L")
    dist(jr, tr, "joint_to_target_R")

    # Distância pálpebra superior → inferior
    dist(ull, lll, "lid_span_L")
    dist(ulr, llr, "lid_span_R")

    # Simetria bilateral (diferença entre esquerdo e direito)
    if isinstance(m.get("joint_to_target_L"), float) and isinstance(m.get("joint_to_target_R"), float):
        m["joint_target_symmetry_delta"] = round(
            abs(m["joint_to_target_L"] - m["joint_to_target_R"]), 8)

    # Distância helper → joint (diferença entre centróide helper e joint)
    dist(hl, jl, "helper_to_joint_L")
    dist(hr, jr, "helper_to_joint_R")

    # Vectores de orientação joint → target
    if jl and tl:
        v = (tl - jl)
        n = v.normalized()
        m["orientation_vector_L"] = v3(n)
        m["joint_target_distance_L"] = round(v.length, 7)
    if jr and tr:
        v = (tr - jr)
        n = v.normalized()
        m["orientation_vector_R"] = v3(n)
        m["joint_target_distance_R"] = round(v.length, 7)

    # Profundidade relativa: Y dos joints vs Y mais frontal da face
    wm = obj.matrix_world
    face_ys_head = [
        (wm @ v.co).y for v in obj.data.vertices
        if (wm @ v.co).z > sum((wm @ vv.co).z for vv in obj.data.vertices) / len(obj.data.vertices)
    ]
    if face_ys_head:
        face_y_front = min(face_ys_head)
        m["face_y_most_front"] = round(face_y_front, 7)
        if jl:
            m["joint_L_depth_behind_face"] = round(jl.y - face_y_front, 7)
        if jr:
            m["joint_R_depth_behind_face"] = round(jr.y - face_y_front, 7)
        if hl:
            m["helper_L_depth_behind_face"] = round(hl.y - face_y_front, 7)

    # Comparação com referências históricas
    ref_comp = {}
    if jl:
        ref_comp["joint_L_vs_ref_L_delta"] = round((jl - REFERENCE["left_eye"]).length, 7)
    if jr:
        ref_comp["joint_R_vs_ref_R_delta"] = round((jr - REFERENCE["right_eye"]).length, 7)
    if hl:
        ref_comp["helper_L_vs_ref_L_delta"] = round((hl - REFERENCE["left_eye"]).length, 7)
    if hr:
        ref_comp["helper_R_vs_ref_R_delta"] = round((hr - REFERENCE["right_eye"]).length, 7)
    m["reference_comparison"] = ref_comp

    return m


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=48, w=768, h=768):
    sc = bpy.context.scene
    sc.render.engine    = "CYCLES"
    sc.cycles.device    = "CPU"
    sc.cycles.samples   = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("B10World")
    sc.world.color = (0.06, 0.06, 0.08)


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_light(loc, tgt, energy, size, name="B10Light"):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name = name
    l.data.energy = energy
    l.data.size   = size
    d = Vector(tgt) - Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


def place_ortho_cam(loc, tgt, scale, name="B10Cam"):
    bpy.ops.object.camera_add(location=loc)
    c = bpy.context.active_object
    c.name = name
    c.data.type = "ORTHO"
    c.data.ortho_scale = scale
    d = Vector(tgt) - Vector(loc)
    c.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = c
    return c


def place_persp_cam(loc, tgt, lens, name="B10Cam"):
    bpy.ops.object.camera_add(location=loc)
    c = bpy.context.active_object
    c.name = name
    c.data.type = "PERSP"
    c.data.lens = lens
    d = Vector(tgt) - Vector(loc)
    c.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = c
    return c


def render_to(path, tag):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    ok = Path(path).exists()
    report["renders"][tag] = {"file": Path(path).name, "rendered": ok}
    return ok


def set_markers_visible(markers, visible):
    for m in markers:
        if m and m.name in bpy.data.objects:
            m.hide_render = not visible
            m.hide_viewport = not visible


# ─────────────────────────────────────────────────────────────────────────────
# MARCADORES
# ─────────────────────────────────────────────────────────────────────────────

MARKER_COLORS = {
    "joint":    "#FFDD00",  # amarelo
    "target":   "#00AAFF",  # azul
    "helper":   "#00FF88",  # verde
    "upperlid": "#FF6600",  # laranja
    "lowerlid": "#FF00AA",  # rosa
}

def create_markers(groups_data, body_h):
    markers = []
    legend  = {}
    r_base  = body_h * 0.006

    for gname, info in EYE_GROUPS.items():
        gd = groups_data.get(gname, {})
        if gd.get("status") != "complete" or not gd.get("centroid"):
            continue
        role  = info["role"]
        color = MARKER_COLORS.get(role, "#FFFFFF")
        size  = r_base * (1.4 if role == "helper" else 0.9 if role == "joint" else 0.7)
        cen   = gd["centroid"]

        mat = make_mat(f"B10_Mk_{gname}", color, roughness=0.1, emission_strength=3.0)
        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=size, segments=10, ring_count=6, location=cen)
        mk = bpy.context.active_object
        mk.name = f"B10_Marker_{gname}"
        assign_mat(mk, mat)
        markers.append(mk)

        if role not in legend:
            legend[role] = {"color": color, "description": {
                "helper":   "Centróide do grupo helper-*-eye (região orbital)",
                "joint":    "Centróide do joint ocular (ponto de articulação)",
                "target":   "Centróide do joint-*-eye-target (direcção do olhar)",
                "upperlid": "Centróide da pálpebra superior",
                "lowerlid": "Centróide da pálpebra inferior",
            }.get(role, role)}

    return markers, legend


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS DE DIAGNÓSTICO
# ─────────────────────────────────────────────────────────────────────────────

def create_diagnostic_eye(center, orient_vec, radius, side, variant_id, mat_sclera, mat_iris):
    """
    Cria olho de diagnóstico: esclera + íris.
    O eixo de orientação é dado pelo vector joint → target.
    """
    objects = []

    # Esclera
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=radius, segments=24, ring_count=12, location=center)
    sc = bpy.context.active_object
    sc.name = f"B10_Diag_Sclera_{side}_r{variant_id}"
    assign_mat(sc, mat_sclera)
    objects.append(sc)

    # Íris (disco orientado para a direcção joint→target)
    if orient_vec and orient_vec.length > 1e-6:
        iris_pos = Vector(center) + orient_vec.normalized() * radius * 0.88
    else:
        iris_pos = Vector(center) + Vector((0, -1, 0)) * radius * 0.88

    bpy.ops.mesh.primitive_circle_add(
        radius=radius * 0.55, vertices=24,
        fill_type="NGON", location=iris_pos)
    ir = bpy.context.active_object
    ir.name = f"B10_Diag_Iris_{side}_r{variant_id}"
    # Orientar o disco para a direcção do olhar
    if orient_vec and orient_vec.length > 1e-6:
        ir.rotation_euler = orient_vec.to_track_quat("-Z", "Y").to_euler()
    assign_mat(ir, mat_iris)
    objects.append(ir)

    return objects


# ─────────────────────────────────────────────────────────────────────────────
# INVESTIGAÇÃO DE ASSETS OFICIAIS DO MPFB
# ─────────────────────────────────────────────────────────────────────────────

def research_mpfb_eye_assets():
    """
    Investiga mecanismos oficiais do MPFB para:
    - Criar/carregar assets oculares
    - Posicionar globos oculares
    - Configurar materiais oculares
    Documenta o que encontra sem inventar APIs.
    """
    research = {
        "investigated_at": datetime.now(timezone.utc).isoformat(),
        "methods_found":   [],
        "operators_found": [],
        "data_paths_found": [],
        "conclusion": "",
        "errors":     [],
    }

    # 1. Procurar operadores mpfb relacionados com olhos
    eye_ops = []
    for op_module in dir(bpy.ops):
        try:
            mod = getattr(bpy.ops, op_module)
            for op_name in dir(mod):
                if any(k in op_name.lower() for k in ("eye", "lid", "iris", "pupil", "cornea")):
                    eye_ops.append(f"{op_module}.{op_name}")
        except Exception:
            pass

    research["operators_found"] = eye_ops
    log(f"Operadores oculares encontrados: {eye_ops}")

    # 2. Procurar módulos mpfb carregados que mencionem olhos
    import sys as _sys
    mpfb_eye_modules = [k for k in _sys.modules.keys()
                        if "mpfb" in k.lower() and any(w in k.lower()
                        for w in ("eye", "lid", "iris", "cornea", "pupil"))]
    research["mpfb_eye_modules"] = mpfb_eye_modules
    log(f"Módulos MPFB oculares: {mpfb_eye_modules}")

    # 3. Tentar importar módulos específicos do MPFB
    mpfb_paths_to_try = [
        "bl_ext.user_default.mpfb.entities.objectproperties",
        "bl_ext.user_default.mpfb.services.humanservice",
        "bl_ext.user_default.mpfb.services.eyeservice",
        "bl_ext.user_default.mpfb.entities.eyes",
    ]
    for path in mpfb_paths_to_try:
        try:
            import importlib
            mod = importlib.import_module(path)
            research["methods_found"].append({
                "module": path,
                "status": "importable",
                "attributes": [a for a in dir(mod) if not a.startswith("_")][:20],
            })
            log(f"Módulo importável: {path}")
        except ImportError:
            research["methods_found"].append({
                "module": path,
                "status": "not_found",
            })
        except Exception as e:
            research["methods_found"].append({
                "module": path,
                "status": "error",
                "error":  str(e),
            })

    # 4. Verificar se existe operador mpfb.add_eyes ou similar
    for candidate in ["mpfb.add_eyes", "mpfb.add_eye", "mpfb.create_eyes",
                       "mpfb.attach_eyes", "mpfb.setup_eyes"]:
        parts = candidate.split(".")
        try:
            op = getattr(getattr(bpy.ops, parts[0]), parts[1])
            research["data_paths_found"].append({
                "operator": candidate,
                "available": True,
            })
            log(f"Operador disponível: {candidate}")
        except AttributeError:
            research["data_paths_found"].append({
                "operator": candidate,
                "available": False,
            })
        except Exception as e:
            research["data_paths_found"].append({
                "operator": candidate,
                "available": False,
                "error": str(e),
            })

    # Conclusão
    has_eye_ops   = bool(eye_ops)
    has_modules   = bool(mpfb_eye_modules)
    has_importable = any(m.get("status") == "importable" for m in research["methods_found"])
    has_operator  = any(d.get("available") for d in research["data_paths_found"])

    if has_operator:
        research["conclusion"] = (
            "Encontrado operador oficial MPFB para olhos. "
            "Rever 'operators_found' e 'data_paths_found' para detalhes. "
            "Recomendado usar o operador oficial antes de criar esferas manuais."
        )
    elif has_importable:
        research["conclusion"] = (
            "Encontrados módulos MPFB importáveis relacionados com olhos. "
            "Sem operador directo identificado. "
            "Olhos temporários geométricos utilizados para este diagnóstico."
        )
    elif has_eye_ops or has_modules:
        research["conclusion"] = (
            "Indícios de mecanismos oculares no MPFB mas sem acesso directo confirmado. "
            "Olhos temporários geométricos utilizados para este diagnóstico."
        )
    else:
        research["conclusion"] = (
            "Nenhum mecanismo oficial MPFB para olhos identificado nesta instalação. "
            "O MPFB fornece apenas a malha de pele com grupos de referência ocular. "
            "Olhos temporários geométricos criados para diagnóstico usando "
            "os centróides dos grupos como referência anatómica."
        )

    log(f"Pesquisa MPFB: {research['conclusion'][:80]}...")
    return research


# ─────────────────────────────────────────────────────────────────────────────
# PROGRAMA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

try:

    # ── 1. INSTALAR MPFB ─────────────────────────────────────────────────────
    log("A instalar MPFB...")
    source = Path(os.environ["MPFB_ZIP"])
    with zipfile.ZipFile(source) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix   = manifest[:-len("blender_manifest.toml")]
        repacked = OUT / "mpfb-ext.zip"
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo  = next((r for r in repos if r.module == "user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default",
                         custom_directory=str(OUT / "extensions"))
        repo.enabled = True

    res = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if "FINISHED" not in res and "RUNNING_MODAL" not in res:
        raise RuntimeError(f"MPFB install: {res}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB não ficou activo: {module_id}")
    log(f"MPFB activo: {module_id}")

    # ── 2. INVESTIGAR ASSETS OCULARES OFICIAIS ───────────────────────────────
    log("A investigar assets oculares MPFB...")
    report["mpfb_asset_research"] = research_mpfb_eye_assets()

    # ── 3. CRIAR HUMANO BASE ─────────────────────────────────────────────────
    log("A criar humano...")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    res = bpy.ops.mpfb.create_human()
    if "FINISHED" not in res:
        raise RuntimeError(f"mpfb.create_human: {res}")

    meshes = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True)
    if not meshes:
        raise RuntimeError("Nenhuma malha após create_human")

    human = meshes[0][0]
    n_verts = len(human.data.vertices)
    log(f"Malha: {human.name} — {n_verts} vértices")

    report["human"] = {
        "name":    human.name,
        "vertices": n_verts,
        "expected": TARGET_VERTS,
        "vertex_count_matches_expected": n_verts == TARGET_VERTS,
        "shape_keys": len(human.data.shape_keys.key_blocks) if human.data.shape_keys else 0,
        "vertex_groups": len(human.vertex_groups),
    }
    if n_verts != TARGET_VERTS:
        report["limitations"].append(
            f"Contagem de vértices ({n_verts}) difere do esperado ({TARGET_VERTS}). "
            "As coordenadas de referência podem não ser directamente comparáveis."
        )

    # ── 4. MORPH FEMININO ────────────────────────────────────────────────────
    log("A aplicar morph feminino...")
    if human.data.shape_keys is None:
        raise RuntimeError("Humano sem shape keys — morph feminino impossível")

    keys     = human.data.shape_keys.key_blocks
    z_before = [v.co.z for v in human.data.vertices]

    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0

    bpy.context.view_layer.update()
    z_after  = [v.co.z for v in human.data.vertices]
    changed  = sum(abs(a - b) > 1e-6 for a, b in zip(z_before, z_after))
    log(f"Morph feminino: {changed} vértices alterados")

    report["female_morph"] = {
        "changed_vertices": changed,
        "status": "applied" if changed > 0 else "no_change",
    }
    if changed == 0:
        report["limitations"].append("Morph feminino não alterou nenhum vértice.")

    # ── 5. COORDENADAS AVALIADAS ─────────────────────────────────────────────
    log("A calcular coordenadas avaliadas...")
    eval_coords = evaluated_world_coords(human)
    log(f"Coordenadas avaliadas: {len(eval_coords)} vértices")

    # ── 6. CALCULAR CENTRÓIDES DOS GRUPOS OCULARES ───────────────────────────
    log("A calcular centróides dos grupos oculares...")
    groups_data = {}

    for gname in EYE_GROUPS:
        gd = group_centroid_evaluated(human, gname, eval_coords)
        groups_data[gname] = gd
        if gd["status"] == "complete":
            log(f"  {gname}: {gd['centroid']} ({gd['vertex_count']} verts)")
        else:
            log(f"  {gname}: INDISPONÍVEL — {gd.get('error','?')}")

    report["eye_groups"] = groups_data

    # Verificar grupos críticos
    critical = ["joint-l-eye", "joint-r-eye", "joint-l-eye-target", "joint-r-eye-target"]
    missing_critical = [g for g in critical if groups_data.get(g, {}).get("status") != "complete"]
    if missing_critical:
        report["limitations"].append(
            f"Grupos críticos ausentes: {missing_critical}. "
            "Posicionamento ocular baseado em referências alternativas."
        )

    # ── 7. MEDIÇÕES GEOMÉTRICAS ───────────────────────────────────────────────
    log("A calcular medições...")
    measurements = compute_measurements(groups_data, eval_coords, human)
    report["measurements"] = measurements

    # Guardar measurements.json separado
    (OUT / "measurements.json").write_text(
        json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Medições guardadas: {len(measurements)} entradas")

    # ── 8. DIMENSÕES DO CORPO E CABEÇA ───────────────────────────────────────
    wm   = human.matrix_world
    wvs  = [wm @ v.co for v in human.data.vertices]
    bmin = min(v.z for v in wvs)
    bmax = max(v.z for v in wvs)
    bh   = bmax - bmin

    # Cabeça = vértices acima de 80% da altura
    hv   = [v for v in wvs if v.z > bmin + bh * 0.80]
    hcx  = sum(v.x for v in hv) / len(hv)
    hcy  = sum(v.y for v in hv) / len(hv)
    hcz  = (max(v.z for v in hv) + min(v.z for v in hv)) / 2
    hspan = max(
        max(v.x for v in hv) - min(v.x for v in hv),
        max(v.y for v in hv) - min(v.y for v in hv),
        max(v.z for v in hv) - min(v.z for v in hv)) * 1.3
    head_center = Vector((hcx, hcy, hcz))
    face_y_front = min(v.y for v in hv)

    log(f"Altura corpo: {bh:.4f}m | Cabeça: ({hcx:.4f},{hcy:.4f},{hcz:.4f}) span={hspan:.4f}")

    # ── 9. POSIÇÃO DE REFERÊNCIA DOS OLHOS ───────────────────────────────────
    # Escolher centróides: preferir joint-l-eye, fallback helper-l-eye, fallback histórico

    def best_center(primary, fallback, ref_historic):
        pd = groups_data.get(primary, {})
        fd = groups_data.get(fallback, {})
        if pd.get("status") == "complete" and pd.get("centroid"):
            return Vector(pd["centroid"]), primary
        if fd.get("status") == "complete" and fd.get("centroid"):
            return Vector(fd["centroid"]), fallback
        report["limitations"].append(
            f"Grupos {primary} e {fallback} indisponíveis. "
            f"Usando referência histórica: {v3(ref_historic)}. "
            "ESTE POSICIONAMENTO PODE NÃO SER PRECISO."
        )
        return ref_historic, "historic_reference"

    eye_cL, eye_src_L = best_center("joint-l-eye", "helper-l-eye", REFERENCE["left_eye"])
    eye_cR, eye_src_R = best_center("joint-r-eye", "helper-r-eye", REFERENCE["right_eye"])

    # Vectores de orientação
    tL = groups_data.get("joint-l-eye-target", {}).get("centroid")
    tR = groups_data.get("joint-r-eye-target", {}).get("centroid")
    orient_L = (Vector(tL) - eye_cL).normalized() if tL else Vector((0, -1, 0))
    orient_R = (Vector(tR) - eye_cR).normalized() if tR else Vector((0, -1, 0))

    report["eye_position_sources"] = {
        "L": {"center": v3(eye_cL), "source": eye_src_L,
              "orientation": v3(orient_L)},
        "R": {"center": v3(eye_cR), "source": eye_src_R,
              "orientation": v3(orient_R)},
    }
    log(f"Centro L ({eye_src_L}): {v3(eye_cL)}")
    log(f"Centro R ({eye_src_R}): {v3(eye_cR)}")
    log(f"Orientação L: {v3(orient_L)}")
    log(f"Orientação R: {v3(orient_R)}")

    # ── 10. APLICAR MATERIAL DE PELE ─────────────────────────────────────────
    skin_mat = make_mat("B10_Skin", "#A66A48", roughness=0.52)
    assign_mat(human, skin_mat)

    # ── 11. CRIAR MARCADORES GEOMÉTRICOS ─────────────────────────────────────
    log("A criar marcadores geométricos...")
    markers, legend = create_markers(groups_data, bh)
    report["markers_legend"] = legend
    log(f"Marcadores criados: {len(markers)}")

    # ── 12. CONFIGURAR RENDER ─────────────────────────────────────────────────
    log("A configurar render...")
    setup_render(samples=48)

    # ══════════════════════════════════════════════════════════════════════════
    # SÉRIE DE VARIANTES — raios × deslocamentos
    # ══════════════════════════════════════════════════════════════════════════
    log(f"A gerar {len(DIAGNOSTIC_RADII) * len(DIAGNOSTIC_OFFSETS)} variantes...")
    variants_data = []

    mat_sclera = make_mat("B10_Sclera", "#F5F0E8", roughness=0.05)
    mat_iris_L = make_mat("B10_IrisL",  "#5C3A1E", roughness=0.10)
    mat_iris_R = make_mat("B10_IrisR",  "#5C3A1E", roughness=0.10)

    for ri, radius in enumerate(DIAGNOSTIC_RADII):
        for oi, offset in enumerate(DIAGNOSTIC_OFFSETS):
            vid = f"r{ri}_o{oi}"

            # Posição com offset ao longo do vector de orientação
            cL_off = eye_cL + orient_L * offset
            cR_off = eye_cR + orient_R * offset

            # Criar olhos
            eye_objs = []
            eye_objs += create_diagnostic_eye(cL_off, orient_L, radius, "L", vid, mat_sclera, mat_iris_L)
            eye_objs += create_diagnostic_eye(cR_off, orient_R, radius, "R", vid, mat_sclera, mat_iris_R)

            # Renderizar apenas a variante com radius=DIAGNOSTIC_RADII[1], offset=0.0
            # (variante central) para evitar centenas de imagens
            is_reference_variant = (radius == DIAGNOSTIC_RADII[1] and offset == 0.0)

            variant_info = {
                "variant_id":   vid,
                "radius_m":     radius,
                "offset_m":     offset,
                "center_L":     v3(cL_off),
                "center_R":     v3(cR_off),
                "orientation_L": v3(orient_L),
                "orientation_R": v3(orient_R),
                "objects_created": [o.name for o in eye_objs],
                "is_reference_variant": is_reference_variant,
                "rendered":     False,
                "note":         "Posicionamento não validado anatomicamente",
            }

            # Calcular distâncias dos olhos à superfície facial
            eye_cL_depth = round(cL_off.y - face_y_front, 6)
            eye_cR_depth = round(cR_off.y - face_y_front, 6)
            variant_info["depth_behind_face_L"] = eye_cL_depth
            variant_info["depth_behind_face_R"] = eye_cR_depth
            variant_info["eye_surface_y_L"] = round(cL_off.y - radius, 6)
            variant_info["eye_surface_y_R"] = round(cR_off.y - radius, 6)
            variant_info["intersects_face_L"] = (cL_off.y - radius) < face_y_front
            variant_info["intersects_face_R"] = (cR_off.y - radius) < face_y_front

            variants_data.append(variant_info)

            # Remover olhos (serão recriados para render das variantes seleccionadas)
            for o in eye_objs:
                if o.name in bpy.data.objects:
                    bpy.data.objects.remove(o, do_unlink=True)

    report["diagnostic_variants"] = variants_data
    log(f"Variantes calculadas: {len(variants_data)}")

    # ══════════════════════════════════════════════════════════════════════════
    # RENDERS PRINCIPAIS
    # ══════════════════════════════════════════════════════════════════════════
    # Criar olhos da variante de referência para renders
    ref_radius = DIAGNOSTIC_RADII[1]   # 0.013m
    ref_offset = 0.0
    cL_ref = eye_cL + orient_L * ref_offset
    cR_ref = eye_cR + orient_R * ref_offset
    render_eye_objs = []
    render_eye_objs += create_diagnostic_eye(cL_ref, orient_L, ref_radius, "L", "ref", mat_sclera, mat_iris_L)
    render_eye_objs += create_diagnostic_eye(cR_ref, orient_R, ref_radius, "R", "ref", mat_sclera, mat_iris_R)
    log(f"Olhos de referência criados: raio={ref_radius}m, offset={ref_offset}m")

    # Centro médio dos olhos para enquadramento
    eye_mid    = Vector(((cL_ref.x + cR_ref.x)/2, (cL_ref.y + cR_ref.y)/2,
                          (cL_ref.z + cR_ref.z)/2))
    eye_sep    = abs(cL_ref.x - cR_ref.x)
    eye_span   = max(eye_sep * 2.5, ref_radius * 8)
    face_front = min(v.y for v in hv)

    # ── RENDER 1: Frontal diagnóstico (com marcadores) ────────────────────────
    log("Render: front_diagnostic.png")
    set_markers_visible(markers, True)
    clear_lights_cameras()
    add_light((hcx + 0.3, face_front - 0.5, hcz + 0.2),
              head_center, energy=600, size=0.4, name="B10L1")
    add_light((hcx - 0.2, face_front - 0.4, hcz + 0.3),
              head_center, energy=350, size=0.3, name="B10L2")
    place_ortho_cam((hcx, face_front - hspan * 2, hcz),
                    head_center, hspan * 1.2, "B10Cam")
    render_to(OUT_RENDERS / "front_diagnostic.png", "front_diagnostic")

    # ── RENDER 2: Frontal limpo (sem marcadores) ──────────────────────────────
    log("Render: front_clean.png")
    set_markers_visible(markers, False)
    render_to(OUT_RENDERS / "front_clean.png", "front_clean")

    # ── RENDER 3: Lateral esquerda ────────────────────────────────────────────
    log("Render: left_side.png")
    clear_lights_cameras()
    add_light((hcx + 0.5, face_front - 0.3, hcz + 0.2),
              head_center, energy=500, size=0.4, name="B10L1")
    add_light((hcx + 0.1, face_front - 0.2, hcz + 0.1),
              head_center, energy=250, size=0.3, name="B10L2")
    place_ortho_cam((hcx + hspan * 2, hcy, hcz), head_center, hspan * 1.1, "B10Cam")
    render_to(OUT_RENDERS / "left_side.png", "left_side")

    # ── RENDER 4: Lateral direita ─────────────────────────────────────────────
    log("Render: right_side.png")
    clear_lights_cameras()
    add_light((hcx - 0.5, face_front - 0.3, hcz + 0.2),
              head_center, energy=500, size=0.4, name="B10L1")
    add_light((hcx - 0.1, face_front - 0.2, hcz + 0.1),
              head_center, energy=250, size=0.3, name="B10L2")
    place_ortho_cam((hcx - hspan * 2, hcy, hcz), head_center, hspan * 1.1, "B10Cam")
    render_to(OUT_RENDERS / "right_side.png", "right_side")

    # ── RENDER 5: Vista superior ──────────────────────────────────────────────
    log("Render: top.png")
    clear_lights_cameras()
    add_light((hcx, hcy - 0.2, bmax + 0.5), head_center, energy=600, size=0.5, name="B10L1")
    add_light((hcx + 0.3, hcy - 0.1, bmax + 0.3), head_center, energy=300, size=0.3, name="B10L2")
    place_ortho_cam((hcx, hcy, bmax + hspan * 2), head_center, hspan * 1.1, "B10Cam")
    render_to(OUT_RENDERS / "top.png", "top")

    # ── RENDER 6: Close-up frontal dos olhos (com marcadores) ─────────────────
    log("Render: eye_closeup_front.png")
    set_markers_visible(markers, True)
    clear_lights_cameras()
    add_light((hcx + eye_sep * 0.8, face_front - eye_span, eye_mid.z + ref_radius),
              eye_mid, energy=250, size=0.2, name="B10L1")
    add_light((hcx - eye_sep * 0.6, face_front - eye_span * 0.8, eye_mid.z),
              eye_mid, energy=150, size=0.15, name="B10L2")
    place_ortho_cam((hcx, face_front - eye_span * 3, eye_mid.z),
                    eye_mid, eye_span * 1.3, "B10Cam")
    render_to(OUT_RENDERS / "eye_closeup_front.png", "eye_closeup_front")

    # ── RENDER 7: Close-up lateral dos olhos ─────────────────────────────────
    log("Render: eye_closeup_side.png")
    clear_lights_cameras()
    add_light((hcx + eye_sep * 1.5, face_front - eye_span, eye_mid.z + ref_radius),
              eye_mid, energy=200, size=0.15, name="B10L1")
    add_light((hcx + eye_sep * 0.5, face_front - eye_span * 0.5, eye_mid.z),
              eye_mid, energy=100, size=0.1, name="B10L2")
    place_persp_cam((hcx + eye_span * 3, face_front, eye_mid.z),
                    eye_mid, 85, "B10Cam")
    render_to(OUT_RENDERS / "eye_closeup_side.png", "eye_closeup_side")

    # ── 13. GRAVAR .BLEND ─────────────────────────────────────────────────────
    blend_path = OUT / "b10_diagnostic.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    report["blend_saved"] = blend_path.exists()
    log(f"BLEND guardado: {blend_path.exists()}")

    # ── 14. VALIDAÇÕES ────────────────────────────────────────────────────────
    report["validations"] = {
        "human_created":         True,
        "vertex_count_expected": n_verts == TARGET_VERTS,
        "vertex_count_found":    n_verts,
        "female_morph_applied":  changed > 0,
        "eye_groups_found":      {g: (groups_data.get(g, {}).get("status") == "complete")
                                  for g in EYE_GROUPS},
        "centers_calculated_from_evaluated_coords": True,
        "world_coordinates_used": True,
        "diagnostic_eyes_created": len(render_eye_objs) > 0,
        "renders_attempted":     len(report["renders"]),
        "renders_successful":    sum(1 for r in report["renders"].values() if r.get("rendered")),
        "blend_saved":           blend_path.exists(),
        "measurements_saved":    (OUT / "measurements.json").exists(),
        "success_technical":     True,
        "validation_anatomical": False,
        "validation_note": (
            "Sucesso técnico ≠ validação anatómica. "
            "Inspeccionar imagens para determinar se os olhos estão correctamente posicionados."
        ),
    }

    report["status"] = "success_technical"
    log("Estado: sucesso técnico. Validação anatómica requer inspecção das imagens.")

except Exception as exc:
    report["status"] = "failed"
    report["errors"].append({"error": str(exc), "traceback": traceback.format_exc()})
    log(f"ERRO: {exc}")
    traceback.print_exc()

finally:
    # Relatório principal
    diag_path = OUT / "diagnostic_report.json"
    diag_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Relatório guardado: {diag_path}")

    # Relatório de validação em Markdown
    val_lines = [
        "# B10 — Atlas Eye Diagnostic — Validation Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Status",
        f"**{report.get('status','?')}**",
        "",
        "## Grupos oculares",
    ]
    for gname, gd in report.get("eye_groups", {}).items():
        s = "✅" if gd.get("status") == "complete" else "❌"
        c = str(gd.get("centroid","N/A"))
        val_lines.append(f"- {s} `{gname}`: {c}")
    val_lines += [
        "",
        "## Medições",
        f"- Distância interocular (joint): {report.get('measurements',{}).get('interocular_distance_joint','N/A')}",
        f"- Distância joint→target L: {report.get('measurements',{}).get('joint_to_target_L','N/A')}",
        f"- Distância joint→target R: {report.get('measurements',{}).get('joint_to_target_R','N/A')}",
        "",
        "## Validações técnicas",
    ]
    for k, v in report.get("validations", {}).items():
        if isinstance(v, bool):
            val_lines.append(f"- {'✅' if v else '❌'} {k}: {v}")
    val_lines += [
        "",
        "## Limitações",
    ]
    for lim in report.get("limitations", []):
        val_lines.append(f"- ⚠️ {lim}")
    val_lines += [
        "",
        "## Nota",
        "> Sucesso técnico não implica validação anatómica.",
        "> Inspeccionar imagens em `renders/` para avaliação visual.",
    ]
    (OUT / "validation_report.md").write_text("\n".join(val_lines), encoding="utf-8")

    # Pesquisa de assets MPFB em Markdown
    ar = report.get("mpfb_asset_research", {})
    res_lines = [
        "# B10 — MPFB Eye Asset Research",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Conclusão",
        ar.get("conclusion", "N/A"),
        "",
        "## Operadores oculares encontrados",
    ]
    for op in ar.get("operators_found", []):
        res_lines.append(f"- `{op}`")
    if not ar.get("operators_found"):
        res_lines.append("- Nenhum operador ocular encontrado.")
    res_lines += [
        "",
        "## Módulos MPFB oculares",
    ]
    for mod in ar.get("mpfb_eye_modules", []):
        res_lines.append(f"- `{mod}`")
    if not ar.get("mpfb_eye_modules"):
        res_lines.append("- Nenhum módulo ocular encontrado.")
    res_lines += [
        "",
        "## Módulos testados",
    ]
    for m in ar.get("methods_found", []):
        res_lines.append(f"- `{m['module']}`: {m['status']}")
    res_lines += [
        "",
        "## Operadores candidatos verificados",
    ]
    for d in ar.get("data_paths_found", []):
        av = "✅" if d.get("available") else "❌"
        res_lines.append(f"- {av} `{d['operator']}`")

    (OUT / "mpfb_eye_asset_research.md").write_text("\n".join(res_lines), encoding="utf-8")

    print("MPFB_B10_REPORT:", json.dumps({
        "status":          report.get("status"),
        "vertices":        report.get("human", {}).get("vertices"),
        "groups_found":    {g: (report.get("eye_groups", {}).get(g, {}).get("status") == "complete")
                            for g in EYE_GROUPS},
        "renders":         {k: v.get("rendered") for k, v in report.get("renders", {}).items()},
        "blend_saved":     report.get("blend_saved"),
        "errors":          len(report.get("errors", [])),
    }, ensure_ascii=False))

if report["status"] == "failed":
    sys.exit(1)
