"""B6: clothing derived from evaluated body surface; safe eye handling.
Blender 5.2.1 / MPFB. Usa bpy.ops.extensions (sem --factory-startup).
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
        raise RuntimeError('Torso patch insufficient')
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


def render_views(lo, hi):
    """Render previews — non-fatal if it fails."""
    scene = bpy.context.scene

    # Detectar o nome correcto do motor de render disponível
    render_engine = None
    for candidate in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'CYCLES'):
        try:
            scene.render.engine = candidate
            render_engine = candidate
            break
        except TypeError:
            continue
    if render_engine is None:
        R['limitations'].append('No suitable render engine found; previews skipped.')
        return

    scene.render.resolution_x = 640
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    if scene.world is None:
        scene.world = bpy.data.worlds.new('World')
    scene.world.color = (.35, .35, .35)
    center = (0, 0, (lo + hi) / 2)

    for label, loc in [('front', (0, -3.5, center[2])),
                       ('back', (0, 3.5, center[2])),
                       ('side', (3.5, 0, center[2]))]:
        ca = bpy.data.cameras.new('B6_Camera_' + label)
        ob = bpy.data.objects.new('B6_Camera_' + label, ca)
        bpy.context.collection.objects.link(ob)
        ob.location = loc
        direction = Vector(center) - ob.location
        ob.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
        ca.type = 'ORTHO'
        ca.ortho_scale = 2.2
        scene.camera = ob
        scene.render.filepath = str(OUT / ('b6-' + label + '.png'))
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            R['limitations'].append(f'Preview {label} failed: {e}')
        bpy.data.objects.remove(ob, do_unlink=True)


def install_mpfb_as_extension(zip_path: Path) -> str:
    repacked = OUT / 'mpfb-extension.zip'
    with zipfile.ZipFile(zip_path) as z:
        manifest = next(n for n in z.namelist() if n.endswith('blender_manifest.toml'))
        prefix = manifest[:-len('blender_manifest.toml')]
        with zipfile.ZipFile(repacked, 'w', zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith('/') and '..' not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == 'user_default'), None)
    if repo is None:
        repo = repos.new(
            name='User Default',
            module='user_default',
            custom_directory=str(OUT / 'extensions'),
        )
    repo.enabled = True

    result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked),
        repo=repo.module,
        enable_on_install=True,
    )
    if 'FINISHED' not in result and 'RUNNING_MODAL' not in result:
        raise RuntimeError(f'package_install_files devolveu: {result}')

    import addon_utils
    module_id = f'bl_ext.{repo.module}.mpfb'
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(
            f'MPFB não ficou activo. Addons: {list(bpy.context.preferences.addons.keys())}'
        )
    return module_id


try:
    # ── LIMPAR CENA ──────────────────────────────────────────────────────────
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for col in [bpy.data.meshes, bpy.data.cameras, bpy.data.lights,
                bpy.data.materials, bpy.data.curves]:
        for block in list(col):
            col.remove(block)

    # ── INSTALAR MPFB ────────────────────────────────────────────────────────
    src = Path(os.environ['MPFB_ZIP'])
    module_id = install_mpfb_as_extension(src)
    R['components']['mpfb'] = {'status': 'active', 'module': module_id}

    # ── CRIAR HUMANO ─────────────────────────────────────────────────────────
    if 'FINISHED' not in bpy.ops.mpfb.create_human():
        raise RuntimeError('MPFB create_human failed')
    h = bpy.data.objects.get('Human')
    assert h and h.type == 'MESH', 'Human mesh not found after create_human'

    # ── MORPH FEMININO ───────────────────────────────────────────────────────
    keys = h.data.shape_keys.key_blocks
    for k in keys:
        n = k.name
        if n.startswith(('$md-$as-', '$md-$ca-', '$md-$af-')) and '-$yn' in n:
            k.value = 1 / 3 if '-$fe-' in n else 0
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

    # ── VESTUÁRIO + CABELO ───────────────────────────────────────────────────
    lo, hi_z, bodyverts = build_garment(h, None, dressmat)
    R['components']['eyes'] = {
        'method': 'no_additional_eyes',
        'reason': 'Anatomical landmarks unverified'
    }
    build_hair(bodyverts, lo, hi_z, material('B6_Hair', '#1A0D08'))

    R['limitations'] += [
        'Hair deferred until face orientation established.',
        'Dress torso patch may include arm seams.',
        'No rigging or garment physics.',
    ]
    R['checks']['body_height'] = abs(final - 1.7) <= .01

    # ── GRAVAR .BLEND E EXPORTAR GLB ─────────────────────────────────────────
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'mpfb-female-b6.blend'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(
        filepath=str(OUT / 'mpfb-female-b6.glb'),
        export_format='GLB',
        use_selection=True,
        export_cameras=False,
        export_lights=False,
    )
    R['glb_bytes'] = (OUT / 'mpfb-female-b6.glb').stat().st_size

    # ── RENDER DE PREVIEWS (não-fatal) ───────────────────────────────────────
    try:
        render_views(lo, hi_z)
    except Exception as e:
        R['limitations'].append(f'render_views failed (non-fatal): {e}')

    R['status'] = 'success_b6_geometry_experimental'

except Exception as e:
    R.update(status='failed', error=str(e), traceback=traceback.format_exc())

finally:
    (OUT / 'mpfb-b6-report.json').write_text(json.dumps(R, ensure_ascii=False, indent=2))
    print('MPFB_B6_REPORT:', json.dumps(R, ensure_ascii=False))

if R['status'] != 'success_b6_geometry_experimental':
    sys.exit(1)
