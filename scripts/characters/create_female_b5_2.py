import bpy
import bmesh
import math
import json
import os
import sys

# ── Configuração ─────────────────────────────────────────────────────────────
OUTPUT_DIR = os.environ.get("MPFB_OUTPUT", "/tmp/mpfb_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

report = {
    "version": "b5.2",
    "status": "in_progress",
    "mpfb_investigation": {},
    "anatomy": {},
    "components": {},
    "validations": {},
    "errors": [],
    "warnings": []
}

def log(msg):
    print(f"[B5.2] {msg}", flush=True)

def warn(msg):
    log(f"WARN: {msg}")
    report["warnings"].append(msg)

def record_error(msg):
    log(f"ERROR: {msg}")
    report["errors"].append(msg)

def save_report():
    p = os.path.join(OUTPUT_DIR, "mpfb-b5-2-report.json")
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log(f"Relatorio guardado: {p}")

# ── 1. Limpar cena ────────────────────────────────────────────────────────────
log("=== FASE 1: Limpeza da cena ===")
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=True)
for block in list(bpy.data.meshes):    bpy.data.meshes.remove(block)
for block in list(bpy.data.curves):   bpy.data.curves.remove(block)
for block in list(bpy.data.materials):bpy.data.materials.remove(block)
for block in list(bpy.data.objects):  bpy.data.objects.remove(block)
log("Cena limpa.")

# ── 2. Investigar MPFB antes de criar o humano ───────────────────────────────
log("=== FASE 2: Investigacao dos controlos MPFB ===")

mpfb_ops = []
for op_mod_name in dir(bpy.ops):
    if 'mpfb' in op_mod_name.lower():
        op_mod = getattr(bpy.ops, op_mod_name)
        for op_name in dir(op_mod):
            mpfb_ops.append(f"bpy.ops.{op_mod_name}.{op_name}")

log(f"Operadores MPFB encontrados: {len(mpfb_ops)}")
report["mpfb_investigation"]["operators"] = mpfb_ops

mpfb_props = [p for p in dir(bpy.context.scene)
              if 'mpfb' in p.lower() or 'makehuman' in p.lower()]
log(f"Propriedades MPFB na cena: {len(mpfb_props)}")
report["mpfb_investigation"]["scene_properties"] = mpfb_props

# ── 3. Criar humano via MPFB ──────────────────────────────────────────────────
log("=== FASE 3: Criacao do humano ===")
try:
    bpy.ops.mpfb.create_human()
    log("create_human() OK")
    report["components"]["mpfb_human"] = "created"
except Exception as e:
    record_error(f"create_human falhou: {e}")
    report["status"] = "failed"
    save_report()
    sys.exit(1)

# ── 4. Inventariar o que o MPFB criou ────────────────────────────────────────
log("=== FASE 4: Inventario pos-criacao ===")
created_objects = {}
for obj in bpy.data.objects:
    created_objects[obj.name] = {
        "type": obj.type,
        "vertices": len(obj.data.vertices) if obj.type == 'MESH' else None,
        "location": list(obj.location)
    }
log(f"Objectos criados: {list(created_objects.keys())}")
report["mpfb_investigation"]["created_objects"] = created_objects

eye_keywords = ['eye', 'cornea', 'eyeball', 'iris', 'pupil', 'sclera', 'teeth', 'tongue']
body_obj = None
eye_objects = []

for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    name_lower = obj.name.lower()
    if any(k in name_lower for k in eye_keywords):
        eye_objects.append(obj.name)
    elif body_obj is None or len(obj.data.vertices) > len(body_obj.data.vertices):
        body_obj = obj

if not body_obj:
    record_error("Nenhum mesh de corpo encontrado")
    report["status"] = "failed"
    save_report()
    sys.exit(1)

log(f"Corpo identificado: {body_obj.name} ({len(body_obj.data.vertices)} vertices)")
log(f"Olhos nativos detectados: {eye_objects}")
report["components"]["body_mesh"] = body_obj.name
report["components"]["native_eyes"] = eye_objects

# ── 5. Investigar shape keys disponiveis ─────────────────────────────────────
log("=== FASE 5: Investigacao de shape keys anatomicas ===")
bpy.context.view_layer.objects.active = body_obj
body_obj.select_set(True)

shape_key_inventory = {
    "female": [], "male": [], "age": [],
    "proportions": [], "all": []
}

