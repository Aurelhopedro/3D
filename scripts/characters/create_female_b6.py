"""B6: clothing derived from evaluated body surface; safe eye handling.
Blender 5.2.1 / MPFB. Fixes vs B5: no --factory-startup, scene cleared
via Python, MPFB installed via filesystem extraction (addon_utils).
"""
import bpy, bmesh, json, math, os, sys, traceback, zipfile
from pathlib import Path
from mathutils import Vector

OUT = Path(os.environ.get('MPFB_OUTPUT', 'output/mpfb-b6')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
R = {'status': 'started', 'blender': bpy.app.version_string,
     'components': {}, 'checks': {}, 'limitations': []}


def linear(c): return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4


def rgba(h):
    h = h.lstrip('#')
    return tuple(linear(int(h[i:i+2], 16) / 255) for i in (0, 2, 4)) + (1,)


def material(name, h, rough=.65):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nd = m.node_tree.nodes
    nd.clear()
    p = nd.new('ShaderNodeBsdfPrincipled')
    p.inputs['Base Color'].default_value = rgba(h)
    p.inputs['Roughness'].default_value = rough
    o = nd.new('ShaderNodeOutputMaterial')
    m.node_tree.links.new(p.outputs['BSDF'], o.inputs['Surface'])
    m.diffuse_color = rgba(h)
    return m


def mat_on(o, m):
    o.data.materials.clear()
    o.data.materials.append(m)


def mesh_object(name, verts, faces, mat):
    me = bpy.data.meshes.new(name + '_mesh')
    me.from_pydata(verts, [], faces)
    me.update()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    mat_on(o, mat)
    for p in me.polygons:
        p.use_smooth = True
    return o


def evaluated(h):
    bpy.context.view_layer.update()
    e = h.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = e.to_mesh()
    try:
        verts = [e.matrix_world @ v.co for v in me.vertices]
        faces = [tuple(p.vertices) for p in me.polygons]
        return verts, faces
    finally:
        e.to_mesh_clear()


def build_garment(h, skin, dress_mat):
    verts, faces = evaluated(h)
    lo = min(v.z for v in verts)
    hi = max(v.z for v in verts)
    height = hi - lo
    z0 = lo + .505 * height
    z1 = lo + .795 * height
    chosen = []
    for f in faces:
        vs = [verts[i] for i in f]
        z = sum(v.z for v in vs) / len(vs)
        if not (z0 <= z <= z1):
            continue
        if max(abs(v.x) for v in vs) > .205:
            continue
        if max(v.z for v in vs) > z1 + .018 or min(v.z for v in vs) < z0 - .018:
            continue
        chosen.append(f)
    if len(chosen) < 80:
        raise RuntimeError('Torso patch insufficient; inspect MPFB topology before proceeding')
    indices = sorted({i for f in chosen for i in f})
    imap = {idx: k for k, idx in enumerate(indices)}
    newverts = []
    for i in indices:
        v = verts[i]
        radial = Vector((v.x, v.y, 0))
        if radial.length < .015:
            radial = Vector((0, -1, 0))
        radial.normalize()
        newverts.append(tuple(v + radial * .009))
    newfaces = [tuple(imap[i] for i in f) for f in chosen]
    bodice = mesh_object('B6_Bodice_Surface', newverts, newfaces, dress_mat)
    pelvis = [v for v in verts if z0 - .03 <= v.z <= z0 + .025 and abs(v.x) < .35]
    if len(pelvis) < 20:
        raise RuntimeError('Pelvis region not detected')
    rx = min(max(abs(v.x) for v in pelvis) + .016, .25)
    ry = min(max(abs(v.y) for v in pelvis) + .016, .23)
    hem = lo + .285 * height
    segments = 48
    rings = 10
    vs = []
    fs = []
    for j in range(rings):
        t = j / (rings - 1)
        z = z0 + (hem - z0) * t
        flare = 1 + .44 * (t ** 1.6)
        for k in range(segments):
            a = 2 * math.pi * k / segments
            vs.append((rx * flare * math.cos(a), ry * flare * math.sin(a), z))
    for j in range(rings - 1):
        for k in range(segments):
            a = j * segments + k
            b = j * segments + (k + 1) % segments
            c = (j + 1) * segments + (k + 1) % segments
            d = (j + 1) * segments + k
            fs.append((a, b, c, d))
    skirt = mesh_object('B6_Skirt_Flared', vs, fs, dress_mat)
    R['components']['dress'] = {
        'method': 'evaluated_MPFB_face_patch_plus_elliptic_skirt',
        'torso_faces': len(chosen), 'skirt_faces': len(fs),
        'parts': [bodice.name, skirt.name]
    }
    R['checks']['torso_faces_minimum'] = len(chosen) >= 80
    return lo, hi, verts


def build_hair(bodyverts, lo, hi, hair_mat):
    height = hi - lo
    head = [v for v in bodyverts if v.z > lo + .855 * height and abs(v.x) < .17]
    if len(head) < 40:
        raise RuntimeError('Head region insufficient')
    R['components']['hair'] = {
        'status': 'deferred_for_face_orientation_safety',
        'head_top_z': max(v.z for v in head),
        'reason': 'Avoid strands crossing eyes before front direction is verified'
    }


def camera(name, location, target):
    ca = bpy.data.cameras.new(name)
    ob = bpy.data.objects.new(name, ca)
    bpy.context.collection.objects.link(ob)
    ob.location = location
    direction = Vector(target) - ob.location
    ob.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    ca.type = 'ORTHO'
    ca.ortho_scale = 2.2
    return ob


def render_views(lo, hi):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.render.resolution_x = 640
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.world.color = (.35, .35, .35)
    center = (0, 0, (lo + hi) / 2)
    for label, loc in [('front', (0, -3.5, center[2])),
                       ('back', (0, 3.5, center[2])),
                       ('side', (3.5, 0, center[2]))]:
        cam = camera('B6_Camera_' + label, loc, center)
        scene.camera = cam
        scene.render.filepath = str(OUT / ('b6-' + label + '.png'))
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            R['limitations'].append('Preview ' + label + ' failed: ' + str(e))
        bpy.data.objects.remove(cam, do_unlink=True)


def install_mpfb_headless(zip_path: Path) -> str:
    """
    Instala o MPFB sem depender de bpy.ops.extensions (que falha headless).
    Extrai os ficheiros do addon directamente para a pasta de scripts do
    utilizador do Blender e carrega com addon_utils.enable().
    Devolve o module_id activo.
    """
    import addon_utils

    # Pasta de addons do utilizador (Blender 4.x / 5.x)
    user_addon_dir = Path(bpy.utils.user_resource('SCRIPTS')) / 'addons'
    user_addon_dir.mkdir(parents=True, exist_ok=True)

    mpfb_dir = user_addon_dir / 'mpfb'
    if mpfb_dir.exists():
        import shutil
        shutil.rmtree(mpfb_dir)

    with zipfile.ZipFile(zip_path) as z:
        # Descobrir o prefixo dentro do ZIP (ex: "mpfb2-latest/src/mpfb/")
        # O __init__.py do addon está em <prefix>/__init__.py
        init_entries = [n for n in z.namelist() if n.endswith('/__init__.py')]
        # Escolher o __init__.py mais curto (o do root do addon, não sub-módulos)
        init_entries.sort(key=lambda x: x.count('/'))
        if not init_entries:
            raise RuntimeError('Não encontrei __init__.py no ZIP do MPFB')
        prefix = init_entries[0][:-len('__init__.py')]  # ex: "mpfb2-latest/src/mpfb/"

        for member in z.namelist():
            if not member.startswith(prefix):
                continue
            if member.endswith('/'):
                continue
            rel = member[len(prefix):]  # caminho relativo dentro do addon
            if '..' in Path(rel).parts:
                continue
            dest = mpfb_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(member))

    if not (mpfb_dir / '__init__.py').exists():
        raise RuntimeError(f'__init__.py não encontrado em {mpfb_dir}')

    # Adicionar a pasta de addons ao sys.path para que o Blender encontre o módulo
    scripts_dir = str(user_addon_dir.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    addons_dir = str(user_addon_dir)
    if addons_dir not in sys.path:
        sys.path.insert(0, addons_dir)

    # Recarregar a lista de addons disponíveis
    bpy.ops.preferences.addon_refresh()

    # Tentar activar com o nome do módulo directamente
    addon_utils.enable('mpfb', default_set=True)

    # Verificar se ficou activo
    if 'mpfb' in bpy.context.preferences.addons:
        return 'mpfb'

    # Fallback: tentar como extensão bl_ext.user_default.mpfb
    try:
        addon_utils.enable('bl_ext.user_default.mpfb', default_set=True)
        if 'bl_ext.user_default.mpfb' in bpy.context.preferences.addons:
            return 'bl_ext.user_default.mpfb'
    except Exception:
        pass

    raise RuntimeError(f'MPFB não ficou activo. Addons: {list(bpy.context.preferences.addons.keys())}')


try:
    # ── LIMPAR CENA (necessário sem --factory-startup) ────────────────────
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block_col in [bpy.data.meshes, bpy.data.cameras, bpy.data.lights,
                      bpy.data.materials, bpy.data.curves]:
        for block in list(block_col):
            block_col.remove(block)

    # ── INSTALAR MPFB ────────────────────────────────────────────────────────
    src = Path(os.environ['MPFB_ZIP'])
    module_id = install_mpfb_headless(src)
    R['components']['mpfb'] = {'status': 'active', 'module': module_id}

    # ── CRIAR HUMANO ─────────────────────────────────────────────────────────
    if 'FINISHED' not in bpy.ops.mpfb.create_human():
        raise RuntimeError('MPFB create_human failed')
    h = bpy.data.objects.get('Human')
    assert h and h.type == 'MESH', 'Human mesh not found after create_human'

    # ── MORPH FEMININO (B5 confirmado) ───────────────────────────────────────
    keys = h.data.shape_keys.key_blocks
    for k in keys:
        n = k.name
        if n.startswith(('$md-$as-', '$md-$ca-', '$md-$af-')) and '-$yn' in n:
            if '-$fe-' in n:
                k.value = 1 / 3
            elif '-$ma-' in n:
                k.value = 0
    bpy.context.view_layer.update()

    # ── ESCALA PARA 1,70 m ───────────────────────────────────────────────────
    v, _ = evaluated(h)
    initial = max(p.z for p in v) - min(p.z for p in v)
    factor = 1.7 / initial
    h.scale = tuple(s * factor for s in h.scale)
    bpy.context.view_layer.update()
    v, _ = evaluated(h)
    final = max(p.z for p in v) - min(p.z for p in v)
    if abs(final - 1.7) > .01:
        raise RuntimeError('World height mismatch: ' + str(final))
    R['components']['body'] = {
        'vertices': len(h.data.vertices),
        'initial_height_m': initial,
        'final_height_m': final,
    }

    # ── MATERIAIS ────────────────────────────────────────────────────────────
    mat_on(h, material('B6_Skin', '#A66A48', .55))
    dressmat = material('B6_RedFabric', '#C62828', .76)

    # ── VESTUÁRIO ────────────────────────────────────────────────────────────
    lo, hi_z, bodyverts = build_garment(h, None, dressmat)

    # ── CABELO ───────────────────────────────────────────────────────────────
    R['components']['eyes'] = {
        'method': 'no_additional_eyes',
        'reason': 'B5 added eyes behind head; anatomical landmarks unverified'
    }
    build_hair(bodyverts, lo, hi_z, material('B6_Hair', '#1A0D08'))

    R['limitations'] += [
        'Hair not generated until face orientation is established.',
        'Dress torso patch may include holes or arm seams.',
        'No rigging, garment physics or anatomical landmark detection.',
    ]
    R['checks']['body_height'] = abs(final - 1.7) <= .01

    # ── GRAVAR ───────────────────────────────────────────────────────────────
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'mpfb-female-b6.blend'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(
        filepath=str(OUT / 'mpfb-female-b6.glb'),
        export_format='GLB',
        use_selection=True,
        export_cameras=False,
        export_lights=False,
    )
    render_views(lo, hi_z)
    R['status'] = 'success_b6_geometry_experimental'
    R['glb_bytes'] = (OUT / 'mpfb-female-b6.glb').stat().st_size

except Exception as e:
    R.update(status='failed', error=str(e), traceback=traceback.format_exc())

finally:
    (OUT / 'mpfb-b6-report.json').write_text(json.dumps(R, ensure_ascii=False, indent=2))
    print('MPFB_B6_REPORT:', json.dumps(R, ensure_ascii=False))

if R['status'] != 'success_b6_geometry_experimental':
    sys.exit(1)
