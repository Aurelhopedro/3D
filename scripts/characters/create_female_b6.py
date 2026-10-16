"""B6: clothing derived from evaluated body surface; safe eye handling.
Blender 5.2.1 / MPFB. Not runtime-tested. Keeps B5 unchanged.
"""
import bpy, bmesh, json, math, os, sys, traceback, zipfile
from pathlib import Path
from mathutils import Vector

OUT=Path(os.environ.get('MPFB_OUTPUT','output/mpfb-b6')).resolve(); OUT.mkdir(parents=True,exist_ok=True)
R={'status':'started','blender':bpy.app.version_string,'components':{},'checks':{},'limitations':[]}

def linear(c): return c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4
def rgba(h):
    h=h.lstrip('#'); return tuple(linear(int(h[i:i+2],16)/255) for i in (0,2,4))+(1,)
def material(name,h,rough=.65):
    m=bpy.data.materials.new(name);m.use_nodes=True
    nd=m.node_tree.nodes;nd.clear();p=nd.new('ShaderNodeBsdfPrincipled');p.inputs['Base Color'].default_value=rgba(h);p.inputs['Roughness'].default_value=rough
    o=nd.new('ShaderNodeOutputMaterial');m.node_tree.links.new(p.outputs['BSDF'],o.inputs['Surface']);m.diffuse_color=rgba(h);return m
def mat_on(o,m): o.data.materials.clear();o.data.materials.append(m)
def mesh_object(name,verts,faces,mat):
    me=bpy.data.meshes.new(name+'_mesh');me.from_pydata(verts,[],faces);me.update();o=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(o);mat_on(o,mat)
    for p in me.polygons:p.use_smooth=True
    return o

def evaluated(h):
    bpy.context.view_layer.update();e=h.evaluated_get(bpy.context.evaluated_depsgraph_get());me=e.to_mesh()
    try:
        verts=[e.matrix_world@v.co for v in me.vertices]; faces=[tuple(p.vertices) for p in me.polygons]
        return verts,faces
    finally:e.to_mesh_clear()

def build_garment(h,skin,dress_mat):
    verts,faces=evaluated(h);lo=min(v.z for v in verts);hi=max(v.z for v in verts);height=hi-lo
    # Anatomical torso surface patch, using actual MPFB faces, not revolved circles.
    # Face must be entirely inside torso zone and central horizontal span; avoids arms.
    z0=lo+.505*height;z1=lo+.795*height
    chosen=[]
    for f in faces:
        vs=[verts[i] for i in f];z=sum(v.z for v in vs)/len(vs)
        if not(z0<=z<=z1):continue
        if max(abs(v.x) for v in vs)>.205:continue
        if max(v.z for v in vs)>z1+.018 or min(v.z for v in vs)<z0-.018:continue
        chosen.append(f)
    if len(chosen)<80:raise RuntimeError('Torso patch insufficient; inspect MPFB topology before proceeding')
    indices=sorted({i for f in chosen for i in f});imap={idx:k for k,idx in enumerate(indices)}
    # Push vertices outward relative to horizontal torso centerline (not normals which may face inward).
    newverts=[]
    for i in indices:
        v=verts[i]; radial=Vector((v.x,v.y,0))
        if radial.length<.015: radial=Vector((0,-1,0))
        radial.normalize();offset=.009
        newverts.append(tuple(v+radial*offset))
    newfaces=[tuple(imap[i] for i in f) for f in chosen]
    bodice=mesh_object('B6_Bodice_Surface',newverts,newfaces,dress_mat)
    # Separate flared skirt: cross-sections are elliptic and centered from actual pelvis points.
    # Top follows measured pelvis envelope, not a single constant radius.
    pelvis=[v for v in verts if z0-.03<=v.z<=z0+.025 and abs(v.x)<.35]
    if len(pelvis)<20:raise RuntimeError('Pelvis region not detected')
    rx=max(abs(v.x) for v in pelvis)+.016; ry=max(abs(v.y) for v in pelvis)+.016
    # Prevent excessive dimensions if hands intrude into pelvis band.
    rx=min(rx,.25);ry=min(ry,.23)
    hem=lo+.285*height; segments=48; rings=10; vs=[];fs=[]
    for j in range(rings):
        t=j/(rings-1);z=z0+(hem-z0)*t
        flare=1+.44*(t**1.6)
        for k in range(segments):
            a=2*math.pi*k/segments
            vs.append((rx*flare*math.cos(a),ry*flare*math.sin(a),z))
    for j in range(rings-1):
        for k in range(segments):
            a=j*segments+k;b=j*segments+(k+1)%segments;c=(j+1)*segments+(k+1)%segments;d=(j+1)*segments+k
            fs.append((a,b,c,d))
    skirt=mesh_object('B6_Skirt_Flared',vs,fs,dress_mat)
    R['components']['dress']={'method':'evaluated_MPF_Body_face_patch_plus_elliptic_skirt','torso_faces':len(chosen),'skirt_faces':len(fs),'parts':[bodice.name,skirt.name],'note':'No circular torso cylinder; actual torso surface patch. Skirt is independent mesh; fit is not guaranteed.'}
    R['checks']['torso_faces_minimum']=len(chosen)>=80
    return lo,hi,verts

