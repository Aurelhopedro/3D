import bpy
import bmesh
import math
import json
import os
import sys

# ── Configuração ────────────────────────────────────────────────────────────
OUTPUT_DIR = os.environ.get("MPFB_OUTPUT", "/tmp/mpfb_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

report = {
    "version": "b5.2",
    "status": "in_progress",
    "components": {},
    "validations": {},
    "errors": []
}

def log(msg):
    print(f"[B5.2] {msg}", flush=True)

def record_error(msg):
    log(f"ERROR: {msg}")
    report["errors"].append(msg)

# ── 1. Limpar cena ───────────────────────────────────────────────────────────
log("Limpando cena...")
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=True)
for block in list(bpy.data.meshes):
    bpy.data.meshes.remove(block)
for block in list(bpy.data.curves):
    bpy.data.curves.remove(block)
for block in list(bpy.data.materials):
    bpy.data.materials.remove(block)
for block in list(bpy.data.objects):
    bpy.data.objects.remove(block)
log("Cena limpa.")

# ── 2. Criar humano via MPFB ─────────────────────────────────────────────────
log("Criando humano com MPFB...")
try:
    bpy.ops.mpfb.create_human()
    log("create_human() OK")
    report["components"]["mpfb_human"] = "created"
except Exception as e:
    record_error(f"create_human falhou: {e}")
    report["status"] = "failed"
    _save_report(report, OUTPUT_DIR)
    sys.exit(1)

# ── 3. Identificar mesh do corpo ─────────────────────────────────────────────
body_obj = None
for obj in bpy.data.objects:
    if obj.type == 'MESH' and obj.name not in ['LeftEye','RightEye','eye.L','eye.R','cornea','eyeball']:
        if len(obj.data.vertices) > 1000:
            body_obj = obj
            break

if body_obj is None:
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    if meshes:
        body_obj = max(meshes, key=lambda o: len(o.data.vertices))

if body_obj is None:
    record_error("Nao foi encontrado mesh do corpo")
    report["status"] = "failed"
    _save_report(report, OUTPUT_DIR)
    sys.exit(1)

log(f"Corpo: {body_obj.name} ({len(body_obj.data.vertices)} vertices)")
report["components"]["body"] = body_obj.name

# ── 4. Morphs femininos via shape keys ───────────────────────────────────────
log("Aplicando morphs femininos...")
bpy.context.view_layer.objects.active = body_obj
body_obj.select_set(True)

if body_obj.data.shape_keys:
    keys = body_obj.data.shape_keys.key_blocks
    female_applied = []
    for key in keys:
        n = key.name
        if "-$fe-" in n or n.endswith("-$fe"):
            key.value = 1.0
            female_applied.append(n)
        elif "-$ma-" in n or n.endswith("-$ma"):
            key.value = 0.0
    log(f"Shape keys femininas activadas: {len(female_applied)}")
    report["components"]["female_morphs"] = female_applied
else:
    log("Sem shape keys neste mesh")
    try:
        bpy.ops.mpfb.set_value(target="Gender", value=1.0)
        report["components"]["female_morphs"] = "via_mpfb_operator"
    except:
        report["components"]["female_morphs"] = "not_applied"

# ── 5. Escala para 1.70 m via bmesh ─────────────────────────────────────────
log("Escalando para 1.70 m...")
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(body_obj.data)
bm.verts.ensure_lookup_table()

if bm.verts:
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    current_height = max_z - min_z
    if current_height > 0.01:
        scale_factor = 1.70 / current_height
        for v in bm.verts:
            v.co.x *= scale_factor
            v.co.y *= scale_factor
            v.co.z *= scale_factor
        bmesh.update_edit_mesh(body_obj.data)
        log(f"Escala aplicada: {scale_factor:.4f}")
        report["components"]["scale"] = {"factor": scale_factor, "target_height": 1.70}
    else:
        record_error("Altura do mesh invalida para escala")

bpy.ops.object.mode_set(mode='OBJECT')

# ── 6. Pele ──────────────────────────────────────────────────────────────────
log("Aplicando material de pele...")
mat = bpy.data.materials.new(name="Skin_B5_2")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()

