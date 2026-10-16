import bpy, json, math, os, sys
from mathutils import Vector

BASE = 'assets/characters/human_base/'
TARGETS = [('targets/macrodetails/caucasian-female-old.target', .65), ('targets/head/head-age-decr.target', .35)]

def target(path, count):
    values = {}
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            s = line.split('#')[0].split()
            if not s: continue
            if len(s) != 4: raise ValueError('Malformed target entry in '+path)
            idx = int(s[0]); delta = Vector(tuple(map(float,s[1:])))
            if not 0 <= idx < count or idx in values: raise ValueError('Invalid/duplicate vertex index '+str(idx))
            values[idx] = delta
    if not values: raise ValueError('Empty target '+path)
    return values

def bounds(coords):
    lo = [min(v[i] for v in coords) for i in range(3)]
    hi = [max(v[i] for v in coords) for i in range(3)]
    return {'min':lo,'max':hi,'dimensions':[hi[i]-lo[i] for i in range(3)]}

def export(obj, filepath):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True); bpy.context.view_layer.objects.active=obj
    bpy.ops.export_scene.gltf(filepath=filepath, export_format='GLB', use_selection=True, export_cameras=False, export_lights=False)

def main():
    output=os.path.abspath(sys.argv[sys.argv.index('--')+1]); os.makedirs(output,exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=BASE+'base.obj')
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if len(meshes)!=1: raise RuntimeError('Expected exactly one mesh, got '+str(len(meshes)))
    orig=meshes[0]; orig.name='Original'
    mod=orig.copy(); mod.data=orig.data.copy(); bpy.context.collection.objects.link(mod); mod.name='Modificado'
    a=[v.co.copy() for v in orig.data.vertices]
    report_targets=[]
    for rel, strength in TARGETS:
        changes=target(BASE+rel,len(a))
        for i, delta in changes.items(): mod.data.vertices[i].co += strength*delta
        report_targets.append({'file':rel,'strength':strength,'entries':len(changes)})
    mod.data.update()
    b=[v.co.copy() for v in mod.data.vertices]
    d=[(y-x).length for x,y in zip(a,b)]
    maxd=max(d)
    if maxd<1e-7: raise RuntimeError('No geometric changes')
    bb_a=bounds(a); bb_b=bounds(b)
    height=bb_a['dimensions'][2]
    # Color heatmap: blue -> cyan -> yellow -> red, per-vertex, exported into GLB.
    heat=mod.data.color_attributes.new(name='Deslocamento',type='FLOAT_COLOR',domain='POINT')
    for i, distance in enumerate(d):
        t=min(distance/maxd,1)
        if t < .33: color=(0,3*t,1)
        elif t < .66: color=(3*(t-.33),1,1-3*(t-.33))
        else: color=(1,1-2.9*(t-.66),0)
        heat.data[i].color=(*[max(0,min(1,v)) for v in color],1)
    neutral=bpy.data.materials.new('Cinza neutro'); neutral.diffuse_color=(.65,.65,.65,1)
    neutral.use_nodes=True
    neutral.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=(.65,.65,.65,1)
    for obj in [orig,mod]: obj.data.materials.clear(); obj.data.materials.append(neutral)
    export(orig,os.path.join(output,'original.glb'))
    export(mod,os.path.join(output,'modificado.glb'))
    heatmat=bpy.data.materials.new('Mapa de deslocamento'); heatmat.use_nodes=True
    nodes=heatmat.node_tree.nodes; links=heatmat.node_tree.links
    attr=nodes.new('ShaderNodeVertexColor'); attr.layer_name='Deslocamento'
    links.new(attr.outputs['Color'],nodes['Principled BSDF'].inputs['Base Color'])
    mod.data.materials.clear(); mod.data.materials.append(heatmat)
    export(mod,os.path.join(output,'mapa_diferencas.glb'))
    report={'vertices_total':len(a),'vertices_moved':sum(x>1e-7 for x in d),
      'max_displacement':maxd,'mean_displacement':sum(d)/len(d),
      'rms_displacement':math.sqrt(sum(x*x for x in d)/len(d)),
      'relative_max_displacement_to_original_height':maxd/height if height else None,
      'original_bounds':bb_a,'modified_bounds':bb_b,
      'dimension_changes':[bb_b['dimensions'][i]-bb_a['dimensions'][i] for i in range(3)],
      'targets':report_targets,
      'note':'Color indicates geometric displacement, not anatomical correctness. GLB vertex color support depends on viewer.'}
    with open(os.path.join(output,'diagnostico.json'),'w') as f:json.dump(report,f,indent=2)
    print('DIAGNOSTICO '+json.dumps(report))
    # Saved scene displays original and heatmap side by side.
    separation=max(bb_a['dimensions'][0],bb_b['dimensions'][0])*1.5
    orig.location.x=-separation; mod.location.x=separation
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output,'comparacao.blend'))

if __name__=='__main__':main()
