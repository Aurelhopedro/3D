import bpy, json, os, sys, traceback, zipfile
from pathlib import Path

out = Path(os.environ.get('MPFB_OUTPUT', 'output/mpfb-b2')).resolve()
out.mkdir(parents=True, exist_ok=True)
source_zip = Path(os.environ['MPFB_ZIP']).resolve()
report = {'blender_version': bpy.app.version_string, 'status': 'started'}
try:
    with zipfile.ZipFile(source_zip) as archive:
        names = archive.namelist()
        if any(n.startswith('/') or '..' in Path(n).parts for n in names):
            raise RuntimeError('Unsafe ZIP path')
        manifests = [n for n in names if n.endswith('blender_manifest.toml')]
        if not manifests:
            raise RuntimeError('Manifest not found')
        prefix = min(manifests, key=len).removesuffix('blender_manifest.toml')
        prepared = out / 'mpfb-extension.zip'
        with zipfile.ZipFile(prepared, 'w', compression=zipfile.ZIP_DEFLATED) as dest:
            for name in names:
                if name.startswith(prefix) and not name.endswith('/'):
                    dest.writestr(name[len(prefix):], archive.read(name))

    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == 'user_default' and r.enabled), None)
    if repo is None:
        repo = repos.new(name='User Default', module='user_default', custom_directory=str(out / 'extensions'))
        repo.enabled = True
    result = bpy.ops.extensions.package_install_files(filepath=str(prepared), repo=repo.module, enable_on_install=True)
    if 'FINISHED' not in result:
        raise RuntimeError('Extension install failed: ' + str(result))
    import addon_utils
    module_name = f'bl_ext.{repo.module}.mpfb'
    if module_name not in bpy.context.preferences.addons:
        addon_utils.enable(module_name, default_set=True)
    report['extension_enabled'] = module_name in bpy.context.preferences.addons
    if not report['extension_enabled']:
        raise RuntimeError('MPFB not enabled')

    # Remove the startup cube (and other default scene objects) BEFORE creating a human.
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    report['scene_objects_after_cleanup'] = [o.name for o in bpy.data.objects]

    op = bpy.ops.mpfb.create_human
    report['operator_exists'] = hasattr(bpy.ops.mpfb, 'create_human')
    if not report['operator_exists']:
        raise RuntimeError('MPFB create_human operator missing')
    report['operator_result'] = sorted(op())
    if 'FINISHED' not in report['operator_result']:
        raise RuntimeError('Create human did not finish')

    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    report['meshes'] = [{'name': o.name, 'vertices': len(o.data.vertices)} for o in meshes]
    humans = [o for o in meshes if o.name == 'Human' or o.name.startswith('Human.')]
    if len(humans) != 1:
        raise RuntimeError('Expected exactly one Human mesh; found ' + str([o.name for o in meshes]))
    human = humans[0]
    report['human_vertices'] = len(human.data.vertices)
    report['other_meshes'] = [o.name for o in meshes if o != human]

    # Save the scene and export ONLY the Human mesh.
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'mpfb-human.blend'))
    bpy.ops.object.select_all(action='DESELECT')
    human.hide_set(False)
    human.hide_viewport = False
    human.select_set(True)
    bpy.context.view_layer.objects.active = human
    bpy.ops.export_scene.gltf(filepath=str(out / 'mpfb-human.glb'), export_format='GLB', use_selection=True, export_cameras=False, export_lights=False)
    report['glb_bytes'] = (out / 'mpfb-human.glb').stat().st_size
    report['status'] = 'success_clean_human'
    report['note'] = 'Exports only Human. Anatomy/legs must still be checked visually; adult/female parameters not applied.'
except Exception as exc:
    report['status'] = 'failed'
    report['error'] = str(exc)
    report['traceback'] = traceback.format_exc()
finally:
    (out / 'mpfb-b2-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('MPFB_B2_REPORT:', json.dumps(report, ensure_ascii=False))
if report['status'] != 'success_clean_human':
    sys.exit(1)
