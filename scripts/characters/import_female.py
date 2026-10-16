"""Import an existing licensed female character; render four previews and export.
Run with: blender --background --python import_female.py -- INPUT OUTPUT_DIR
"""
import bpy
import json
import math
import os
import sys
import traceback
from mathutils import Vector


def render_view(name, position, target, out_dir):
    camera = bpy.data.objects.get('CharacterPreviewCamera')
    camera.location = position
    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.render.filepath = os.path.join(out_dir, name + '.png')
    bpy.ops.render.render(write_still=True)


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 2:
        raise ValueError('Expected INPUT_FILE and OUTPUT_DIR after --')
    source, out_dir = map(os.path.abspath, args)
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isfile(source):
        raise FileNotFoundError('Base mesh missing: ' + source)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(source)[1].lower()
    if ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=source)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=source)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=source)
    else:
        raise ValueError('Supported formats: GLB, GLTF, FBX, OBJ')
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not meshes:
        raise ValueError('Input contained no mesh objects')
    # Preserve original scale, rigging, materials and mesh topology.
    bounds = [o.matrix_world @ Vector(corner) for o in meshes for corner in o.bound_box]
    min_xyz = Vector(tuple(min(v[i] for v in bounds) for i in range(3)))
    max_xyz = Vector(tuple(max(v[i] for v in bounds) for i in range(3)))
    center = (min_xyz + max_xyz) / 2
    height = max(max_xyz.z - min_xyz.z, 0.1)
    span = max(max_xyz.x - min_xyz.x, max_xyz.y - min_xyz.y, height)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.render.resolution_x = 768
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.film_transparent = False
    scene.world.color = (0.15, 0.15, 0.15)
    bpy.ops.object.camera_add(location=(center.x, center.y - span * 2.5, center.z))
    cam = bpy.context.object
    cam.name = 'CharacterPreviewCamera'
    cam.data.type = 'ORTHO'
    cam.data.ortho_scale = span * 1.35
    scene.camera = cam
    for pos, energy, size in [((center.x + span, center.y - span, center.z + span), 1100, 4), ((center.x - span, center.y + span, center.z + span), 800, 4)]:
        bpy.ops.object.light_add(type='AREA', location=pos)
        light = bpy.context.object
        light.data.energy = energy
        light.data.shape = 'DISK'
        light.data.size = size
        light.rotation_euler = (Vector(center) - light.location).to_track_quat('-Z', 'Y').to_euler()
    distance = span * 2.5
    views = {
        'front': (center.x, center.y - distance, center.z),
        'side': (center.x + distance, center.y, center.z),
        'back': (center.x, center.y + distance, center.z),
        'perspective': (center.x + distance * .75, center.y - distance * .9, center.z + span * .3),
    }
    for name, position in views.items():
        render_view(name, position, center, out_dir)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, 'female-model.blend'))
    bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, 'female-model.glb'), export_format='GLB', use_selection=False, export_cameras=False, export_lights=False)
    metadata = {
        'success': True, 'source_file': os.path.basename(source),
        'mesh_count': len(meshes), 'armature_count': len([o for o in scene.objects if o.type == 'ARMATURE']),
        'height_in_blender_units': round(height, 4),
        'dimensions_in_blender_units': [round(max_xyz[i]-min_xyz[i], 4) for i in range(3)],
        'note': 'Dimensions depend on imported asset units. Rigging and topology are inherited, not generated.',
    }
    with open(os.path.join(out_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(json.dumps(metadata))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
