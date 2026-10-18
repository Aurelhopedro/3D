"""
T1 — Variante experimental baseada na B7: correcao da posicao e da orientacao
dos olhos.

Este ficheiro NAO modifica create_female_b7.py. Toda a construcao do corpo,
do cabelo, do calcado e da pele e identica a da B7 (mesmas funcoes, mesmos
parametros). A unica parte substituida e build_eyes().

PROBLEMAS IDENTIFICADOS NO CODIGO ORIGINAL DA B7 (build_eyes):

  PROBLEMA A — Ancoragem por centroide de banda generica:
    A B7 estimava o centro de cada olho fazendo a media de todos os vertices
    de uma banda Z larga (eye_band_lo..eye_band_hi) com wco.y < -hr*0.3, sem
    isolar a regiao orbital. Essa banda pode incluir nariz, bochechas e
    outras zonas da face, deslocando o centroide. CONFIRMADO por leitura
    directa do codigo-fonte de create_female_b7.py.

  PROBLEMA B — Orientacao por omissao:
    A B7 cria os cilindros da iris e da pupila com primitive_cylinder_add()
    sem qualquer rotation_euler. O eixo predefinido de um cilindro no
    Blender e Z, pelo que os discos ficavam deitados (perpendiculares a Z,
    virados para cima) em vez de virados para a frente (perpendiculares a
    Y). CONFIRMADO por leitura directa do codigo — nao existe nenhuma linha
    de rotacao em build_eyes() na B7.

CORRECOES APLICADAS NESTA VERSAO (T1):

  CORRECAO A — Ancoragem + raycasting BVH:
    1. Se existirem grupos de vertices auxiliares dos olhos
       ("helper-l-eye" / "helper-r-eye" ou variantes), o centro X/Z de cada
       grupo e usado como ancora (CONFIRMADO, mais preciso que a banda
       generica da B7).
    2. Caso nao existam, usa-se uma ancora por proporcao antropometrica
       (X = +/- head_radius*0.38, Z = head_center_z + head_radius*0.10),
       igual ao valor de recurso ja usado na B7 — mas aqui tratada
       explicitamente como estimativa (INFERIDO), nunca como posicao final.
    3. A partir da ancora X/Z, um raycast BVH a partir de Y muito negativo,
       direccao (0,1,0), encontra a superficie frontal real da face nesse
       ponto. A esclera e colocada imediatamente a frente dessa superficie,
       nunca atras e nunca a uma distancia arbitraria do centro da cabeca.
       Isto visa impedir tanto a flutuacao a frente da face como a
       penetracao na geometria facial.

  CORRECAO B — Orientacao:
    Os cilindros da iris e da pupila recebem rotation_euler = (pi/2, 0, 0),
    aplicada com transform_apply(rotation=True), para que o disco fique
    perpendicular ao eixo Y (direccao frontal).

  Nenhum destes dois problemas e assumido como certo sem verificacao: a
  funcao build_eyes() regista no dicionario global eye_diag a classificacao
  de cada afirmacao (CONFIRMADO / INFERIDO / DESCONHECIDO / REFUTADO), que
  e depois volcada em diagnostic_report.json e validation_report.md.

Tudo o resto (corpo, morph feminino, escala, cabelo, calcado, pele) e
reutilizado tal como esta na B7 — sem alteracoes de logica, apenas troca do
prefixo "B7_" por "T1_" nos nomes de objectos e materiais, para nao colidir
com a B7 na mesma cena/ficheiro.
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
from mathutils.bvhtree import BVHTree

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACAO
# ─────────────────────────────────────────────────────────────────────────────

TARGET_HEIGHT_M    = 1.70
HEIGHT_TOLERANCE_M = 0.01

COLOR_SKIN   = "#A66A48"
COLOR_HAIR   = "#1A0D08"
COLOR_SHOE   = "#171717"
COLOR_SCLERA = "#F5F0E8"
COLOR_IRIS   = "#5C3A1E"
COLOR_PUPIL  = "#0A0808"

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-t1")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "t1",
    "based_on": "scripts/characters/create_female_b7.py (nao modificado)",
    "status": "started",
    "components": {},
    "validations": {},
    "renders": {},
    "visual_limitations": [],
}

# Diagnostico detalhado especifico dos olhos — volcado em diagnostic_report.json
eye_diag = {
    "status": "not_started",
    "anchor_L": {}, "anchor_R": {},
    "raycast_L": {}, "raycast_R": {},
    "final_position_L": {}, "final_position_R": {},
    "orientation_fix": {},
    "eye_separation_m": None,
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITARIOS (identicos a B7)
# ─────────────────────────────────────────────────────────────────────────────

def srgb_to_linear(c: float) -> float:
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgba(hex_color: str, alpha: float = 1.0) -> tuple:
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


def evaluated_z_coords(obj):
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return [v.co.z for v in mesh.vertices]
    finally:
        ev.to_mesh_clear()


# ─────────────────────────────────────────────────────────────────────────────
# ESCALA (identica a B7)
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
# CABELO (identico a B7, apenas prefixo T1_)
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head_center_z, head_radius, waist_z, shoulder_z, bust_z):
    hair_mat = make_material("T1_Hair", COLOR_HAIR, roughness=0.85)
    objects_created = []

    CAP_SEGS  = 24
    CAP_RINGS = 10
    cap_r = head_radius * 1.03

    cap_mesh = bpy.data.meshes.new("T1_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("T1_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    pole_z = head_center_z + cap_r
    pole = bm.verts.new((0.0, 0.0, pole_z))

    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi = (math.pi * 0.52) * ri / CAP_RINGS
        z  = head_center_z + cap_r * math.cos(phi)
        r  = cap_r * math.sin(phi)
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
            phi_birth  = math.pi * 0.30
            strand_len_factor = 0.75
            swing_back = 0.45
        else:
            phi_birth  = math.pi * 0.15 + math.pi * 0.20 * ((i % 4) / 3.0)
            strand_len_factor = 1.0
            swing_back = 0.0

        root_x = cap_r * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cap_r * math.sin(phi_birth) * math.sin(azimuth)
        root_z = head_center_z + cap_r * math.cos(phi_birth)

        tip_z = bust_z + (shoulder_z - bust_z) * strand_len_factor * 0.6

        cdata = bpy.data.curves.new(name=f"T1_Strand_{i:02d}", type="CURVE")
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
            swing_y = swing_back * cap_r * t * 0.40
            px = root_x * splay
            py = root_y * splay + swing_y
            sp.points[pi_idx].co = (px, py, pz, 1.0)

        sp.use_endpoint_u = True
        sp.order_u = min(4, STRAND_SEGS)

        strand_obj = bpy.data.objects.new(f"T1_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    return objects_created


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS — LOGICA SUBSTITUIDA (ver cabecalho do ficheiro)
# ─────────────────────────────────────────────────────────────────────────────

def build_eyes(human, head_center_z, head_radius):
    global eye_diag

    sclera_mat = make_material("T1_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material("T1_Iris",   COLOR_IRIS,   roughness=0.12)
    pupil_mat  = make_material("T1_Pupil",  COLOR_PUPIL,  roughness=0.04)
    objects_created = []

    hr = head_radius
    wmat = human.matrix_world
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(human, depsgraph)

    # ---- 1. Ancoragem X/Z: grupo de vertices auxiliar, se existir ----------
    anchors = {}
    for side, sign, gnames in (
        ("L", +1, ["helper-l-eye", "l-eye", "eye-l"]),
        ("R", -1, ["helper-r-eye", "r-eye", "eye-r"]),
    ):
        found = None
        for gname in gnames:
            if gname in human.vertex_groups:
                gidx = human.vertex_groups[gname].index
                coords = [wmat @ v.co for v in human.data.vertices
                          for g in v.groups if g.group == gidx and g.weight > 0]
                if coords:
                    cx = sum(c.x for c in coords) / len(coords)
                    cz = sum(c.z for c in coords) / len(coords)
                    found = {
                        "x": cx, "z": cz, "source": gname,
                        "vertex_count": len(coords),
                        "classification": "CONFIRMADO",
                        "note": (f"Centro X/Z do grupo de vertices '{gname}' "
                                 f"({len(coords)} vertices com peso > 0)."),
                    }
                break
        if found is None:
            found = {
                "x": sign * hr * 0.38,
                "z": head_center_z + hr * 0.10,
                "source": "proporcao_antropometrica_fallback",
                "vertex_count": 0,
                "classification": "INFERIDO",
                "note": ("Nenhum grupo de vertices auxiliar dos olhos foi "
                         "encontrado nesta malha; X/Z estimados por proporcao "
                         "da cabeca (mesmo valor de recurso ja usado na B7), "
                         "a confirmar pela profundidade via raycasting."),
            }
        anchors[side] = found

    eye_diag["anchor_L"] = dict(anchors["L"])
    eye_diag["anchor_R"] = dict(anchors["R"])

    # ---- 2. Raycast BVH para localizar a superficie frontal real ----------
    def raycast_face_surface(x, z):
        origin = Vector((x, -5.0, z))
        hit, normal, idx, dist = bvh.ray_cast(origin, Vector((0.0, 1.0, 0.0)))
        if hit is None:
            origin2 = Vector((x, -1.0, z))
            hit, normal, idx, dist = bvh.ray_cast(origin2, Vector((0.0, 1.0, 0.0)))
        return hit, normal

    sclera_r = hr * 0.135
    final_positions = {}

    for side in ("L", "R"):
        anc = anchors[side]
        hit, normal = raycast_face_surface(anc["x"], anc["z"])

        if hit is not None:
            depth_gap = sclera_r * 0.18
            final_positions[side] = {
                "x": anc["x"], "y": hit.y - depth_gap, "z": anc["z"],
                "face_surface_y": round(hit.y, 6),
                "method": "bvh_raycast_from_anchor",
                "classification": "CONFIRMADO",
                "note": ("Posicao confirmada por raycast BVH: esclera colocada "
                         "imediatamente a frente da superficie facial real "
                         "detectada no ponto X/Z da ancora."),
            }
        else:
            final_positions[side] = {
                "x": anc["x"], "y": -hr * 0.88, "z": anc["z"],
                "face_surface_y": None,
                "method": "proportional_fallback_no_raycast_hit",
                "classification": "DESCONHECIDO",
                "note": ("O raycast BVH nao encontrou interseccao com a malha "
                         "facial neste ponto; usada a mesma posicao de recurso "
                         "da B7 (nao confirmada geometricamente)."),
            }

    eye_diag["raycast_L"] = {
        "hit": final_positions["L"]["face_surface_y"] is not None,
        "face_surface_y": final_positions["L"]["face_surface_y"],
    }
    eye_diag["raycast_R"] = {
        "hit": final_positions["R"]["face_surface_y"] is not None,
        "face_surface_y": final_positions["R"]["face_surface_y"],
    }

    # ---- 3. Construcao da geometria, com orientacao corrigida -------------
    for side in ("L", "R"):
        pos = final_positions[side]
        ex, ey, ez = pos["x"], pos["y"], pos["z"]

        iris_r  = sclera_r * 0.60
        pupil_r = iris_r * 0.50

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24, ring_count=12, radius=sclera_r,
            location=(ex, ey, ez)
        )
        sc = bpy.context.active_object
        sc.name = f"T1_Sclera_{side}"
        assign_material(sc, sclera_mat)

        iris_y = ey - sclera_r * 0.80
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32, radius=iris_r, depth=sclera_r * 0.10,
            location=(ex, iris_y, ez)
        )
        ir = bpy.context.active_object
        ir.name = f"T1_Iris_{side}"
        # -- CORRECAO B: disco perpendicular a Y (direccao frontal) --------
        ir.rotation_euler = (math.pi / 2, 0.0, 0.0)
        set_active(ir)
        bpy.ops.object.transform_apply(rotation=True)
        assign_material(ir, iris_mat)

        pupil_y = ey - sclera_r * 0.84
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=24, radius=pupil_r, depth=sclera_r * 0.07,
            location=(ex, pupil_y, ez)
        )
        pu = bpy.context.active_object
        pu.name = f"T1_Pupil_{side}"
        pu.rotation_euler = (math.pi / 2, 0.0, 0.0)
        set_active(pu)
        bpy.ops.object.transform_apply(rotation=True)
        assign_material(pu, pupil_mat)

        objects_created.extend([sc.name, ir.name, pu.name])
        final_positions[side]["sclera_r"] = sclera_r
        final_positions[side]["iris_r"] = iris_r
        final_positions[side]["pupil_r"] = pupil_r

    eye_diag["final_position_L"] = final_positions["L"]
    eye_diag["final_position_R"] = final_positions["R"]
    eye_diag["orientation_fix"] = {
        "applied": True,
        "classification": "CONFIRMADO",
        "note": ("Rotacao de 90 graus em X aplicada aos cilindros da iris e "
                 "da pupila. Confirmado por inspeccao do codigo-fonte da B7 "
                 "que build_eyes() nao continha nenhuma rotacao."),
    }
    eye_diag["eye_separation_m"] = round(
        abs(final_positions["L"]["x"] - final_positions["R"]["x"]), 6
    )
    eye_diag["status"] = "built"

    return objects_created, final_positions


# ─────────────────────────────────────────────────────────────────────────────
# CALCADO (identico a B7, apenas prefixo T1_)
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z):
    shoe_mat = make_material("T1_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)

    SHOE_L   = 0.24
    SHOE_W   = 0.09
    SHOE_H   = 0.045
    SHOE_SEP = 0.10

    objects_created = []
    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx = sign * SHOE_SEP / 2.0

        mesh_data = bpy.data.meshes.new(f"T1_Shoe_{side}_mesh")
        shoe_obj  = bpy.data.objects.new(f"T1_Shoe_{side}", mesh_data)
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
# RENDER — validacao visual obrigatoria (secao 10 da especificacao)
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=64, w=768, h=768):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.05, 0.05, 0.08)


def clear_lights_cameras():
    for o in list(bpy.data.objects):
        if o.type in ("LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)


def add_light(loc, target, energy, size, name):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.active_object
    l.name = name
    l.data.energy = energy
    l.data.size = size
    d = Vector(target) - Vector(loc)
    l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return l


def place_camera_persp(loc, target, lens_mm, name="T1Cam"):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "PERSP"
    cam.data.lens = lens_mm
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def place_camera_ortho(loc, target, ortho_scale, name="T1Cam"):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = ortho_scale
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def render_to(path, tag, samples=64):
    bpy.context.scene.cycles.samples = samples
    bpy.context.scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as exc:
        report["renders"][tag] = {"rendered": False, "error": str(exc)}
        return False
    exists = Path(path).exists()
    report["renders"][tag] = {"rendered": exists, "file": Path(path).name}
    return exists


def set_objects_visibility(obj_names, hide):
    for name in obj_names:
        obj = bpy.data.objects.get(name)
        if obj:
            obj.hide_render = hide


# ─────────────────────────────────────────────────────────────────────────────
# RELATORIO LEGIVEL (validation_report.md)
# ─────────────────────────────────────────────────────────────────────────────

def write_validation_report(final_positions):
    lines = []
    lines.append("# T1 — Relatorio de Validacao (correcao experimental dos olhos)\n")
    lines.append(f"Baseado em: `scripts/characters/create_female_b7.py` (nao modificado)\n")
    lines.append(f"Estado geral da execucao: **{report.get('status', 'desconhecido')}**\n")

    lines.append("\n## Ancoragem X/Z dos olhos\n")
    for side in ("L", "R"):
        a = eye_diag.get(f"anchor_{side}", {})
        lines.append(f"- Olho {side}: `{a.get('classification', 'DESCONHECIDO')}` — {a.get('note', '')}")

    lines.append("\n## Posicao final (profundidade via raycast)\n")
    for side in ("L", "R"):
        p = eye_diag.get(f"final_position_{side}", {})
        lines.append(
            f"- Olho {side}: `{p.get('classification', 'DESCONHECIDO')}` — "
            f"X={p.get('x')}, Y={p.get('y')}, Z={p.get('z')}, "
            f"superficie facial detectada Y={p.get('face_surface_y')} — {p.get('note', '')}"
        )

    lines.append("\n## Orientacao da iris e da pupila\n")
    of = eye_diag.get("orientation_fix", {})
    lines.append(f"- `{of.get('classification', 'DESCONHECIDO')}` — {of.get('note', '')}")

    lines.append(f"\n## Separacao entre os olhos\n")
    lines.append(f"- {eye_diag.get('eye_separation_m')} m")

    lines.append("\n## Imagens de validacao\n")
    for tag, data in report.get("renders", {}).items():
        status = "OK" if data.get("rendered") else "FALHOU"
        lines.append(f"- `{tag}`: {status} ({data.get('file', data.get('error', ''))})")
    if not report.get("renders"):
        lines.append("- DESCONHECIDO — nenhuma imagem foi gerada nesta execucao.")

    lines.append("\n## Limitacoes conhecidas\n")
    for item in report.get("visual_limitations", []):
        lines.append(f"- {item}")

    lines.append("\n## Conclusao\n")
    if report.get("status") == "success_t1_complete" and all(
        d.get("rendered") for d in report.get("renders", {}).values()
    ) and report.get("renders"):
        lines.append(
            "Os ficheiros foram gerados e as imagens de validacao foram "
            "produzidas com sucesso. **A aprovacao visual final (olhos "
            "correctamente posicionados e orientados nas orbitas) ainda "
            "requer inspeccao humana das imagens** — este script nao "
            "classifica a si proprio como 'RESOLVIDO E VALIDADO "
            "VISUALMENTE'. Classificacao sugerida: "
            "IMPLEMENTADO, MAS NAO VALIDADO VISUALMENTE."
        )
    else:
        lines.append(
            "A execucao nao foi concluida com sucesso ou nem todas as "
            "imagens de validacao foram produzidas. Classificacao "
            "sugerida: NAO RESOLVIDO ou PARCIALMENTE RESOLVIDO, consoante "
            "o detalhe de 'status' e 'renders' acima."
        )

    (out / "validation_report.md").write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUCAO DA PERSONAGEM
# ─────────────────────────────────────────────────────────────────────────────

try:

    # ── INSTALAR MPFB (identico a B7) ────────────────────────────────────────
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
        "vertex_groups": [g.name for g in human.vertex_groups],
    }

    # ── MORPH FEMININO (identico a B7) ───────────────────────────────────────
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
        "status": "reused_from_b7",
    }

    # ── ESCALA PARA 1,70 m (identico a B7) ───────────────────────────────────
    hi = scale_mesh_to_height(human, TARGET_HEIGHT_M)
    world_height = hi["h_final_m"]
    body_min_z   = hi["body_min_z"]
    body_max_z   = hi["body_max_z"]

    if abs(world_height - TARGET_HEIGHT_M) > HEIGHT_TOLERANCE_M:
        raise RuntimeError(
            f"Altura final ({world_height:.4f} m) fora da tolerancia "
            f"(alvo {TARGET_HEIGHT_M} m +/- {HEIGHT_TOLERANCE_M} m)")

    report["components"]["height"] = {
        "world_height_initial_m": hi["h_initial_m"],
        "scale_factor":           hi["factor"],
        "world_height_final_m":   world_height,
        "body_min_z":             body_min_z,
        "body_max_z":             body_max_z,
        "within_tolerance":       True,
        "method":                 "bmesh_vertex_scale",
    }

    # ── REFERENCIAS PROPORCIONAIS (identicas a B7) ───────────────────────────
    body_h        = body_max_z - body_min_z
    waist_z       = body_min_z + body_h * 0.575
    bust_z        = body_min_z + body_h * 0.695
    shoulder_z    = body_min_z + body_h * 0.800
    head_center_z = body_min_z + body_h * 0.920
    head_radius   = body_h * 0.075

    # ── MATERIAL DE PELE (identico a B7) ─────────────────────────────────────
    skin_mat = make_material("T1_Skin", COLOR_SKIN, roughness=0.52, subsurface=0.18)
    assign_material(human, skin_mat)
    report["components"]["skin"] = {"material": "T1_Skin", "subsurface": 0.18}

    # ── SEM VESTIDO (identico a B7) ──────────────────────────────────────────
    report["components"]["dress"] = {
        "status": "omitted_by_design",
        "reason": "Herdado da B7 — modelo base sem roupa"
    }

    # ── CABELO (identico a B7) ───────────────────────────────────────────────
    hair_objects = build_hair(head_center_z, head_radius,
                              waist_z, shoulder_z, bust_z)
    report["components"]["hair"] = {
        "objects": hair_objects,
        "cap": "T1_HairCap",
        "strands": len(hair_objects) - 1,
        "color_hex": COLOR_HAIR,
        "status": "reused_from_b7",
    }

    # ── OLHOS (logica substituida — ver build_eyes) ──────────────────────────
    eye_objects, eye_final_positions = build_eyes(human, head_center_z, head_radius)
    report["components"]["eyes"] = {
        "objects": eye_objects,
        "positioning": "bvh_raycast_confirmed_surface_plus_orientation_fix",
        "diagnostic": eye_diag,
    }

    # ── CALCADO (identico a B7) ──────────────────────────────────────────────
    shoe_objects = build_shoes(body_min_z)
    report["components"]["shoes"] = {
        "objects": shoe_objects,
        "color_hex": COLOR_SHOE,
        "status": "reused_from_b7",
    }

    # ── VALIDACOES ESTRUTURAIS ────────────────────────────────────────────────
    expected = {
        "Human", "T1_HairCap",
        "T1_Sclera_L", "T1_Iris_L", "T1_Pupil_L",
        "T1_Sclera_R", "T1_Iris_R", "T1_Pupil_R",
        "T1_Shoe_L", "T1_Shoe_R",
    }
    for i in range(24):
        expected.add(f"T1_Strand_{i:02d}")

    scene_objects = {o.name for o in bpy.data.objects if o.type != "LIGHT" and o.type != "CAMERA"}
    missing = expected - scene_objects

    report["validations"]["scene_objects"] = {
        "expected_count": len(expected),
        "found_count": len(scene_objects),
        "missing": sorted(missing),
        "pass": len(missing) == 0,
    }
    report["validations"]["world_height"] = {
        "value_m": world_height,
        "target_m": TARGET_HEIGHT_M,
        "tolerance_m": HEIGHT_TOLERANCE_M,
        "pass": abs(world_height - TARGET_HEIGHT_M) <= HEIGHT_TOLERANCE_M,
    }

    eye_sep = eye_diag.get("eye_separation_m") or 0.0
    report["validations"]["eye_geometry"] = {
        "eye_separation_m": eye_sep,
        "separation_plausible": 0.02 <= eye_sep <= 0.12,
        "eye_L_classification": eye_diag.get("final_position_L", {}).get("classification"),
        "eye_R_classification": eye_diag.get("final_position_R", {}).get("classification"),
        "both_confirmed_by_raycast": (
            eye_diag.get("final_position_L", {}).get("classification") == "CONFIRMADO"
            and eye_diag.get("final_position_R", {}).get("classification") == "CONFIRMADO"
        ),
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
        if isinstance(v, dict) and "pass" in v
    )

    report["visual_limitations"] = [
        "Morph feminino experimental — nao utiliza presets oficiais do MPFB (herdado da B7)",
        "Cabelo em curvas NURBS com bevel — sem sistema de particulas nem guias (herdado da B7)",
        "Calcado em prisma bmesh basico — sem salto, sem detalhes de sola (herdado da B7)",
        "Nao ha rigging nem armature — personagem em pose T (herdado da B7)",
        "Sem vestuario (herdado da B7)",
        "Posicao final dos olhos confirmada por raycast apenas quando ha interseccao com a malha; "
        "quando o raycast falha, a posicao usa o mesmo valor de recurso da B7 e fica classificada "
        "como DESCONHECIDO no diagnostico",
        "A aprovacao visual da correcao dos olhos requer inspeccao humana das imagens geradas "
        "(este script nao se auto-declara 'validado visualmente')",
    ]

    # ── RENDERIZACAO — 5 imagens de validacao obrigatorias ───────────────────
    setup_render(samples=48)

    head_tgt  = Vector((0.0, 0.0, head_center_z))
    cam_face_y = -head_radius * 5.0

    clear_lights_cameras()
    add_light((head_radius * 2.5, cam_face_y + head_radius, head_center_z + head_radius * 0.5),
              head_tgt, 600, 0.6, "T1_Key")
    add_light((-head_radius * 2.0, cam_face_y + head_radius * 0.8, head_center_z),
              head_tgt, 300, 0.5, "T1_Fill")
    add_light((0.0, head_radius * 2.0, head_center_z + head_radius),
              head_tgt, 200, 0.4, "T1_Rim")

    # 1. Vista frontal
    place_camera_persp((0.0, cam_face_y, head_center_z), head_tgt, 85, name="T1Cam_Front")
    render_to(out / "t1_view_front.png", "front")

    # 2. Vista lateral
    place_camera_persp((head_radius * 4.0, 0.0, head_center_z), head_tgt, 85, name="T1Cam_Side")
    render_to(out / "t1_view_side.png", "side")

    # 3. Vista de tres quartos
    place_camera_persp((head_radius * 2.8, cam_face_y * 0.6, head_center_z), head_tgt, 85,
                        name="T1Cam_ThreeQuarter")
    render_to(out / "t1_view_threequarter.png", "three_quarter")

    # 4. Aproximacao dos olhos
    eye_L = eye_final_positions["L"]
    eye_R = eye_final_positions["R"]
    eye_mid_z = (eye_L["z"] + eye_R["z"]) / 2.0
    eye_mid_y = (eye_L["y"] + eye_R["y"]) / 2.0
    eye_sep_m = abs(eye_L["x"] - eye_R["x"])
    eye_ortho = max(eye_sep_m * 3.0, eye_L.get("sclera_r", head_radius * 0.135) * 8)
    eye_tgt = Vector((0.0, eye_mid_y, eye_mid_z))
    cam_eye_y = eye_mid_y - eye_ortho * 3.0
    clear_lights_cameras()
    add_light((eye_sep_m * 0.8, cam_eye_y + eye_sep_m, eye_mid_z + head_radius * 0.1),
              eye_tgt, 300, 0.3, "T1_EyeKey")
    add_light((-eye_sep_m * 0.6, cam_eye_y + eye_sep_m * 0.8, eye_mid_z),
              eye_tgt, 150, 0.25, "T1_EyeFill")
    place_camera_ortho((0.0, cam_eye_y, eye_mid_z), eye_tgt, eye_ortho, name="T1Cam_Eyes")
    render_to(out / "t1_view_eyes_closeup.png", "eyes_closeup", samples=96)

    # 5. Vista frontal com cabelo oculto (isolar a regiao ocular)
    set_objects_visibility(hair_objects, True)
    clear_lights_cameras()
    add_light((head_radius * 2.5, cam_face_y + head_radius, head_center_z + head_radius * 0.5),
              head_tgt, 600, 0.6, "T1_Key2")
    add_light((-head_radius * 2.0, cam_face_y + head_radius * 0.8, head_center_z),
              head_tgt, 300, 0.5, "T1_Fill2")
    place_camera_persp((0.0, cam_face_y, head_center_z), head_tgt, 85, name="T1Cam_FrontNoHair")
    render_to(out / "t1_view_front_no_hair.png", "front_no_hair")
    set_objects_visibility(hair_objects, False)

    # ── GRAVAR .BLEND E EXPORTAR GLB ─────────────────────────────────────────
    blend_path = out / "mpfb-female-t1.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-t1.glb"
    bpy.ops.object.select_all(action="SELECT")
    for obj in bpy.context.selected_objects:
        if obj.type in ("LIGHT", "CAMERA"):
            obj.select_set(False)
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

    report["status"] = "success_t1_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "mpfb-t1-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    (out / "diagnostic_report.json").write_text(
        json.dumps({"blender": report.get("blender"),
                     "mpfb_module": report.get("components", {}).get("mpfb", {}).get("module"),
                     "script": "scripts/characters/create_female_t1.py",
                     "eyes": eye_diag,
                     "renders": report.get("renders", {}),
                     "errors_and_warnings": {
                         "error": report.get("error"),
                         "traceback": report.get("traceback"),
                     },
                     "overall_status": report.get("status")},
                    ensure_ascii=False, indent=2)
    )
    try:
        write_validation_report(eye_diag)
    except Exception:
        (out / "validation_report.md").write_text(
            "# T1 — Relatorio de Validacao\n\nFalha ao gerar o relatorio legivel. "
            "Consultar diagnostic_report.json e mpfb-t1-report.json.\n",
            encoding="utf-8",
        )
    print("MPFB_T1_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success_t1_complete":
    sys.exit(1)
