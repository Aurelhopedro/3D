"""
T3 — Variante experimental baseada na B7 (e nos resultados da T1/T2):
olhos dentro das orbitas, em vez de em cima da testa.

Este ficheiro NAO modifica create_female_b7.py, create_female_t1.py nem
create_female_t2.py. Corpo, morph feminino, escala, calcado e pele sao os da B7.
Mudam: (1) a ordem do pipeline (shape keys cozidas antes de medir), (2) build_eyes()
e (3) a touca do cabelo (para nao cobrir testa/olhos).

CAUSA PROVAVEL DO BUG DA T1/T2  (INFERIDO por leitura do codigo — a confirmar
com o diagnostic_report.json desta execucao, campo "bake_effect"):

  A B7/T1/T2 aplicam o morph feminino mexendo nos VALORES das shape keys e depois
  medem tudo com `human.data.vertices[i].co`. Em Blender isso e a malha BASE
  (basis, sem o mix das shape keys). Mas o GLB e exportado com export_apply=True,
  ou seja, com o mix aplicado. Resultado: o centro do olho (grupo helper-l-eye /
  helper-r-eye) era calculado na cabeca "neutra" e as esferas eram postas ai,
  enquanto a cara que se ve e a da cabeca "feminina", noutra altura. Os olhos
  ficam acima das orbitas, junto a linha do cabelo. A escala para 1,70 m tambem
  era calculada sobre a malha base, e as proporcoes do cabelo (0.92 da altura)
  herdavam o mesmo erro.

  Segundo problema (T1/T2): a esfera da esclera era colocada a frente da
  superficie da pele (raycast, y = hit.y - folga), logo o globo ficava a flutuar
  fora da cara em vez de dentro da orbita.

  Terceiro problema (B7/T1/T2): a touca do cabelo era uma esfera de raio 0.13 m
  com a borda abaixo da linha dos olhos, a cobrir testa e sobrancelhas.

CORRECOES DA T3:

  1. bake_shape_keys(): o mix das shape keys passa a ser gravado na malha e as
     shape keys sao removidas. A partir dai, data.vertices == o que se ve.
     Os centros dos helpers sao medidos antes/depois (prova do efeito).
  2. Escala para 1,70 m, medicoes e BVH feitos so depois do bake.
  3. Olho = esfera centrada no centro (bbox) do grupo helper-l/r-eye da malha
     final, com raio vindo da bbox do helper (limitado a 9.5-14.5 mm). A
     frente do globo aparece pela abertura das palpebras. Iris e pupila sao
     calotes esfericas coladas a superficie, a olhar para -Y.
  4. O raycast so serve para verificar: se houver pele a frente do globo, o
     globo avanca o minimo necessario (max 12 mm) e fica registado.
  5. Touca do cabelo: elipsoide ajustada a cabeca real, sem faces abaixo da
     linha do cabelo na zona frontal (testa e olhos ficam livres).

NAO resolvido aqui (herdado da B7): as madeixas frontais caem a frente do
pescoco/peito; e a pose, o calcado e o rigging.

O script nao se auto-declara "validado visualmente": isso exige olhar para
t3_view_front.png e t3_view_eyes_closeup.png.
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

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-t3")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "blender": bpy.app.version_string,
    "version": "t3",
    "based_on": "create_female_b7.py (nao modificado); corrige o bug de olhos na testa "
                "visto na T1/T2",
    "status": "started",
    "components": {},
    "validations": {},
    "renders": {},
    "visual_limitations": [],
}

eye_diag = {
    "status": "not_started",
    "bake_effect": {},
    "helpers_before_bake": {},
    "helpers_after_bake": {},
    "helpers_final": {},
    "placement_L": {},
    "placement_R": {},
    "eye_separation_m": None,
    "head_geometry": {},
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITARIOS
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


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def vl(v, d=5):
    return [round(v.x, d), round(v.y, d), round(v.z, d)]


# ─────────────────────────────────────────────────────────────────────────────
# SHAPE KEYS -> MALHA (correccao 1)
# ─────────────────────────────────────────────────────────────────────────────

def bake_shape_keys(obj):
    """Grava o mix actual das shape keys na malha e remove as shape keys.
    Depois disto, obj.data.vertices[i].co e a posicao que se ve/exporta."""
    me = obj.data
    info = {"had_shape_keys": me.shape_keys is not None}
    if me.shape_keys is None:
        info["baked"] = False
        return info

    info["key_count_before"] = len(me.shape_keys.key_blocks)
    info["nonzero_key_values"] = {
        k.name: round(k.value, 4) for k in me.shape_keys.key_blocks
        if abs(k.value) > 1e-6
    }
    basis_before = [v.co.copy() for v in me.vertices]

    mix = obj.shape_key_add(name="T3_baked_mix", from_mix=True)
    mixed = [d.co.copy() for d in mix.data]
    obj.shape_key_clear()

    flat = []
    for co in mixed:
        flat.extend((co.x, co.y, co.z))
    me.vertices.foreach_set("co", flat)
    me.update()
    bpy.context.view_layer.update()

    shifts = [(a - b).length for a, b in zip(mixed, basis_before)]
    info["baked"] = True
    info["max_vertex_shift_m"] = round(max(shifts), 5)
    info["mean_vertex_shift_m"] = round(sum(shifts) / len(shifts), 5)
    info["shape_keys_after"] = 0 if me.shape_keys is None else len(me.shape_keys.key_blocks)
    return info


# ─────────────────────────────────────────────────────────────────────────────
# ESCALA (identica a B7; agora corre sobre a malha ja "cozida")
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
# MEDICAO DOS HELPERS DOS OLHOS
# ─────────────────────────────────────────────────────────────────────────────

def measure_eye_helpers(obj):
    wm = obj.matrix_world
    result = {}
    for side, names in (("L", ("helper-l-eye", "joint-l-eye")),
                        ("R", ("helper-r-eye", "joint-r-eye"))):
        found = None
        for gname in names:
            vg = obj.vertex_groups.get(gname)
            if vg is None:
                continue
            gi = vg.index
            coords = []
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.group == gi and g.weight > 0.0:
                        coords.append(wm @ v.co)
                        break
            if coords:
                xs = [c.x for c in coords]
                ys = [c.y for c in coords]
                zs = [c.z for c in coords]
                found = {
                    "group": gname,
                    "vertex_count": len(coords),
                    "center": Vector(((min(xs) + max(xs)) / 2,
                                      (min(ys) + max(ys)) / 2,
                                      (min(zs) + max(zs)) / 2)),
                    "centroid": Vector((sum(xs) / len(xs),
                                        sum(ys) / len(ys),
                                        sum(zs) / len(zs))),
                    "dims": Vector((max(xs) - min(xs),
                                    max(ys) - min(ys),
                                    max(zs) - min(zs))),
                }
                break
        result[side] = found
    return result


def public_helpers(h):
    pub = {}
    for side, a in h.items():
        if a is None:
            pub[side] = None
        else:
            pub[side] = {
                "group": a["group"],
                "vertex_count": a["vertex_count"],
                "bbox_center": vl(a["center"]),
                "centroid": vl(a["centroid"]),
                "dims": vl(a["dims"]),
            }
    return pub


# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRIA REAL DA CABECA (para a touca do cabelo)
# ─────────────────────────────────────────────────────────────────────────────

def measure_head(human, eye_z):
    bpy.context.view_layer.update()
    ev = human.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    try:
        pts = [human.matrix_world @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()

    head = [p for p in pts if p.z >= eye_z - 0.02]
    ok = len(head) > 50
    if ok:
        top_z = max(p.z for p in head)
        half_x = max(abs(p.x) for p in head)
        y_back = max(p.y for p in head)
        fore = [p for p in head if p.z >= eye_z + 0.03]
        y_front = min(p.y for p in (fore if fore else head))
    else:
        top_z, half_x, y_back, y_front = eye_z + 0.115, 0.078, 0.095, -0.105

    # limites anatomicos: se a medicao fugir muito, usa valores tipicos
    sane = (0.05 <= half_x <= 0.11 and 0.14 <= (y_back - y_front) <= 0.27
            and 0.08 <= (top_z - eye_z) <= 0.17)
    if not sane:
        top_z, half_x, y_back, y_front = eye_z + 0.115, 0.078, 0.095, -0.105

    info = {
        "measured_ok": bool(ok and sane),
        "top_z": top_z, "half_x": half_x, "y_back": y_back, "y_front_forehead": y_front,
    }
    return info


# ─────────────────────────────────────────────────────────────────────────────
# CABELO (B7 com touca elipsoidal e sem faces sobre testa/olhos)
# ─────────────────────────────────────────────────────────────────────────────

def build_hair(head, eye_z, waist_z, shoulder_z, bust_z):
    hair_mat = make_material("T3_Hair", COLOR_HAIR, roughness=0.85)
    objects_created = []

    cz = eye_z - 0.010
    a = head["half_x"] + 0.007
    b = (head["y_back"] - head["y_front_forehead"]) / 2.0 + 0.007
    cy = (head["y_back"] + head["y_front_forehead"]) / 2.0
    c = (head["top_z"] - cz) + 0.006
    hairline_z = eye_z + 0.060

    CAP_SEGS  = 32
    CAP_RINGS = 12
    PHI_MAX   = math.pi * 0.52
    FRONT_PHI   = 3.0 * math.pi / 2.0   # frente = Y negativo no MPFB
    FRONT_RANGE = math.radians(60)

    cap_mesh = bpy.data.meshes.new("T3_HairCap_mesh")
    cap_obj  = bpy.data.objects.new("T3_HairCap", cap_mesh)
    bpy.context.collection.objects.link(cap_obj)

    bm = bmesh.new()
    pole = bm.verts.new((0.0, cy, cz + c))
    rings = []
    for ri in range(1, CAP_RINGS + 1):
        phi = PHI_MAX * ri / CAP_RINGS
        ring = []
        for s in range(CAP_SEGS):
            th = 2.0 * math.pi * s / CAP_SEGS
            ring.append(bm.verts.new((
                a * math.sin(phi) * math.cos(th),
                cy + b * math.sin(phi) * math.sin(th),
                cz + c * math.cos(phi),
            )))
        rings.append(ring)

    for s in range(CAP_SEGS):
        s_next = (s + 1) % CAP_SEGS
        bm.faces.new([pole, rings[0][s], rings[0][s_next]])
    for ri in range(len(rings) - 1):
        for s in range(CAP_SEGS):
            s_next = (s + 1) % CAP_SEGS
            bm.faces.new([rings[ri][s], rings[ri][s_next],
                          rings[ri + 1][s_next], rings[ri + 1][s]])

    bm.normal_update()
    centre = Vector((0.0, cy, cz))
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - centre) < 0:
            f.normal_flip()

    # sem cabelo sobre a testa/olhos: remove faces frontais abaixo da linha do cabelo
    to_delete = []
    for f in bm.faces:
        ctr = f.calc_center_median()
        th = math.atan2(ctr.y - cy, ctr.x) % (2.0 * math.pi)
        d = abs(th - FRONT_PHI)
        if d > math.pi:
            d = 2.0 * math.pi - d
        if d < math.radians(80) and ctr.z < hairline_z:
            to_delete.append(f)
    if to_delete:
        bmesh.ops.delete(bm, geom=to_delete, context="FACES")

    bm.to_mesh(cap_mesh)
    bm.free()
    cap_mesh.update()
    cap_obj.data.materials.append(hair_mat)
    objects_created.append(cap_obj.name)

    N_STRANDS   = 24
    STRAND_SEGS = 8

    for i in range(N_STRANDS):
        azimuth = 2.0 * math.pi * i / N_STRANDS

        diff = abs(azimuth - FRONT_PHI)
        if diff > math.pi:
            diff = 2.0 * math.pi - diff
        is_frontal = diff < FRONT_RANGE

        if is_frontal:
            phi_birth = math.pi * 0.30
            strand_len_factor = 0.75
            swing_back = 0.45
        else:
            phi_birth = math.pi * 0.15 + math.pi * 0.20 * ((i % 4) / 3.0)
            strand_len_factor = 1.0
            swing_back = 0.0

        root_x = a * math.sin(phi_birth) * math.cos(azimuth)
        root_y = cy + b * math.sin(phi_birth) * math.sin(azimuth)
        root_z = cz + c * math.cos(phi_birth)

        tip_z = bust_z + (shoulder_z - bust_z) * strand_len_factor * 0.6

        cdata = bpy.data.curves.new(name=f"T3_Strand_{i:02d}", type="CURVE")
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
            swing_y = swing_back * b * t * 0.40
            px = root_x * splay
            py = cy + (root_y - cy) * splay + swing_y
            sp.points[pi_idx].co = (px, py, pz, 1.0)

        sp.use_endpoint_u = True
        sp.order_u = min(4, STRAND_SEGS)

        strand_obj = bpy.data.objects.new(f"T3_Strand_{i:02d}", cdata)
        bpy.context.collection.objects.link(strand_obj)
        strand_obj.data.materials.append(hair_mat)
        objects_created.append(strand_obj.name)

    return objects_created, {
        "cap_center": [0.0, round(cy, 5), round(cz, 5)],
        "cap_semi_axes_abc": [round(a, 5), round(b, 5), round(c, 5)],
        "hairline_z": round(hairline_z, 5),
        "faces_removed_in_front": len(to_delete),
    }


# ─────────────────────────────────────────────────────────────────────────────
# OLHOS (logica nova)
# ─────────────────────────────────────────────────────────────────────────────

def build_eye_cap(name, centre, R, theta_max, mat):
    """Calote esferica de raio R centrada em `centre`, virada para -Y (frente)."""
    SEGS, RINGS = 32, 6
    me = bpy.data.meshes.new(name + "_mesh")
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)

    def pt(theta, psi):
        return (centre.x + R * math.sin(theta) * math.cos(psi),
                centre.y - R * math.cos(theta),
                centre.z + R * math.sin(theta) * math.sin(psi))

    bm = bmesh.new()
    pole = bm.verts.new(pt(0.0, 0.0))
    ring_list = []
    for ri in range(1, RINGS + 1):
        th = theta_max * ri / RINGS
        ring_list.append([bm.verts.new(pt(th, 2.0 * math.pi * s / SEGS))
                          for s in range(SEGS)])
    for s in range(SEGS):
        bm.faces.new([pole, ring_list[0][s], ring_list[0][(s + 1) % SEGS]])
    for ri in range(len(ring_list) - 1):
        for s in range(SEGS):
            sn = (s + 1) % SEGS
            bm.faces.new([ring_list[ri][s], ring_list[ri][sn],
                          ring_list[ri + 1][sn], ring_list[ri + 1][s]])

    bm.normal_update()
    for f in bm.faces:
        if f.normal.dot(f.calc_center_median() - centre) < 0:
            f.normal_flip()
    bm.to_mesh(me)
    bm.free()
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    assign_material(ob, mat)
    return ob


def build_eyes(human, anchors, body_min_z, body_h):
    sclera_mat = make_material("T3_Sclera", COLOR_SCLERA, roughness=0.05)
    iris_mat   = make_material("T3_Iris",   COLOR_IRIS,   roughness=0.12)
    pupil_mat  = make_material("T3_Pupil",  COLOR_PUPIL,  roughness=0.04)

    bpy.context.view_layer.update()
    bvh = BVHTree.FromObject(human, bpy.context.evaluated_depsgraph_get())
    scale_ref = body_h / 1.70

    objects_created = []
    final = {}

    for side, sign in (("L", 1), ("R", -1)):
        a = anchors.get(side)
        radius = 0.012 * scale_ref
        center = None
        source = None
        classification = None
        note = ""

        if a is not None:
            c = a["center"]
            z_rel = (c.z - body_min_z) / body_h
            plausible = (0.90 <= z_rel <= 0.965 and 0.015 <= abs(c.x) <= 0.05 and c.y < 0.0)
            if plausible:
                center = Vector(c)
                radius = clamp(max(a["dims"]) / 2.0, 0.0095 * scale_ref, 0.0145 * scale_ref)
                source = a["group"]
                classification = "CONFIRMADO_GRUPO_HELPER_NA_MALHA_FINAL"
                note = (f"Centro (bbox) do grupo '{a['group']}' ({a['vertex_count']} vertices) "
                        f"medido na malha com shape keys cozidas e escalada.")
            else:
                note = (f"Grupo '{a['group']}' fora dos limites anatomicos "
                        f"(altura relativa={z_rel:.3f}, x={c.x:.4f}, y={c.y:.4f}); usado fallback.")

        if center is None:
            fx = sign * 0.031 * scale_ref
            fz = body_min_z + body_h * 0.935
            h0, _n, _i, _d = bvh.ray_cast(Vector((fx, -5.0, fz)), Vector((0.0, 1.0, 0.0)))
            fy = (h0.y + radius * 0.9) if h0 is not None else -0.115 * scale_ref
            center = Vector((fx, fy, fz))
            source = "fallback_proporcao_sobre_malha_final"
            classification = "INFERIDO_FALLBACK"
            note = (note + " " if note else "") + "Altura 0.935 do corpo, x=+/-31 mm, y a 0.9 R atras da pele."

        # verificacao contra a pele (so diagnostico + avanco minimo se a pele tapar o globo)
        hit, _n, _i, _d = bvh.ray_cast(Vector((center.x, -5.0, center.z)), Vector((0.0, 1.0, 0.0)))
        front_y = center.y - radius
        shift = 0.0
        if hit is None:
            skin_state = "sem_pele_a_frente (abertura)"
            hit_y = None
        else:
            hit_y = hit.y
            if hit.y >= center.y - 0.5 * radius:
                skin_state = "abertura_ou_pele_atras_do_centro"
            elif hit.y < front_y - 0.001:
                shift = min(front_y - hit.y + 0.0008, 0.012)
                center.y -= shift
                skin_state = "pele_tapava_o_globo_avancado"
            else:
                skin_state = "pele_junto_a_superficie_do_globo_ok"

        iris_r  = radius * 0.52
        pupil_r = iris_r * 0.45

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16, radius=radius, location=tuple(center))
        sc = bpy.context.active_object
        sc.name = f"T3_Sclera_{side}"
        for p in sc.data.polygons:
            p.use_smooth = True
        assign_material(sc, sclera_mat)

        ir = build_eye_cap(f"T3_Iris_{side}", center, radius * 1.010,
                           math.asin(min(iris_r / radius, 0.99)), iris_mat)
        pu = build_eye_cap(f"T3_Pupil_{side}", center, radius * 1.020,
                           math.asin(min(pupil_r / radius, 0.99)), pupil_mat)

        objects_created.extend([sc.name, ir.name, pu.name])
        final[side] = {
            "x": center.x, "y": center.y, "z": center.z,
            "radius": radius, "iris_r": iris_r, "pupil_r": pupil_r,
            "source": source, "classification": classification, "note": note,
            "skin_state": skin_state,
            "skin_hit_y": None if hit_y is None else round(hit_y, 6),
            "forward_shift_m": round(shift, 6),
            "height_ratio": round((center.z - body_min_z) / body_h, 4),
        }
        eye_diag[f"placement_{side}"] = dict(final[side])

    eye_diag["eye_separation_m"] = round(abs(final["L"]["x"] - final["R"]["x"]), 6)
    eye_diag["status"] = "built"
    return objects_created, final


# ─────────────────────────────────────────────────────────────────────────────
# CALCADO (identico a B7)
# ─────────────────────────────────────────────────────────────────────────────

def build_shoes(body_min_z):
    shoe_mat = make_material("T3_Shoes", COLOR_SHOE, roughness=0.30, metallic=0.05)

    SHOE_L, SHOE_W, SHOE_H, SHOE_SEP = 0.24, 0.09, 0.045, 0.10

    objects_created = []
    for side, sign in [("L", -1), ("R", 1)]:
        shoe_cx = sign * SHOE_SEP / 2.0

        mesh_data = bpy.data.meshes.new(f"T3_Shoe_{side}_mesh")
        shoe_obj  = bpy.data.objects.new(f"T3_Shoe_{side}", mesh_data)
        bpy.context.collection.objects.link(shoe_obj)

        bm = bmesh.new()
        hw     = SHOE_W / 2.0
        hl     = SHOE_L / 2.0
        toe_hw = hw * 0.35
        bz     = body_min_z

        verts = [
            bm.verts.new((shoe_cx - hw,     -hl, bz)),
            bm.verts.new((shoe_cx + hw,     -hl, bz)),
            bm.verts.new((shoe_cx + toe_hw,  hl, bz)),
            bm.verts.new((shoe_cx - toe_hw,  hl, bz)),
            bm.verts.new((shoe_cx - hw,     -hl, bz + SHOE_H)),
            bm.verts.new((shoe_cx + hw,     -hl, bz + SHOE_H)),
            bm.verts.new((shoe_cx + toe_hw,  hl, bz + SHOE_H * 0.70)),
            bm.verts.new((shoe_cx - toe_hw,  hl, bz + SHOE_H * 0.70)),
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
# RENDER (validacao visual)
# ─────────────────────────────────────────────────────────────────────────────

def setup_render(samples=48, w=768, h=768):
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


def place_camera_persp(loc, target, lens_mm, name):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.active_object
    cam.name = name
    cam.data.type = "PERSP"
    cam.data.lens = lens_mm
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def place_camera_ortho(loc, target, ortho_scale, name):
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
# RELATORIO LEGIVEL
# ─────────────────────────────────────────────────────────────────────────────

def write_validation_report():
    L = []
    L.append("# T3 — Relatorio de Validacao (olhos dentro das orbitas)\n")
    L.append("Baseado em: `scripts/characters/create_female_b7.py` (nao modificado)\n")
    L.append(f"Estado geral da execucao: **{report.get('status', 'desconhecido')}**\n")

    L.append("\n## Prova da causa (shape keys cozidas)\n")
    be = eye_diag.get("bake_effect", {})
    L.append(f"- Bake: {json.dumps(report.get('components', {}).get('bake', {}), ensure_ascii=False)}")
    for side in ("L", "R"):
        L.append(f"- Deslocamento do centro do olho {side} causado pelo morph (antes -> depois do bake, m): "
                 f"{be.get('center_shift_' + side)}")
    L.append("- Se o deslocamento em Z for de alguns cm, confirma-se a causa da T1/T2 "
             "(olhos medidos na cabeca 'neutra', cara exportada com o morph feminino).")

    L.append("\n## Posicao final dos olhos\n")
    for side in ("L", "R"):
        p = eye_diag.get(f"placement_{side}", {})
        L.append(f"- Olho {side}: `{p.get('classification')}` fonte `{p.get('source')}` — "
                 f"x={p.get('x')}, y={p.get('y')}, z={p.get('z')}, raio={p.get('radius')}, "
                 f"altura relativa={p.get('height_ratio')}, pele: {p.get('skin_state')} "
                 f"(avanco {p.get('forward_shift_m')} m). {p.get('note', '')}")
    L.append(f"\n- Separacao entre olhos: {eye_diag.get('eye_separation_m')} m")

    L.append("\n## Cabeca / cabelo\n")
    L.append(f"- {json.dumps(eye_diag.get('head_geometry', {}), ensure_ascii=False)}")

    L.append("\n## Imagens de validacao\n")
    for tag, data in report.get("renders", {}).items():
        st = "OK" if data.get("rendered") else "FALHOU"
        L.append(f"- `{tag}`: {st} ({data.get('file', data.get('error', ''))})")
    if not report.get("renders"):
        L.append("- nenhuma imagem gerada nesta execucao.")

    L.append("\n## Limitacoes conhecidas\n")
    for item in report.get("visual_limitations", []):
        L.append(f"- {item}")

    L.append("\n## Conclusao\n")
    if report.get("status") == "success_t3_complete" and report.get("renders") and all(
        d.get("rendered") for d in report["renders"].values()
    ):
        L.append("Ficheiros e imagens gerados. **A aprovacao final exige olhar para "
                 "`t3_view_front.png` e `t3_view_eyes_closeup.png`.** Classificacao sugerida: "
                 "IMPLEMENTADO, A VALIDAR VISUALMENTE.")
    else:
        L.append("Execucao incompleta. Ver `status`, `error` e `renders`.")

    (out / "validation_report.md").write_text("\n".join(L), encoding="utf-8")


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

    # ── LIMPAR CENA / CRIAR HUMANO ───────────────────────────────────────────
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

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
        "modifiers": [m.type for m in human.modifiers],
    }

    # helpers ANTES do morph (malha neutra) — para provar o efeito
    eyes_before = measure_eye_helpers(human)
    eye_diag["helpers_before_bake"] = public_helpers(eyes_before)

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

    # ── CORRECCAO 1: gravar o mix das shape keys na malha ────────────────────
    bake_info = bake_shape_keys(human)
    report["components"]["bake"] = bake_info
    if not bake_info.get("baked"):
        raise RuntimeError("bake_shape_keys nao gravou o mix")

    eyes_after_bake = measure_eye_helpers(human)
    eye_diag["helpers_after_bake"] = public_helpers(eyes_after_bake)
    for side in ("L", "R"):
        b0, b1 = eyes_before.get(side), eyes_after_bake.get(side)
        if b0 is not None and b1 is not None:
            eye_diag["bake_effect"][f"center_shift_{side}"] = vl(b1["center"] - b0["center"])

    # ── ESCALA PARA 1,70 m (agora sobre a malha final) ───────────────────────
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
        "method":                 "bmesh_vertex_scale_after_bake",
    }

    body_h        = body_max_z - body_min_z
    waist_z       = body_min_z + body_h * 0.575
    bust_z        = body_min_z + body_h * 0.695
    shoulder_z    = body_min_z + body_h * 0.800

    # ── PELE ─────────────────────────────────────────────────────────────────
    skin_mat = make_material("T3_Skin", COLOR_SKIN, roughness=0.52, subsurface=0.18)
    assign_material(human, skin_mat)
    report["components"]["skin"] = {"material": "T3_Skin", "subsurface": 0.18}
    report["components"]["dress"] = {"status": "omitted_by_design", "reason": "Herdado da B7"}

    # ── OLHOS (primeiro, porque a touca do cabelo depende da altura dos olhos)
    eyes_final = measure_eye_helpers(human)
    eye_diag["helpers_final"] = public_helpers(eyes_final)

    eye_objects, eye_final_positions = build_eyes(human, eyes_final, body_min_z, body_h)
    report["components"]["eyes"] = {
        "objects": eye_objects,
        "positioning": "helper_group_bbox_center_on_baked_scaled_mesh",
        "diagnostic": eye_diag,
    }

    eye_mid_z = (eye_final_positions["L"]["z"] + eye_final_positions["R"]["z"]) / 2.0

    # ── CABELO (touca ajustada a cabeca real) ────────────────────────────────
    head = measure_head(human, eye_mid_z)
    eye_diag["head_geometry"] = {k: (round(v, 5) if isinstance(v, float) else v)
                                 for k, v in head.items()}
    hair_objects, hair_info = build_hair(head, eye_mid_z, waist_z, shoulder_z, bust_z)
    eye_diag["head_geometry"]["hair"] = hair_info
    report["components"]["hair"] = {
        "objects": hair_objects,
        "cap": "T3_HairCap",
        "strands": len(hair_objects) - 1,
        "color_hex": COLOR_HAIR,
        "info": hair_info,
    }

    # ── CALCADO ──────────────────────────────────────────────────────────────
    shoe_objects = build_shoes(body_min_z)
    report["components"]["shoes"] = {"objects": shoe_objects, "color_hex": COLOR_SHOE}

    # ── VALIDACOES ───────────────────────────────────────────────────────────
    expected = {
        "Human", "T3_HairCap",
        "T3_Sclera_L", "T3_Iris_L", "T3_Pupil_L",
        "T3_Sclera_R", "T3_Iris_R", "T3_Pupil_R",
        "T3_Shoe_L", "T3_Shoe_R",
    }
    for i in range(24):
        expected.add(f"T3_Strand_{i:02d}")

    scene_objects = {o.name for o in bpy.data.objects if o.type not in ("LIGHT", "CAMERA")}
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
    ratios = [eye_final_positions[s]["height_ratio"] for s in ("L", "R")]
    report["validations"]["eye_geometry"] = {
        "eye_separation_m": eye_sep,
        "separation_plausible": 0.05 <= eye_sep <= 0.075,
        "height_ratio_L_R": ratios,
        "height_ratio_plausible": all(0.90 <= r <= 0.965 for r in ratios),
        "source_L": eye_final_positions["L"]["source"],
        "source_R": eye_final_positions["R"]["source"],
        "from_helper_groups": all(
            eye_final_positions[s]["classification"] == "CONFIRMADO_GRUPO_HELPER_NA_MALHA_FINAL"
            for s in ("L", "R")),
        "pass": (0.05 <= eye_sep <= 0.075) and all(0.90 <= r <= 0.965 for r in ratios),
    }

    mat_check = {}
    for oname in expected:
        obj = bpy.data.objects.get(oname)
        if obj and hasattr(obj.data, "materials"):
            mat_check[oname] = len(obj.data.materials) > 0
        else:
            mat_check[oname] = bool(obj)
    report["validations"]["materials"] = {
        "per_object": mat_check,
        "pass": all(mat_check.values()),
    }
    report["validations"]["overall_pass"] = all(
        v.get("pass", False) for v in report["validations"].values()
        if isinstance(v, dict) and "pass" in v
    )

    report["visual_limitations"] = [
        "Morph feminino experimental — nao usa presets oficiais do MPFB (herdado da B7)",
        "Cabelo em curvas NURBS; madeixas frontais ainda caem a frente do pescoco/peito (herdado da B7)",
        "Calcado em prisma bmesh basico (herdado da B7)",
        "Sem rigging nem armature; pose T (herdado da B7)",
        "Sem vestuario (herdado da B7)",
        "O globo ocular usa o centro/raio do grupo helper do MPFB; se as palpebras da malha "
        "base estiverem fechadas, o globo so aparece se avancar (ver skin_state no diagnostico)",
        "A causa 'shape keys nao cozidas' e INFERIDA por leitura de codigo; confirmar em "
        "diagnostic_report.json > bake_effect",
        "Aprovacao visual exige inspeccao humana das imagens geradas",
    ]

    # ── RENDERS ──────────────────────────────────────────────────────────────
    head_radius   = body_h * 0.075
    head_center_z = eye_mid_z - 0.012
    setup_render(samples=48)

    head_tgt   = Vector((0.0, 0.0, head_center_z))
    cam_face_y = -head_radius * 5.0

    clear_lights_cameras()
    add_light((head_radius * 2.5, cam_face_y + head_radius, head_center_z + head_radius * 0.5),
              head_tgt, 600, 0.6, "T3_Key")
    add_light((-head_radius * 2.0, cam_face_y + head_radius * 0.8, head_center_z),
              head_tgt, 300, 0.5, "T3_Fill")
    add_light((0.0, head_radius * 2.0, head_center_z + head_radius),
              head_tgt, 200, 0.4, "T3_Rim")

    place_camera_persp((0.0, cam_face_y, head_center_z), head_tgt, 85, "T3Cam_Front")
    render_to(out / "t3_view_front.png", "front")

    place_camera_persp((head_radius * 4.0, 0.0, head_center_z), head_tgt, 85, "T3Cam_Side")
    render_to(out / "t3_view_side.png", "side")

    place_camera_persp((head_radius * 2.8, cam_face_y * 0.6, head_center_z), head_tgt, 85,
                       "T3Cam_ThreeQuarter")
    render_to(out / "t3_view_threequarter.png", "three_quarter")

    eL, eR = eye_final_positions["L"], eye_final_positions["R"]
    eye_mid_y = (eL["y"] + eR["y"]) / 2.0
    eye_sep_m = abs(eL["x"] - eR["x"])
    eye_ortho = max(eye_sep_m * 3.0, eL["radius"] * 8)
    eye_tgt = Vector((0.0, eye_mid_y, eye_mid_z))
    cam_eye_y = eye_mid_y - eye_ortho * 3.0
    clear_lights_cameras()
    add_light((eye_sep_m * 0.8, cam_eye_y + eye_sep_m, eye_mid_z + head_radius * 0.1),
              eye_tgt, 300, 0.3, "T3_EyeKey")
    add_light((-eye_sep_m * 0.6, cam_eye_y + eye_sep_m * 0.8, eye_mid_z),
              eye_tgt, 150, 0.25, "T3_EyeFill")
    place_camera_ortho((0.0, cam_eye_y, eye_mid_z), eye_tgt, eye_ortho, "T3Cam_Eyes")
    render_to(out / "t3_view_eyes_closeup.png", "eyes_closeup", samples=96)

    set_objects_visibility(hair_objects, True)
    clear_lights_cameras()
    add_light((head_radius * 2.5, cam_face_y + head_radius, head_center_z + head_radius * 0.5),
              head_tgt, 600, 0.6, "T3_Key2")
    add_light((-head_radius * 2.0, cam_face_y + head_radius * 0.8, head_center_z),
              head_tgt, 300, 0.5, "T3_Fill2")
    place_camera_persp((0.0, cam_face_y, head_center_z), head_tgt, 85, "T3Cam_FrontNoHair")
    render_to(out / "t3_view_front_no_hair.png", "front_no_hair")
    set_objects_visibility(hair_objects, False)

    # ── .BLEND E GLB ─────────────────────────────────────────────────────────
    blend_path = out / "mpfb-female-t3.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    if not blend_path.exists():
        raise RuntimeError(f".blend nao gravado: {blend_path}")
    report["blend_bytes"] = blend_path.stat().st_size

    glb_path = out / "mpfb-female-t3.glb"
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

    report["status"] = "success_t3_complete"

except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    report["traceback"] = traceback.format_exc()

finally:
    (out / "mpfb-t3-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )
    (out / "diagnostic_report.json").write_text(
        json.dumps({"blender": report.get("blender"),
                    "mpfb_module": report.get("components", {}).get("mpfb", {}).get("module"),
                    "script": "scripts/characters/create_female_t3.py",
                    "bake": report.get("components", {}).get("bake"),
                    "eyes": eye_diag,
                    "renders": report.get("renders", {}),
                    "errors_and_warnings": {
                        "error": report.get("error"),
                        "traceback": report.get("traceback"),
                    },
                    "overall_status": report.get("status")},
                   ensure_ascii=False, indent=2, default=str)
    )
    try:
        write_validation_report()
    except Exception:
        (out / "validation_report.md").write_text(
            "# T3 — Relatorio de Validacao\n\nFalha ao gerar o relatorio legivel. "
            "Consultar diagnostic_report.json e mpfb-t3-report.json.\n",
            encoding="utf-8",
        )
    print("MPFB_T3_REPORT:", json.dumps(report, ensure_ascii=False, default=str))

if report["status"] != "success_t3_complete":
    sys.exit(1)
