"""Intermediate MakeHuman geometry comparison for Blender 5.x."""
import bpy, json, math, os, sys
from mathutils import Vector

TARGETS = [
    ("assets/characters/human_base/targets/macrodetails/caucasian-female-old.target", 0.65),
    ("assets/characters/human_base/targets/head/head-age-decr.target", 0.35),
]

def read_target(path, count):
    result = {}
    with open(path, encoding="utf-8-sig") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.partition("#")[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 4:
                raise ValueError(f"{path}:{line_number}: expected index dx dy dz")
            index = int(fields[0])
            if index < 0 or index >= count or index in result:
                raise ValueError(f"{path}:{line_number}: invalid or repeated vertex index {index}")
            delta = Vector(tuple(map(float, fields[1:])))
            if not all(math.isfinite(value) for value in delta):
                raise ValueError(f"{path}:{line_number}: invalid displacement")
            result[index] = delta
    if not result:
        raise ValueError(f"Empty target: {path}")
    return result

def make_material():
    mat = bpy.data.materials.new("Neutral_comparison_material")
    mat.diffuse_color = (0.64, 0.62, 0.58, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = mat.diffuse_color
    shader.inputs["Roughness"].default_value = 0.8
    return mat

def export_one(obj, path):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB",
                              use_selection=True, export_cameras=False,
                              export_lights=False)

def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 2:
        raise ValueError("Expected BASE_OBJ OUTPUT_DIR")
    source, output = map(os.path.abspath, args)
    os.makedirs(output, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=source)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise ValueError(f"Expected one MakeHuman mesh, got {len(meshes)}")
    original = meshes[0]
    original.name = "ORIGINAL"
    modified = original.copy()
    modified.data = original.data.copy()
    bpy.context.collection.objects.link(modified)
    modified.name = "MODIFIED"
    mat = make_material()
    for obj in (original, modified):
        obj.data.materials.clear()
        obj.data.materials.append(mat)

    before = [vertex.co.copy() for vertex in original.data.vertices]
    records = []
    for relative, strength in TARGETS:
        if not os.path.isfile(relative):
            raise FileNotFoundError(relative)
        changes = read_target(relative, len(before))
        for index, delta in changes.items():
            modified.data.vertices[index].co += delta * strength
        records.append({"file": relative, "strength": strength,
                        "target_entries": len(changes)})
    modified.data.update()

    distances = [(modified.data.vertices[i].co - before[i]).length
                 for i in range(len(before))]
    moved = sum(distance > 1e-7 for distance in distances)
    if not moved:
        raise RuntimeError("Targets did not move any vertices")
    max_distance = max(distances)
    rms = math.sqrt(sum(d*d for d in distances)/len(distances))
    print(f"GEOMETRY_CHECK moved={moved}/{len(before)} max={max_distance:.6f} rms={rms:.6f}")

    points = [original.matrix_world @ vertex.co for vertex in original.data.vertices]
    points += [modified.matrix_world @ vertex.co for vertex in modified.data.vertices]
    minimum = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    maximum = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    center = (minimum + maximum)/2
    span = max(maximum[i]-minimum[i] for i in range(3))
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 12
    scene.render.resolution_x = 540
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world = bpy.data.worlds.new("ComparisonWorld")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.75
    bpy.ops.object.camera_add(location=(center.x, center.y-span*2.6, center.z+span*0.1))
    camera = bpy.context.object
    camera.rotation_euler = (center-camera.location).to_track_quat("-Z","Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span*1.45
    scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(center.x+span, center.y-span, center.z+span))
    light = bpy.context.object
    light.data.energy = 900
    light.data.shape = "DISK"
    light.data.size = 4
    light.rotation_euler = (center-light.location).to_track_quat("-Z","Y").to_euler()

    for obj, name in ((original, "original"), (modified, "modified")):
        other = modified if obj == original else original
        other.hide_render = True
        obj.hide_render = False
        scene.render.filepath = os.path.join(output, f"{name}.png")
        bpy.ops.render.render(write_still=True)
        export_one(obj, os.path.join(output, f"{name}.glb"))
    original.hide_render = False
    modified.hide_render = False
    # Position the meshes apart only in the saved Blender scene for easy inspection.
    original.location.x -= span*0.7
    modified.location.x += span*0.7
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "comparison.blend"))
    report = {
        "status": "geometry_modified",
        "vertices_total": len(before),
        "vertices_moved": moved,
        "maximum_displacement_model_units": round(max_distance, 7),
        "rms_displacement_model_units": round(rms, 7),
        "targets": records,
        "caution": "The old-age macrodetail is a diagnostic, not a final adult-female preset. Vertex-index validity does not prove vertex-order compatibility."
    }
    with open(os.path.join(output, "comparison.json"), "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)
    print("COMPARISON_REPORT " + json.dumps(report))

if __name__ == "__main__":
    main()
