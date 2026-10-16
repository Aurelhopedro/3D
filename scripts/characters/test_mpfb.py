import bpy
import json
import os
import sys
import traceback
import zipfile
from pathlib import Path

source_zip = Path(os.environ['MPFB_ZIP']).resolve()
out = Path(os.environ.get('MPFB_OUTPUT', 'output/mpfb-b1')).resolve()
out.mkdir(parents=True, exist_ok=True)
report = {'blender_version': bpy.app.version_string, 'status': 'started'}

try:
    report['zip_bytes'] = source_zip.stat().st_size
    with zipfile.ZipFile(source_zip) as archive:
        names = archive.namelist()
        report['zip_files'] = len(names)
        if any(name.startswith('/') or '..' in Path(name).parts for name in names):
            raise RuntimeError('Unsafe ZIP paths')
        manifests = [name for name in names if name.endswith('blender_manifest.toml')]
        report['manifest_locations'] = manifests
        candidates = [name for name in manifests if name == 'blender_manifest.toml' or name.endswith('/blender_manifest.toml')]
        if not candidates:
            raise RuntimeError('No Blender extension manifest found')
        manifest = min(candidates, key=len)
        prefix = manifest[:-len('blender_manifest.toml')]
        if prefix and prefix + '__init__.py' not in names:
            raise RuntimeError('Extension __init__.py not found')
        # Blender expects the manifest at the root of the installable ZIP.
        prepared_zip = out / 'mpfb-extension.zip'
        with zipfile.ZipFile(prepared_zip, 'w', compression=zipfile.ZIP_DEFLATED) as dest:
            count = 0
            for name in names:
                if not name.startswith(prefix) or name.endswith('/'):
                    continue
                relative = name[len(prefix):]
                if not relative:
                    continue
                dest.writestr(relative, archive.read(name))
                count += 1
        report['repacked_files'] = count

    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == 'user_default' and r.enabled), None)
    if repo is None:
        repo = repos.new(name='User Default', module='user_default', custom_directory=str(out / 'extensions'))
        repo.enabled = True
    report['repository'] = repo.module
    result = bpy.ops.extensions.package_install_files(
        filepath=str(prepared_zip), repo=repo.module, enable_on_install=True
    )
    report['install_operator_result'] = sorted(result)
    if 'FINISHED' not in result:
        raise RuntimeError('Blender extension installation did not finish')

    import addon_utils
    module_name = 'bl_ext.' + repo.module + '.mpfb'
    report['extension_module'] = module_name
    if module_name not in bpy.context.preferences.addons:
        addon_utils.enable(module_name, default_set=True)
    report['extension_enabled'] = module_name in bpy.context.preferences.addons
    report['registered'] = report['extension_enabled']
    report['create_human_operator_exists'] = hasattr(bpy.ops.mpfb, 'create_human')
    report['status'] = 'success' if report['extension_enabled'] else 'failed'
except Exception as exc:
    report['status'] = 'failed'
    report['error'] = str(exc)
    report['traceback'] = traceback.format_exc()
finally:
    (out / 'mpfb-b1-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('MPFB_B1_REPORT:', json.dumps(report, ensure_ascii=False))
if report['status'] != 'success':
    sys.exit(1)