if body_obj.data.shape_keys:
    for key in body_obj.data.shape_keys.key_blocks:
        n = key.name
        shape_key_inventory["all"].append(n)
        if any(x in n for x in ['-$fe-', '-$fe', 'female', 'woman']):
            shape_key_inventory["female"].append(n)
        elif any(x in n for x in ['-$ma-', '-$ma', 'male', 'man']):
            shape_key_inventory["male"].append(n)
        elif any(x in n for x in ['age', 'young', 'old', 'adult']):
            shape_key_inventory["age"].append(n)
        elif any(x in n for x in ['breast', 'hip', 'waist', 'shoulder', 'pelvis']):
            shape_key_inventory["proportions"].append(n)

    log(f"Shape keys femininas: {len(shape_key_inventory['female'])}")
    log(f"Shape keys masculinas: {len(shape_key_inventory['male'])}")
    log(f"Total shape keys: {len(shape_key_inventory['all'])}")
else:
    log("Nenhuma shape key encontrada no mesh")

report["mpfb_investigation"]["shape_keys"] = shape_key_inventory

# ── 6. Tentar controlos oficiais MPFB para sexo/idade ────────────────────────
log("=== FASE 6: Controlos oficiais MPFB para sexo/idade ===")
gender_ops_tried = []
for op_name in ['set_gender', 'set_sex', 'apply_gender', 'set_value']:
    if hasattr(bpy.ops.mpfb, op_name):
        try:
            op = getattr(bpy.ops.mpfb, op_name)
            result = op(target="gender", value=1.0) if op_name == 'set_value' else op(gender='female')
            log(f"mpfb.{op_name}() OK — {result}")
            gender_ops_tried.append(f"{op_name}: success")
        except Exception as e:
            log(f"mpfb.{op_name}() falhou: {e}")
            gender_ops_tried.append(f"{op_name}: {str(e)[:80]}")
    else:
        gender_ops_tried.append(f"{op_name}: not_found")

report["anatomy"]["gender_operators_tried"] = gender_ops_tried

age_ops_tried = []
for op_name in ['set_age', 'apply_age']:
    if hasattr(bpy.ops.mpfb, op_name):
        try:
            result = getattr(bpy.ops.mpfb, op_name)(age=30)
            log(f"mpfb.{op_name}() OK")
            age_ops_tried.append(f"{op_name}: success")
        except Exception as e:
            age_ops_tried.append(f"{op_name}: {str(e)[:80]}")
    else:
        age_ops_tried.append(f"{op_name}: not_found")

report["anatomy"]["age_operators_tried"] = age_ops_tried

# ── 7. Aplicar shape keys femininas (metodo B5 confirmado) ───────────────────
log("=== FASE 7: Aplicar morphs femininos via shape keys ===")
female_keys_applied = []
male_keys_zeroed = []

if body_obj.data.shape_keys:
    for key in body_obj.data.shape_keys.key_blocks:
        n = key.name
        if '-$fe-' in n or n.endswith('-$fe'):
            key.value = 1.0
            female_keys_applied.append(n)
        elif '-$ma-' in n or n.endswith('-$ma'):
            key.value = 0.0
            male_keys_zeroed.append(n)

    log(f"Shape keys femininas activadas: {len(female_keys_applied)}")
    if not female_keys_applied:
        warn("Nenhuma shape key feminina (-$fe-) encontrada — anatomia pode ser neutra")

report["anatomy"]["female_shape_keys_applied"] = female_keys_applied
report["anatomy"]["male_shape_keys_zeroed"] = male_keys_zeroed
report["anatomy"]["method"] = "shape_keys_plus_mpfb_operators"

# ── 8. Escala para 1.70 m via bmesh ──────────────────────────────────────────
log("=== FASE 8: Escala para 1.70 m ===")
bpy.context.view_layer.objects.active = body_obj
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
        log(f"Escala: {scale_factor:.4f}x ({current_height:.3f} m -> 1.70 m)")
        report["components"]["scale"] = {
            "method": "bmesh_vertex_scale",
            "original_height_m": round(current_height, 4),
            "target_height_m": 1.70,
            "factor": round(scale_factor, 4)
        }
    else:
        record_error("Altura invalida para calcular escala")

bpy.ops.object.mode_set(mode='OBJECT')

# ── 9. Pele ───────────────────────────────────────────────────────────────────
log("=== FASE 9: Material de pele ===")
mat = bpy.data.materials.new(name="Skin_B5_2")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()

