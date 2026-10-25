#!/usr/bin/env python3
"""Montador de Cenas 3D — Fase 5.

Constrói uma cena Blender a partir de uma configuração JSON:
  - importa modelos de ambiente (GLTF/GLB)
  - importa personagens animadas
  - posiciona e escala todos os objetos
  - aplica iluminação (preset daylight ou studio)
  - configura câmara (character_focus / scene_overview / cinematic)
  - renderiza PNG e/ou MP4
  - exporta GLB opcional

Uso (pelo GitHub Actions — Blender em modo background):
  blender --background --python library/tools/build_scene.py -- \\
      --scene-config scenes/knight_test.json \\
      --output-dir   /tmp/scene-output \\
      --report       /tmp/scene-report.json \\
      [--render-preview] \\
      [--render-animation] \\
      [--export-glb] \\
      [--resolution 640] \\
      [--max-frames 26] \\
      [--fps 24]

Compatível com Blender 3.6 / 4.x / 5.x (inclui Action Slots do 5.x).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

try:
    _sep  = sys.argv.index("--")
    _argv = sys.argv[_sep + 1:]
except ValueError:
    _argv = []

try:
    import bpy
    import mathutils
    from bpy_extras.object_utils import world_to_camera_view  # type: ignore
except ImportError:
    print("ERRO: executar dentro do Blender.", file=sys.stderr)
    sys.exit(2)

_BPY_MAJOR = bpy.app.version[0]
_HAS_SLOTS  = _BPY_MAJOR >= 5

# ─────────────────────────────────────────────────────────────────────────────
# SEGURANÇA
# ─────────────────────────────────────────────────────────────────────────────
_LIBRARY_ROOT: Optional[pathlib.Path] = None

def _set_library_root(path: pathlib.Path) -> None:
    global _LIBRARY_ROOT
    _LIBRARY_ROOT = path.resolve()

def _safe_asset_path(rel_or_abs: str) -> pathlib.Path:
    p = pathlib.Path(rel_or_abs).resolve()
    if _LIBRARY_ROOT is None:
        return p
    allowed = (_LIBRARY_ROOT / "assets").resolve()
    try:
        p.relative_to(allowed)
    except ValueError:
        raise ValueError(f"Caminho de asset fora do sandbox: {p}\n  Sandbox: {allowed}")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO
# ─────────────────────────────────────────────────────────────────────────────

def _empty_report(scene_name: str) -> Dict[str, Any]:
    return {
        "scene_name":                scene_name,
        "blender_version":           ".".join(str(v) for v in bpy.app.version),
        "scene_assets_imported":     0,
        "character_imported":        False,
        "character_animated":        False,
        "animation_motion_confirmed": False,
        "moving_bones":              0,
        "camera_mode":               "cinematic",
        "camera_created":            False,
        "lighting_preset":           "daylight",
        "lights_created":            0,
        "ground_created":            False,
        "ground_z":                  None,
        "frames_rendered":           0,
        "frames_requested":          0,
        "preview_success":           False,
        "preview_path":              None,
        "video_path":                None,
        "glb_exported":              False,
        "glb_path":                  None,
        "glb_importable":            False,
        "render_time_s":             None,
        "framing_success":           False,
        # Dados espaciais
        "building_world_bounds":     None,
        "character_world_bounds":    None,
        "character_position":        None,
        "character_ground_clearance": None,
        "character_building_overlap": None,
        "character_not_above_building": None,
        "placement_validated":       False,
        "errors":                    [],
        "warnings":                  [],
        "asset_details":             [],
    }

def _save_report(report: Dict[str, Any], path: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ─────────────────────────────────────────────────────────────────────────────
# VALIDAÇÃO DO JSON DE CENA
# ─────────────────────────────────────────────────────────────────────────────

def _validate_scene_config(cfg: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(cfg.get("scene_name"), str) or not cfg["scene_name"]:
        errors.append("scene_name obrigatório (string não vazia)")
    env = cfg.get("environment", {})
    if env:
        for i, asset in enumerate(env.get("assets", [])):
            if not isinstance(asset.get("asset_id"), str):
                errors.append(f"environment.assets[{i}]: asset_id obrigatório")
            pos = asset.get("position", [0, 0, 0])
            if not (isinstance(pos, list) and len(pos) == 3):
                errors.append(f"environment.assets[{i}]: position deve ser [x,y,z]")
    for i, char in enumerate(cfg.get("characters", [])):
        if not isinstance(char.get("asset_id"), str):
            errors.append(f"characters[{i}]: asset_id obrigatório")
    camera = cfg.get("camera", {})
    if camera.get("mode") not in (None, "character_focus", "scene_overview", "cinematic"):
        errors.append("camera.mode deve ser: character_focus | scene_overview | cinematic")
    lighting = cfg.get("lighting", {})
    if lighting.get("preset") not in (None, "daylight", "studio", "cinematic"):
        errors.append("lighting.preset deve ser: daylight | studio | cinematic")
    output = cfg.get("output", {})
    res = output.get("resolution", 640)
    if not isinstance(res, int) or not (128 <= res <= 2048):
        errors.append("output.resolution deve ser int entre 128 e 2048")
    fps = output.get("fps", 24)
    if not isinstance(fps, int) or not (1 <= fps <= 60):
        errors.append("output.fps deve ser int entre 1 e 60")
    mf = output.get("max_frames", 72)
    if not isinstance(mf, int) or not (1 <= mf <= 300):
        errors.append("output.max_frames deve ser int entre 1 e 300")
    return errors


# ─────────────────────────────────────────────────────────────────────────────
# RESOLVER DE ASSETS
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_asset_glb(asset_id: str, library_root: pathlib.Path,
                       category_hint: str = "") -> Optional[pathlib.Path]:
    assets_root = library_root / "assets"
    def _find_in_dir(adir: pathlib.Path) -> Optional[pathlib.Path]:
        if not adir.is_dir():
            return None
        for ext in (".glb", ".gltf"):
            matches = sorted(adir.rglob(f"*{ext}"))
            if matches:
                return matches[0]
        return None
    if category_hint:
        p = _find_in_dir(assets_root / category_hint / asset_id)
        if p:
            return p
    if assets_root.is_dir():
        for cat_dir in sorted(assets_root.iterdir()):
            if not cat_dir.is_dir():
                continue
            p = _find_in_dir(cat_dir / asset_id)
            if p:
                return p
    return None

def _resolve_assets_from_config(cfg: Dict[str, Any], library_root: pathlib.Path,
                                 report: Dict[str, Any]) -> Dict[str, pathlib.Path]:
    needed: Dict[str, str] = {}
    for asset in cfg.get("environment", {}).get("assets", []):
        needed[asset["asset_id"]] = "environments"
    for char in cfg.get("characters", []):
        needed[char["asset_id"]] = "characters"
    resolved: Dict[str, pathlib.Path] = {}
    for asset_id, cat_hint in needed.items():
        path = _resolve_asset_glb(asset_id, library_root, cat_hint)
        if path is None:
            report["errors"].append(
                f"Asset '{asset_id}' não encontrado em library/assets/. "
                f"Execute o download antes de construir a cena.")
        else:
            try:
                resolved[asset_id] = _safe_asset_path(str(path))
                report["asset_details"].append({"asset_id": asset_id, "path": str(path), "found": True})
            except ValueError as e:
                report["errors"].append(str(e))
    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# COLEÇÕES
# ─────────────────────────────────────────────────────────────────────────────

_COLLECTIONS = ["SCENE", "ENVIRONMENT", "BUILDINGS", "ROADS",
                "VEGETATION", "PROPS", "CHARACTERS", "LIGHTING", "CAMERAS"]

def _make_collections() -> Dict[str, Any]:
    scene = bpy.context.scene
    cols: Dict[str, Any] = {}
    for name in _COLLECTIONS:
        existing = bpy.data.collections.get(name)
        cols[name] = existing if existing else bpy.data.collections.new(name)
    sc = scene.collection
    if cols["SCENE"].name not in [c.name for c in sc.children]:
        sc.children.link(cols["SCENE"])
    for sub in ("ENVIRONMENT", "BUILDINGS", "ROADS", "VEGETATION",
                "PROPS", "CHARACTERS", "LIGHTING", "CAMERAS"):
        parent = cols["SCENE"]
        child  = cols[sub]
        if child.name not in [c.name for c in parent.children]:
            parent.children.link(child)
    return cols


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _objects_before(scene: Any) -> set:
    return {o.name for o in scene.objects}

def _import_glb(path: pathlib.Path) -> Tuple[bool, str, List[Any]]:
    if not path.is_file():
        return False, f"ficheiro não encontrado: {path}", []
    if path.stat().st_size == 0:
        return False, f"ficheiro vazio: {path}", []
    scene  = bpy.context.scene
    before = _objects_before(scene)
    try:
        r = bpy.ops.import_scene.gltf(filepath=str(path))
        if r != {"FINISHED"}:
            return False, f"importação retornou: {r}", []
    except Exception:
        return False, traceback.format_exc(limit=8), []
    new_objs = [o for o in scene.objects if o.name not in before]
    return True, "", new_objs

def _move_to_collection(objects: List[Any], target_col: Any) -> None:
    for obj in objects:
        for col in list(obj.users_collection):
            col.objects.unlink(obj)
        target_col.objects.link(obj)


# ─────────────────────────────────────────────────────────────────────────────
# BOUNDS — todas as medições em world space
# ─────────────────────────────────────────────────────────────────────────────

def _world_bounds_of(objects: List[Any]) -> Optional[Tuple[float,float,float,float,float,float]]:
    """Bounding box world-space de uma lista de objectos. Devolve None se vazio."""
    min_v = mathutils.Vector((math.inf, math.inf, math.inf))
    max_v = mathutils.Vector((-math.inf, -math.inf, -math.inf))
    found = False
    for obj in objects:
        if not hasattr(obj, "bound_box"):
            continue
        mat = obj.matrix_world
        for corner in obj.bound_box:
            wc = mat @ mathutils.Vector(corner)
            min_v.x = min(min_v.x, wc.x); max_v.x = max(max_v.x, wc.x)
            min_v.y = min(min_v.y, wc.y); max_v.y = max(max_v.y, wc.y)
            min_v.z = min(min_v.z, wc.z); max_v.z = max(max_v.z, wc.z)
        found = True
    if not found or math.isinf(min_v.x):
        return None
    return (min_v.x, max_v.x, min_v.y, max_v.y, min_v.z, max_v.z)

def _scene_bounds() -> Tuple[float,float,float,float,float,float]:
    b = _world_bounds_of([o for o in bpy.context.scene.objects if o.type == "MESH"])
    return b if b else (-2.0, 2.0, -2.0, 2.0, 0.0, 2.0)


# ─────────────────────────────────────────────────────────────────────────────
# PLACEMENT — posicionar assets com base em bounds reais
# ─────────────────────────────────────────────────────────────────────────────

def _apply_transform_to_ground(objects: List[Any],
                                target_xy: Tuple[float, float],
                                ground_z: float,
                                rotation_deg: List[float],
                                scale: List[float]) -> None:
    """Move o grupo de objectos para que:
       - a base (min_z) fique em ground_z
       - o centro XY fique em target_xy

    Aplica escala e rotação antes da translação.
    Usa bounds world-space reais depois de actualizar transforms.
    """
    if not objects:
        return

    # Aplicar escala e rotação primeiro (apenas objectos raiz)
    for obj in objects:
        if obj.parent is None:
            obj.scale.x *= scale[0]
            obj.scale.y *= scale[1]
            obj.scale.z *= scale[2]
            obj.rotation_euler.x += math.radians(rotation_deg[0])
            obj.rotation_euler.y += math.radians(rotation_deg[1])
            obj.rotation_euler.z += math.radians(rotation_deg[2])

    # Forçar actualização de transforms
    bpy.context.view_layer.update()

    # Medir bounds reais após transformações
    b = _world_bounds_of(objects)
    if b is None:
        # Sem bounding box — mover pelo location do primeiro objecto raiz
        root = next((o for o in objects if o.parent is None), objects[0])
        cx_cur, cy_cur = root.location.x, root.location.y
        base_z = root.location.z
    else:
        xmn, xmx, ymn, ymx, zmn, zmx = b
        cx_cur = (xmn + xmx) / 2.0
        cy_cur = (ymn + ymx) / 2.0
        base_z = zmn  # base real do grupo

    # Calcular offset para alinhar base ao ground_z e centro XY ao target_xy
    offset_x = target_xy[0] - cx_cur
    offset_y = target_xy[1] - cy_cur
    offset_z = ground_z - base_z

    for obj in objects:
        if obj.parent is None:
            obj.location.x += offset_x
            obj.location.y += offset_y
            obj.location.z += offset_z

    # Segunda actualização
    bpy.context.view_layer.update()


def _place_character_beside_building(
        char_objects: List[Any],
        building_bounds: Tuple[float,float,float,float,float,float],
        ground_z: float,
        report: Dict[str, Any],
) -> None:
    """Posiciona o personagem NO SOLO, AO LADO do prédio.

    Algoritmo baseado em bounds reais:
    1. Mede footprint XY do prédio.
    2. Calcula posição ao lado (exterior do bounding box + margem de segurança).
    3. Alinha a base do personagem ao ground_z.
    4. Valida ausência de overlap XY.
    5. Valida ground clearance.
    """
    if not char_objects:
        return

    bxmn, bxmx, bymn, bymx, bzmn, bzmx = building_bounds
    b_cx = (bxmn + bxmx) / 2.0
    b_cy = (bymn + bymx) / 2.0
    b_dx = bxmx - bxmn  # largura X do prédio
    b_dy = bymx - bymn  # profundidade Y do prédio

    # Margens:
    # SAFETY_GAP = espaço livre entre o exterior do prédio e o personagem
    # Usar metade da maior dimensão horizontal do prédio como gap mínimo,
    # com mínimo absoluto de 1.5 m para assets pequenos.
    safety_gap = max(max(b_dx, b_dy) * 0.5, 1.5)

    # Posição ao lado: deslocado no eixo X positivo do prédio
    # (lado direito do prédio ao olhar de frente)
    char_target_x = bxmx + safety_gap
    char_target_y = b_cy  # alinhado com o centro Y do prédio

    print(f"  [placement] prédio bounds: X=[{bxmn:.2f},{bxmx:.2f}] "
          f"Y=[{bymn:.2f},{bymx:.2f}] Z=[{bzmn:.2f},{bzmx:.2f}]")
    print(f"  [placement] safety_gap={safety_gap:.2f}  "
          f"alvo personagem: X={char_target_x:.2f} Y={char_target_y:.2f} Z(solo)={ground_z:.4f}")

    # Mover personagem: base ao solo, XY ao lado do prédio
    _apply_transform_to_ground(
        char_objects,
        target_xy=(char_target_x, char_target_y),
        ground_z=ground_z,
        rotation_deg=[0.0, 0.0, 0.0],
        scale=[1.0, 1.0, 1.0],
    )

    # ── Validar posicionamento ────────────────────────────────────────────
    bpy.context.view_layer.update()
    char_b = _world_bounds_of(char_objects)

    if char_b is None:
        report["warnings"].append("Não foi possível medir bounds do personagem após placement")
        return

    cxmn, cxmx, cymn, cymx, czmn, czmx = char_b

    # Ground clearance: base do personagem vs solo
    ground_clearance = round(czmn - ground_z, 4)
    report["character_ground_clearance"] = ground_clearance
    report["character_position"] = [
        round((cxmn+cxmx)/2, 4),
        round((cymn+cymx)/2, 4),
        round(czmn, 4),
    ]
    report["character_world_bounds"] = {
        "min": [round(cxmn,4), round(cymn,4), round(czmn,4)],
        "max": [round(cxmx,4), round(cymx,4), round(czmx,4)],
    }

    # Overlap XY entre personagem e prédio (margens de segurança incluídas)
    # Dois bounding boxes não se sobrepõem se um está completamente fora do outro em qualquer eixo
    overlap_x = not (cxmx <= bxmn or cxmn >= bxmx)
    overlap_y = not (cymx <= bymn or cymn >= bymx)
    overlap_z = not (czmx <= bzmn or czmn >= bzmx)
    has_overlap = overlap_x and overlap_y and overlap_z
    report["character_building_overlap"] = has_overlap

    # Personagem não está em cima do prédio:
    # "em cima" = personagem Z está acima do topo do prédio E há overlap XY
    above_building = (czmn > bzmx) and overlap_x and overlap_y
    report["character_not_above_building"] = not above_building

    # Tolerância de ground clearance: até 0.05 m aceitável
    ground_ok = abs(ground_clearance) <= 0.05

    placement_ok = (not has_overlap) and (not above_building) and ground_ok
    report["placement_validated"] = placement_ok

    print(f"  [placement] personagem bounds: X=[{cxmn:.2f},{cxmx:.2f}] "
          f"Y=[{cymn:.2f},{cymx:.2f}] Z=[{czmn:.2f},{czmx:.2f}]")
    print(f"  [placement] ground_clearance={ground_clearance:.4f}  "
          f"overlap={has_overlap}  above_building={above_building}  "
          f"validated={placement_ok}")

    if has_overlap:
        report["errors"].append(
            f"ERRO DE PLACEMENT: personagem intersecta o prédio. "
            f"char_X=[{cxmn:.2f},{cxmx:.2f}] vs building_X=[{bxmn:.2f},{bxmx:.2f}]"
        )
    if above_building:
        report["errors"].append(
            f"ERRO DE PLACEMENT: personagem está em cima do prédio. "
            f"char_Z_min={czmn:.2f} > building_Z_max={bzmx:.2f}"
        )
    if not ground_ok:
        report["warnings"].append(
            f"Ground clearance fora de tolerância: {ground_clearance:.4f} m "
            f"(tolerância ±0.05 m)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ANIMAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _get_armature_from(objects: List[Any]) -> Optional[Any]:
    for o in objects:
        if o.type == "ARMATURE":
            return o
    return None

def _assign_action(arm_obj: Any, action: Any) -> None:
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    anim_data = arm_obj.animation_data
    anim_data.action = action
    if _HAS_SLOTS and hasattr(action, "slots") and action.slots:
        chosen = None
        for slot in action.slots:
            tid = getattr(slot, "target_id_type", "") or ""
            if tid in ("ARMATURE", "OBJECT", ""):
                chosen = slot
                break
        if chosen is None:
            chosen = action.slots[0]
        if hasattr(anim_data, "action_slot"):
            try:
                anim_data.action_slot = chosen
            except Exception:
                pass

def _find_action(name: str) -> Optional[Any]:
    for a in bpy.data.actions:
        if a.name == name:
            return a
    name_l = name.lower()
    for a in bpy.data.actions:
        if name_l in a.name.lower():
            return a
    return None

def _check_animation_motion(action: Any, report: Dict[str, Any]) -> None:
    """Verifica se a animação move realmente ossos (não é pose estática)."""
    if action is None:
        return
    moving = set()
    for fc in action.fcurves:
        pts = fc.keyframe_points
        if len(pts) < 2:
            continue
        vals = [p.co.y for p in pts]
        if max(vals) - min(vals) > 1e-4:
            # Extrair nome do osso do data_path (ex: 'pose.bones["Hips"].rotation_euler')
            dp = fc.data_path
            if '"' in dp:
                bone_name = dp.split('"')[1]
                moving.add(bone_name)
    report["animation_motion_confirmed"] = len(moving) > 0
    report["moving_bones"] = len(moving)
    print(f"  [animação] ossos em movimento: {len(moving)}  "
          f"confirmed={len(moving) > 0}")


# ─────────────────────────────────────────────────────────────────────────────
# CÂMARA — enquadrar personagem + prédio
# ─────────────────────────────────────────────────────────────────────────────

def _place_camera_for_bounds(
        scene: Any,
        bounds: Tuple[float,float,float,float,float,float],
        mode: str,
        report: Dict[str, Any],
) -> Any:
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0
    cz = (zmn + zmx) / 2.0
    dx = xmx - xmn or 0.1
    dy = ymx - ymn or 0.1
    dz = zmx - zmn or 0.1

    radius      = math.sqrt(dx*dx + dy*dy + dz*dz) / 2.0
    radius_safe = radius * 1.35
    fov_rad     = math.radians(46.8)
    cam_dist    = radius_safe / math.sin(fov_rad / 2.0)

    if mode == "scene_overview":
        elev = math.radians(35); azim = math.radians(45); cam_dist *= 1.4
    elif mode == "character_focus":
        elev = math.radians(15); azim = math.radians(30); cam_dist *= 0.85
    else:  # cinematic
        elev = math.radians(18); azim = math.radians(35)

    cam_x = cx + cam_dist * math.cos(elev) * math.sin(azim)
    cam_y = cy - cam_dist * math.cos(elev) * math.cos(azim)
    cam_z = cz + cam_dist * math.sin(elev)

    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam_obj = bpy.context.active_object
    cam_obj.name = "SceneCam"

    target_pt = mathutils.Vector((cx, cy, cz))
    direction  = target_pt - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    cam_obj.data.lens       = 35.0  # mais aberto para mostrar cena completa
    cam_obj.data.clip_start = 0.01
    cam_obj.data.clip_end   = cam_dist * 15.0
    scene.camera = cam_obj

    report["camera_created"] = True
    print(f"  [câmara] modo={mode}  dist={cam_dist:.2f}  centro=({cx:.2f},{cy:.2f},{cz:.2f})")
    return cam_obj

def _adjust_camera_framing(
        scene: Any, cam_obj: Any, objects_to_frame: List[Any],
        margin: float = 0.08, max_iter: int = 8, report: Dict[str, Any] = {},
) -> None:
    """Ajusta câmara para enquadrar todos os objectos da lista (personagem + prédio)."""
    if not objects_to_frame:
        return

    safe_lo = margin
    safe_hi = 1.0 - margin

    for iteration in range(max_iter + 1):
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        all_min_x, all_max_x = math.inf, -math.inf
        all_min_y, all_max_y = math.inf, -math.inf

        for obj in objects_to_frame:
            if obj.type != "MESH":
                continue
            try:
                obj_eval  = obj.evaluated_get(depsgraph)
                mesh_eval = obj_eval.to_mesh()
                if mesh_eval is None:
                    continue
                mat = obj_eval.matrix_world
                for v in mesh_eval.vertices:
                    co = world_to_camera_view(scene, cam_obj, mat @ v.co)
                    if co.z > 0:
                        all_min_x = min(all_min_x, co.x); all_max_x = max(all_max_x, co.x)
                        all_min_y = min(all_min_y, co.y); all_max_y = max(all_max_y, co.y)
                obj_eval.to_mesh_clear()
            except Exception:
                pass

        if math.isinf(all_min_x):
            break

        ok = (all_min_x >= safe_lo and all_max_x <= safe_hi and
              all_min_y >= safe_lo and all_max_y <= safe_hi)
        report["framing_success"] = ok

        if ok:
            print(f"  [enquadramento] OK  x=[{all_min_x:.3f},{all_max_x:.3f}]"
                  f"  y=[{all_min_y:.3f},{all_max_y:.3f}]")
            return

        if iteration == max_iter:
            report["warnings"].append(
                f"enquadramento não garantido após {max_iter} ajustes")
            return

        overshoot = max(
            safe_lo - all_min_x if all_min_x < safe_lo else 0,
            all_max_x - safe_hi if all_max_x > safe_hi else 0,
            safe_lo - all_min_y if all_min_y < safe_lo else 0,
            all_max_y - safe_hi if all_max_y > safe_hi else 0,
        )
        factor = 1.0 + max(0.12, min(0.45, overshoot * 1.5))

        # Calcular centro de todos os objectos
        b = _world_bounds_of([o for o in objects_to_frame if o.type == "MESH"])
        if b:
            target = mathutils.Vector(((b[0]+b[1])/2, (b[2]+b[3])/2, (b[4]+b[5])/2))
        else:
            break
        direction = (mathutils.Vector(cam_obj.location) - target).normalized()
        dist = (mathutils.Vector(cam_obj.location) - target).length
        cam_obj.location = target + direction * (dist * factor)
        print(f"  [enquadramento] ajuste {iteration+1}: factor={factor:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# ILUMINAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _setup_daylight(scene: Any,
                    bounds: Tuple[float,float,float,float,float,float],
                    report: Dict[str, Any]) -> None:
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn+xmx)/2; cy = (ymn+ymx)/2
    h  = zmx - zmn or 2.0
    lights_ok = 0

    try:
        bpy.ops.object.light_add(type="SUN", location=(cx + 5, cy - 3, zmn + h*2.5))
        sun = bpy.context.active_object
        sun.name = "Sun_Key"
        sun.data.energy = 3.5
        sun.data.color  = (1.0, 0.97, 0.88)
        sun.data.angle  = math.radians(3.0)
        direction = mathutils.Vector((cx, cy, zmn + h*0.5)) - sun.location
        sun.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        lights_ok += 1
    except Exception as e:
        report["warnings"].append(f"Sol principal falhou: {e}")

    try:
        bpy.ops.object.light_add(type="AREA", location=(cx - 4, cy + 2, zmn + h*1.5))
        fill = bpy.context.active_object
        fill.name = "Sky_Fill"
        fill.data.energy = 200.0
        fill.data.size   = max(h * 2.0, 4.0)
        fill.data.color  = (0.55, 0.70, 1.0)
        direction = mathutils.Vector((cx, cy, zmn + h*0.5)) - fill.location
        fill.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        lights_ok += 1
    except Exception as e:
        report["warnings"].append(f"Fill Sky falhou: {e}")

    try:
        world = bpy.data.worlds.new("DaylightWorld")
        world.use_nodes = True
        nt = world.node_tree
        bg_node = nt.nodes.get("Background") or nt.nodes.new("ShaderNodeBackground")
        sky_node = nt.nodes.new("ShaderNodeTexSky")
        sky_node.sky_type = "NISHITA"
        sky_node.sun_elevation = math.radians(45.0)
        sky_node.sun_rotation  = math.radians(20.0)
        sky_node.altitude      = 0.0
        sky_node.air_density   = 1.0
        sky_node.dust_density  = 0.5
        tc_node = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(tc_node.outputs["Generated"], sky_node.inputs["Vector"])
        nt.links.new(sky_node.outputs["Color"],    bg_node.inputs["Color"])
        bg_node.inputs["Strength"].default_value = 1.0
        out_node = nt.nodes.get("World Output") or nt.nodes.new("ShaderNodeOutputWorld")
        nt.links.new(bg_node.outputs["Background"], out_node.inputs["Surface"])
        scene.world = world
    except Exception as e:
        try:
            world = bpy.data.worlds.new("DaylightFallback")
            world.use_nodes = True
            bg = world.node_tree.nodes.get("Background")
            if bg:
                bg.inputs["Color"].default_value    = (0.35, 0.55, 0.85, 1.0)
                bg.inputs["Strength"].default_value = 0.8
            scene.world = world
        except Exception:
            pass
        report["warnings"].append(f"Nishita não disponível: {e}. Fallback azul simples.")

    report["lights_created"] = lights_ok
    print(f"  [iluminação] preset=daylight  luzes={lights_ok}")

def _setup_three_point(scene: Any,
                       bounds: Tuple[float,float,float,float,float,float],
                       cam_obj: Any, preset: str, report: Dict[str, Any]) -> None:
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn+xmx)/2; cy = (ymn+ymx)/2; cz = (zmn+zmx)/2
    h  = zmx - zmn or 2.0
    r  = max(xmx-xmn, ymx-ymn) or 1.0
    area_size = max(h * 0.8, 0.5)
    cam_dist  = (mathutils.Vector(cam_obj.location) - mathutils.Vector((cx,cy,cz))).length
    e_scale   = max(1.0, (cam_dist / 3.0) ** 2)
    lights_ok = 0

    def _add_area(name, loc, base_e, color=(1.,1.,1.)):
        nonlocal lights_ok
        try:
            bpy.ops.object.light_add(type="AREA", location=loc)
            lt = bpy.context.active_object
            lt.name = name; lt.data.energy = base_e * e_scale
            lt.data.size = area_size; lt.data.color = color
            direction = mathutils.Vector((cx,cy,cz)) - lt.location
            lt.rotation_euler = direction.to_track_quat("-Z","Y").to_euler()
            lights_ok += 1
        except Exception as e:
            report["warnings"].append(f"luz {name} falhou: {e}")

    if preset == "cinematic":
        _add_area("KeyLight",  (cx+r*1.5, cy-r*1.2, cz+h*0.9), 80.0, (1.0,0.97,0.90))
        _add_area("FillLight", (cx-r*1.8, cy-r*0.5, cz+h*0.4), 18.0, (0.85,0.90,1.0))
        _add_area("RimLight",  (cx-r*0.3, cy+r*2.0, cz+h*1.1), 45.0, (0.7,0.8,1.0))
        world = bpy.data.worlds.new("CinWorld"); world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg: bg.inputs["Color"].default_value = (0.03,0.03,0.05,1.0); bg.inputs["Strength"].default_value = 0.05
        scene.world = world
    else:
        _add_area("KeyLight",  (cx+r*1.4, cy-r*1.0, cz+h*0.8), 70.0, (1.0,0.98,0.95))
        _add_area("FillLight", (cx-r*1.6, cy-r*0.4, cz+h*0.5), 40.0, (0.95,0.97,1.0))
        _add_area("RimLight",  (cx-r*0.2, cy+r*1.8, cz+h*1.0), 35.0, (1.0,1.0,1.0))
        world = bpy.data.worlds.new("StudioWorld"); world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg: bg.inputs["Color"].default_value = (0.20,0.21,0.24,1.0); bg.inputs["Strength"].default_value = 0.25
        scene.world = world

    report["lights_created"] = lights_ok
    print(f"  [iluminação] preset={preset}  luzes={lights_ok}")


# ─────────────────────────────────────────────────────────────────────────────
# CHÃO
# ─────────────────────────────────────────────────────────────────────────────

def _add_ground_plane(scene: Any,
                      bounds: Tuple[float,float,float,float,float,float],
                      style: str, report: Dict[str, Any]) -> float:
    """Cria plano de chão e devolve o Z do chão."""
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn+xmx)/2; cy = (ymn+ymx)/2
    ground_z   = zmn - 0.005
    plane_size = max((xmx-xmn)*3.0, (ymx-ymn)*3.0, 10.0)

    try:
        bpy.ops.mesh.primitive_plane_add(size=plane_size, location=(cx, cy, ground_z))
        plane = bpy.context.active_object
        plane.name = "SceneGround"
        mat = bpy.data.materials.new("GroundMat_Scene")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            col = {"daylight": (0.22,0.20,0.16,1.0), "cinematic": (0.06,0.06,0.08,1.0)}.get(style, (0.28,0.28,0.30,1.0))
            bsdf.inputs["Base Color"].default_value = col
            bsdf.inputs["Roughness"].default_value = 0.92
            for k in ("Specular IOR Level", "Specular"):
                try: bsdf.inputs[k].default_value = 0.02; break
                except KeyError: pass
        if plane.data.materials: plane.data.materials[0] = mat
        else: plane.data.materials.append(mat)
        plane.visible_shadow = True
        report["ground_created"] = True
        report["ground_z"] = round(ground_z, 4)
        print(f"  [chão] z={ground_z:.4f}  tamanho={plane_size:.2f}m")
    except Exception as e:
        report["warnings"].append(f"chão não criado: {e}")
        ground_z = zmn

    return ground_z


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE RENDER
# ─────────────────────────────────────────────────────────────────────────────

def _setup_render(scene: Any, resolution: int, fps: int) -> None:
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try: scene.render.engine = eng; break
        except Exception: continue
    engine = scene.render.engine
    if "EEVEE" in engine and hasattr(scene, "eevee"):
        ev = scene.eevee
        for attr, val in [("taa_render_samples",16),("use_bloom",False),("use_gtao",True),("use_ssr",False)]:
            if hasattr(ev, attr):
                try: setattr(ev, attr, val)
                except Exception: pass
    elif "CYCLES" in engine:
        scene.cycles.samples = 32; scene.cycles.use_denoising = False; scene.cycles.device = "CPU"
    scene.render.resolution_x = resolution; scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100; scene.render.fps = fps
    scene.render.image_settings.file_format = "PNG"; scene.render.film_transparent = False
    try: scene.view_settings.view_transform = "Filmic"; scene.view_settings.look = "None"
    except Exception: pass
    print(f"  [render] motor={engine}  res={resolution}×{resolution}  fps={fps}")


# ─────────────────────────────────────────────────────────────────────────────
# EXPORTAÇÃO GLB
# ─────────────────────────────────────────────────────────────────────────────

def _export_glb(output_path: pathlib.Path, report: Dict[str, Any]) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.export_scene.gltf(
            filepath=str(output_path), export_format="GLB",
            export_animations=True, export_skins=True,
            export_materials="EXPORT", export_cameras=False,
            export_lights=False, use_selection=False)
    except Exception:
        report["errors"].append(f"exportação GLB falhou:\n{traceback.format_exc(limit=6)}")
        return False
    if not output_path.is_file() or output_path.stat().st_size < 100:
        report["errors"].append(f"GLB exportado mas parece inválido: {output_path}")
        return False
    size_kb = output_path.stat().st_size // 1024
    report["glb_exported"] = True; report["glb_path"] = str(output_path)
    print(f"  [GLB] exportado: {output_path}  ({size_kb} KB)")
    try:
        before = {o.name for o in bpy.context.scene.objects}
        bpy.ops.import_scene.gltf(filepath=str(output_path))
        new_objs = [o for o in bpy.context.scene.objects if o.name not in before]
        if new_objs:
            report["glb_importable"] = True
            for o in new_objs: bpy.data.objects.remove(o, do_unlink=True)
            print(f"  [GLB] reimportação OK — {len(new_objs)} objetos")
        else:
            report["warnings"].append("GLB reimportado mas sem objetos novos")
    except Exception as e:
        report["warnings"].append(f"GLB reimportação de teste falhou: {e}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-config",     required=True)
    ap.add_argument("--output-dir",       required=True)
    ap.add_argument("--report",           required=True)
    ap.add_argument("--library-root",     default="")
    ap.add_argument("--render-preview",   action="store_true")
    ap.add_argument("--render-animation", action="store_true")
    ap.add_argument("--export-glb",       action="store_true")
    ap.add_argument("--resolution",       type=int, default=640)
    ap.add_argument("--max-frames",       type=int, default=26)
    ap.add_argument("--fps",              type=int, default=24)
    args = ap.parse_args(_argv)

    if args.library_root:
        _set_library_root(pathlib.Path(args.library_root))

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = pathlib.Path(args.scene_config)
    if not cfg_path.is_file():
        print(f"ERRO: ficheiro não encontrado: {cfg_path}", file=sys.stderr)
        return 1
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERRO: JSON inválido: {e}", file=sys.stderr)
        return 1

    scene_name = cfg.get("scene_name", "scene")
    report = _empty_report(scene_name)
    report["camera_mode"]     = cfg.get("camera", {}).get("mode", "cinematic")
    report["lighting_preset"] = cfg.get("lighting", {}).get("preset", "daylight")
    require_scene_assets: bool = bool(cfg.get("require_scene_assets", False))

    errs = _validate_scene_config(cfg)
    if errs:
        for e in errs: report["errors"].append(f"config inválida: {e}")
        _save_report(report, args.report); return 1

    output_cfg       = cfg.get("output", {})
    resolution       = max(args.resolution, output_cfg.get("resolution", 640))
    fps              = output_cfg.get("fps", args.fps)
    max_frames       = min(args.max_frames, output_cfg.get("max_frames", 72), 300)
    render_preview   = args.render_preview   or ("png" in output_cfg.get("formats", []))
    render_animation = args.render_animation or ("mp4" in output_cfg.get("formats", []))
    do_export_glb    = args.export_glb       or ("glb" in output_cfg.get("formats", []))

    lib_root = (pathlib.Path(args.library_root).resolve()
                if args.library_root
                else pathlib.Path(__file__).resolve().parents[1])
    resolved = _resolve_assets_from_config(cfg, lib_root, report)
    if report["errors"]:
        _save_report(report, args.report); return 1

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception:
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save_report(report, args.report); return 1

    scene = bpy.context.scene
    cols  = _make_collections()
    lighting_preset = cfg.get("lighting", {}).get("preset", "daylight")
    env_assets = cfg.get("environment", {}).get("assets", [])

    # ── PASSO 1: Importar e posicionar assets de ambiente ─────────────────
    imported_env_count = 0
    env_import_errors: List[str] = []
    env_objects_all: List[Any] = []

    for env_asset in env_assets:
        asset_id = env_asset["asset_id"]
        position = env_asset.get("position", [0.0, 0.0, 0.0])
        rot_deg  = env_asset.get("rotation", [0.0, 0.0, 0.0])
        scale    = env_asset.get("scale",    [1.0, 1.0, 1.0])

        if asset_id not in resolved:
            err_msg = f"asset de ambiente '{asset_id}' não resolvido"
            env_import_errors.append(err_msg); report["errors"].append(err_msg)
            continue

        ok, err, objects = _import_glb(resolved[asset_id])
        if not ok:
            err_msg = f"falha ao importar '{asset_id}': {err}"
            env_import_errors.append(err_msg); report["errors"].append(err_msg)
            continue

        col_name = "ENVIRONMENT"
        name_lo  = asset_id.lower()
        if any(k in name_lo for k in ("road", "street")): col_name = "ROADS"
        elif any(k in name_lo for k in ("tree", "bush", "plant", "vegetation")): col_name = "VEGETATION"
        elif any(k in name_lo for k in ("building", "house", "edif", "tower")): col_name = "BUILDINGS"
        elif any(k in name_lo for k in ("prop", "barrel", "chest", "bench")): col_name = "PROPS"

        _move_to_collection(objects, cols[col_name])

        # Posicionar ambiente: base ao solo (Z=0), centro XY na posição pedida
        _apply_transform_to_ground(
            objects,
            target_xy=(position[0], position[1]),
            ground_z=position[2],  # Z do preset = Z do solo pretendido para o prédio
            rotation_deg=rot_deg,
            scale=scale,
        )

        env_objects_all.extend(objects)
        imported_env_count += 1
        print(f"  [ambiente] '{asset_id}' importado → coleção {col_name}")

    report["scene_assets_imported"] = imported_env_count

    if require_scene_assets and env_assets and imported_env_count == 0:
        msg = (f"ERRO DE INTEGRIDADE: '{scene_name}' requer ambiente mas nenhum importado. "
               f"Erros: {env_import_errors}")
        report["errors"].append(msg)
        print(f"\n{'='*64}\n{msg}\n{'='*64}\n", file=sys.stderr)
        _save_report(report, args.report); return 1

    # ── PASSO 2: Medir bounds do ambiente e determinar o solo real ────────
    building_bounds: Optional[Tuple] = None
    ground_z: float = 0.0  # padrão se não houver prédio

    if env_objects_all:
        bpy.context.view_layer.update()
        building_bounds = _world_bounds_of(env_objects_all)
        if building_bounds:
            bxmn, bxmx, bymn, bymx, bzmn, bzmx = building_bounds
            ground_z = bzmn  # solo = base real do prédio
            report["building_world_bounds"] = {
                "min": [round(bxmn,4), round(bymn,4), round(bzmn,4)],
                "max": [round(bxmx,4), round(bymx,4), round(bzmx,4)],
            }
            print(f"  [bounds prédio] X=[{bxmn:.2f},{bxmx:.2f}] "
                  f"Y=[{bymn:.2f},{bymx:.2f}] Z=[{bzmn:.2f},{bzmx:.2f}]  "
                  f"solo={ground_z:.4f}")

    # ── PASSO 3: Criar chão ao nível do solo real ─────────────────────────
    # O chão é criado ANTES de importar o personagem para que o solo seja correcto
    # Usar bounds do prédio se disponível, senão bounds de cena
    has_road_assets = any(
        any(k in a["asset_id"].lower() for k in ("road","street","floor","pavement"))
        for a in env_assets
    )
    if not has_road_assets:
        if building_bounds:
            ground_z = _add_ground_plane(scene, building_bounds, lighting_preset, report)
        else:
            # Sem prédio — chão em Z=0
            dummy_bounds = (-3.0, 3.0, -3.0, 3.0, 0.0, 0.0)
            ground_z = _add_ground_plane(scene, dummy_bounds, lighting_preset, report)

    report["ground_z"] = round(ground_z, 4)

    # ── PASSO 4: Importar personagens e posicionar ao lado do prédio ──────
    char_objects_all: List[Any] = []
    char_arm_obj: Optional[Any] = None
    char_action:  Optional[Any] = None

    for char_cfg in cfg.get("characters", []):
        asset_id  = char_cfg["asset_id"]
        anim_name = char_cfg.get("animation", "")

        if asset_id not in resolved:
            report["errors"].append(f"personagem '{asset_id}' não resolvida"); continue

        ok, err, objects = _import_glb(resolved[asset_id])
        if not ok:
            report["errors"].append(f"falha ao importar personagem '{asset_id}': {err}"); continue

        _move_to_collection(objects, cols["CHARACTERS"])

        # Posicionamento do personagem:
        # SE existe prédio → usar _place_character_beside_building (baseado em bounds reais)
        # SE não existe prédio → colocar em Z=ground_z, XY=(0,0)
        if building_bounds is not None:
            # Primeiro mover para uma posição temporária (longe do prédio) para medir bounds
            _apply_transform_to_ground(
                objects,
                target_xy=(0.0, 0.0),
                ground_z=ground_z,
                rotation_deg=[0.0, 0.0, 0.0],
                scale=[1.0, 1.0, 1.0],
            )
            # Agora posicionar ao lado do prédio com base em bounds reais
            _place_character_beside_building(objects, building_bounds, ground_z, report)
        else:
            # Sem prédio — colocar no centro ao nível do solo
            _apply_transform_to_ground(
                objects,
                target_xy=(0.0, 0.0),
                ground_z=ground_z,
                rotation_deg=[0.0, 0.0, 0.0],
                scale=[1.0, 1.0, 1.0],
            )

        char_objects_all.extend(objects)
        report["character_imported"] = True

        arm_obj = _get_armature_from(objects)
        if arm_obj and anim_name:
            action = _find_action(anim_name)
            if action:
                try:
                    _assign_action(arm_obj, action)
                    report["character_animated"] = True
                    char_arm_obj = arm_obj
                    char_action  = action
                    _check_animation_motion(action, report)
                    print(f"  [personagem] '{asset_id}' animada com '{action.name}'")
                except Exception as e:
                    report["warnings"].append(f"animação '{anim_name}' não aplicada: {e}")
            else:
                avail = [a.name for a in bpy.data.actions[:5]]
                report["warnings"].append(
                    f"animação '{anim_name}' não encontrada. Disponíveis: {avail}")

    # ── PASSO 5: Câmara enquadra personagem + prédio ──────────────────────
    bpy.context.view_layer.update()
    all_bounds = _scene_bounds()
    cam_mode   = cfg.get("camera", {}).get("mode", "cinematic")

    # Para cinematic: câmara centrada na cena completa (prédio + personagem)
    cam_obj = _place_camera_for_bounds(scene, all_bounds, cam_mode, report)
    for col in list(cam_obj.users_collection):
        col.objects.unlink(cam_obj)
    cols["CAMERAS"].objects.link(cam_obj)

    # ── PASSO 6: Iluminação ───────────────────────────────────────────────
    if lighting_preset == "daylight":
        _setup_daylight(scene, all_bounds, report)
    else:
        _setup_three_point(scene, all_bounds, cam_obj, lighting_preset, report)

    # ── PASSO 7: Render ───────────────────────────────────────────────────
    _setup_render(scene, resolution, fps)

    # Ajustar câmara para enquadrar cena completa (prédio + personagem)
    objects_to_frame = [o for o in (env_objects_all + char_objects_all) if o.type == "MESH"]
    if objects_to_frame:
        _adjust_camera_framing(scene, cam_obj, objects_to_frame,
                                margin=0.08, max_iter=8, report=report)

    # Frames
    if char_action:
        fr_start = int(char_action.frame_range[0])
        fr_end   = int(char_action.frame_range[1])
    else:
        fr_start, fr_end = 1, max_frames

    total_frames = max(1, fr_end - fr_start + 1)
    if total_frames <= max_frames:
        frames_to_render = list(range(fr_start, fr_end + 1))
    else:
        step = total_frames / max_frames
        frames_to_render = [fr_start + int(i * step) for i in range(max_frames)]

    scene.frame_start = fr_start; scene.frame_end = fr_end

    # Preview PNG
    if render_preview:
        mid_frame    = frames_to_render[len(frames_to_render) // 2]
        preview_path = output_dir / f"{scene_name}_preview.png"
        scene.frame_set(mid_frame)
        bpy.context.view_layer.update()
        scene.render.filepath = str(preview_path)
        t0 = time.time()
        try:
            bpy.ops.render.render(write_still=True)
            if preview_path.is_file() and preview_path.stat().st_size > 0:
                report["preview_success"] = True
                report["preview_path"]    = str(preview_path)
                print(f"  [PNG] preview OK: {preview_path}")
            else:
                report["errors"].append("PNG de preview não foi gerado")
        except Exception:
            report["errors"].append(f"erro ao renderizar preview:\n{traceback.format_exc(limit=5)}")
        report["render_time_s"] = round(time.time() - t0, 1)

    # Animação → MP4
    if render_animation:
        frames_dir = output_dir / "frames_tmp"
        frames_dir.mkdir(parents=True, exist_ok=True)
        rendered_paths: List[pathlib.Path] = []
        t0 = time.time()
        for i, frame in enumerate(frames_to_render):
            fp = frames_dir / f"frame_{i:04d}.png"
            scene.render.filepath = str(fp)
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            try:
                bpy.ops.render.render(write_still=True)
                if fp.is_file() and fp.stat().st_size > 0:
                    rendered_paths.append(fp)
            except Exception:
                report["warnings"].append(f"frame {frame} não renderizado:\n{traceback.format_exc(limit=3)}")
                break
        report["frames_rendered"]  = len(rendered_paths)
        report["frames_requested"] = len(frames_to_render)
        report["render_time_s"]    = round(time.time() - t0, 1)
        if rendered_paths:
            import subprocess
            video_path = output_dir / f"{scene_name}.mp4"
            ffmpeg_cmd = ["ffmpeg","-y","-framerate",str(fps),"-i",
                          str(frames_dir/"frame_%04d.png"),"-c:v","libx264",
                          "-pix_fmt","yuv420p","-preset","fast","-crf","23",
                          "-movflags","+faststart",str(video_path)]
            try:
                r = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=300)
                if r.returncode == 0 and video_path.is_file():
                    report["video_path"] = str(video_path)
                    print(f"  [MP4] gerado: {video_path}")
                else:
                    report["errors"].append(f"FFmpeg falhou: {r.stderr[:500]}")
            except Exception as e:
                report["errors"].append(f"FFmpeg não executou: {e}")
            import shutil; shutil.rmtree(frames_dir, ignore_errors=True)
        else:
            report["errors"].append("nenhum frame renderizado — MP4 não gerado")

    # GLB
    if do_export_glb:
        _export_glb(output_dir / f"{scene_name}.glb", report)

    _save_report(report, args.report)
    _print_summary(report)
    return 1 if report["errors"] else 0


def _print_summary(report: Dict[str, Any]) -> None:
    r = report
    chk = lambda b: "✅" if b else "❌"
    print("\n" + "=" * 64)
    print(f"CENA 3D: {r['scene_name']}  (Blender {r['blender_version']})")
    print("=" * 64)
    print(f"  assets de ambiente:   {r['scene_assets_imported']}")
    print(f"  personagem:           {chk(r['character_imported'])}")
    print(f"  animação:             {chk(r['character_animated'])}")
    print(f"  ossos em movimento:   {r.get('moving_bones', 0)}")
    print(f"  câmara ({r['camera_mode']}):   {chk(r['camera_created'])}")
    print(f"  enquadramento:        {chk(r['framing_success'])}")
    print(f"  iluminação ({r['lighting_preset']}): {r['lights_created']} luzes")
    print(f"  chão:                 {chk(r['ground_created'])}  z={r.get('ground_z')}")
    print(f"  preview PNG:          {chk(r['preview_success'])}")
    # Dados espaciais
    bb = r.get("building_world_bounds")
    cb = r.get("character_world_bounds")
    if bb:
        print(f"  prédio Z:             [{bb['min'][2]:.2f}, {bb['max'][2]:.2f}]")
    if cb:
        print(f"  personagem Z:         [{cb['min'][2]:.2f}, {cb['max'][2]:.2f}]")
    if r.get("character_ground_clearance") is not None:
        print(f"  ground clearance:     {r['character_ground_clearance']:.4f} m")
    if r.get("character_building_overlap") is not None:
        print(f"  overlap c/ prédio:    {chk(not r['character_building_overlap'])}")
    if r.get("character_not_above_building") is not None:
        print(f"  não está em cima:     {chk(r['character_not_above_building'])}")
    print(f"  placement validado:   {chk(r.get('placement_validated', False))}")
    if r.get("frames_requested"):
        print(f"  frames:               {r['frames_rendered']}/{r['frames_requested']}")
    if r.get("video_path"):   print(f"  MP4:                  ✅ {r['video_path']}")
    if r.get("glb_exported"): print(f"  GLB:                  ✅ {r['glb_path']}")
    if r["errors"]:
        print("\nERROS:")
        for e in r["errors"]: print(f"  ! {str(e)[:300]}")
    if r["warnings"]:
        print("\nAVISOS:")
        for w in r["warnings"]: print(f"  ~ {w}")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    sys.exit(main())
