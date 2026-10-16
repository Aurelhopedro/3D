"""Import a licensed character/base mesh, render previews and export Blender/GLB.
Usage: blender --background --python-exit-code 1 --python import_female.py -- INPUT OUTPUT_DIR
"""
import bpy
import json
import os
import sys
from mathutils import Vector


def render_view(camera, name, position, target, output_dir):
    camera.location = position
    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.render.filepath = os.path.join(output_dir, name + '.png')
    bpy.ops.render.render(write_still=True)


def main():
    if '--' not in sys.argv:
        raise ValueError('Expected -- INPUT_FILE OUTPUT_DIR')
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 2:
        raise ValueError('Expected INPUT_FILE and OUTPUT_DIR')
    source, output_dir = (os.path.abspath(a) for a in args)
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    os.makedirs(output_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(source)[1].lower()
    if ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=source)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=source)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=source)
    else:
        raise ValueError('Unsupported input extension: ' + ext)

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    if not meshes:
        raise RuntimeError('The imported file contains no mesh')
    corners = [obj.matrix_world @ Vector(c) for obj in meshes for c in obj.bound_box]
    minimum = Vector(tuple(min(p[i] for p in corners) for i in range(3)))
    maximum = Vector(tuple(max(p[i] for p in corners) for i in range(3)))
    center = (minimum + maximum) / 2
    dimensions = maximum - minimum
    span = max(dimensions.x, dimensions.y, dimensions.z, 0.1)

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.render.resolution_x = 512
    scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new('CharacterWorld')
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get('Background')
    if background:
        background.inputs['Color'].default_value = (0.15, 0.15, 0.15, 1)
        background.inputs['Strength'].default_value = 0.8

    bpy.ops.object.camera_add(location=(center.x, center.y - span * 2.5, center.z))
    camera = bpy.context.object
    camera.name = 'CharacterPreviewCamera'
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = span * 1.35
    scene.camera = camera

    for position, energy in [
        ((center.x + span, center.y - span, center.z + span), 1100),
        ((center.x - span, center.y + span, center.z + span), 800),
    ]:
        bpy.ops.object.light_add(type='AREA', location=position)
        light = bpy.context.object
        light.data.energy = energy
        light.data.shape = 'DISK'
        light.data.size = max(span, 1.0)
        light.rotation_euler = (center - light.location).to_track_quat('-Z', 'Y').to_euler()

    distance = span * 2.5
    views = {
        'front': (center.x, center.y - distance, center.z),
        'side': (center.x + distance, center.y, center.z),
        'back': (center.x, center.y + distance, center.z),
        'perspective': (center.x + distance * 0.75, center.y - distance * 0.9, center.z + span * 0.3),
    }
    for name, position in views.items():
        render_view(camera, name, position, center, output_dir)

    blend_path = os.path.join(output_dir, 'female-model.blend')
    glb_path = os.path.join(output_dir, 'female-model.glb')
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    # Export only imported meshes and armatures, not preview lights/camera.
    bpy.ops.object.select_all(action='DESELECT')
    for obj in scene.objects:
        if obj.type in {'MESH', 'ARMATURE'}:
            obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=glb_path, export_format='GLB', use_selection=True)

    metadata = {
        'success': True,
        'source_file': os.path.basename(source),
        'mesh_count': len(meshes),
        'armature_count': sum(o.type == 'ARMATURE' for o in scene.objects),
        'dimensions_in_blender_units': [round(v, 4) for v in dimensions],
        'note': 'Imported base geometry. No automatic character shaping or rigging performed.',
    }
    with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)
    expected = [blend_path, glb_path, os.path.join(output_dir, 'metadata.json')]
    expected += [os.path.join(output_dir, name + '.png') for name in views]
    missing = [p for p in expected if not os.path.isfile(p) or os.path.getsize(p) == 0]
    if missing:
        raise RuntimeError('Missing or empty outputs: ' + ', '.join(missing))
    print('CHARACTER_IMPORT_SUCCESS ' + json.dumps(metadata))


if __name__ == '__main__':
    main()
