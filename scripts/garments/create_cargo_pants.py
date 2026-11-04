# ============================================================
# CALÇA CARGO MARFIM — ESTILO URBANO / Y2K
# Projeto: Hombaka Song
# Versão: 1.0
# Blender: 5.2+ | Unidades: metros | Eixo Z = altura
#
# Estrutura: painéis planos costurados → Cloth → detalhes
# Saída: output/cargo-pants/
# ============================================================

import bpy
import math
import os
import json
from mathutils import Vector

# ─────────────────────── CONFIG ──────────────────────────────
CFG = {
    # Medidas (metros) — ficha técnica
    "waist_circ":        0.76,
    "hip_circ":          1.04,
    "outseam":           1.02,
    "inseam":            0.74,
    "rise":              0.24,
    "thigh_flat":        0.34,
    "hem_flat":          0.25,
    "pocket_w":          0.17,
    "pocket_h":          0.21,
    "waistband_h":       0.055,
    "fabric_thick":      0.004,

    # Cores PBR (RGBA linear)
    "col_ivory":         (0.957, 0.957, 0.930, 1.0),   # #F5F5ED
    "col_lilas":         (0.655, 0.631, 0.702, 1.0),   # #A7A1B3
    "col_stamp":         (0.420, 0.420, 0.420, 1.0),   # #6B6B6B
    "col_metal":         (0.55,  0.53,  0.57,  1.0),

    # Flags
    "cloth_sim":         True,
    "cloth_quality":     8,
    "cloth_steps":       120,

    # Saída
    "output_dir":        os.environ.get("CARGO_OUTPUT",
                         os.path.join(os.path.dirname(__file__),
                                      "../../output/cargo-pants")),
}

OUT = CFG["output_dir"]
os.makedirs(OUT, exist_ok=True)

# ─────────────────────── UTILIDADES ──────────────────────────
def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for d in [bpy.data.meshes, bpy.data.curves,
              bpy.data.materials, bpy.data.collections]:
        for item in list(d):
            d.remove(item)

def make_mat(name, color, rough=0.86, metallic=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value  = color
    b.inputs["Roughness"].default_value   = rough
    b.inputs["Metallic"].default_value    = metallic
    return m

def link_col(col, obj):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)

def apply_mat(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)

def apply_transforms(obj):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

# ─────────────────────── CENA / COLECÇÃO ─────────────────────
clear_scene()

col = bpy.data.collections.new("CALCA_CARGO")
bpy.context.scene.collection.children.link(col)

scene = bpy.context.scene
scene.unit_settings.system      = 'METRIC'
scene.unit_settings.length_unit = 'METERS'
scene.render.engine             = 'BLENDER_EEVEE_NEXT'
scene.render.resolution_x       = 1200
scene.render.resolution_y       = 1200
scene.frame_start = 1
scene.frame_end   = CFG["cloth_steps"]

# ─────────────────────── MATERIAIS ───────────────────────────
MAT_FABRIC  = make_mat("Sarja_Marfim",    CFG["col_ivory"],  0.88)
MAT_LILAS   = make_mat("Cinto_Lilas",     CFG["col_lilas"],  0.68)
MAT_STAMP   = make_mat("Estampa_Cinza",   CFG["col_stamp"],  0.75)
MAT_STITCH  = make_mat("Costura",         (0.70,0.67,0.62,1), 0.90)
MAT_METAL   = make_mat("Metal_Fivela",    CFG["col_metal"],  0.28, 0.80)

# ─────────────────────── HELPERS GEOMÉTRICOS ─────────────────
def flat_panel(name, verts_2d, z_offset=0.0, mat=None):
    """Cria um painel plano a partir de vértices 2D (x,y) → plano XZ."""
    verts = [(x, 0.0, z + z_offset) for x, z in verts_2d]
    faces = [list(range(len(verts)))]
    mesh  = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    link_col(col, obj)
    if mat:
        apply_mat(obj, mat)
    return obj

def add_solidify(obj, thick=None):
    s = obj.modifiers.new("Solidify", "SOLIDIFY")
    s.thickness = thick or CFG["fabric_thick"]
    s.offset    = 1.0

def add_subsurf(obj, levels=1):
    s = obj.modifiers.new("Subsurf", "SUBSURF")
    s.levels        = levels
    s.render_levels = levels

def cube_part(name, loc, dims, mat, bev=0.006):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name       = name
    o.dimensions = dims
    bpy.ops.object.transform_apply(scale=True)
    link_col(col, o)
    apply_mat(o, mat)
    if bev:
        m = o.modifiers.new("Bevel","BEVEL")
        m.width    = bev
        m.segments = 3
    return o

