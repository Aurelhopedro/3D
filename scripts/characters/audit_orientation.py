"""Audit MakeHuman OBJ -> Blender -> GLB orientation without changing base geometry."""
import bpy
import json
import os
import sys
from mathutils import Vector


def bounds(points):
    if not points:
        raise ValueError('No vertices found')
    minimum = [min(p[i] for p in points) for i in range(3)]
    maximum = [max(p[i] for p in points) for i in range(3)]
    dimensions = [maximum[i] - minimum[i] for i in range(3)]
    return {'min': [round(v, 6) for v in minimum],
            'max': [round(v, 6) for v in maximum],
            'dimensions': [round(v, 6) for v in dimensions],
            'longest_axis': 'XYZ'[dimensions.index(max(dimensions))]}


def mesh_stats(objects):
    meshes = [o for o in objects if o.type == 'MESH']
    if not meshes:
        raise ValueError('No mesh objects')
    local = [tuple(v.co) for o in meshes for v in o.data.vertices]
    world = [tuple(o.matrix_world @ v.co) for o in meshes for v in o.data.vertices]
    return {'mesh_count': len(meshes), 'vertex_count': len(local),
            'local_bounds': bounds(local), 'world_bounds': bounds(world),
            'object_transforms': [{'name': o.name,
              'rotation_euler': [round(float(x), 6) for x in o.rotation_euler],
              'scale': [round(float(x), 6) for x in o.scale],
              'matrix_world': [[round(float(x), 6) for x in row] for row in o.matrix_world]}
              for o in meshes]}


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    if len(args) != 2:
        raise ValueError('Usage: -- BASE_OBJ OUTPUT_DIRECTORY')
    source, output = map(os.path.abspath, args)
    os.makedirs(output, exist_ok=True)
    raw = []
    with open(source, 'r', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if line.startswith('v '):
                parts = line.split()
                raw.append(tuple(float(x) for x in parts[1:4]))
    raw_bounds = bounds(raw)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=source)
    imported = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    blender_stats = mesh_stats(imported)
    for o in bpy.context.scene.objects:
        o.select_set(False)
    for o in imported:
        o.select_set(True)
    bpy.context.view_layer.objects.active = imported[0]
    glb = os.path.join(output, 'orientation-check.glb')
    bpy.ops.export_scene.gltf(filepath=glb, export_format='GLB', use_selection=True,
                              export_cameras=False, export_lights=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=glb)
    roundtrip_stats = mesh_stats(list(bpy.context.scene.objects))
    report = {'source': os.path.basename(source), 'raw_obj': {'vertex_count': len(raw), 'bounds': raw_bounds},
              'blender_after_obj_import': blender_stats,
              'blender_after_glb_roundtrip': roundtrip_stats,
              'interpretation': 'Compare WORLD bounds and longest_axis, not only local mesh bounds. GLB round-trip should preserve world-space orientation. No source vertices were modified.'}
    with open(os.path.join(output, 'orientation-report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('ORIENTATION_REPORT ' + json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
