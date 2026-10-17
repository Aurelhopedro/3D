"""
diagnose_b9_eye_placement.py — Diagnóstico geométrico completo do posicionamento
ocular da B9 Rev2.

CONTEXTO
--------
A B9 Rev2 colocou os olhos sobre a testa, não nas órbitas.
Este script não tenta corrigir — apenas mede e documenta.

TAREFAS
-------
1. Recriar a personagem B9 (morph + escala) sem construir olhos.
2. Para cada objeto ocular hipotético, calcular:
   - object.location (seria)
   - matrix_world.translation
   - Centro mundial a partir dos vértices
   - Bounding box mundial
3. Investigar as órbitas:
   - Posição da abertura ocular por análise da curvatura da malha
   - Grupos nativos como referência, não como posição final
   - Superfície facial local para cada olho separadamente
4. Calcular distâncias entre:
   - Centro do grupo helper-eye (centróide geométrico)
   - Bounding box mínimo Y do grupo (ponto mais frontal)
   - Estimativa da abertura orbital por análise de curvatura
5. Render diagnóstico com marcadores coloridos:
   - Vermelho: centro do grupo helper-eye
   - Verde: ponto mais frontal do grupo (Y mínimo)
   - Azul: estimativa da abertura orbital (curvatura)
   - Amarelo: onde a B9 Rev2 colocou a esclera
   - Ciano: bounding box do grupo
6. Verificar dimensões:
   - Diâmetro da esclera B9 Rev2 vs geometria nativa

PRESERVAÇÃO
-----------
Não modifica B5, B7, B8, B9 nem diagnósticos anteriores.
Apenas cria a cena base (human + morph + escala) para medição.
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

TARGET_HEIGHT_M = 1.70

out = Path(os.environ.get("MPFB_OUTPUT", "output/diagnose-b9-eyes")).resolve()
out.mkdir(parents=True, exist_ok=True)

# Valores da B9 Rev2 (para comparação directa)
B9_REV2 = {
    "center_L":          Vector(( 0.030777, -0.124536, 1.546157)),
    "center_R":          Vector((-0.030777, -0.124536, 1.546157)),
    "sclera_y_target":   -0.175261,
    "face_y_at_eye_level": -0.16554,
    "sclera_radius":     0.017157,
    "iris_radius":       0.009951,
    "group_y_range":     (-0.1392, -0.10987),
}

report = {
    "diagnostic":  "b9_eye_placement",
    "version":     "1.0",
    "blender":     bpy.app.version_string,
    "status":      "started",
    "b9_rev2_reference": {
        "center_L":           list(B9_REV2["center_L"]),
        "center_R":           list(B9_REV2["center_R"]),
        "sclera_y_target":    B9_REV2["sclera_y_target"],
        "face_y_at_eye_level":B9_REV2["face_y_at_eye_level"],
        "sclera_radius_m":    B9_REV2["sclera_radius"],
        "sclera_diameter_mm": round(B9_REV2["sclera_radius"] * 2 * 1000, 1),
        "native_eye_dim_mm":  27.6,
        "size_ratio":         round((B9_REV2["sclera_radius"] * 2) / 0.0276, 3),
    },
    "body": {},
    "groups": {},
    "orbital_analysis": {},
    "marker_positions": {},
    "distances": {},
    "renders": {},
    "conclusions": [],
    "errors": [],
}


def log(msg):
    print(f"[DIAG_B9] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def v3(v, d=6):
    return [round(v.x, d), round(v.y, d), round(v.z, d)]


def make_mat_emission(name, r, g, b, strength=5.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    if "Emission Color" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    bsdf.inputs["Roughness"].default_value = 0.3
    out_node = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def sphere_marker(loc, name, mat, radius=0.004):
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=radius, segments=12, ring_count=8, location=loc)
    m = bpy.context.active_object
    m.name = name
    if m.data.materials:
        m.data.materials[0] = mat
    else:
        m.data.materials.append(mat)
    return m


def cylinder_marker(loc, name, mat, radius=0.002, depth=0.02):
    """Marcador cilíndrico orientado para Y (eixo de profundidade)."""
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=8, radius=radius, depth=depth, location=loc)
    m = bpy.context.active_object
    m.name = name
    m.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    if m.data.materials:
        m.data.materials[0] = mat
    else:
        m.data.materials.append(mat)
    return m


def wire_box(center, dims, name, mat):
    """Cria um wireframe cúbico representando a bounding box."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    box = bpy.context.active_object
    box.name = name
    box.scale = (dims[0] / 2, dims[1] / 2, dims[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    # Converter para wireframe
    box.display_type = "WIRE"
    if box.data.materials:
        box.data.materials[0] = mat
    else:
        box.data.materials.append(mat)
    return box


def add_line(p1, p2, name, mat, thickness=0.001):
    """Linha entre dois pontos implementada como cilindro."""
    mid = (Vector(p1) + Vector(p2)) / 2
    diff = Vector(p2) - Vector(p1)
    length = diff.length
    if length < 1e-6:
        return None
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=6, radius=thickness, depth=length, location=mid)
    ln = bpy.context.active_object
    ln.name = name
    ln.rotation_euler = diff.to_track_quat("Z", "Y").to_euler()
    bpy.ops.object.transform_apply(rotation=True)
    if ln.data.materials:
        ln.data.materials[0] = mat
    else:
        ln.data.materials.append(mat)
    return ln


def setup_render(w=1024, h=1024, samples=48):
    sc = bpy.context.scene
    sc.render.engine   = "CYCLES"
    sc.cycles.device   = "CPU"
    sc.cycles.samples  = samples
    sc.render.resolution_x          = w
    sc.render.resolution_y          = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.04, 0.04, 0.06)


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def place_cam_ortho(loc, target, scale, name):
    clear_lights_cameras()
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = scale
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    # Luz ambiente suave + luz direcional
    bpy.ops.object.light_add(type="AREA", location=(
        loc[0] + 0.3, loc[1] + 0.3, loc[2] + 0.3))
    l1 = bpy.context.active_object
    l1.name = "DiagLight1"
    l1.data.energy = 500
    l1.data.size   = 0.5
    d1 = Vector(target) - l1.location
    l1.rotation_euler = d1.to_track_quat("-Z", "Y").to_euler()

    bpy.ops.object.light_add(type="AREA", location=(
        loc[0] - 0.2, loc[1] + 0.2, loc[2] - 0.1))
    l2 = bpy.context.active_object
    l2.name = "DiagLight2"
    l2.data.energy = 300
    l2.data.size   = 0.4
    d2 = Vector(target) - l2.location
    l2.rotation_euler = d2.to_track_quat("-Z", "Y").to_euler()

    return cam


