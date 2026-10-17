"""
diagnose_mpfb_mesh.py

Nao cria nenhum modelo. Apenas:
  1. Instala o MPFB
  2. Chama create_human()
  3. Mede tudo o que interessa para posicionar olhos, cabelo e proporcoes
  4. Grava um JSON com os valores reais

Objectivo: eliminar adivinhacoes nos scripts B9+.
"""

import bpy
import bmesh
import json
import math
import os
import sys
import traceback
import zipfile
from pathlib import Path
from mathutils import Vector

out = Path(os.environ.get("MPFB_OUTPUT", "output/mpfb-diag")).resolve()
out.mkdir(parents=True, exist_ok=True)

report = {
    "version": "diagnose_mpfb_mesh_v1",
    "status": "started",
    "blender": bpy.app.version_string,
    "scene_objects": {},
    "body_mesh": {},
    "head_geometry": {},
    "eye_zone": {},
    "proportions": {},
    "errors": [],
}

def log(msg):
    print(f"[DIAG] {msg}", flush=True)

try:
    # ── INSTALAR MPFB ─────────────────────────────────────────────────────────
    source = Path(os.environ["MPFB_ZIP"])
    with zipfile.ZipFile(source) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix   = manifest[:-len("blender_manifest.toml")]
        repacked = out / "mpfb-extension.zip"
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo  = next((r for r in repos if r.module == "user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default",
                         custom_directory=str(out / "extensions"))
        repo.enabled = True

    result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    log(f"MPFB install: {result}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)

    # ── LIMPAR E CRIAR HUMANO ────────────────────────────────────────────────
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result = bpy.ops.mpfb.create_human()
    log(f"create_human: {result}")
    bpy.context.view_layer.update()

    # ── INVENTÁRIO DE TODOS OS OBJECTOS ──────────────────────────────────────
    log("Inventariando objectos da cena...")
    scene_inv = {}
    for obj in bpy.data.objects:
        info = {"type": obj.type, "location": list(obj.location)}
        if obj.type == "MESH":
            verts = obj.data.vertices
            if verts:
                xs = [v.co.x for v in verts]
                ys = [v.co.y for v in verts]
                zs = [v.co.z for v in verts]
                info["vertex_count"] = len(verts)
                info["bbox"] = {
                    "x": [round(min(xs),4), round(max(xs),4)],
                    "y": [round(min(ys),4), round(max(ys),4)],
                    "z": [round(min(zs),4), round(max(zs),4)],
                }
                info["center"] = [
                    round((min(xs)+max(xs))/2, 4),
                    round((min(ys)+max(ys))/2, 4),
                    round((min(zs)+max(zs))/2, 4),
                ]
        elif obj.type == "CURVE":
            info["splines"] = len(obj.data.splines)
        scene_inv[obj.name] = info

    report["scene_objects"] = scene_inv
    log(f"Objectos na cena: {list(scene_inv.keys())}")

    # ── IDENTIFICAR MESH PRINCIPAL DO CORPO ──────────────────────────────────
    # Maior mesh por numero de vertices
    meshes = [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"]
    meshes.sort(key=lambda x: x[1], reverse=True)
    log(f"Meshes por tamanho: {[(m[0].name, m[1]) for m in meshes]}")

    body = meshes[0][0] if meshes else None
    if body is None:
        raise RuntimeError("Nenhum mesh encontrado apos create_human")

    log(f"Mesh principal: {body.name} ({len(body.data.vertices)} vertices)")

    # ── MEDIR O CORPO COMPLETO ────────────────────────────────────────────────
    bpy.context.view_layer.update()
    verts_world = [body.matrix_world @ v.co for v in body.data.vertices]
    all_x = [v.x for v in verts_world]
    all_y = [v.y for v in verts_world]
    all_z = [v.z for v in verts_world]

    body_min_z = min(all_z)
    body_max_z = max(all_z)
    body_h     = body_max_z - body_min_z

    report["body_mesh"] = {
        "name":         body.name,
        "vertex_count": len(body.data.vertices),
        "body_min_z":   round(body_min_z, 6),
        "body_max_z":   round(body_max_z, 6),
        "body_height":  round(body_h, 6),
        "body_width_x": round(max(all_x) - min(all_x), 6),
        "body_depth_y": round(max(all_y) - min(all_y), 6),
    }
    log(f"Altura do corpo: {body_h:.4f} m  (Z: {body_min_z:.4f} → {body_max_z:.4f})")

    # ── ANÁLISE DA CABEÇA ─────────────────────────────────────────────────────
    # Zona da cabeca = top 15% do corpo (acima de 85% da altura)
    head_zone_lo = body_min_z + body_h * 0.82
    head_zone_hi = body_max_z

    head_verts = [v for v in verts_world if head_zone_lo <= v.z <= head_zone_hi]
    log(f"Vertices na zona da cabeca (Z > {head_zone_lo:.3f}): {len(head_verts)}")

    if head_verts:
        hx = [v.x for v in head_verts]
        hy = [v.y for v in head_verts]
        hz = [v.z for v in head_verts]
        head_width  = max(hx) - min(hx)
        head_depth  = max(hy) - min(hy)
        head_height = max(hz) - min(hz)
        # centro geometrico da cabeca
        head_cx = (max(hx) + min(hx)) / 2
        head_cy = (max(hy) + min(hy)) / 2
        head_cz = (max(hz) + min(hz)) / 2
        # raio aproximado (media de largura e profundidade)
        head_radius_est = (head_width + head_depth) / 4

        report["head_geometry"] = {
            "zone_lo_z":     round(head_zone_lo, 4),
            "zone_hi_z":     round(head_zone_hi, 4),
            "vertex_count":  len(head_verts),
            "head_width_x":  round(head_width, 4),
            "head_depth_y":  round(head_depth, 4),
            "head_height_z": round(head_height, 4),
            "head_center":   [round(head_cx,4), round(head_cy,4), round(head_cz,4)],
            "head_radius_est": round(head_radius_est, 4),
            "head_top_z":    round(max(hz), 4),
            "head_bot_z":    round(min(hz), 4),
            "face_y_min":    round(min(hy), 4),   # Y mais negativo = frente da face
            "face_y_max":    round(max(hy), 4),   # Y mais positivo = nuca
        }
        log(f"Cabeca: centro={[round(head_cx,3),round(head_cy,3),round(head_cz,3)]} "
            f"raio_est={head_radius_est:.3f} face_y_min={min(hy):.3f}")

    # ── ZONA DOS OLHOS — ANÁLISE DETALHADA ───────────────────────────────────
    # Vamos varrer fatias horizontais de 1 cm na zona 75%-95% da altura
    # e para cada fatia medir: largura total, largura frontal, separacao lateral
    log("Analisando zona dos olhos em fatias de 1cm...")

    slices = {}
    slice_step = 0.01  # 1 cm
    scan_lo = body_min_z + body_h * 0.75
    scan_hi = body_min_z + body_h * 0.97

    z = scan_lo
    while z <= scan_hi:
        z_lo = z
        z_hi = z + slice_step
        sv = [v for v in verts_world if z_lo <= v.z < z_hi]
        if sv:
            sx = [v.x for v in sv]
            sy = [v.y for v in sv]
            # frente da face nesta fatia (Y mais negativo)
            front_y = min(sy)
            # verts frontais = Y < media dos Y desta fatia
            mean_y = sum(sy) / len(sy)
            front_verts = [v for v in sv if v.y < mean_y]
            if front_verts:
                fx = [v.x for v in front_verts]
                fy = [v.y for v in front_verts]
                # separar esq/dir
                left  = [v for v in front_verts if v.x > 0.01]
                right = [v for v in front_verts if v.x < -0.01]
                slices[round(z_lo, 3)] = {
                    "total_verts": len(sv),
                    "front_verts": len(front_verts),
                    "width_x":     round(max(sx) - min(sx), 4),
                    "front_y":     round(front_y, 4),
                    "front_x_span": round(max(fx) - min(fx), 4) if fx else 0,
                    "left_centroid":  [round(sum(v.x for v in left)/len(left),4),
                                       round(sum(v.y for v in left)/len(left),4),
                                       round((z_lo+z_hi)/2, 4)] if left else None,
                    "right_centroid": [round(sum(v.x for v in right)/len(right),4),
                                       round(sum(v.y for v in right)/len(right),4),
                                       round((z_lo+z_hi)/2, 4)] if right else None,
                }
        z += slice_step

    report["eye_zone"]["slices"] = slices

    # Identificar a fatia com maior separacao lateral na frente (provavelmente os olhos)
    best_z = None
    best_span = 0
    for z_key, s in slices.items():
        if s["front_x_span"] > best_span and s["left_centroid"] and s["right_centroid"]:
            # verificar que left e right estao simetricamente separados
            lx = s["left_centroid"][0]
            rx = s["right_centroid"][0]
            if lx > 0.02 and rx < -0.02:
                best_span = s["front_x_span"]
                best_z = z_key

    if best_z:
        best = slices[best_z]
        report["eye_zone"]["best_estimate"] = {
            "z_slice":       best_z,
            "z_pct_height":  round((best_z - body_min_z) / body_h * 100, 1),
            "left_centroid": best["left_centroid"],
            "right_centroid":best["right_centroid"],
            "front_y":       best["front_y"],
            "note": "fatia com maior separacao lateral na face frontal"
        }
        log(f"Melhor estimativa para olhos: Z={best_z} ({report['eye_zone']['best_estimate']['z_pct_height']}% da altura)")
        log(f"  Olho E: {best['left_centroid']}")
        log(f"  Olho D: {best['right_centroid']}")
    else:
        report["eye_zone"]["best_estimate"] = {"note": "nao foi possivel determinar automaticamente"}
        log("Nao foi possivel determinar zona dos olhos automaticamente")

    # ── PROPORÇÕES CALCULADAS ─────────────────────────────────────────────────
    report["proportions"] = {
        "pct_80": round(body_min_z + body_h * 0.80, 4),
        "pct_82": round(body_min_z + body_h * 0.82, 4),
        "pct_84": round(body_min_z + body_h * 0.84, 4),
        "pct_85": round(body_min_z + body_h * 0.85, 4),
        "pct_86": round(body_min_z + body_h * 0.86, 4),
        "pct_88": round(body_min_z + body_h * 0.88, 4),
        "pct_90": round(body_min_z + body_h * 0.90, 4),
        "pct_92": round(body_min_z + body_h * 0.92, 4),
        "note": "valores de Z em metros para diferentes percentagens da altura"
    }

    # ── SHAPE KEYS DISPONÍVEIS ────────────────────────────────────────────────
    if body.data.shape_keys:
        keys_list = [k.name for k in body.data.shape_keys.key_blocks]
        report["shape_keys"] = {
            "count": len(keys_list),
            "names": keys_list,
            "female_keys": [k for k in keys_list if "-$fe-" in k or k.endswith("-$fe")],
            "male_keys":   [k for k in keys_list if "-$ma-" in k or k.endswith("-$ma")],
        }
        log(f"Shape keys: {len(keys_list)} total, "
            f"{len(report['shape_keys']['female_keys'])} femininas")
    else:
        report["shape_keys"] = {"count": 0, "note": "sem shape keys"}

    # ── OPERADORES MPFB DISPONÍVEIS ───────────────────────────────────────────
    mpfb_ops = []
    for mod_name in dir(bpy.ops):
        if "mpfb" in mod_name.lower():
            mod = getattr(bpy.ops, mod_name)
            for op_name in dir(mod):
                mpfb_ops.append(f"{mod_name}.{op_name}")
    report["mpfb_operators"] = mpfb_ops
    log(f"Operadores MPFB: {len(mpfb_ops)}")

    report["status"] = "success"

except Exception as exc:
    report["status"] = "failed"
    report["errors"].append(str(exc))
    report["traceback"] = traceback.format_exc()
    log(f"ERRO: {exc}")

finally:
    report_path = out / "mpfb-diag-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"Relatorio gravado: {report_path}")
    print("MPFB_DIAG_REPORT:", json.dumps(report, ensure_ascii=False))

if report["status"] != "success":
    sys.exit(1)
