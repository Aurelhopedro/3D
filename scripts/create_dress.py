"""Vestido Cobre & Ônix: geometria procedural e renderização headless.
Blender 3.6+; nenhuma dependência Python externa. Modelo de demonstração,
não réplica validada por fotogrametria. Execute com blender -b -t 2 --python ...
"""
import bpy, math, os, json, random, sys, traceback
from mathutils import Vector
from math import sin, cos, pi

ROOT=os.environ.get('DRESS_OUTPUT', os.path.abspath('outputs'))
os.makedirs(ROOT,exist_ok=True)
random.seed(72)

# Configuração
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
scene.render.engine='CYCLES'; scene.cycles.samples=int(os.environ.get('DRESS_SAMPLES','24'))
scene.render.resolution_x=int(os.environ.get('DRESS_RES','720'))
scene.render.resolution_y=int(os.environ.get('DRESS_RES','720'))
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.world.color=(.12,.10,.10)
scene.view_settings.view_transform='Standard'

# Modelo manequim estilizado, 1.70m. Y negativo é frente.
# Corpo sob a roupa: volumes estilizados, sem anatomia rigorosa.
def mat(name, base, metallic=0, rough=.5, alpha=1):
 m=bpy.data.materials.new(name);m.diffuse_color=(*base,alpha);m.use_nodes=True
 p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*base,1)
 p.inputs['Metallic'].default_value=metallic;p.inputs['Roughness'].default_value=rough
 if alpha<1:
  p.inputs['Alpha'].default_value=alpha
  if hasattr(m,'surface_render_method'): m.surface_render_method='DITHERED'
  elif hasattr(m,'blend_method'): m.blend_method='BLEND'
 return m
skin=mat('Manequim | bronze neutro',(.36,.18,.12),rough=.58)
copper=mat('Cobre | bordado orgânico',(.40,.105,.052),metallic=.22,rough=.38)
black=mat('Onix | tecido preto brilhante',(.014,.010,.014),metallic=.34,rough=.28)
edge=mat('Acabamentos cobre',(.54,.22,.075),metallic=.65,rough=.26)
# Shader ornamental: Voronoi distance-to-edge (renda irregular) + ruído de microbrilhos.
def ornament(m, scale, tint, strength):
 nt=m.node_tree;n=nt.nodes;l=nt.links;p=n.get('Principled BSDF')
 tex=n.new('ShaderNodeTexCoord');tex.location=(-950,0)
 mapping=n.new('ShaderNodeVectorMath');mapping.operation='SCALE';mapping.inputs[3].default_value=scale
 l.new(tex.outputs['Generated'],mapping.inputs[0])
 v=n.new('ShaderNodeTexVoronoi');v.feature='DISTANCE_TO_EDGE';v.location=(-650,150);l.new(mapping.outputs['Vector'],v.inputs['Vector'])
 ramp=n.new('ShaderNodeValToRGB');ramp.location=(-400,150)
 ramp.color_ramp.elements[0].position=.015;ramp.color_ramp.elements[0].color=(*tint,1)
 ramp.color_ramp.elements[1].position=.075;ramp.color_ramp.elements[1].color=(*m.diffuse_color[:3],1)
 l.new(v.outputs['Distance'],ramp.inputs[0]);l.new(ramp.outputs['Color'],p.inputs['Base Color'])
 bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=strength;bump.inputs['Distance'].default_value=.006
 l.new(v.outputs['Distance'],bump.inputs['Height']);l.new(bump.outputs['Normal'],p.inputs['Normal'])
 # Variação de rugosidade de microdetalhe
 noise=n.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=150
 l.new(tex.outputs['Generated'],noise.inputs['Vector'])
 rough=n.new('ShaderNodeMapRange');rough.inputs['From Min'].default_value=.25;rough.inputs['From Max'].default_value=.8
 rough.inputs['To Min'].default_value=.18;rough.inputs['To Max'].default_value=.48
 l.new(noise.outputs['Fac'],rough.inputs['Value']);l.new(rough.outputs['Result'],p.inputs['Roughness'])
ornament(copper,17,(.8,.35,.12),.20)
ornament(black,24,(.32,.21,.14),.14)

