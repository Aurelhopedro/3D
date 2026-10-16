"""
B4 — Primeira modelo feminina completa (versão revista).

Estado de cada secção:
  MPFB install     — confirmado por execução anterior (B3.2)
  Morph feminino   — confirmado por execução anterior (B3.2)
  Escala 1,70 m    — confirmado por execução anterior (B3.2)
  Altura em coords mundo — nova verificação, hipótese técnica
  Material de pele — nova funcionalidade, hipótese técnica
  Vestido          — nova funcionalidade, hipótese técnica
  Cabelo (mechas)  — nova funcionalidade, hipótese técnica
  Olhos            — nova funcionalidade, hipótese técnica
  Calçado          — nova funcionalidade, hipótese técnica
  Validação final  — nova funcionalidade, hipótese técnica

Nada neste script foi testado no Blender. Toda a verificação
foi estática (leitura de código e API docs do Blender 5).
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
from mathutils import Vector, Matrix

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M = 1.70
HEIGHT_TOLERANCE_M = 0.03      # ±3 cm é aceitável

# Cores em sRGB hexadecimal — convertidas para linear no utilitário abaixo
COLOR_SKIN   = "#A66A48"
COLOR_DRESS  = "#C62828"
COLOR_HAIR   = "#211510"
COLOR_SHOE   = "#171717"
COLOR_SCLERA = "#F5F0E8"
COLOR_IRIS   = "#5C3A1E"

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-b4")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "status": "started",
    "components": {},
    "validations": {},
    "visual_limitations": []
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def srgb_to_linear(c: float) -> float:
    """
    Conversão IEC 61966-2-1: sRGB [0,1] → RGB linear.
    Idêntica à função em scripts/materials.py (confirmada no repositório).
    """
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgba(hex_color: str, alpha: float = 1.0) -> tuple:
    """Converte '#RRGGBB' para tuplo RGBA linear para uso no Blender."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Cor inválida: {hex_color!r}")
    r = srgb_to_linear(int(h[0:2], 16) / 255.0)
    g = srgb_to_linear(int(h[2:4], 16) / 255.0)
    b = srgb_to_linear(int(h[4:6], 16) / 255.0)
    return (r, g, b, alpha)


