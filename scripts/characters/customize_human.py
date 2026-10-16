"""Experimental MakeHuman target application in Blender 5.x.
Usage: blender --background --python-exit-code 1 --python customize_human.py -- BASE_OBJ OUTPUT_DIR
Uses original OBJ vertex indices; do not edit/reorder the source mesh.
"""
import bpy
import json
import math
import os
import sys
from mathutils import Vector

TARGETS = [
    ('assets/characters/human_base/targets/macrodetails/caucasian-female-old.target', 0.20),
    ('assets/characters/human_base/targets/head/head-age-decr.target', 0.10),
]

def load_target(path, vertex_count):
    deltas = {}
    with open(path, encoding='utf-8-sig') as stream:
        for line_no, raw in enumerate(stream, 1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 4:
                raise ValueError(f'{path}:{line_no}: expected index dx dy dz')
            index = int(parts[0])
            if not 0 <= index < vertex_count:
                raise ValueError(f'{path}:{line_no}: vertex {index} outside 0..{vertex_count - 1}; source mesh/target mismatch')
            if index in deltas:
                raise ValueError(f'{path}:{line_no}: duplicate vertex index {index}')
            delta = Vector(tuple(float(v) for v in parts[1:]))
            if not all(math.isfinite(v) for v in delta):
                raise ValueError(f'{path}:{line_no}: non-finite displacement')
            deltas[index] = delta
    if not deltas:
        raise ValueError(f'Target is empty: {path}')
    return deltas

def main():
    if '--' not in sys.argv:
        raise ValueError('Expected -- BASE_OBJ OUTPUT_DIR')
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 2:
        raise ValueError('Expected exactly BASE_OBJ and OUTPUT_DIR')
    source, output = map(os.path.abspath, args)
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if os.path.splitext(source)[1].lower() != '.obj':
        raise ValueError('This workflow expects the original MakeHuman base.obj')
    os.makedirs(output, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=source)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    if len(meshes) != 1:
        raise ValueError(f'Expected one mesh from base.obj, found {len(meshes)}. Cannot safely map target indices.')
    obj = meshes[0]
    obj.name = 'MakeHuman_Customized_Experimental'
    vertices = obj.data.vertices
    original = [v.co.copy() for v in vertices]
    details = []
    for relative, strength in TARGETS:
        path = os.path.abspath(relative)
        if not os.path.isfile(path):
            raise FileNotFoundError(f'Missing modifier: {relative}')
        deltas = load_target(path, len(vertices))
        for index, delta in deltas.items():
            vertices[index].co += strength * delta
        details.append({'file': relative, 'strength': strength, 'affected_vertices': len(deltas)})
    obj.data.update()
    moved = sum((v.co - original[i]).length > 1e-8 for i, v in enumerate(vertices))
    if moved == 0:
        raise RuntimeError('No vertices moved')
    # Neutral diagnostic material, not realistic skin.
    mat = bpy.data.materials.new('Diagnostic_skin_tone')
    mat.diffuse_color = (0.58, 0.36, 0.28, 1.0)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = mat.diffuse_color
        bsdf.inputs['Roughness'].default_value = 0.7
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    coords = [obj.matrix_world @ v.co for v in vertices]
    lower = Vector(tuple(min(p[i] for p in coords) for i in range(3)))
    upper = Vector(tuple(max(p[i] for p in coords) for i in range(3)))
    center = (lower + upper) / 2
    span = max(upper[i] - lower[i] for i in range(3))
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 12
    scene.render.resolution_x = 512
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.world = bpy.data.worlds.new('DiagnosticWorld')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.35, 0.35, 0.35, 1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.7
    bpy.ops.object.camera_add(location=(center.x, center.y - span * 2.4, center.z + span * 0.12))
    camera = bpy.context.object
    camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = span * 1.4
    scene.camera = camera
    bpy.ops.object.light_add(type='AREA', location=(center.x + span, center.y - span, center.z + span))
    light = bpy.context.object
    light.data.energy = 900
    light.data.shape = 'DISK'
    light.data.size = 4
    light.rotation_euler = (center - light.location).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = os.path.join(output, 'preview.png')
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, 'customized-human.blend'))
    bpy.ops.export_scene.gltf(filepath=os.path.join(output, 'customized-human.glb'), export_format='GLB', export_cameras=False, export_lights=False)
    metadata = {
        'status': 'experimental', 'source': os.path.relpath(source),
        'vertex_count': len(vertices), 'moved_vertices': moved,
        'targets': details,
        'warning': 'A matching vertex count and valid indices do NOT prove identical vertex ordering. Inspect the preview for deformations. This is not a final realistic female character.',
    }
    with open(os.path.join(output, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(json.dumps(metadata))

if __name__ == '__main__':
    main()
