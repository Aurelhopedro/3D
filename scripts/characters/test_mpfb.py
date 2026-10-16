import bpy, json, os, sys, zipfile, traceback, importlib
from pathlib import Path

zip_path = Path(os.environ['MPFB_ZIP']).resolve()
out = Path(os.environ.get('MPFB_OUTPUT','output/mpfb-b1')).resolve()
out.mkdir(parents=True, exist_ok=True)
report = {'blender_version': bpy.app.version_string, 'zip_bytes': zip_path.stat().st_size, 'status': 'started'}
try:
    install = out / 'extension'
    install.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        report['zip_files'] = len(names)
        if any(n.startswith('/') or '..' in Path(n).parts for n in names):
            raise RuntimeError('Unsafe ZIP paths')
        z.extractall(install)
    candidates = [p for p in install.rglob('blender_manifest.toml') if (p.parent/'__init__.py').exists()]
    report['manifest_locations'] = [str(p.relative_to(install)) for p in candidates]
    if not candidates:
        raise RuntimeError('MPFB extension package not found')
    root = min((p.parent for p in candidates), key=lambda p: len(p.parts))
    sys.path.insert(0, str(root.parent))
    module = importlib.import_module(root.name)
    report['imported_module'] = module.__name__
    module.register()
    report['registered'] = True
    report['create_human_operator_exists'] = hasattr(bpy.ops.mpfb, 'create_human')
    report['status'] = 'success' if report['create_human_operator_exists'] else 'operator_missing'
except Exception as exc:
    report['status'] = 'failed'
    report['error'] = str(exc)
    report['traceback'] = traceback.format_exc()
finally:
    (out/'mpfb-b1-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('MPFB_B1_REPORT:',json.dumps(report,ensure_ascii=False))
if report['status'] != 'success':
    sys.exit(1)