objects=[]
def mesh(name,verts,faces,material,subsurf=0):
 me=bpy.data.meshes.new(name);me.from_pydata(verts,[],faces);me.update()
 ob=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(ob);ob.data.materials.append(material)
 for f in me.polygons:f.use_smooth=True
 if subsurf:
  mod=ob.modifiers.new('Suavização','SUBSURF');mod.levels=subsurf;mod.render_levels=subsurf
 objects.append(ob);return ob

def rings(name,rows,cols,coord,material,closed=True):
 vs=[];fs=[]
 for i in range(rows+1):
  for j in range(cols+(0 if closed else 1)):
   t=j/cols;vs.append(coord(i/rows,t))
 stride=cols if closed else cols+1
 for i in range(rows):
  for j in range(cols):
   jn=(j+1)%cols if closed else j+1
   a=i*stride+j;b=i*stride+jn;c=(i+1)*stride+jn;d=(i+1)*stride+j
   fs.append((a,b,c,d))
 return mesh(name,vs,fs,material)

def profile(z):
 # elipses do tronco para busto, cintura e anca
 levels=[(.80,.245,.155),(.92,.23,.145),(1.03,.166,.118),(1.14,.15,.105),(1.29,.195,.135),(1.37,.205,.146),(1.46,.18,.12)]
 for (z0,x0,y0),(z1,x1,y1) in zip(levels,levels[1:]):
  if z<=z1:
   a=max(0,min(1,(z-z0)/(z1-z0)));a=a*a*(3-2*a)
   return x0+(x1-x0)*a,y0+(y1-y0)*a
 return levels[-1][1:]
# Cabeça e braços são manequim de referência, não personagem final.
def uvball(name,loc,scale,material):
 bpy.ops.mesh.primitive_uv_sphere_add(segments=24,ring_count=12,location=loc)
 ob=bpy.context.object;ob.name=name;ob.scale=scale;ob.data.materials.append(material)
 for f in ob.data.polygons:f.use_smooth=True
 return ob
uvball('Manequim | cabeça',(0,0,1.59),(.105,.088,.14),skin)
uvball('Manequim | pescoço',(0,0,1.455),(.055,.054,.095),skin)
# Tronco pele por baixo do vestido
rings('Manequim | tronco',24,64,lambda u,t:(profile(.81+u*.62)[0]*cos(2*pi*t)*.98,profile(.81+u*.62)[1]*sin(2*pi*t)*.98,.81+u*.62),skin)
for side in (-1,1):
 # braços discretos em pose A
 def arm(u,t,side=side):
  z=1.385-u*.62;x=side*(.205+u*.15);r=.052-u*.023
  return (x+r*cos(2*pi*t),-.006+r*sin(2*pi*t),z)
 rings('Manequim | braço '+str(side),20,24,arm,skin)
 for j in range(2):
  uvball('Manequim | perna '+str(side)+' '+str(j),(side*.115,0,.43),(.09,.1,.43),skin)
# Corpete: parte superior com borda variável para criar recortes dos ombros.
def bodice(u,t):
 a=2*pi*t;front=max(0,-sin(a))
 # gola/peito: costas e laterais descem, frente central chega ao pescoço
 ztop=1.405 + .055*(front**8) - .09*(abs(cos(a))**8)
 # V inferior profundo no centro da frente
 zbottom=.995-.075*(front**12)
 z=zbottom+(ztop-zbottom)*u
 rx,ry=profile(z);return ((rx+.004)*cos(a),(ry+.004)*sin(a),z)
rings('VESTIDO | corpete cobre',36,128,bodice,copper)
# Gola curta independente
rings('VESTIDO | gola',5,48,lambda u,t:(.061*cos(2*pi*t),.058*sin(2*pi*t),1.45+.038*u),copper)
# Manga com abertura longitudinal real. Malha paramétrica aberta: fenda orientada para fora/frente.
for side in (-1,1):
 def sleeve(u,t,side=side):
  z=1.385-.60*u;x=side*(.211+.155*u)
  r=.058-.025*u
  # abertura de formato folha: apenas zona intermédia, deslocada para frente
  center= -.65 if side==1 else pi+.65
  width=.001 + .58*(sin(pi*max(0,min(1,(u-.18)/.67)))**1.5 if .18<u<.85 else 0)
  # percorre círculo sem o setor da abertura, bordas abertas
  a=center+width/2+(2*pi-width)*t
  return (x+r*cos(a),r*sin(a),z)
 rings('VESTIDO | manga recortada '+str(side),48,60,sleeve,copper,closed=False)