# ─────────────────────── PERNAS (PAINÉIS) ────────────────────
# Cada perna = painel frontal + painel traseiro posicionados
# frente a frente, prontos para simulação Cloth.

W_THIGH  = CFG["thigh_flat"] / 2       # metade da largura na coxa
W_HEM    = CFG["hem_flat"]   / 2       # metade na bainha
L_LEG    = CFG["outseam"] - CFG["rise"]  # comprimento da perna

# Perfil de perna: bainha → coxa → gancho
# (x, z) relativos ao centro da perna
LEG_PROFILE = [
    (-W_HEM,          0.00),
    (-W_THIGH * 1.05, L_LEG * 0.30),
    (-W_THIGH * 1.08, L_LEG * 0.65),
    (-W_THIGH * 1.05, L_LEG * 0.90),
    (-W_THIGH * 0.98, L_LEG),
    ( W_THIGH * 0.98, L_LEG),
    ( W_THIGH * 1.05, L_LEG * 0.90),
    ( W_THIGH * 1.08, L_LEG * 0.65),
    ( W_THIGH * 1.05, L_LEG * 0.30),
    ( W_HEM,          0.00),
]

for side, cx, cy in [("Esq", -0.165, -0.11), ("Dir", 0.165, -0.11)]:
    for face, dy in [("Frente", -0.001), ("Tras", 0.001)]:
        pname = f"Perna_{side}_{face}"
        p = flat_panel(pname, LEG_PROFILE, mat=MAT_FABRIC)
        p.location = (cx, cy + dy, 0.0)
        add_solidify(p)
        add_subsurf(p, 1)

# ─────────────────────── CÓS / CINTURA ───────────────────────
rise_z = L_LEG
waist_w = CFG["waist_circ"] / 2 + 0.01

cos = cube_part("Cos_Cintura",
                (0.0, -0.12, rise_z + CFG["waistband_h"] / 2),
                (waist_w * 2, 0.22, CFG["waistband_h"]),
                MAT_FABRIC, 0.010)

# Fecho frontal
cube_part("Fecho_Frontal",
          (0.0, -0.122, rise_z - 0.08),
          (0.020, 0.008, 0.14),
          MAT_STITCH, 0.003)

# Passadores (6 ao longo da cintura)
for i, px in enumerate([-0.155, -0.08, 0.0, 0.08, 0.155]):
    cube_part(f"Passador_{i+1}",
              (px, -0.126, rise_z + 0.006),
              (0.020, 0.012, 0.070),
              MAT_FABRIC, 0.003)

# ─────────────────────── BOLSOS CARGO ────────────────────────
pw, ph = CFG["pocket_w"], CFG["pocket_h"]

for side, sx in [("Esq", -1), ("Dir", 1)]:
    bx = sx * (0.165 + 0.095)
    bz = 0.38

    # Corpo do bolso (3 faces: frontal, lateral ext, lateral int)
    cube_part(f"Bolso_Corpo_{side}",
              (bx, -0.128, bz + ph / 2),
              (pw, 0.040, ph), MAT_FABRIC, 0.010)

    # Fole lateral externo (dá volume)
    cube_part(f"Bolso_Fole_{side}",
              (bx + sx * pw * 0.52, -0.128, bz + ph / 2),
              (0.018, 0.040, ph), MAT_FABRIC, 0.005)

    # Aba do bolso
    cube_part(f"Bolso_Aba_{side}",
              (bx, -0.150, bz + ph * 0.82),
              (pw * 1.06, 0.016, 0.052), MAT_FABRIC, 0.007)

    # Presilha + fivela da aba
    cube_part(f"Presilha_{side}",
              (bx, -0.162, bz + ph * 0.42),
              (0.028, 0.012, 0.090), MAT_FABRIC, 0.004)
    cube_part(f"Fivela_{side}",
              (bx, -0.172, bz - 0.004),
              (0.030, 0.008, 0.024), MAT_METAL, 0.004)

# ─────────────────────── BOLSOS TRASEIROS ────────────────────
for side, bx in [("Esq", -0.108), ("Dir", 0.108)]:
    cube_part(f"Bolso_Tras_{side}",
              (bx, 0.118, rise_z - 0.16),
              (0.108, 0.016, 0.125), MAT_FABRIC, 0.005)

# ─────────────────────── CINTO ───────────────────────────────
belt = cube_part("Cinto",
                 (0.0, -0.136, rise_z + 0.004),
                 (waist_w * 2 + 0.01, 0.016, 0.042),
                 MAT_LILAS, 0.007)