def do_render(path, tag):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    exists = Path(path).exists()
    report["renders"][tag] = {"rendered": exists, "file": Path(path).name}
    log(f"  Render {tag}: {exists}")
    return exists


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISE ORBITAL — curvatura e abertura
# ─────────────────────────────────────────────────────────────────────────────

def analyse_orbital_opening(human, center_hint, search_radius, side_label):
    """
    Analisa a geometria facial em torno de center_hint para encontrar
    a abertura orbital real.

    Método:
    1. Recolher todos os vértices dentro de search_radius em XZ.
    2. Para cada fatia horizontal (Z), encontrar o Y mais frontal (min Y).
    3. Calcular o ponto de inflexão: onde dY/dZ muda de sinal (côncavo → convexo).
       Esse ponto corresponde à borda inferior da órbita (onde a pele começa a
       curvar para dentro em direcção ao globo ocular).
    4. Calcular o centróide da região côncava (Y mais negativo local).

    Devolve dict com:
      orbital_center_estimate — estimativa do centro da abertura orbital
      orbital_depth_min_y     — Y mais frontal na região orbital
      orbital_z_range         — faixa Z da abertura
      vertex_count            — vértices analisados
    """
    wmat = human.matrix_world
    cx   = center_hint.x
    cz   = center_hint.z

    # Recolher vértices na zona orbital
    zone_verts = []
    for v in human.data.vertices:
        wco = wmat @ v.co
        dx = abs(wco.x - cx)
        dz = abs(wco.z - cz)
        if dx <= search_radius and dz <= search_radius:
            zone_verts.append(wco)

    if len(zone_verts) < 10:
        return {"error": "poucos vertices na zona", "vertex_count": len(zone_verts)}

    # Ordenar por Z e criar fatias
    zone_verts.sort(key=lambda v: v.z)
    z_min = zone_verts[0].z
    z_max = zone_verts[-1].z
    z_range = z_max - z_min

    N_SLICES = 16
    slice_height = z_range / N_SLICES
    slices = []
    for i in range(N_SLICES):
        z_lo = z_min + i * slice_height
        z_hi = z_lo + slice_height
        verts_in_slice = [v for v in zone_verts if z_lo <= v.z < z_hi]
        if verts_in_slice:
            min_y = min(v.y for v in verts_in_slice)
            avg_x = sum(v.x for v in verts_in_slice) / len(verts_in_slice)
            avg_z = sum(v.z for v in verts_in_slice) / len(verts_in_slice)
            slices.append({
                "z_center": round((z_lo + z_hi) / 2, 5),
                "min_y":    round(min_y, 5),
                "avg_x":    round(avg_x, 5),
                "count":    len(verts_in_slice),
            })

    if not slices:
        return {"error": "sem fatias", "vertex_count": len(zone_verts)}

    # Encontrar a fatia com Y mais negativo (mais frontal = mais à frente)
    deepest = min(slices, key=lambda s: s["min_y"])

    # Calcular o ponto de curvatura máxima (convexidade local):
    # Procurar a fatia onde dY/dZ é mínimo (Y desce mais rapidamente)
    dy_dz_list = []
    for i in range(1, len(slices)):
        dz = slices[i]["z_center"] - slices[i-1]["z_center"]
        dy = slices[i]["min_y"]    - slices[i-1]["min_y"]
        if abs(dz) > 1e-6:
            dy_dz_list.append({
                "z": (slices[i]["z_center"] + slices[i-1]["z_center"]) / 2,
                "dy_dz": round(dy / dz, 4),
                "min_y": slices[i]["min_y"],
            })

    # Ponto de inflexão: máxima variação de curvatura (onde Y para de descer)
    if len(dy_dz_list) >= 2:
        # Encontrar onde dy_dz é mínimo (Y desce mais = orbital rim inferior)
        inflection = min(dy_dz_list, key=lambda x: x["dy_dz"])
    else:
        inflection = None

    # Centróide da região mais côncava (20% de slices com Y mais negativo)
    n_deep = max(1, len(slices) // 5)
    deep_slices = sorted(slices, key=lambda s: s["min_y"])[:n_deep]
    orb_x = sum(s["avg_x"] for s in deep_slices) / len(deep_slices)
    orb_y = sum(s["min_y"] for s in deep_slices) / len(deep_slices)
    orb_z = sum(s["z_center"] for s in deep_slices) / len(deep_slices)

    return {
        "vertex_count":             len(zone_verts),
        "search_radius":            round(search_radius, 4),
        "z_range":                  [round(z_min, 5), round(z_max, 5)],
        "deepest_slice":            deepest,
        "orbital_depth_min_y":      deepest["min_y"],
        "orbital_center_estimate":  [round(orb_x, 5), round(orb_y, 5), round(orb_z, 5)],
        "inflection_point":         inflection,
        "slices":                   slices,
        "side":                     side_label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISE DE GRUPO
# ─────────────────────────────────────────────────────────────────────────────

def analyse_group_full(human, group_name):
    if group_name not in human.vertex_groups:
        return None
    gidx = human.vertex_groups[group_name].index
    wmat = human.matrix_world
    coords = []
    for v in human.data.vertices:
        for g in v.groups:
            if g.group == gidx and g.weight > 0:
                coords.append(wmat @ v.co)
                break
    if not coords:
        return {"vertex_count": 0}

    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]

    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    cz = sum(zs) / len(zs)

    face_count = sum(
        1 for f in human.data.polygons
        if all(vi in {v.index for v in human.data.vertices
                      for g2 in v.groups if g2.group == gidx and g2.weight > 0}
               for vi in f.vertices)
    )

    # Ponto mais frontal (Y mínimo) e mais traseiro (Y máximo)
    min_y_vert = min(coords, key=lambda v: v.y)
    max_y_vert = max(coords, key=lambda v: v.y)

    # Distribuição de Y em decis
    ys_sorted = sorted(ys)
    n = len(ys_sorted)
    percentiles = {
        "p10": round(ys_sorted[n // 10], 5),
        "p25": round(ys_sorted[n // 4],  5),
        "p50": round(ys_sorted[n // 2],  5),
        "p75": round(ys_sorted[3 * n // 4], 5),
        "p90": round(ys_sorted[9 * n // 10], 5),
    }

    return {
        "group_name":     group_name,
        "vertex_count":   len(coords),
        "face_count":     face_count,
        "centroid":       [round(cx, 6), round(cy, 6), round(cz, 6)],
        "bbox": {
            "x": [round(min(xs), 6), round(max(xs), 6)],
            "y": [round(min(ys), 6), round(max(ys), 6)],
            "z": [round(min(zs), 6), round(max(zs), 6)],
        },
        "dimensions_m": {
            "x": round(max(xs) - min(xs), 6),
            "y": round(max(ys) - min(ys), 6),
            "z": round(max(zs) - min(zs), 6),
        },
        "dimensions_mm": {
            "x": round((max(xs) - min(xs)) * 1000, 2),
            "y": round((max(ys) - min(ys)) * 1000, 2),
            "z": round((max(zs) - min(zs)) * 1000, 2),
        },
        "point_most_front":  v3(min_y_vert),   # Y mais negativo = mais à frente
        "point_most_back":   v3(max_y_vert),
        "y_percentiles":     percentiles,
        "y_range":           [round(min(ys), 6), round(max(ys), 6)],
    }


# ─────────────────────────────────────────────────────────────────────────────
# INÍCIO
# ─────────────────────────────────────────────────────────────────────────────

try:

    # ── INSTALAR MPFB ────────────────────────────────────────────────────────
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
        raise RuntimeError(f"MPFB nao ficou activo: {module_id}")
    log("MPFB activo")

    # ── CRIAR HUMANO ─────────────────────────────────────────────────────────
    log("A criar humano...")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    bpy.ops.mpfb.create_human()
    bpy.context.view_layer.update()

    meshes = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True,
    )
    if not meshes:
        raise RuntimeError("Nenhuma mesh encontrada")
    human = meshes[0][0]

    # ── MORPH FEMININO ────────────────────────────────────────────────────────
    log("A aplicar morph feminino...")
    if human.data.shape_keys:
        for key in human.data.shape_keys.key_blocks:
            n = key.name
            if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
                if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                    key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
                elif n.startswith("$md-universal-"):
                    key.value = 1.0 if "-$fe-" in n else 0.0
    bpy.context.view_layer.update()

    # ── ESCALA PARA 1.70m ────────────────────────────────────────────────────
    log("A escalar para 1.70m...")
    bpy.ops.object.select_all(action="DESELECT")
    human.select_set(True)
    bpy.context.view_layer.objects.active = human
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()

    vz = [v.co.z for v in human.data.vertices]
    h_initial = max(vz) - min(vz)
    factor    = TARGET_HEIGHT_M / h_initial

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(human.data)
    bm.verts.ensure_lookup_table()
    z_min = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z = z_min + (v.co.z - z_min) * factor
        v.co.x *= factor
        v.co.y *= factor
    bmesh.update_edit_mesh(human.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()

    wmat  = human.matrix_world
    all_w = [wmat @ v.co for v in human.data.vertices]
    body_min_z = min(v.z for v in all_w)
    body_max_z = max(v.z for v in all_w)
    body_h     = body_max_z - body_min_z
    head_radius = body_h * 0.075
    head_center_z = body_min_z + body_h * 0.920

    report["body"] = {
        "vertices":      len(human.data.vertices),
        "body_min_z":    round(body_min_z, 6),
        "body_max_z":    round(body_max_z, 6),
        "body_h_m":      round(body_h, 6),
        "head_center_z": round(head_center_z, 6),
        "head_radius_m": round(head_radius, 6),
        "head_radius_mm": round(head_radius * 1000, 1),
    }
    log(f"Corpo: min_z={body_min_z:.4f} max_z={body_max_z:.4f} h={body_h:.4f}")

    # ── ANÁLISE DOS GRUPOS ────────────────────────────────────────────────────
    log("A analisar grupos helper-eye...")
    gL = analyse_group_full(human, "helper-l-eye")
    gR = analyse_group_full(human, "helper-r-eye")

    if not gL or not gR:
        raise RuntimeError("Grupos helper-eye não encontrados")

    report["groups"] = {
        "helper_l_eye": gL,
        "helper_r_eye": gR,
    }

    log(f"Grupo L: centróide={gL['centroid']}  bbox_y={gL['bbox']['y']}  dims_mm={gL['dimensions_mm']}")
    log(f"Grupo R: centróide={gR['centroid']}  bbox_y={gR['bbox']['y']}  dims_mm={gR['dimensions_mm']}")

    # ── ANÁLISE ORBITAL ───────────────────────────────────────────────────────
    log("A analisar abertura orbital...")

    # Raio de busca: usar a dimensão do grupo + 50%
    search_r = max(gL["dimensions_m"]["x"], gL["dimensions_m"]["z"]) * 1.5

    center_L_hint = Vector(gL["centroid"])
    center_R_hint = Vector(gR["centroid"])

    orb_L = analyse_orbital_opening(human, center_L_hint, search_r, "L")
    orb_R = analyse_orbital_opening(human, center_R_hint, search_r, "R")

    report["orbital_analysis"] = {
        "search_radius_m":  round(search_r, 5),
        "left":  orb_L,
        "right": orb_R,
    }

    log(f"Orbital L: centro_estimado={orb_L.get('orbital_center_estimate')}  min_y={orb_L.get('orbital_depth_min_y')}")
    log(f"Orbital R: centro_estimado={orb_R.get('orbital_center_estimate')}  min_y={orb_R.get('orbital_depth_min_y')}")

    # ── POSIÇÕES DOS MARCADORES ───────────────────────────────────────────────
    # Marcador Vermelho: centróide do grupo helper (= o que B9 Rev2 usou como base)
    red_L  = Vector(gL["centroid"])
    red_R  = Vector(gR["centroid"])

    # Marcador Verde: ponto mais frontal do grupo (Y mínimo do bbox)
    green_L = Vector((gL["centroid"][0], gL["bbox"]["y"][0], gL["centroid"][2]))
    green_R = Vector((gR["centroid"][0], gR["bbox"]["y"][0], gR["centroid"][2]))

    # Marcador Azul: estimativa orbital por curvatura
    orb_c_L = orb_L.get("orbital_center_estimate", gL["centroid"])
    orb_c_R = orb_R.get("orbital_center_estimate", gR["centroid"])
    blue_L  = Vector(orb_c_L)
    blue_R  = Vector(orb_c_R)

    # Marcador Amarelo: onde a B9 Rev2 colocou a esclera
    yellow_L = Vector((B9_REV2["center_L"].x, B9_REV2["sclera_y_target"], B9_REV2["center_L"].z))
    yellow_R = Vector((B9_REV2["center_R"].x, B9_REV2["sclera_y_target"], B9_REV2["center_R"].z))

    # Marcador Magenta: bounding box mínimo Y do grupo (ponto mais frontal individual)
    magenta_L = Vector(gL["point_most_front"])
    magenta_R = Vector(gR["point_most_front"])

    report["marker_positions"] = {
        "RED_helper_centroid": {
            "L": v3(red_L),   "R": v3(red_R),
            "description": "Centroide do grupo helper-eye (base da B9 Rev2)",
        },
        "GREEN_helper_min_y": {
            "L": v3(green_L), "R": v3(green_R),
            "description": "Ponto mais frontal do grupo (Y minimo do bbox)",
        },
        "BLUE_orbital_estimate": {
            "L": v3(blue_L),  "R": v3(blue_R),
            "description": "Estimativa da abertura orbital por analise de curvatura",
        },
        "YELLOW_b9_sclera": {
            "L": v3(yellow_L), "R": v3(yellow_R),
            "description": "Onde a B9 Rev2 colocou o centro da esclera",
        },
        "MAGENTA_helper_front_vertex": {
            "L": v3(magenta_L), "R": v3(magenta_R),
            "description": "Vertice mais frontal do grupo helper-eye",
        },
    }

    # ── DISTÂNCIAS ────────────────────────────────────────────────────────────
    def dist(a, b):
        return round((Vector(a) - Vector(b)).length * 1000, 2)  # mm

    report["distances"] = {
        "RED_to_YELLOW_mm": {
            "L": dist(red_L, yellow_L),
            "R": dist(red_R, yellow_R),
            "description": "Deslocamento da B9 Rev2 em relacao ao centroide",
        },
        "RED_to_BLUE_mm": {
            "L": dist(red_L, blue_L),
            "R": dist(red_R, blue_R),
            "description": "Diferenca entre centroide e estimativa orbital",
        },
        "RED_to_GREEN_mm": {
            "L": dist(red_L, green_L),
            "R": dist(red_R, green_R),
            "description": "Profundidade do centroide: distancia Y ao ponto mais frontal",
        },
        "BLUE_to_YELLOW_mm": {
            "L": dist(blue_L, yellow_L),
            "R": dist(blue_R, yellow_R),
            "description": "Erro de posicionamento da B9 Rev2 vs orbital real estimada",
        },
        "RED_Z_vs_HEAD_CENTER_Z_mm": {
            "L": round((red_L.z - head_center_z) * 1000, 2),
            "description": "Altura do centroide do grupo em relacao ao centro da cabeca",
        },
        "sclera_diameter_b9_mm": round(B9_REV2["sclera_radius"] * 2000, 1),
        "native_eye_diameter_mm": 27.6,
        "size_difference_mm":     round(B9_REV2["sclera_radius"] * 2000 - 27.6, 1),
    }

    log(f"Distâncias (L): RED→YELLOW={report['distances']['RED_to_YELLOW_mm']['L']}mm  RED→BLUE={report['distances']['RED_to_BLUE_mm']['L']}mm")

    # ── MATERIAIS DE DIAGNÓSTICO ──────────────────────────────────────────────
    mat_skin    = bpy.data.materials.new("DiagSkin")
    mat_skin.use_nodes = True
    mat_skin.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.6, 0.4, 0.3, 1.0)
    mat_skin.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value  = 0.6
    if human.data.materials:
        human.data.materials[0] = mat_skin
    else:
        human.data.materials.append(mat_skin)

    mat_red     = make_mat_emission("M_Red",     1.0, 0.0, 0.0, 8.0)
    mat_green   = make_mat_emission("M_Green",   0.0, 1.0, 0.2, 8.0)
    mat_blue    = make_mat_emission("M_Blue",    0.0, 0.4, 1.0, 8.0)
    mat_yellow  = make_mat_emission("M_Yellow",  1.0, 0.9, 0.0, 8.0)
    mat_magenta = make_mat_emission("M_Magenta", 1.0, 0.0, 0.8, 6.0)
    mat_white   = make_mat_emission("M_White",   1.0, 1.0, 1.0, 4.0)

    marker_r = head_radius * 0.04   # raio dos marcadores esféricos

    # ── COLOCAR MARCADORES ────────────────────────────────────────────────────
    log("A criar marcadores...")
    all_markers = []

    for side, rl, gl, bl, yl, ml in [
        ("L", red_L,  green_L, blue_L,  yellow_L, magenta_L),
        ("R", red_R,  green_R, blue_R,  yellow_R, magenta_R),
    ]:
        all_markers.append(sphere_marker(rl, f"M_Red_{side}",     mat_red,     marker_r))
        all_markers.append(sphere_marker(gl, f"M_Green_{side}",   mat_green,   marker_r * 0.8))
        all_markers.append(sphere_marker(bl, f"M_Blue_{side}",    mat_blue,    marker_r))
        all_markers.append(sphere_marker(yl, f"M_Yellow_{side}",  mat_yellow,  marker_r * 0.8))
        all_markers.append(sphere_marker(ml, f"M_Magenta_{side}", mat_magenta, marker_r * 0.6))

        # Linha vermelha→azul (erro de Z/posição)
        ln = add_line(rl, bl, f"Line_Red_Blue_{side}", mat_white, thickness=0.0005)
        if ln:
            all_markers.append(ln)

        # Linha amarela→azul (erro de posicionamento B9 Rev2)
        ln2 = add_line(yl, bl, f"Line_Yellow_Blue_{side}", mat_yellow, thickness=0.0005)
        if ln2:
            all_markers.append(ln2)

    # Marcador extra: onde os olhos DEVERIAM estar (orbital estimate frontal)
    # Círculo de referência do tamanho da geometria nativa (27.6mm)
    native_r = 0.0276 / 2
    for side, center in [("L", blue_L), ("R", blue_R)]:
        bpy.ops.mesh.primitive_circle_add(
            vertices=32, radius=native_r,
            location=(center.x, center.y, center.z))
        circ = bpy.context.active_object
        circ.name = f"NativeSize_Circle_{side}"
        circ.rotation_euler = (math.pi / 2, 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        if circ.data.materials:
            circ.data.materials[0] = mat_blue
        else:
            circ.data.materials.append(mat_blue)
        all_markers.append(circ)

    # ── RENDER 1 — Vista frontal da cabeça ───────────────────────────────────
    log("Render 1: frontal...")
    setup_render(1024, 1024, 48)

    # Campo de visão: cabeça completa
    head_view_scale = head_radius * 3.0
    cam_y_dist      = head_radius * 6
    place_cam_ortho(
        (0, -cam_y_dist, head_center_z),
        (0, 0,           head_center_z),
        head_view_scale, "DiagCam_Front"
    )
    do_render(out / "diag_b9_01_front.png", "01_front")

    # ── RENDER 2 — Vista lateral esquerda ────────────────────────────────────
    log("Render 2: lateral...")
    place_cam_ortho(
        (cam_y_dist, -0.1, head_center_z),
        (0, -0.1,          head_center_z),
        head_view_scale, "DiagCam_Side"
    )
    do_render(out / "diag_b9_02_side.png", "02_side_left")

    # ── RENDER 3 — Close-up zona orbital ─────────────────────────────────────
    log("Render 3: close-up orbital...")
    eye_zone_scale = head_radius * 1.2
    place_cam_ortho(
        (0, -cam_y_dist * 0.6, red_L.z),
        (0, 0,                  red_L.z),
        eye_zone_scale, "DiagCam_Eyes"
    )
    do_render(out / "diag_b9_03_orbital_closeup.png", "03_orbital_closeup")

    # ── RENDER 4 — Vista de baixo para cima (eixo Z) para ver profundidade Y ─
    log("Render 4: vista superior...")
    place_cam_ortho(
        (0, -0.1, head_center_z + head_radius * 4),
        (0, -0.1, head_center_z),
        head_radius * 2.5, "DiagCam_Top"
    )
    do_render(out / "diag_b9_04_top_depth.png", "04_top_view_depth_Y")

    # ── CONCLUSÕES AUTOMÁTICAS ────────────────────────────────────────────────
    log("A calcular conclusões...")

    # 1. Z do centróide vs Z do orbital estimate
    z_error_L = round((red_L.z - blue_L.z) * 1000, 1)
    z_error_R = round((red_R.z - blue_R.z) * 1000, 1)

    # 2. Y do centróide vs Y mais frontal da face
    y_centroid = gL["centroid"][1]
    y_min_group = gL["bbox"]["y"][0]
    y_face_front = orb_L.get("orbital_depth_min_y", B9_REV2["face_y_at_eye_level"])

    # 3. Comparação de tamanhos
    size_ok = abs(B9_REV2["sclera_radius"] * 2 - 0.0276) < 0.005

    report["conclusions"] = [
        {
            "id": "C1_Z_OFFSET",
            "finding": f"Centróide do grupo helper-eye está {z_error_L}mm acima do orbital estimate (lado L).",
            "detail": (
                f"centróide Z={gL['centroid'][2]:.4f}m  "
                f"orbital_estimate Z={round(blue_L.z, 4)}m  "
                f"head_center_z={round(head_center_z, 4)}m"
            ),
            "implication": (
                "O centróide do grupo inclui a parte superior das órbitas (bordo orbital superior). "
                "O ponto visual do olho fica mais abaixo."
            ),
        },
        {
            "id": "C2_Y_DEPTH",
            "finding": (
                f"Y do centróide={round(y_centroid, 4)}m  "
                f"Y mais frontal do grupo={round(y_min_group, 4)}m  "
                f"Y mais frontal da face na zona={round(y_face_front, 4)}m"
            ),
            "detail": (
                "O centróide Y está atrás do ponto mais frontal do grupo. "
                f"Diferença: {round((y_centroid - y_min_group) * 1000, 1)}mm. "
                "A esclera deve ser posicionada em Y próximo do Y mínimo do grupo, não do centróide."
            ),
            "implication": (
                "A B9 Rev2 calculou sclera_y_target a partir da face frontal geral, "
                "mas o varrimento incluiu o nariz (ponto mais frontal), "
                "não a superfície orbital local."
            ),
        },
        {
            "id": "C3_SIZE",
            "finding": (
                f"Esclera B9 Rev2: diâmetro={round(B9_REV2['sclera_radius'] * 2000, 1)}mm. "
                f"Geometria nativa: {27.6}mm. "
                f"Diferença: {round(B9_REV2['sclera_radius'] * 2000 - 27.6, 1)}mm."
            ),
            "implication": (
                "Tamanho ligeiramente maior que a geometria nativa. "
                "Aceite para a próxima versão se o posicionamento for corrigido primeiro."
            ) if not size_ok else "Tamanho aceitável.",
        },
        {
            "id": "C4_HAIR_CAP",
            "finding": "O HairCap (raio=head_radius*1.03) pode cobrir a zona dos olhos.",
            "detail": (
                f"head_center_z={round(head_center_z, 4)}m  "
                f"head_radius={round(head_radius, 4)}m  "
                f"eye_z={gL['centroid'][2]:.4f}m  "
                f"HairCap_phi_at_eye_z: z_rel={(gL['centroid'][2] - head_center_z):.4f}m"
            ),
            "calculation": (
                f"cos(phi) = {round((gL['centroid'][2] - head_center_z) / (head_radius * 1.03), 3)}  "
                f"→ phi dentro do HairCap? "
                f"{'SIM — HairCap cobre os olhos' if abs((gL['centroid'][2] - head_center_z) / (head_radius * 1.03)) <= 1.0 else 'NÃO'}"
            ),
        },
        {
            "id": "C5_RECOMMENDED_CORRECTION",
            "finding": "Posições recomendadas para a próxima versão (baseadas neste diagnóstico):",
            "detail": {
                "eye_Z_use":     "orbital_center_estimate Z (não centróide do grupo)",
                "eye_Y_use":     "orbital_depth_min_y local (não face_y geral que inclui nariz)",
                "eye_X_use":     "centróide X do grupo (correcto)",
                "sclera_radius": "0.0138m (= metade da dimensão nativa de 27.6mm)",
                "orbital_L":     orb_L.get("orbital_center_estimate"),
                "orbital_R":     orb_R.get("orbital_center_estimate"),
            },
        },
    ]

    for c in report["conclusions"]:
        log(f"  [{c['id']}] {c['finding']}")

    report["status"] = "success"

except Exception as exc:
    report["status"] = "failed"
    report["errors"].append(str(exc))
    report["traceback"] = traceback.format_exc()
    log(f"ERRO: {exc}")
    traceback.print_exc()

finally:
    (out / "b9_eye_diag_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    log("Relatório gravado.")
    print("B9_EYE_DIAG:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success":
    sys.exit(1)