# Saia: setor aberto (fenda real) que se alarga progressivamente da coxa até bainha.
# Y negativo = frente. ângulo frontal ~3pi/2.
def skirt(u,t):
 ztop=.998;z=.045+(.998-.045)*(1-u)
 a0=1.5*pi
 # abertura quase fechada na anca e maior em direção à bainha
 width=.02 + .44*(u**2.0)
 a=a0+width/2+(2*pi-width)*t
 rx,ry=profile(max(.80,z))
 # coxa justa, depois evasê
 flare=max(0,(u-.43)/.57)**1.7
 rx=rx*(1-.06*u)+.24*flare;ry=ry*(1-.06*u)+.29*flare
 # pregas suaves, progressivamente mais marcadas no fim
 fold=(.003+.025*flare)*sin(12*a+1.7*u)*sin(pi*u/2)
 rx+=fold;ry+=fold
 # cauda posterior: prolongar tecido para trás em z baixo (Y positivo)
 back=max(0,sin(a))**4
 ytail=.55*back*(u**7)
 return (rx*cos(a),ry*sin(a)+ytail,z-.028*back*(u**5))
rings('VESTIDO | saia preta com fenda e cauda',80,160,skirt,black,closed=False)
# Microcristais instanciados como uma única malha de tetraedros: só superfície preta.
verts=[];faces=[]
for k in range(1800):
 u=random.uniform(.03,.97);t=random.uniform(.015,.985)
 p=Vector(skirt(u,t));size=random.uniform(.0009,.0026)
 # tetraedros: reflexos pontuais discretos
 b=len(verts);verts.extend([(p.x-size,p.y,p.z),(p.x+size,p.y,p.z),(p.x,p.y-size,p.z),(p.x,p.y,p.z+size*2)])
 faces.extend([(b,b+1,b+3),(b+1,b+2,b+3),(b+2,b,b+3)])
crystals=mat('Cristais | cobre e prata',(.57,.44,.32),metallic=.85,rough=.13)
mesh('VESTIDO | microcristais da saia',verts,faces,crystals)
# Palco e iluminação
floor=mat('Palco | grafite',(.045,.038,.043),metallic=.18,rough=.32)
bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,0));bpy.context.object.name='Palco';bpy.context.object.data.materials.append(floor)
def area(name,loc,power,size):
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.name=name;o.data.energy=power;o.data.shape='DISK';o.data.size=size
 direction=Vector((0,0,.85))-o.location;o.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
area('Key softbox',(-2,-3,4),500,3.0);area('Fill',(2,-2,3),270,2.5);area('Rim',(0,2.5,3.0),650,2.0)
def render(name,loc):
 bpy.ops.object.camera_add(location=loc);cam=bpy.context.object;scene.camera=cam
 direction=Vector((0,0,.92))-cam.location;cam.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
 cam.data.type='ORTHO';cam.data.ortho_scale=2.28
 scene.render.filepath=os.path.join(ROOT,name+'.png');bpy.ops.render.render(write_still=True)
 bpy.data.objects.remove(cam,do_unlink=True)
render('front',(0,-4,1.3));render('side',(4,0,1.3));render('three_quarter',(3,-4,1.4));render('back',(0,4,1.3))
blend=os.path.join(ROOT,'cobre_onix_dress.blend');bpy.ops.wm.save_as_mainfile(filepath=blend)
# Exportação do vestido e manequim; excluir cenário e luzes.
for o in bpy.context.selected_objects:o.select_set(False)
for o in bpy.context.scene.objects:
 if o.type=='MESH' and o.name!='Palco':o.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.join(ROOT,'cobre_onix_dress.glb'),export_format='GLB',use_selection=True)
report={'status':'generated_not_visually_approved','blender':bpy.app.version_string,'geometry':[{'name':o.name,'vertices':len(o.data.vertices),'faces':len(o.data.polygons)} for o in objects],'renders':['front.png','side.png','three_quarter.png','back.png'],'notes':['Reconstituição estilizada de referência frontal; costas e estrutura interna hipotéticas.','Manequim provisório: substituir por personagem real para ajuste final.','Sem rigging nem simulação de tecido.','Validação visual humana obrigatória.']}
with open(os.path.join(ROOT,'report.json'),'w') as f:json.dump(report,f,indent=2,ensure_ascii=False)
print('DRESS_BUILD_SUCCESS',blend)
