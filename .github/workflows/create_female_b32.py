import bpy, json, os, sys, traceback, zipfile, math
from pathlib import Path

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-b32")).resolve()
out.mkdir(parents=True, exist_ok=True)
report = {"blender": bpy.app.version_string, "status": "started"}
try:
    source = Path(os.environ["MPFB_ZIP"])
    with zipfile.ZipFile(source) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix = manifest[:-len("blender_manifest.toml")]
        repacked = out / "mpfb-extension.zip"
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))
    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == "user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default", custom_directory=str(out / "extensions"))
        repo.enabled = True
    bpy.ops.extensions.package_install_files(filepath=str(repacked), repo=repo.module, enable_on_install=True)
    import addon_utils
    module = f"bl_ext.{repo.module}.mpfb"
    if module not in bpy.context.preferences.addons:
        addon_utils.enable(module, default_set=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    result = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result:
        raise RuntimeError(f"Create human failed: {result}")
    human = bpy.data.objects.get("Human")
    if human is None or human.type != "MESH":
        raise RuntimeError("Human mesh not found")
    keys = human.data.shape_keys.key_blocks
    before = {k.name: float(k.value) for k in keys}
    def evaluated_coords():
        bpy.context.view_layer.update()
        ev = human.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = ev.to_mesh()
        try:
            return [tuple(v.co) for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()
    base_coords = evaluated_coords()
    # Experimental feminine macro blend. Do not assert that this is an MPFB official preset.
    # Keep the 3 ethnicity weights balanced; preserve unrelated breast controls.
    for key in keys:
        name = key.name
        if name.startswith("$md-$") and "-$yn" in name and ("-$fe-" in name or "-$ma-" in name):
            if name.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                key.value = 1.0 / 3.0 if "-$fe-" in name else 0.0
            elif name.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in name else 0.0
    bpy.context.view_layer.update()
    female_coords = evaluated_coords()
    if len(base_coords) != len(female_coords):
        raise RuntimeError("Vertex count changed during morph")
    max_delta = max(max(abs(a[i]-b[i]) for i in range(3)) for a,b in zip(base_coords,female_coords))
    changed_vertices = sum(any(abs(a[i]-b[i]) > 1e-6 for i in range(3)) for a,b in zip(base_coords,female_coords))
    report["geometry_comparison"] = {"changed_vertices": changed_vertices, "max_coordinate_delta_m": max_delta}
    if changed_vertices == 0:
        raise RuntimeError("Morph values changed but evaluated geometry did not")
    # Uniform scale preserves the shape proportions; it does not alter biological age.
    current_height = max(p[2] for p in female_coords) - min(p[2] for p in female_coords)
    if current_height <= 0:
        raise RuntimeError("Invalid mesh height")
    factor = 1.70 / current_height
    human.scale = tuple(float(s) * factor for s in human.scale)
    bpy.context.view_layer.update()
    report["height_adjustment"] = {"original_height_m": current_height, "target_height_m": 1.70, "uniform_scale_factor": factor}
    after = {k.name: float(k.value) for k in keys}
    changed = {n: {"before": before[n], "after": v} for n, v in after.items() if abs(v-before[n]) > 1e-5}
    if not changed:
        raise RuntimeError("No morph values changed")
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = human.evaluated_get(deps)
    eval_mesh = evaluated.to_mesh()
    try:
        coords = [tuple(v.co) for v in eval_mesh.vertices]
        report["evaluated_bounds_local"] = {axis: [min(v[i] for v in coords), max(v[i] for v in coords)] for i, axis in enumerate("xyz")}
    finally:
        evaluated.to_mesh_clear()
    report.update(vertices=len(human.data.vertices), morph_changes=changed, status="success_female_scaled_experimental")
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "mpfb-female-b32.blend"))
    bpy.ops.object.select_all(action="DESELECT")
    human.select_set(True)
    bpy.context.view_layer.objects.active = human
    bpy.ops.export_scene.gltf(filepath=str(out / "mpfb-female-b32.glb"), export_format="GLB", use_selection=True, export_cameras=False, export_lights=False)
    report["note"] = "Experimental female morph blend + uniform 1.70m scale. Adult age not verified; not an official MPFB female preset."
    report["glb_bytes"] = (out / "mpfb-female-b32.glb").stat().st_size
except Exception as exc:
    report.update(status="failed", error=str(exc), traceback=traceback.format_exc())
finally:
    (out / "mpfb-b32-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("MPFB_B32_REPORT:", json.dumps(report, ensure_ascii=False))
if report["status"] != "success_female_scaled_experimental":
    sys.exit(1)