def build_hair(bodyverts,lo,hi,hair_mat):
    height=hi-lo; head=[v for v in bodyverts if v.z>lo+.855*height and abs(v.x)<.17]
    if len(head)<40:raise RuntimeError('Head region insufficient')
    top=max(v.z for v in head); base=lo+.87*height
    # Conservative hair placement: back half, no strands across face.
    # Face direction not reliably inferred; do not place hair until direction is verified.
    # Create a small crown cap from upper head vertices as a surface patch.
    # Select faces in caller's evaluated mesh to maintain native head contour.
    R['components']['hair']={'status':'deferred_for_face_orientation_safety','head_top_z':top,'reason':'Avoid strands crossing eyes before front direction is verified'}

def camera(name,location,target):
    ca=bpy.data.cameras.new(name);ob=bpy.data.objects.new(name,ca);bpy.context.collection.objects.link(ob)
    ob.location=location;direction=Vector(target)-ob.location;ob.rotation_euler=direction.to_track_quat('-Z','Y').to_euler();ca.type='ORTHO';ca.ortho_scale=2.2;return ob

def render_views(lo,hi):
    scene=bpy.context.scene;scene.render.engine='BLENDER_EEVEE_NEXT';scene.render.resolution_x=640;scene.render.resolution_y=900;scene.render.resolution_percentage=100
    scene.world.color=(.35,.35,.35);center=(0,0,(lo+hi)/2)
    for label,loc in [('front',(0,-3.5,center[2])),('back',(0,3.5,center[2])),('side',(3.5,0,center[2]))]:
        cam=camera('B6_Camera_'+label,loc,center);scene.camera=cam
        scene.render.filepath=str(OUT/('b6-'+label+'.png'))
        try:bpy.ops.render.render(write_still=True)
        except Exception as e:R['limitations'].append('Preview '+label+' failed: '+str(e))
        bpy.data.objects.remove(cam,do_unlink=True)

try:
    src=Path(os.environ['MPFB_ZIP']);repacked=OUT/'mpfb-extension.zip'
    with zipfile.ZipFile(src) as z:
        manifest=next(n for n in z.namelist() if n.endswith('blender_manifest.toml'));prefix=manifest[:-len('blender_manifest.toml')]
        with zipfile.ZipFile(repacked,'w',zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith('/') and '..' not in Path(n).parts:dest.writestr(n[len(prefix):],z.read(n))
    repos=bpy.context.preferences.extensions.repos
    repo=next((r for r in repos if r.module=='user_default' and r.enabled),None)
    if repo is None:
        repo=repos.new(name='User Default',module='user_default',custom_directory=str(OUT/'extensions'));repo.enabled=True
    res=bpy.ops.extensions.package_install_files(filepath=str(repacked),repo=repo.module,enable_on_install=True)
    if 'FINISHED' not in res:raise RuntimeError('MPFB install failed '+str(res))
    import addon_utils
    mod=f'bl_ext.{repo.module}.mpfb'
    if mod not in bpy.context.preferences.addons:addon_utils.enable(mod,default_set=True)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    if 'FINISHED' not in bpy.ops.mpfb.create_human():raise RuntimeError('MPFB create_human failed')
    h=bpy.data.objects.get('Human');assert h and h.type=='MESH'
    # Preserve proven B5 morph settings; fix previously unreachable universal branch.
    keys=h.data.shape_keys.key_blocks
    for k in keys:
        n=k.name
        if n.startswith(('$md-$as-','$md-$ca-','$md-$af-')) and '-$yn' in n:
            if '-$fe-' in n:k.value=1/3
            elif '-$ma-' in n:k.value=0
    bpy.context.view_layer.update()
    # Do not bake shape keys or edit base vertices; use object scale for height.
    v,_=evaluated(h);initial=max(p.z for p in v)-min(p.z for p in v)
    factor=1.7/initial;h.scale=tuple(s*factor for s in h.scale);bpy.context.view_layer.update()
    v,_=evaluated(h);final=max(p.z for p in v)-min(p.z for p in v)
    if abs(final-1.7)>.01:raise RuntimeError('World height mismatch: '+str(final))
    R['components']['body']={'vertices':len(h.data.vertices),'initial_height_m':initial,'final_height_m':final,'morph':'B5 experimental six regional keys'}
    mat_on(h,material('B6_Skin','#A66A48',.55))
    dressmat=material('B6_RedFabric','#C62828',.76)
    lo,hi,bodyverts=build_garment(h,None,dressmat)
    # No fake eyes: preserve MPFB original eye geometry/material; no added eyes behind head.
    R['components']['eyes']={'method':'no_additional_eyes','reason':'B5 added eyes behind head; anatomical landmarks unverified'}
    build_hair(bodyverts,lo,hi,material('B6_Hair','#1A0D08'))
    R['limitations']+=['Hair not generated until face orientation is established; avoids eye intersection.','Dress torso patch may include holes or arm seams and skirt may intersect legs; inspect previews.','No official MPFB gender or age controls validated.','No rigging, garment physics or anatomical landmark detection.']
    # Use B5 footwear only if already available in scene; no unverified shoe geometry.
    R['checks']['body_height']=abs(final-1.7)<=.01
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'mpfb-female-b6.blend'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(filepath=str(OUT/'mpfb-female-b6.glb'),export_format='GLB',use_selection=True,export_cameras=False,export_lights=False)
    render_views(lo,hi)
    R['status']='success_b6_geometry_experimental'
    R['glb_bytes']=(OUT/'mpfb-female-b6.glb').stat().st_size
except Exception as e:
    R.update(status='failed',error=str(e),traceback=traceback.format_exc())
finally:
    (OUT/'mpfb-b6-report.json').write_text(json.dumps(R,ensure_ascii=False,indent=2))
    print('MPFB_B6_REPORT:',json.dumps(R,ensure_ascii=False))
if R['status']!='success_b6_geometry_experimental':sys.exit(1)