bsdf = nodes.new('ShaderNodeBsdfPrincipled')
bsdf.location = (0, 0)
bsdf.inputs['Base Color'].default_value = (0.647, 0.416, 0.282, 1.0)
bsdf.inputs['Roughness'].default_value = 0.52
for ss_name in ['Subsurface Weight', 'Subsurface']:
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

log("Pele: #A66A48, roughness=0.52, subsurface=0.18")
report["components"]["skin"] = {
    "hex": "#A66A48", "linear_rgb": [0.647, 0.416, 0.282],
    "roughness": 0.52, "subsurface": 0.18
}

# ── 10. Olhos ─────────────────────────────────────────────────────────────────
log("=== FASE 10: Olhos ===")
if eye_objects:
    log(f"Olhos nativos MPFB: {eye_objects} — nada a adicionar")
    report["components"]["eyes"] = {"source": "mpfb_native", "objects": eye_objects}
else:
    log("Sem olhos nativos — omitindo (orientacao frontal nao confirmada)")
    report["components"]["eyes"] = {
        "source": "none",
        "reason": "sem olhos nativos; olhos artificiais omitidos ate confirmacao de orientacao frontal"
    }

# ── 11. Cabelo — calote + 24 mechas NURBS ────────────────────────────────────
log("=== FASE 11: Cabelo (B5 + correccao frontal) ===")

bm2 = bmesh.new()
bm2.from_mesh(body_obj.data)
bm2.verts.ensure_lookup_table()
top_z = max(v.co.z for v in bm2.verts)
bm2.free()

skull_r = 1.70 * 0.095
skull_cz = top_z - skull_r * 0.4

bpy.ops.mesh.primitive_uv_sphere_add(
    segments=24, ring_count=10, radius=skull_r,
    location=(0, 0, skull_cz)
)
skull = bpy.context.active_object
skull.name = "Hair_Skull"

bpy.ops.object.mode_set(mode='EDIT')
bm3 = bmesh.from_edit_mesh(skull.data)
bm3.verts.ensure_lookup_table()
to_del = [v for v in bm3.verts if (v.co.z + skull_cz) < skull_cz - 0.003]
bmesh.ops.delete(bm3, geom=to_del, context='VERTS')
bmesh.update_edit_mesh(skull.data)
bpy.ops.object.mode_set(mode='OBJECT')

hm = bpy.data.materials.new("Hair_Dark_B52")
hm.use_nodes = True
hn = hm.node_tree.nodes; hl = hm.node_tree.links; hn.clear()
hb = hn.new('ShaderNodeBsdfPrincipled')
hb.inputs['Base Color'].default_value = (0.03, 0.015, 0.008, 1.0)
hb.inputs['Roughness'].default_value = 0.65
ho = hn.new('ShaderNodeOutputMaterial'); ho.location = (300, 0)
hl.new(hb.outputs['BSDF'], ho.inputs['Surface'])
skull.data.materials.append(hm)

FRONTAL_RANGE = 60
strands_created = []

for i in range(24):
    phi = (i / 24) * 2 * math.pi
    front_phi = 3 * math.pi / 2
    diff = abs(phi - front_phi)
    if diff > math.pi:
        diff = 2 * math.pi - diff
    is_frontal = math.degrees(diff) < FRONTAL_RANGE

    if is_frontal:
        phi_birth = 0.60
        swing_back = 0.55
        strand_len = 0.20
    else:
        phi_birth = 0.32
        swing_back = 0.0
        strand_len = 0.30

    bx = skull_r * math.sin(phi_birth) * math.cos(phi)
    by = skull_r * math.sin(phi_birth) * math.sin(phi)
    bz = skull_cz + skull_r * math.cos(phi_birth)

    cd = bpy.data.curves.new(f"Strand_{i:02d}", type='CURVE')
    cd.dimensions = '3D'
    cd.bevel_depth = 0.0025
    cd.bevel_resolution = 2
    sp = cd.splines.new('NURBS')
    sp.points.add(3)

    pts = [
        (bx,        by,                        bz,                    1),
        (bx * 1.04, by + swing_back * 0.03,    bz - strand_len * 0.28, 1),
        (bx * 1.07, by + swing_back * 0.07,    bz - strand_len * 0.62, 1),
        (bx * 1.09, by + swing_back * 0.13,    bz - strand_len,        1),
    ]
    for j, (px, py, pz, pw) in enumerate(pts):
        sp.points[j].co = (px, py, pz, pw)
    sp.use_endpoint_u = True

    so = bpy.data.objects.new(f"Strand_{i:02d}", cd)
    bpy.context.collection.objects.link(so)
    so.data.materials.append(hm)
    strands_created.append(so.name)

