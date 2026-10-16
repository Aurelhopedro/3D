import bpy, json, os, traceback
from pathlib import Path
out=Path('output/mpfb-b3');out.mkdir(parents=True,exist_ok=True)
r={'status':'started','blender':bpy.app.version_string}
try:
 bpy.ops.wm.open_mainfile(filepath=os.environ['MPFB_B2_BLEND'])
 h=bpy.data.objects['Human']
 r['properties']={k:str(h[k]) for k in h.keys() if k.startswith('MPFB_HUM_')}
 r['shape_keys']=[{'name':k.name,'value':k.value} for k in (h.data.shape_keys.key_blocks if h.data.shape_keys else [])]
 r['vertices']=len(h.data.vertices)
 r['status']='success'
except Exception as e:
 r.update(status='failed',error=str(e),traceback=traceback.format_exc())
finally:
 (out/'inspection.json').write_text(json.dumps(r,indent=2,ensure_ascii=False));print('B3_REPORT',json.dumps(r,ensure_ascii=False))
if r['status']!='success':raise SystemExit(1)