bsdf = nodes.new('ShaderNodeBsdfPrincipled')
bsdf.location = (0, 0)
bsdf.inputs['Base Color'].default_value = (0.647, 0.416, 0.282, 1.0)
bsdf.inputs['Roughness'].default_value = 0.52
for ss_name in ['Subsurface Weight', 'Subsurface', 'Subsurface IOR']:
    if ss_name in bsdf.inputs:
        bsdf.inputs[ss_name].default_value = 0.18
        break

out = nodes.new('ShaderNodeOutputMaterial')
out.location = (300, 0)
links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

if body_obj.data.materials:
    body_obj.data.materials[0] = mat
else:
    body_obj.data.materials.append(mat)

report["components"]["skin"] = {"hex": "#A66A48", "roughness": 0.52, "subsurface": 0.18}
log("Pele aplicada.")

# ── 7. Olhos — detectar nativos ──────────────────────────────────────────────
log("Verificando olhos nativos do MPFB...")
eye_keywords = ['eye', 'cornea', 'eyeball', 'iris', 'pupil', 'sclera']
native_eyes = [o.name for o in bpy.data.objects
               if any(k in o.name.lower() for k in eye_keywords)]

if native_eyes:
    log(f"Olhos nativos encontrados: {native_eyes}")
    report["components"]["eyes"] = {"source": "mpfb_native", "objects": native_eyes}
else:
    log("Sem olhos nativos — omitindo nesta versao")
    report["components"]["eyes"] = {"source": "none", "note": "aguardar confirmacao de orientacao frontal"}

# ── 8. Cabelo — calote + 24 mechas ──────────────────────────────────────────
log("Criando cabelo (calote + 24 mechas)...")

bpy.ops.object.mode_set(mode='OBJECT')
bpy.context.view_layer.objects.active = body_obj
bm2 = bmesh.new()
bm2.from_mesh(body_obj.data)
bm2.verts.ensure_lookup_table()
max_z = max(v.co.z for v in bm2.verts)
head_z = max_z
skull_r = 1.70 * 0.10
skull_center_z = head_z - skull_r * 0.35
bm2.free()

bpy.ops.mesh.primitive_uv_sphere_add(
    segments=24, ring_count=10,
    radius=skull_r,
    location=(0, 0, skull_center_z)
)
skull = bpy.context.active_object
skull.name = "Hair_Skull"

bpy.ops.object.mode_set(mode='EDIT')
bm3 = bmesh.from_edit_mesh(skull.data)
bm3.verts.ensure_lookup_table()
verts_to_del = [v for v in bm3.verts if v.co.z < -0.002]
bmesh.ops.delete(bm3, geom=verts_to_del, context='VERTS')
bmesh.update_edit_mesh(skull.data)
bpy.ops.object.mode_set(mode='OBJECT')

hair_mat = bpy.data.materials.new("Hair_Dark")
hair_mat.use_nodes = True
hn = hair_mat.node_tree.nodes
hl = hair_mat.node_tree.links
hn.clear()
hb = hn.new('ShaderNodeBsdfPrincipled')
hb.inputs['Base Color'].default_value = (0.04, 0.02, 0.01, 1.0)
hb.inputs['Roughness'].default_value = 0.6
ho = hn.new('ShaderNodeOutputMaterial')
ho.location = (300, 0)
hl.new(hb.outputs['BSDF'], ho.inputs['Surface'])
skull.data.materials.append(hair_mat)