log(f"Cabelo: 1 calote + {len(strands_created)} mechas")
report["components"]["hair"] = {
    "skull_cap": skull.name,
    "strands": len(strands_created),
    "frontal_correction_degrees": FRONTAL_RANGE,
    "frontal_phi_birth": 0.60,
    "frontal_swing_back": 0.55,
    "lateral_phi_birth": 0.32
}

# ── 12. Iluminacao ────────────────────────────────────────────────────────────
log("=== FASE 12: Iluminacao ===")
bpy.ops.object.light_add(type='SUN', location=(3, -3, 6))
sun = bpy.context.active_object
sun.name = "Sun_Key"; sun.data.energy = 3.5
bpy.ops.object.light_add(type='AREA', location=(-2, 1, 4))
fill = bpy.context.active_object
fill.name = "Fill_Light"; fill.data.energy = 1.2

# ── 13. Guardar .blend ────────────────────────────────────────────────────────
log("=== FASE 13: Exportacao ===")
blend_path = os.path.join(OUTPUT_DIR, "mpfb-female-b5-2.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
log(f".blend: {blend_path}")
report["components"]["blend_file"] = blend_path

# ── 14. Exportar .glb ─────────────────────────────────────────────────────────
glb_path = os.path.join(OUTPUT_DIR, "mpfb-female-b5-2.glb")
try:
    bpy.ops.export_scene.gltf(
        filepath=glb_path, export_format='GLB',
        export_apply=True, export_materials='EXPORT',
        export_cameras=False, export_lights=False
    )
    log(f"GLB: {glb_path}")
    report["components"]["glb_file"] = glb_path
except Exception as e:
    record_error(f"Exportacao GLB falhou: {e}")

# ── 15. Validacoes ────────────────────────────────────────────────────────────
report["validations"] = {
    "body_mesh_found": body_obj is not None,
    "female_morphs_applied": len(female_keys_applied) > 0,
    "scale_applied": "scale" in report["components"],
    "skin_applied": True,
    "hair_created": len(strands_created) == 24,
    "no_clothing": True,
    "no_shoes": True,
    "blend_exported": os.path.exists(blend_path),
    "glb_exported": os.path.exists(glb_path),
    "native_eyes_detected": len(eye_objects) > 0
}

# ── 16. Render separado (nao-fatal) ──────────────────────────────────────────
log("=== FASE 16: Previews (nao-fatal) ===")

def try_render(suffix, cam_loc, cam_rot_deg):
    try:
        scene = bpy.context.scene
        for eng in ['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES']:
            try:
                scene.render.engine = eng
                log(f"Motor: {eng}")
                break
            except:
                continue
        scene.render.resolution_x = 800
        scene.render.resolution_y = 1200
        bpy.ops.object.camera_add(
            location=cam_loc,
            rotation=[math.radians(d) for d in cam_rot_deg]
        )
        cam = bpy.context.active_object
        cam.name = f"Cam_{suffix}"
        scene.camera = cam
        render_path = os.path.join(OUTPUT_DIR, f"preview_{suffix}.png")
        scene.render.filepath = render_path
        bpy.ops.render.render(write_still=True)
        log(f"Preview {suffix}: OK")
        report["components"][f"preview_{suffix}"] = render_path
    except Exception as e:
        warn(f"Preview {suffix} falhou (nao-fatal): {str(e)[:120]}")

try_render("front", (0, -3.2, 0.85), (90, 0, 0))
try_render("back",  (0,  3.2, 0.85), (90, 0, 180))
try_render("side",  (3.2, 0,  0.85), (90, 0, 90))

# ── 17. Relatorio final ───────────────────────────────────────────────────────
report["status"] = "success" if not report["errors"] else "partial"
save_report()
log(f"=== B5.2 CONCLUIDO — status: {report['status']} ===")
log(f"Shape keys femininas aplicadas: {len(female_keys_applied)}")
log(f"Olhos nativos: {eye_objects if eye_objects else 'nenhum'}")
log(f"Mechas de cabelo: {len(strands_created)}")