def make_material(name: str, hex_color: str,
                  roughness: float = 0.6,
                  metallic: float = 0.0,
                  alpha: float = 1.0,
                  subsurface: float = 0.0) -> bpy.types.Material:
    """
    Cria um material Principled BSDF com conversão sRGB correcta.
    Compatível com exportação GLB (KHR_materials_transmission não é usado).
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (-300, 0)
    bsdf.inputs["Base Color"].default_value = hex_to_linear_rgba(hex_color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic

    # Blender 5 — nome do input de Specular mudou para "Specular IOR Level"
    for spec_name in ("Specular IOR Level", "Specular"):
        if spec_name in bsdf.inputs:
            bsdf.inputs[spec_name].default_value = 0.5
            break

    # Subsurface (pele)
    if subsurface > 0.0:
        for ss_name in ("Subsurface Weight", "Subsurface"):
            if ss_name in bsdf.inputs:
                bsdf.inputs[ss_name].default_value = subsurface
                break

    # Transparência
    if alpha < 1.0:
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = alpha
        mat.blend_method = "BLEND"

    out_node = nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (0, 0)
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def assign_material(obj: bpy.types.Object, mat: bpy.types.Material,
                    slot: int = 0) -> None:
    """Atribui material ao slot indicado, criando-o se necessário."""
    while len(obj.data.materials) <= slot:
        obj.data.materials.append(None)
    obj.data.materials[slot] = mat


def world_coords(obj: bpy.types.Object) -> list:
    """
    Devolve coordenadas de mundo de todos os vértices do objecto,
    aplicando matrix_world. Usa a malha em modo OBJECT (sem depsgraph)
    porque o objecto já teve transform_apply.
    """
    mw = obj.matrix_world
    return [mw @ v.co for v in obj.data.vertices]


def world_bounds(obj: bpy.types.Object) -> dict:
    """Bounds em coordenadas de mundo."""
    wc = world_coords(obj)
    return {
        "min_x": min(v.x for v in wc),
        "max_x": max(v.x for v in wc),
        "min_y": min(v.y for v in wc),
        "max_y": max(v.y for v in wc),
        "min_z": min(v.z for v in wc),
        "max_z": max(v.z for v in wc),
    }


def evaluated_z_coords(obj: bpy.types.Object) -> list:
    """
    Coordenadas Z avaliadas (shape keys aplicados) em espaço local.
    Usado antes de transform_apply para calcular a escala.
    """
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return [v.co.z for v in mesh.vertices]
    finally:
        ev.to_mesh_clear()


def deselect_all():
    bpy.ops.object.select_all(action="DESELECT")


def set_active(obj: bpy.types.Object):
    deselect_all()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_transforms(obj: bpy.types.Object):
    set_active(obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUÇÃO DA PERSONAGEM
# ─────────────────────────────────────────────────────────────────────────────

try:

    # ── INSTALAR MPFB (idêntico ao B3.2 — confirmado funcional) ─────────────
    source = Path(os.environ["MPFB_ZIP"])
    with zipfile.ZipFile(source) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix = manifest[:-len("blender_manifest.toml")]
        repacked = out / "mpfb-extension.zip"
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == "user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(
            name="User Default",
            module="user_default",
            custom_directory=str(out / "extensions")
        )
        repo.enabled = True

    install_result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked),
        repo=repo.module,
        enable_on_install=True
    )
    if "FINISHED" not in install_result and "RUNNING_MODAL" not in install_result:
        raise RuntimeError(f"MPFB install_files devolveu: {install_result}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)

    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB não ficou activo após enable: {module_id}")

    report["components"]["mpfb"] = {"status": "installed_and_active", "module": module_id}

    # ── LIMPAR CENA ──────────────────────────────────────────────────────────
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    if len(bpy.data.objects) > 0:
        raise RuntimeError(f"Cena não ficou limpa; objectos restantes: {[o.name for o in bpy.data.objects]}")

    # ── CRIAR HUMANO ─────────────────────────────────────────────────────────
    result = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result:
        raise RuntimeError(f"mpfb.create_human devolveu: {result}")

    human = bpy.data.objects.get("Human")
    if human is None or human.type != "MESH":
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            raise RuntimeError("Nenhuma malha encontrada após create_human")
        human = meshes[0]

    if human.data.shape_keys is None:
        raise RuntimeError("Human não tem shape keys — MPFB pode não ter sido inicializado correctamente")

    report["components"]["human_base"] = {
        "name": human.name,
        "vertices": len(human.data.vertices),
        "shape_keys": len(human.data.shape_keys.key_blocks)
    }

    # ── MORPH FEMININO (idêntico ao B3.2 — confirmado funcional) ────────────
    keys = human.data.shape_keys.key_blocks
    before_vals = {k.name: float(k.value) for k in keys}

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
        raise RuntimeError("Morph feminino: nenhum vértice alterado")

    after_vals = {k.name: float(k.value) for k in keys}
    morph_diff = {n: {"before": before_vals[n], "after": v}
                  for n, v in after_vals.items() if abs(v - before_vals[n]) > 1e-5}

    report["components"]["female_morph"] = {
        "changed_vertices": changed_v,
        "shape_keys_modified": len(morph_diff),
        "status": "confirmed_experimental",
        "note": "Não é um preset oficial do MPFB"
    }

    # ── ESCALA PARA 1,70 m ───────────────────────────────────────────────────
    # 1. Calcular a altura inicial em coordenadas de MUNDO:
    #    usa a geometria avaliada (shape keys) × matrix_world actual,
    #    evitando confundir escala local acumulada com altura real.
    bpy.context.view_layer.update()
    mw = human.matrix_world
    ev_obj = human.evaluated_get(bpy.context.evaluated_depsgraph_get())
    ev_mesh = ev_obj.to_mesh()
    try:
        world_z_before = [(mw @ v.co).z for v in ev_mesh.vertices]
    finally:
        ev_obj.to_mesh_clear()

    h_world_initial = max(world_z_before) - min(world_z_before)
    if h_world_initial <= 0:
        raise RuntimeError(f"Altura inicial em mundo inválida: {h_world_initial}")

    # 2. Calcular e aplicar o factor UMA ÚNICA VEZ sobre a escala de mundo.
    scale_factor = TARGET_HEIGHT_M / h_world_initial
    human.scale = (
        human.scale.x * scale_factor,
        human.scale.y * scale_factor,
        human.scale.z * scale_factor,
    )
    apply_transforms(human)
    bpy.context.view_layer.update()

    # 3. Medir a altura final em coordenadas de mundo (vértices reais, pós-apply).
    wc_z = [v.z for v in world_coords(human)]
    world_height = max(wc_z) - min(wc_z)
    body_min_z = min(wc_z)
    body_max_z = max(wc_z)

    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(
            f"Altura em coords de mundo ({world_height:.4f} m) fora da tolerância "
            f"(alvo {TARGET_HEIGHT_M} m ± {HEIGHT_TOLERANCE_M} m)"
        )

    report["components"]["height"] = {
        "world_height_initial_m": h_world_initial,
        "scale_factor": scale_factor,
        "world_height_final_m": world_height,
        "body_min_z": body_min_z,
        "body_max_z": body_max_z,
        "within_tolerance": True
    }

    # ── REFERÊNCIAS DE PROPORÇÃO ─────────────────────────────────────────────
    body_h = body_max_z - body_min_z
    foot_top_z    = body_min_z + body_h * 0.07
    knee_z         = body_min_z + body_h * 0.28
    waist_z        = body_min_z + body_h * 0.57
    bust_z         = body_min_z + body_h * 0.70
    shoulder_z     = body_min_z + body_h * 0.80
    head_center_z  = body_min_z + body_h * 0.92
    torso_radius   = body_h * 0.105
    head_radius    = body_h * 0.075

    # ── MATERIAL DE PELE ─────────────────────────────────────────────────────
    skin_mat = make_material("B4_Skin", COLOR_SKIN, roughness=0.55, subsurface=0.15)
    assign_material(human, skin_mat)

    report["components"]["skin_material"] = {
        "hex_srgb": COLOR_SKIN,
        "material": "B4_Skin",
        "subsurface": 0.15,
        "status": "applied_hypothesis"
    }

    # ─────────────────────────────────────────────────────────────────────────
    # VESTIDO — corpete + saia
    # ─────────────────────────────────────────────────────────────────────────

    dress_mat = make_material("B4_Dress", COLOR_DRESS, roughness=0.75)

    # — Corpete (cintura → ombros) —
    bodice_h = shoulder_z - waist_z
    bodice_cz = waist_z + bodice_h / 2.0

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=32, radius=torso_radius, depth=bodice_h,
        location=(0.0, 0.0, bodice_cz)
    )
    bodice = bpy.context.active_object
    bodice.name = "B4_Bodice"

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(bodice.data)
    bm.verts.ensure_lookup_table()
    local_zs = [v.co.z for v in bm.verts]
    max_local_z = max(local_zs)
    min_local_z = min(local_zs)
    for v in bm.verts:
        t = (v.co.z - min_local_z) / (max_local_z - min_local_z + 1e-9)
        scale_xy = 1.0 + (0.05 * (1.0 - t)) - (0.15 * t)
        v.co.x *= scale_xy
        v.co.y *= scale_xy
    bmesh.update_edit_mesh(bodice.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    assign_material(bodice, dress_mat)
    apply_transforms(bodice)

    # — Saia (joelho → cintura) —
    skirt_h = waist_z - knee_z
    skirt_cz = knee_z + skirt_h / 2.0

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=32, radius=torso_radius, depth=skirt_h,
        location=(0.0, 0.0, skirt_cz)
    )
    skirt = bpy.context.active_object
    skirt.name = "B4_Skirt"

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(skirt.data)
    bm.verts.ensure_lookup_table()
    local_zs = [v.co.z for v in bm.verts]
    max_local_z = max(local_zs)
    min_local_z = min(local_zs)
    for v in bm.verts:
        t = (v.co.z - min_local_z) / (max_local_z - min_local_z + 1e-9)
        scale_xy = 1.0 + 0.80 * (1.0 - t)
        v.co.x *= scale_xy
        v.co.y *= scale_xy
    bmesh.update_edit_mesh(skirt.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    assign_material(skirt, dress_mat)
    apply_transforms(skirt)

    report["components"]["dress"] = {
        "parts": ["B4_Bodice", "B4_Skirt"],
        "color_hex": COLOR_DRESS,
        "bodice_height_m": bodice_h,
        "skirt_height_m": skirt_h,
        "status": "geometry_created_hypothesis",
        "note": "Dois cilindros modificados — sem física de tecido"
    }

    # ─────────────────────────────────────────────────────────────────────────
    # CABELO — 8 mechas NURBS com bevel
    # ─────────────────────────────────────────────────────────────────────────

    hair_mat = make_material("B4_Hair", COLOR_HAIR, roughness=0.82)

    def make_hair_strand(name: str, ctrl_pts: list,
                         bevel_r: float = 0.018) -> bpy.types.Object:
        cdata = bpy.data.curves.new(name=name, type="CURVE")
        cdata.dimensions = "3D"
        cdata.bevel_depth = bevel_r
        cdata.bevel_resolution = 2
        cdata.use_fill_caps = True

        sp = cdata.splines.new("NURBS")
        sp.points.add(len(ctrl_pts) - 1)
        for i, (x, y, z) in enumerate(ctrl_pts):
            sp.points[i].co = (x, y, z, 1.0)
        sp.use_endpoint_u = True
        sp.order_u = min(4, len(ctrl_pts))

        obj = bpy.data.objects.new(name, cdata)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(hair_mat)
        return obj

    ht = head_center_z
    hr = head_radius
    hair_root_z = ht + hr * 0.55
    hair_strands = []

    strand_specs = [
        (0.00,  0.04, -0.04, 1.00),
        (0.40,  0.06, -0.03, 0.95),
        (-0.40, 0.06, -0.03, 0.95),
        (0.70,  0.07, -0.02, 0.88),
        (-0.70, 0.07, -0.02, 0.88),
        (1.10,  0.08, -0.01, 0.80),
        (-1.10, 0.08, -0.01, 0.80),
        (math.pi, 0.02,  0.04, 0.60),
    ]

    for i, (angle, base_r, y_off, length_ratio) in enumerate(strand_specs):
        xb = math.sin(angle) * base_r
        yb = -abs(math.cos(angle)) * base_r + y_off
        hair_tip_z = waist_z + (hair_root_z - waist_z) * (1.0 - length_ratio)

        pts = [
            (xb * 0.5,  yb * 0.5, hair_root_z),
            (xb,        yb,        hair_root_z - (hair_root_z - hair_tip_z) * 0.15),
            (xb * 1.1,  yb * 1.1, ht),
            (xb * 0.95, yb * 1.0, shoulder_z + 0.05),
            (xb * 0.85, yb * 0.9, bust_z),
            (xb * 0.75, yb * 0.8, hair_tip_z),
        ]
        strand = make_hair_strand(f"B4_Hair_{i:02d}", pts, bevel_r=0.014 + 0.008 * (i % 3))
        hair_strands.append(strand.name)

    report["components"]["hair"] = {
        "strands": len(hair_strands),
        "strand_names": hair_strands,
        "color_hex": COLOR_HAIR,
        "type": "nurbs_strands_with_bevel",
        "status": "geometry_created_hypothesis",
        "note": "Curvas NURBS com bevel — não é sistema de partículas"
    }

    # ─────────────────────────────────────────────────────────────────────────
    # OLHOS — esclera + íris
    # ─────────────────────────────────────────────────────────────────────────

    sclera_mat = make_material("B4_Sclera", COLOR_SCLERA, roughness=0.1)
    iris_mat   = make_material("B4_Iris",   COLOR_IRIS,   roughness=0.2)

    eye_z     = head_center_z + head_radius * 0.12
    eye_y     = head_radius * 0.85
    eye_sep_x = head_radius * 0.42
    sclera_r  = head_radius * 0.14
    iris_r    = sclera_r * 0.55
    iris_y_off = sclera_r * 0.88

    eye_objects = []
    for side, sign in [("L", -1), ("R", 1)]:
        ex = sign * eye_sep_x

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=16, ring_count=8,
            radius=sclera_r,
            location=(ex, eye_y, eye_z)
        )
        sclera = bpy.context.active_object
        sclera.name = f"B4_Sclera_{side}"
        assign_material(sclera, sclera_mat)

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=12, ring_count=6,
            radius=iris_r,
            location=(ex, eye_y + iris_y_off, eye_z)
        )
        iris = bpy.context.active_object
        iris.name = f"B4_Iris_{side}"
        iris.scale.y = 0.25
        apply_transforms(iris)
        assign_material(iris, iris_mat)

        eye_objects.extend([sclera.name, iris.name])

    report["components"]["eyes"] = {
        "objects": eye_objects,
        "sclera_radius_m": sclera_r,
        "colors": {"sclera": COLOR_SCLERA, "iris": COLOR_IRIS},
        "status": "geometry_created_hypothesis",
        "note": "Posição aproximada — depende da geometria real do MPFB"
    }

    # ─────────────────────────────────────────────────────────────────────────
    # CALÇADO — prisma bmesh com bico afunilado
    # ─────────────────────────────────────────────────────────────────────────

    shoe_mat = make_material("B4_Shoes", COLOR_SHOE, roughness=0.25, metallic=0.05)

    SHOE_L   = 0.24
    SHOE_W   = 0.09
    SHOE_H   = 0.045
    SHOE_SEP = 0.10

    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx = sign * SHOE_SEP / 2.0

        mesh_data = bpy.data.meshes.new(f"B4_Shoe_{side}_mesh")
        shoe_obj = bpy.data.objects.new(f"B4_Shoe_{side}", mesh_data)
        bpy.context.collection.objects.link(shoe_obj)

        bm = bmesh.new()
        hw = SHOE_W / 2.0
        hl = SHOE_L / 2.0
        toe_hw = hw * 0.35
        heel_zt = SHOE_H
        toe_zt  = SHOE_H * 0.70
        bz = body_min_z

        verts = [
            bm.verts.new((shoe_cx - hw,   -hl, bz)),
            bm.verts.new((shoe_cx + hw,   -hl, bz)),
            bm.verts.new((shoe_cx + toe_hw, hl, bz)),
            bm.verts.new((shoe_cx - toe_hw, hl, bz)),
            bm.verts.new((shoe_cx - hw,   -hl, bz + heel_zt)),
            bm.verts.new((shoe_cx + hw,   -hl, bz + heel_zt)),
            bm.verts.new((shoe_cx + toe_hw, hl, bz + toe_zt)),
            bm.verts.new((shoe_cx - toe_hw, hl, bz + toe_zt)),
        ]
        for f in [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[3,0,4,7]]:
            bm.faces.new([verts[i] for i in f])

        bm.to_mesh(mesh_data)
        bm.free()
        mesh_data.calc_normals()
        assign_material(shoe_obj, shoe_mat)

    report["components"]["shoes"] = {
        "objects": ["B4_Shoe_L", "B4_Shoe_R"],
        "color_hex": COLOR_SHOE,
        "dimensions_m": {"length": SHOE_L, "width": SHOE_W, "height": SHOE_H},
        "type": "bmesh_tapered_sole",
        "status": "geometry_created_hypothesis"
    }

    # ─────────────────────────────────────────────────────────────────────────
    # VALIDAÇÕES FINAIS
    # ─────────────────────────────────────────────────────────────────────────

    expected_objects = {
        "Human", "B4_Bodice", "B4_Skirt",
        "B4_Sclera_L", "B4_Sclera_R", "B4_Iris_L", "B4_Iris_R",
        "B4_Shoe_L", "B4_Shoe_R"
    }
    expected_objects.update(hair_strands)

    scene_objects = {o.name for o in bpy.data.objects}
    missing = expected_objects - scene_objects

    report["validations"]["scene_objects"] = {
        "expected": sorted(expected_objects),
        "found": sorted(scene_objects),
        "missing": sorted(missing),
        "pass": len(missing) == 0
    }

    report["validations"]["world_height"] = {
        "value_m": world_height,
        "target_m": TARGET_HEIGHT_M,
        "tolerance_m": HEIGHT_TOLERANCE_M,
        "pass": abs(world_height - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M
    }

    mat_check = {}
    for oname in expected_objects:
        obj = bpy.data.objects.get(oname)
        if obj and hasattr(obj.data, "materials"):
            mat_check[oname] = len(obj.data.materials) > 0
        elif obj:
            mat_check[oname] = True
    report["validations"]["materials_assigned"] = {
        "per_object": mat_check,
        "pass": all(mat_check.values())
    }

    report["validations"]["overall_pass"] = all(
        v.get("pass", False) for v in report["validations"].values()
    )

    # ─────────────────────────────────────────────────────────────────────────
    # LIMITAÇÕES VISUAIS
    # ─────────────────────────────────────────────────────────────────────────

    report["visual_limitations"] = [
        "Anatomia feminina adulta não validada pelos controlos oficiais do MPFB (preset experimental)",
        "Cor verde residual dos materiais MPFB nos olhos da malha base não foi removida",
        "Olhos geométricos posicionados em frente da cabeça — posição aproximada, não alinhada com os sockets do MPFB",
        "Cabelo representado por curvas NURBS com bevel — sem partículas, sem física",
        "Vestido em dois cilindros modificados — sem costuras, sem física de tecido",
        "Calçado em prisma bmesh — sem tacão separado, sem detalhes de sola",
        "Subsurface scattering da pele declarado mas não verificado por render",
        "A aparência visual final só pode ser confirmada abrindo o ficheiro .blend no Blender",
        "Posições anatómicas são estimativas proporcionais, não derivadas da geometria MPFB"
    ]

    # ─────────────────────────────────────────────────────────────────────────
    # GRAVAR .BLEND E EXPORTAR GLB
    # ─────────────────────────────────────────────────────────────────────────

    blend_path = out / "mpfb-female-b4.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend não foi gravado em {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-b4.glb"
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
        raise RuntimeError(f"GLB não foi exportado em {glb_path}")
    report["glb_bytes"] = glb_path.stat().st_size

    report["status"] = "success_b4_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "mpfb-b4-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print("MPFB_B4_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_b4_complete":
    sys.exit(1)