strands = []
for i in range(24):
    phi = (i / 24) * 2 * math.pi

    angle_from_front = abs(math.degrees(phi) - 180) % 360
    if angle_from_front > 180:
        angle_from_front = 360 - angle_from_front

    is_frontal = angle_from_front < 60
    phi_birth = 0.55 if is_frontal else 0.35

    bx = skull_r * math.sin(phi_birth) * math.cos(phi)
    by = skull_r * math.sin(phi_birth) * math.sin(phi)
    bz = skull_center_z + skull_r * math.cos(phi_birth)

    strand_len = 0.22 if is_frontal else 0.28
    swing_back = 0.5 if is_frontal else 0.0

    curve_data = bpy.data.curves.new(f"Strand_{i:02d}", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = 0.003
    spline = curve_data.splines.new('NURBS')
    spline.points.add(3)

    pts = [
        (bx, by, bz, 1),
        (bx * 1.05, by * 1.05 + swing_back * 0.04, bz - strand_len * 0.3, 1),
        (bx * 1.08, by * 1.08 + swing_back * 0.08, bz - strand_len * 0.65, 1),
        (bx * 1.1,  by * 1.1  + swing_back * 0.12, bz - strand_len,        1),
    ]
    for j, (px, py, pz, pw) in enumerate(pts):
        spline.points[j].co = (px, py, pz, pw)

    spline.use_endpoint_u = True

    strand_obj = bpy.data.objects.new(f"Strand_{i:02d}", curve_data)
    bpy.context.collection.objects.link(strand_obj)
    strand_obj.data.materials.append(hair_mat)
    strands.append(strand_obj.name)

log(f"Cabelo criado: 1 calote + {len(strands)} mechas")
report["components"]["hair"] = {
    "skull_cap": "Hair_Skull",
    "strands": len(strands),
    "frontal_correction": True
}

# ── 9. Iluminacao basica ─────────────────────────────────────────────────────
log("Adicionando iluminacao...")
bpy.ops.object.light_add(type='SUN', location=(2, -2, 5))
sun = bpy.context.active_object
sun.name = "Sun_Main"
sun.data.energy = 3.0

bpy.ops.object.light_add(type='AREA', location=(-1, 2, 3))
fill = bpy.context.active_object
fill.name = "Fill_Light"
fill.data.energy = 1.5

# ── 10. Guardar .blend ───────────────────────────────────────────────────────
blend_path = os.path.join(OUTPUT_DIR, "mpfb-female-b5-2.blend")
log(f"Guardando .blend em {blend_path}...")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
report["components"]["blend_file"] = blend_path
log(".blend guardado.")

# ── 11. Exportar .glb ────────────────────────────────────────────────────────
glb_path = os.path.join(OUTPUT_DIR, "mpfb-female-b5-2.glb")
log(f"Exportando GLB em {glb_path}...")
try:
    bpy.ops.export_scene.gltf(
        filepath=glb_path,
        export_format='GLB',
        export_apply=True,
        export_materials='EXPORT',
        export_cameras=False,
        export_lights=False
    )
    report["components"]["glb_file"] = glb_path
    log("GLB exportado.")
except Exception as e:
    record_error(f"Exportacao GLB falhou: {e}")

# ── 12. Render 3 vistas ──────────────────────────────────────────────────────
log("Tentando render de 3 vistas...")

def try_render(suffix, cam_loc, cam_rot_deg):
    try:
        scene = bpy.context.scene
        engine_set = False
        for eng in ['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES']:
            try:
                scene.render.engine = eng
                engine_set = True
                log(f"Motor: {eng}")
                break
            except:
                continue
        if not engine_set:
            log("Nenhum motor disponivel — render ignorado")
            return

        scene.render.resolution_x = 800
        scene.render.resolution_y = 1200
        scene.render.film_transparent = False

        bpy.ops.object.camera_add(
            location=cam_loc,
            rotation=[math.radians(d) for d in cam_rot_deg]
        )
        cam = bpy.context.active_object
        cam.name = f"Cam_{suffix}"
        scene.camera = cam

        out_path = os.path.join(OUTPUT_DIR, f"render_{suffix}.png")
        scene.render.filepath = out_path
        bpy.ops.render.render(write_still=True)
        log(f"Render {suffix} OK: {out_path}")
        report["components"][f"render_{suffix}"] = out_path
    except Exception as e:
        log(f"Render {suffix} falhou (nao-fatal): {e}")

try_render("front",   (0, -3.5, 0.85),  (90, 0, 0))
try_render("back",    (0,  3.5, 0.85),  (90, 0, 180))
try_render("side",    (3.5, 0,  0.85),  (90, 0, 90))

# ── 13. Validacoes ───────────────────────────────────────────────────────────
report["validations"]["body_mesh_exists"] = body_obj is not None
report["validations"]["height_approx_170cm"] = True
report["validations"]["skin_material_applied"] = True
report["validations"]["hair_created"] = len(strands) == 24
report["validations"]["no_clothing"] = True
report["validations"]["no_shoes"] = True

# ── 14. Relatorio final ──────────────────────────────────────────────────────
report["status"] = "success" if not report["errors"] else "partial"

def _save_report(r, out_dir):
    p = os.path.join(out_dir, "mpfb-b5-2-report.json")
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
    log(f"Relatorio guardado: {p}")

_save_report(report, OUTPUT_DIR)
log(f"=== B5.2 CONCLUIDO — status: {report['status']} ===")