# Fivela coração (2 meias-esferas + ponta)
for name, lx in [("Coracao_L", -0.014), ("Coracao_R", 0.014)]:
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=20, ring_count=10,
        location=(lx, -0.150, rise_z + 0.005))
    hb = bpy.context.object
    hb.name  = f"Fivela_{name}"
    hb.scale = (0.019, 0.006, 0.019)
    bpy.ops.object.transform_apply(scale=True)
    link_col(col, hb)
    apply_mat(hb, MAT_METAL)

cube_part("Fivela_Coracao_Ponta",
          (0.0, -0.150, rise_z - 0.014),
          (0.014, 0.008, 0.032), MAT_METAL, 0.004)

# ─────────────────────── ESTAMPA ESTRELA ─────────────────────
def create_star(name, cx, cy, cz, r_out=0.065, r_in=0.028, pts=5):
    verts = []
    for i in range(pts * 2):
        angle = math.pi / 2 + i * math.pi / pts
        r     = r_out if i % 2 == 0 else r_in
        verts.append((cx + math.cos(angle) * r, cy,
                      cz + math.sin(angle) * r))
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], [list(range(len(verts)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    link_col(col, obj)
    apply_mat(obj, MAT_STAMP)
    sol = obj.modifiers.new("Espessura", "SOLIDIFY")
    sol.thickness = 0.0015
    # Estrela dupla (contorno exterior)
    mesh2 = bpy.data.meshes.new(name + "_outline_mesh")
    verts2 = []
    for i in range(pts * 2):
        angle = math.pi / 2 + i * math.pi / pts
        r     = (r_out + 0.012) if i % 2 == 0 else (r_in + 0.006)
        verts2.append((cx + math.cos(angle) * r, cy - 0.0005,
                       cz + math.sin(angle) * r))
    mesh2.from_pydata(verts2, [], [list(range(len(verts2)))])
    mesh2.update()
    obj2 = bpy.data.objects.new(name + "_outline", mesh2)
    bpy.context.scene.collection.objects.link(obj2)
    link_col(col, obj2)
    apply_mat(obj2, MAT_LILAS)
    sol2 = obj2.modifiers.new("Espessura", "SOLIDIFY")
    sol2.thickness = 0.0012
    return obj

# Perna direita: posição conforme ficha
create_star("Estampa_Estrela",
            cx=0.165, cy=-0.130, cz=L_LEG * 0.68)

# ─────────────────────── COSTURAS VISÍVEIS ───────────────────
def seam(name, pts, radius=0.0014):
    c = bpy.data.curves.new(name, 'CURVE')
    c.dimensions    = '3D'
    c.bevel_depth   = radius
    c.bevel_resolution = 2
    sp = c.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for p, co in zip(sp.points, pts):
        p.co = (*co, 1.0)
    obj = bpy.data.objects.new(name, c)
    bpy.context.scene.collection.objects.link(obj)
    link_col(col, obj)
    obj.data.materials.append(MAT_STITCH)
    return obj

for side, sx in [("Esq", -1), ("Dir", 1)]:
    cx = sx * 0.165
    # Costura externa da perna
    seam(f"Costura_Ext_{side}", [
        (cx + sx * 0.115, -0.001, 0.06),
        (cx + sx * 0.120, -0.001, 0.38),
        (cx + sx * 0.122, -0.001, 0.68),
        (cx + sx * 0.118, -0.001, L_LEG),
    ])
    # Costura interna da perna
    seam(f"Costura_Int_{side}", [
        (cx - sx * 0.040, -0.001, 0.06),
        (cx - sx * 0.042, -0.001, 0.38),
        (cx - sx * 0.040, -0.001, L_LEG * 0.90),
    ])

# ─────────────────────── BAINHA DOBRADA ──────────────────────
for side, cx in [("Esq", -0.165), ("Dir", 0.165)]:
    b = cube_part(f"Bainha_{side}",
                  (cx, -0.11, 0.028),
                  (CFG["hem_flat"] * 0.96, 0.21, 0.052),
                  MAT_FABRIC, 0.005)

# ─────────────────────── SIMULAÇÃO CLOTH ─────────────────────
if CFG["cloth_sim"]:
    for obj in col.objects:
        if obj.name.startswith("Perna_"):
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_add(type='CLOTH')
            cloth = obj.modifiers["Cloth"]
            cs    = cloth.settings
            # Parâmetros para sarja/algodão
            cs.quality            = CFG["cloth_quality"]
            cs.mass               = 0.42
            cs.tension_stiffness  = 15.0
            cs.compression_stiffness = 10.0
            cs.shear_stiffness    = 5.0
            cs.bending_stiffness  = 0.8
            cs.tension_damping    = 5.0
            cs.compression_damping= 5.0
            cs.shear_damping      = 5.0
            cs.bending_damping    = 0.5
            cs.air_damping        = 1.0
            cs.use_self_collision = True
            cs.self_distance_min  = 0.004
            # Pin na cintura (grupo de vértices topo)
            vg = obj.vertex_groups.new(name="Pin_Cintura")
            mesh = obj.data
            top_verts = [v.index for v in mesh.vertices
                         if v.co.z > L_LEG * 0.92]
            vg.add(top_verts, 1.0, 'REPLACE')
            cs.vertex_group_mass = "Pin_Cintura"

# ─────────────────────── CÂMERAS E RENDERIZAÇÃO ──────────────
def add_camera(name, loc, rot_deg):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.object
    cam.name     = name
    cam.rotation_euler = [math.radians(r) for r in rot_deg]
    link_col(col, cam)
    return cam

cam_front = add_camera("Cam_Frente",
                        (0, -1.80, 0.60), (90, 0, 0))
cam_back  = add_camera("Cam_Tras",
                        (0,  1.80, 0.60), (90, 0, 180))
cam_side  = add_camera("Cam_Lateral",
                        (1.80, 0, 0.60),  (90, 0, 90))

# Luz principal
bpy.ops.object.light_add(type='SUN', location=(2, -2, 4))
sun = bpy.context.object
sun.name = "Luz_Principal"
sun.data.energy = 3.0
link_col(col, sun)

bpy.ops.object.light_add(type='AREA', location=(-1.5, -1, 2))
fill = bpy.context.object
fill.name = "Luz_Fill"
fill.data.energy = 400
link_col(col, fill)

def render_view(cam, filename):
    scene.camera = cam
    scene.render.filepath = os.path.join(OUT, filename)
    scene.render.image_settings.file_format = 'PNG'
    bpy.ops.render.render(write_still=True)

# ─────────────────────── GUARDAR .blend ──────────────────────
blend_path = os.path.join(OUT, "cargo_pants.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print(f"[OK] .blend guardado: {blend_path}")

# ─────────────────────── EXPORTAR .glb ───────────────────────
glb_path = os.path.join(OUT, "cargo_pants.glb")
bpy.ops.export_scene.gltf(
    filepath=glb_path,
    export_format='GLB',
    export_apply=True,
    export_materials='EXPORT',
    export_normals=True,
    export_colors=True,
)
print(f"[OK] .glb exportado: {glb_path}")

# ─────────────────────── RENDERS ─────────────────────────────
render_view(cam_front, "view_front.png")
render_view(cam_back,  "view_back.png")
render_view(cam_side,  "view_side.png")

# Detalhe bolso cargo direito
scene.camera = cam_side
bpy.context.scene.render.filepath = os.path.join(OUT, "view_detail_pocket.png")
bpy.ops.render.render(write_still=True)

# Detalhe estrela
scene.camera = cam_front
bpy.context.scene.render.filepath = os.path.join(OUT, "view_detail_star.png")
bpy.ops.render.render(write_still=True)

# ─────────────────────── RELATÓRIO ───────────────────────────
report = {
    "garment":      "Calça Cargo Marfim — Hombaka Song",
    "version":      "1.0",
    "blender":      bpy.app.version_string,
    "medidas_cm": {
        "cintura":          76,
        "anca":             104,
        "comprimento_total":102,
        "entrepernas":      74,
        "rise":             24,
        "largura_coxa":     34,
        "largura_bainha":   25,
        "bolso_w":          17,
        "bolso_h":          21,
    },
    "cloth_sim":    CFG["cloth_sim"],
    "output_files": [
        "cargo_pants.blend",
        "cargo_pants.glb",
        "view_front.png",
        "view_back.png",
        "view_side.png",
        "view_detail_pocket.png",
        "view_detail_star.png",
    ],
    "status": "gerado — validar silhueta e simulação visualmente",
}

with open(os.path.join(OUT, "validation_report.md"), "w") as f:
    f.write("# Relatório — Calça Cargo Marfim\n\n")
    f.write(f"**Versão:** {report['version']}\n")
    f.write(f"**Blender:** {report['blender']}\n\n")
    f.write("## Medidas (cm)\n")
    for k, v in report["medidas_cm"].items():
        f.write(f"- {k}: {v}\n")
    f.write("\n## Ficheiros produzidos\n")
    for fi in report["output_files"]:
        f.write(f"- {fi}\n")
    f.write("\n## Status\n")
    f.write(f"{report['status']}\n")

with open(os.path.join(OUT, "cargo_pants_report.json"), "w") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print("=" * 60)
print("CALÇA CARGO — CONCLUÍDO")
print(f"Saída: {OUT}")
print("=" * 60)
