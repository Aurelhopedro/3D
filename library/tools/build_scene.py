#!/usr/bin/env python3
"""Montador de Cenas 3D — Fase 5.

Constrói uma cena Blender a partir de um JSON:
  - importa ambientes + personagens (GLTF/GLB)
  - normaliza escala (unidades cm→m quando necessário)
  - posiciona personagem ao lado do prédio (bounds reais)
  - iluminação daylight / studio / cinematic
  - câmara enquadra toda a cena
  - renderiza PNG e/ou MP4
  - exporta GLB

Blender 5.x: usa comparação de matrizes de pose para verificar movimento
(Action.fcurves não existe no 5.x — substituído por Action.layers/strips/channelbag).
"""
from __future__ import annotations

import argparse
import json
import math
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
        raise ValueError(f"Caminho fora do sandbox: {p}")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO
# ─────────────────────────────────────────────────────────────────────────────

def _empty_report(scene_name: str) -> Dict[str, Any]:
    return {
        "scene_name":                  scene_name,
        "blender_version":             ".".join(str(v) for v in bpy.app.version),
        "scene_assets_imported":       0,
        "character_imported":          False,
        "character_animated":          False,
        "animation_motion_confirmed":  False,
        "moving_bones":                0,
        "camera_mode":                 "cinematic",
        "camera_created":              False,
        "lighting_preset":             "daylight",
        "lights_created":              0,
        "ground_created":              False,
        "ground_z":                    None,
        "frames_rendered":             0,
        "frames_requested":            0,
        "preview_success":             False,
        "preview_path":                None,
        "video_path":                  None,
        "glb_exported":                False,
        "glb_path":                    None,
        "glb_importable":              False,
        "render_time_s":               None,
        "framing_success":             False,
        # Escala
        "character_scale_applied":     None,
        "building_scale_applied":      None,
        # Dados espaciais
        "building_world_bounds":       None,
        "character_world_bounds":      None,
        "character_position":          None,
        "character_ground_clearance":  None,
        "character_building_overlap":  None,
        "character_not_above_building": None,
        "placement_validated":         False,
        "errors":                      [],
        "warnings":                    [],
        "asset_details":               [],
    }

def _save_report(report: Dict[str, Any], path: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ─────────────────────────────────────────────────────────────────────────────
# VALIDAÇÃO DO JSON
# ─────────────────────────────────────────────────────────────────────────────

def _validate_scene_config(cfg: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(cfg.get("scene_name"), str) or not cfg["scene_name"]:
        errors.append("scene_name obrigatório")
    for i, a in enumerate(cfg.get("environment", {}).get("assets", [])):
        if not isinstance(a.get("asset_id"), str):
            errors.append(f"environment.assets[{i}]: asset_id obrigatório")
    for i, c in enumerate(cfg.get("characters", [])):
        if not isinstance(c.get("asset_id"), str):
            errors.append(f"characters[{i}]: asset_id obrigatório")
    cam = cfg.get("camera", {})
    if cam.get("mode") not in (None, "character_focus", "scene_overview", "cinematic"):
        errors.append("camera.mode inválido")
    lit = cfg.get("lighting", {})
    if lit.get("preset") not in (None, "daylight", "studio", "cinematic"):
        errors.append("lighting.preset inválido")
    out = cfg.get("output", {})
    if not (128 <= out.get("resolution", 640) <= 2048):
        errors.append("output.resolution deve ser 128-2048")
    if not (1 <= out.get("fps", 24) <= 60):
        errors.append("output.fps deve ser 1-60")
    if not (1 <= out.get("max_frames", 26) <= 300):
        errors.append("output.max_frames deve ser 1-300")
    return errors


# ─────────────────────────────────────────────────────────────────────────────
# RESOLVER DE ASSETS
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_asset_glb(asset_id: str, library_root: pathlib.Path,
                       category_hint: str = "") -> Optional[pathlib.Path]:
    assets_root = library_root / "assets"
    def _find(adir: pathlib.Path) -> Optional[pathlib.Path]:
        if not adir.is_dir():
            return None
        for ext in (".glb", ".gltf"):
            m = sorted(adir.rglob(f"*{ext}"))
            if m:
                return m[0]
        return None
    if category_hint:
        p = _find(assets_root / category_hint / asset_id)
        if p:
            return p
    if assets_root.is_dir():
        for cat in sorted(assets_root.iterdir()):
            if cat.is_dir():
                p = _find(cat / asset_id)
                if p:
                    return p
    return None

def _resolve_assets_from_config(cfg: Dict[str, Any], lib_root: pathlib.Path,
                                 report: Dict[str, Any]) -> Dict[str, pathlib.Path]:
    needed: Dict[str, str] = {}
    for a in cfg.get("environment", {}).get("assets", []):
        needed[a["asset_id"]] = "environments"
    for c in cfg.get("characters", []):
        needed[c["asset_id"]] = "characters"
    resolved: Dict[str, pathlib.Path] = {}
    for aid, cat in needed.items():
        path = _resolve_asset_glb(aid, lib_root, cat)
        if path is None:
            report["errors"].append(f"Asset '{aid}' não encontrado em library/assets/")
        else:
            try:
                resolved[aid] = _safe_asset_path(str(path))
                report["asset_details"].append({"asset_id": aid, "path": str(path), "found": True})
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
        cols[name] = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    sc = scene.collection
    if cols["SCENE"].name not in [c.name for c in sc.children]:
        sc.children.link(cols["SCENE"])
    for sub in ("ENVIRONMENT","BUILDINGS","ROADS","VEGETATION","PROPS","CHARACTERS","LIGHTING","CAMERAS"):
        p = cols["SCENE"]; ch = cols[sub]
        if ch.name not in [c.name for c in p.children]:
            p.children.link(ch)
    return cols

def _move_to_collection(objects: List[Any], target_col: Any) -> None:
    for obj in objects:
        for col in list(obj.users_collection):
            col.objects.unlink(obj)
        target_col.objects.link(obj)


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _import_glb(path: pathlib.Path) -> Tuple[bool, str, List[Any]]:
    if not path.is_file():
        return False, f"não encontrado: {path}", []
    if path.stat().st_size == 0:
        return False, f"vazio: {path}", []
    before = {o.name for o in bpy.context.scene.objects}
    try:
        r = bpy.ops.import_scene.gltf(filepath=str(path))
        if r != {"FINISHED"}:
            return False, f"importação retornou: {r}", []
    except Exception:
        return False, traceback.format_exc(limit=8), []
    new_objs = [o for o in bpy.context.scene.objects if o.name not in before]
    return True, "", new_objs


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZAÇÃO DE ESCALA
# ─────────────────────────────────────────────────────────────────────────────
# O KayKit Adventurers (personagem) usa centímetros: altura real ~170 cm
#   → depois de importar como GLTF fica ~170 unidades Blender.
# O KayKit City Builder (ambiente) usa metros: edifício ~1.6–3 unidades Blender.
# Solução: detectar automaticamente se o asset está em cm (altura > 10 unidades)
#   e escalar por 0.01 para converter para metros.
#
# LIMITE DE DETECÇÃO: se bounding box Z > 10 unidades assumimos que está em cm.
# Valores reais do Knight: Z ≈ 170 u (cm) → escalar × 0.01 → 1.70 m ✓
# Valores reais building_A: Z ≈ 1.65 u (m) → sem escala ✓

_CM_TO_M_THRESHOLD = 10.0   # acima disto assumimos que o asset está em cm
_CM_TO_M_FACTOR    = 0.01


def _world_bounds_of(objects: List[Any]) -> Optional[Tuple[float,float,float,float,float,float]]:
    """Bounding box world-space de uma lista de objectos (após update de transforms)."""
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


def _auto_scale_objects(objects: List[Any], asset_id: str,
                         report: Dict[str, Any], role: str) -> float:
    """Detecta e corrige unidades cm→m. Devolve o factor aplicado (1.0 ou 0.01).

    Lógica:
      - Medir bounding box Z após importação.
      - Se altura > _CM_TO_M_THRESHOLD → asset provavelmente em centímetros.
      - Aplicar escala × 0.01 a todos os objectos raiz.
      - Actualizar view_layer para que as medições seguintes sejam correctas.
    """
    bpy.context.view_layer.update()
    b = _world_bounds_of(objects)
    if b is None:
        return 1.0

    height_z = b[5] - b[4]  # zmx - zmn
    factor = 1.0

    if height_z > _CM_TO_M_THRESHOLD:
        factor = _CM_TO_M_FACTOR
        for obj in objects:
            if obj.parent is None:
                obj.scale *= factor
        bpy.context.view_layer.update()
        b2 = _world_bounds_of(objects)
        new_h = (b2[5] - b2[4]) if b2 else height_z * factor
        msg = (f"[escala] '{asset_id}' ({role}): altura original={height_z:.2f} u "
               f"→ escalado ×{factor} → {new_h:.2f} m")
        print(f"  {msg}")
        report["warnings"].append(msg)
    else:
        print(f"  [escala] '{asset_id}' ({role}): altura={height_z:.2f} m (sem escala)")

    if role == "character":
        report["character_scale_applied"] = factor
    else:
        report["building_scale_applied"]  = factor

    return factor


# ─────────────────────────────────────────────────────────────────────────────
# PLACEMENT — posicionar com base em bounds reais
# ─────────────────────────────────────────────────────────────────────────────

def _apply_transform_to_ground(objects: List[Any],
                                target_xy: Tuple[float, float],
                                ground_z: float,
                                rotation_deg: List[float],
                                scale_extra: List[float]) -> None:
    """Move grupo para que base (min_z) fique em ground_z e centro XY em target_xy.
    Aplica rotação e escala extra antes de calcular offset.
    """
    if not objects:
        return
    for obj in objects:
        if obj.parent is None:
            obj.scale.x *= scale_extra[0]
            obj.scale.y *= scale_extra[1]
            obj.scale.z *= scale_extra[2]
            obj.rotation_euler.x += math.radians(rotation_deg[0])
            obj.rotation_euler.y += math.radians(rotation_deg[1])
            obj.rotation_euler.z += math.radians(rotation_deg[2])
    bpy.context.view_layer.update()
    b = _world_bounds_of(objects)
    if b is None:
        return
    cx_cur = (b[0] + b[1]) / 2.0
    cy_cur = (b[2] + b[3]) / 2.0
    base_z = b[4]
    offset_x = target_xy[0] - cx_cur
    offset_y = target_xy[1] - cy_cur
    offset_z = ground_z - base_z
    for obj in objects:
        if obj.parent is None:
            obj.location.x += offset_x
            obj.location.y += offset_y
            obj.location.z += offset_z
    bpy.context.view_layer.update()


def _place_character_beside_building(
        char_objects: List[Any],
        building_bounds: Tuple[float,float,float,float,float,float],
        ground_z: float,
        report: Dict[str, Any],
) -> None:
    """Posiciona o personagem ao lado do prédio, no solo, sem overlap.

    1. Mede footprint XY real do prédio.
    2. Calcula posição exterior + safety_gap.
    3. Alinha base do personagem ao ground_z.
    4. Valida overlap e ground clearance.
    """
    if not char_objects:
        return

    bxmn, bxmx, bymn, bymx, bzmn, bzmx = building_bounds
    b_cx = (bxmn + bxmx) / 2.0
    b_cy = (bymn + bymx) / 2.0
    b_dx = bxmx - bxmn
    b_dy = bymx - bymn

    # Safety gap: mínimo de 0.5 m, máximo de 1× a dimensão do prédio
    # Suficiente para separar claramente, pequeno o suficiente para enquadrar bem
    safety_gap = max(0.5, min(max(b_dx, b_dy), 2.0))

    # Colocar ao lado direito do prédio (+X)
    char_target_x = bxmx + safety_gap
    char_target_y = b_cy

    print(f"  [placement] prédio: X=[{bxmn:.3f},{bxmx:.3f}] "
          f"Y=[{bymn:.3f},{bymx:.3f}] Z=[{bzmn:.3f},{bzmx:.3f}]")
    print(f"  [placement] safety_gap={safety_gap:.3f}  "
          f"alvo: X={char_target_x:.3f} Y={char_target_y:.3f} Z(solo)={ground_z:.4f}")

    _apply_transform_to_ground(
        char_objects,
        target_xy=(char_target_x, char_target_y),
        ground_z=ground_z,
        rotation_deg=[0.0, 0.0, 0.0],
        scale_extra=[1.0, 1.0, 1.0],
    )

    bpy.context.view_layer.update()
    char_b = _world_bounds_of(char_objects)
    if char_b is None:
        report["warnings"].append("Não foi possível medir bounds do personagem após placement")
        return

    cxmn, cxmx, cymn, cymx, czmn, czmx = char_b
    ground_clearance = round(czmn - ground_z, 4)
    report["character_ground_clearance"] = ground_clearance
    report["character_position"] = [round((cxmn+cxmx)/2,4), round((cymn+cymx)/2,4), round(czmn,4)]
    report["character_world_bounds"] = {
        "min": [round(cxmn,4), round(cymn,4), round(czmn,4)],
        "max": [round(cxmx,4), round(cymx,4), round(czmx,4)],
    }

    overlap_x = not (cxmx <= bxmn or cxmn >= bxmx)
    overlap_y = not (cymx <= bymn or cymn >= bymx)
    overlap_z = not (czmx <= bzmn or czmn >= bzmx)
    has_overlap = overlap_x and overlap_y and overlap_z
    report["character_building_overlap"] = has_overlap

    above_building = (czmn > bzmx) and overlap_x and overlap_y
    report["character_not_above_building"] = not above_building

    ground_ok = abs(ground_clearance) <= 0.05
    placement_ok = (not has_overlap) and (not above_building) and ground_ok
    report["placement_validated"] = placement_ok

    print(f"  [placement] personagem: X=[{cxmn:.3f},{cxmx:.3f}] "
          f"Y=[{cymn:.3f},{cymx:.3f}] Z=[{czmn:.3f},{czmx:.3f}]")
    print(f"  [placement] clearance={ground_clearance:.4f}  "
          f"overlap={has_overlap}  above={above_building}  ok={placement_ok}")

    if has_overlap:
        report["errors"].append(
            f"PLACEMENT: personagem intersecta prédio. "
            f"char_X=[{cxmn:.3f},{cxmx:.3f}] vs building_X=[{bxmn:.3f},{bxmx:.3f}]")
    if above_building:
        report["errors"].append(
            f"PLACEMENT: personagem em cima do prédio. "
            f"char_Z_min={czmn:.3f} > building_Z_max={bzmx:.3f}")
    if not ground_ok:
        report["warnings"].append(
            f"Ground clearance fora de tolerância: {ground_clearance:.4f} m (tol ±0.05 m)")


# ─────────────────────────────────────────────────────────────────────────────
# ANIMAÇÃO — compatível com Blender 3.6 / 4.x / 5.x
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
                chosen = slot; break
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


def _check_animation_motion_by_pose(arm_obj: Any, fr_start: int, fr_end: int,
                                     report: Dict[str, Any]) -> None:
    """Verifica movimento real comparando matrizes de pose entre frames.

    COMPATÍVEL COM BLENDER 5.x: não usa Action.fcurves (removido no 5.x).
    Usa arm_obj.pose.bones[].matrix que funciona em todas as versões.
    """
    if fr_end <= fr_start:
        report["warnings"].append("Intervalo de frames inválido para verificar movimento")
        return

    # Amostrar frame inicial e um frame a 25% da animação
    fr_check = fr_start + max(1, (fr_end - fr_start) // 4)

    bpy.context.scene.frame_set(fr_start)
    bpy.context.view_layer.update()
    pose_start = {pb.name: [row[:] for row in pb.matrix] for pb in arm_obj.pose.bones}

    bpy.context.scene.frame_set(fr_check)
    bpy.context.view_layer.update()

    moving = set()
    for pb in arm_obj.pose.bones:
        ms = pose_start.get(pb.name)
        if ms is None:
            continue
        delta = max(abs(ms[ri][ci] - pb.matrix[ri][ci]) for ri in range(4) for ci in range(4))
        if delta > 1e-4:
            moving.add(pb.name)

    report["animation_motion_confirmed"] = len(moving) > 0
    report["moving_bones"] = len(moving)
    print(f"  [animação] ossos em movimento (frame {fr_start}→{fr_check}): {len(moving)}")
    if not moving:
        report["warnings"].append(
            f"Nenhum osso em movimento entre frames {fr_start} e {fr_check}. "
            f"Verificar se a ação foi aplicada correctamente.")


# ─────────────────────────────────────────────────────────────────────────────
# CÂMARA
# ─────────────────────────────────────────────────────────────────────────────

def _place_camera_for_bounds(scene: Any,
                              bounds: Tuple[float,float,float,float,float,float],
                              mode: str, report: Dict[str, Any]) -> Any:
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn+xmx)/2; cy = (ymn+ymx)/2; cz = (zmn+zmx)/2
    dx = xmx-xmn or 0.1; dy = ymx-ymn or 0.1; dz = zmx-zmn or 0.1
    radius   = math.sqrt(dx*dx + dy*dy + dz*dz) / 2.0
    fov_rad  = math.radians(46.8)
    cam_dist = (radius * 1.30) / math.sin(fov_rad / 2.0)

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
    direction = mathutils.Vector((cx, cy, cz)) - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z","Y").to_euler()
    cam_obj.data.lens       = 35.0   # grande angular para mostrar cena completa
    cam_obj.data.clip_start = 0.01
    cam_obj.data.clip_end   = cam_dist * 15.0
    scene.camera = cam_obj
    report["camera_created"] = True
    print(f"  [câmara] modo={mode}  dist={cam_dist:.2f}  focal=35mm  "
          f"centro=({cx:.2f},{cy:.2f},{cz:.2f})")
    return cam_obj

def _adjust_camera_framing(scene: Any, cam_obj: Any, objects_to_frame: List[Any],
                            margin: float = 0.08, max_iter: int = 8,
                            report: Dict[str, Any] = {}) -> None:
    if not objects_to_frame:
        return
    safe_lo = margin; safe_hi = 1.0 - margin
    for iteration in range(max_iter + 1):
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        axmn, axmx, aymn, aymx = math.inf, -math.inf, math.inf, -math.inf
        for obj in objects_to_frame:
            if obj.type != "MESH":
                continue
            try:
                oe = obj.evaluated_get(depsgraph)
                me = oe.to_mesh()
                if me is None:
                    continue
                mat = oe.matrix_world
                for v in me.vertices:
                    co = world_to_camera_view(scene, cam_obj, mat @ v.co)
                    if co.z > 0:
                        axmn = min(axmn,co.x); axmx = max(axmx,co.x)
                        aymn = min(aymn,co.y); aymx = max(aymx,co.y)
                oe.to_mesh_clear()
            except Exception:
                pass
        if math.isinf(axmn):
            break
        ok = axmn>=safe_lo and axmx<=safe_hi and aymn>=safe_lo and aymx<=safe_hi
        report["framing_success"] = ok
        if ok:
            print(f"  [enquadramento] OK  x=[{axmn:.3f},{axmx:.3f}]  y=[{aymn:.3f},{aymx:.3f}]")
            return
        if iteration == max_iter:
            report["warnings"].append(f"enquadramento não garantido após {max_iter} ajustes")
            return
        overshoot = max(
            safe_lo-axmn if axmn<safe_lo else 0, axmx-safe_hi if axmx>safe_hi else 0,
            safe_lo-aymn if aymn<safe_lo else 0, aymx-safe_hi if aymx>safe_hi else 0)
        factor = 1.0 + max(0.12, min(0.45, overshoot * 1.5))
        b = _world_bounds_of([o for o in objects_to_frame if o.type=="MESH"])
        if b:
            tgt = mathutils.Vector(((b[0]+b[1])/2,(b[2]+b[3])/2,(b[4]+b[5])/2))
        else:
            break
        d = (mathutils.Vector(cam_obj.location) - tgt)
        cam_obj.location = tgt + d.normalized() * (d.length * factor)
        print(f"  [enquadramento] ajuste {iteration+1}: factor={factor:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# ILUMINAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _setup_daylight(scene: Any, bounds: Tuple, report: Dict[str,Any]) -> None:
    xmn,xmx,ymn,ymx,zmn,zmx = bounds
    cx=(xmn+xmx)/2; cy=(ymn+ymx)/2; h=zmx-zmn or 2.0
    lights_ok = 0
    try:
        bpy.ops.object.light_add(type="SUN", location=(cx+5, cy-3, zmn+h*2.5))
        sun = bpy.context.active_object; sun.name="Sun_Key"
        sun.data.energy=3.5; sun.data.color=(1.0,0.97,0.88); sun.data.angle=math.radians(3.0)
        sun.rotation_euler = (mathutils.Vector((cx,cy,zmn+h*0.5))-sun.location).to_track_quat("-Z","Y").to_euler()
        lights_ok += 1
    except Exception as e:
        report["warnings"].append(f"Sol falhou: {e}")
    try:
        bpy.ops.object.light_add(type="AREA", location=(cx-4, cy+2, zmn+h*1.5))
        fill = bpy.context.active_object; fill.name="Sky_Fill"
        fill.data.energy=200.0; fill.data.size=max(h*2.0,4.0); fill.data.color=(0.55,0.70,1.0)
        fill.rotation_euler = (mathutils.Vector((cx,cy,zmn+h*0.5))-fill.location).to_track_quat("-Z","Y").to_euler()
        lights_ok += 1
    except Exception as e:
        report["warnings"].append(f"Fill falhou: {e}")
    try:
        world = bpy.data.worlds.new("DaylightWorld"); world.use_nodes = True
        nt = world.node_tree
        bg = nt.nodes.get("Background") or nt.nodes.new("ShaderNodeBackground")
        sky = nt.nodes.new("ShaderNodeTexSky"); sky.sky_type="NISHITA"
        sky.sun_elevation=math.radians(45.0); sky.sun_rotation=math.radians(20.0)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(tc.outputs["Generated"], sky.inputs["Vector"])
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 1.0
        out = nt.nodes.get("World Output") or nt.nodes.new("ShaderNodeOutputWorld")
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
        scene.world = world
    except Exception as e:
        try:
            world = bpy.data.worlds.new("DaylightFallback"); world.use_nodes=True
            bg2 = world.node_tree.nodes.get("Background")
            if bg2: bg2.inputs["Color"].default_value=(0.35,0.55,0.85,1.0); bg2.inputs["Strength"].default_value=0.8
            scene.world = world
        except Exception:
            pass
        report["warnings"].append(f"Nishita indisponível: {e}. Fallback azul.")
    report["lights_created"] = lights_ok

def _setup_three_point(scene: Any, bounds: Tuple, cam_obj: Any,
                        preset: str, report: Dict[str,Any]) -> None:
    xmn,xmx,ymn,ymx,zmn,zmx = bounds
    cx=(xmn+xmx)/2; cy=(ymn+ymx)/2; cz=(zmn+zmx)/2
    h=zmx-zmn or 2.0; r=max(xmx-xmn,ymx-ymn) or 1.0
    area_size=max(h*0.8,0.5)
    cam_dist=(mathutils.Vector(cam_obj.location)-mathutils.Vector((cx,cy,cz))).length
    e_scale=max(1.0,(cam_dist/3.0)**2)
    lights_ok = 0
    def _aa(name,loc,be,col=(1.,1.,1.)):
        nonlocal lights_ok
        try:
            bpy.ops.object.light_add(type="AREA",location=loc)
            lt=bpy.context.active_object; lt.name=name
            lt.data.energy=be*e_scale; lt.data.size=area_size; lt.data.color=col
            lt.rotation_euler=(mathutils.Vector((cx,cy,cz))-lt.location).to_track_quat("-Z","Y").to_euler()
            lights_ok+=1
        except Exception as e:
            report["warnings"].append(f"luz {name} falhou: {e}")
    if preset=="cinematic":
        _aa("KeyLight",(cx+r*1.5,cy-r*1.2,cz+h*0.9),80.0,(1.0,0.97,0.90))
        _aa("FillLight",(cx-r*1.8,cy-r*0.5,cz+h*0.4),18.0,(0.85,0.90,1.0))
        _aa("RimLight",(cx-r*0.3,cy+r*2.0,cz+h*1.1),45.0,(0.7,0.8,1.0))
        world=bpy.data.worlds.new("CinWorld"); world.use_nodes=True
        bg=world.node_tree.nodes.get("Background")
        if bg: bg.inputs["Color"].default_value=(0.03,0.03,0.05,1.0); bg.inputs["Strength"].default_value=0.05
        scene.world=world
    else:
        _aa("KeyLight",(cx+r*1.4,cy-r*1.0,cz+h*0.8),70.0,(1.0,0.98,0.95))
        _aa("FillLight",(cx-r*1.6,cy-r*0.4,cz+h*0.5),40.0,(0.95,0.97,1.0))
        _aa("RimLight",(cx-r*0.2,cy+r*1.8,cz+h*1.0),35.0,(1.0,1.0,1.0))
        world=bpy.data.worlds.new("StudioWorld"); world.use_nodes=True
        bg=world.node_tree.nodes.get("Background")
        if bg: bg.inputs["Color"].default_value=(0.20,0.21,0.24,1.0); bg.inputs["Strength"].default_value=0.25
        scene.world=world
    report["lights_created"] = lights_ok


# ─────────────────────────────────────────────────────────────────────────────
# CHÃO
# ─────────────────────────────────────────────────────────────────────────────

def _add_ground_plane(scene: Any, bounds: Tuple, style: str,
                       report: Dict[str,Any]) -> float:
    xmn,xmx,ymn,ymx,zmn,zmx = bounds
    cx=(xmn+xmx)/2; cy=(ymn+ymx)/2
    ground_z = zmn - 0.005
    plane_size = max((xmx-xmn)*3.0,(ymx-ymn)*3.0,10.0)
    try:
        bpy.ops.mesh.primitive_plane_add(size=plane_size, location=(cx,cy,ground_z))
        plane = bpy.context.active_object; plane.name="SceneGround"
        mat = bpy.data.materials.new("GroundMat"); mat.use_nodes=True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            col={"daylight":(0.22,0.20,0.16,1.0),"cinematic":(0.06,0.06,0.08,1.0)}.get(style,(0.28,0.28,0.30,1.0))
            bsdf.inputs["Base Color"].default_value=col; bsdf.inputs["Roughness"].default_value=0.92
            for k in ("Specular IOR Level","Specular"):
                try: bsdf.inputs[k].default_value=0.02; break
                except KeyError: pass
        if plane.data.materials: plane.data.materials[0]=mat
        else: plane.data.materials.append(mat)
        plane.visible_shadow=True
        report["ground_created"]=True; report["ground_z"]=round(ground_z,4)
        print(f"  [chão] z={ground_z:.4f}  tamanho={plane_size:.2f}m")
    except Exception as e:
        report["warnings"].append(f"chão não criado: {e}")
        ground_z = zmn
    return ground_z


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def _setup_render(scene: Any, resolution: int, fps: int) -> None:
    for eng in ("BLENDER_EEVEE_NEXT","BLENDER_EEVEE","CYCLES"):
        try: scene.render.engine=eng; break
        except Exception: continue
    engine = scene.render.engine
    if "EEVEE" in engine and hasattr(scene,"eevee"):
        ev=scene.eevee
        for attr,val in [("taa_render_samples",16),("use_bloom",False),("use_gtao",True),("use_ssr",False)]:
            if hasattr(ev,attr):
                try: setattr(ev,attr,val)
                except Exception: pass
    elif "CYCLES" in engine:
        scene.cycles.samples=32; scene.cycles.use_denoising=False; scene.cycles.device="CPU"
    scene.render.resolution_x=resolution; scene.render.resolution_y=resolution
    scene.render.resolution_percentage=100; scene.render.fps=fps
    scene.render.image_settings.file_format="PNG"; scene.render.film_transparent=False
    try: scene.view_settings.view_transform="Filmic"; scene.view_settings.look="None"
    except Exception: pass
    print(f"  [render] motor={engine}  res={resolution}×{resolution}  fps={fps}")


# ─────────────────────────────────────────────────────────────────────────────
# GLB
# ─────────────────────────────────────────────────────────────────────────────

def _export_glb(output_path: pathlib.Path, report: Dict[str,Any]) -> bool:
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
        report["errors"].append(f"GLB inválido: {output_path}"); return False
    report["glb_exported"]=True; report["glb_path"]=str(output_path)
    print(f"  [GLB] {output_path.stat().st_size//1024} KB")
    try:
        before={o.name for o in bpy.context.scene.objects}
        bpy.ops.import_scene.gltf(filepath=str(output_path))
        new=[o for o in bpy.context.scene.objects if o.name not in before]
        if new:
            report["glb_importable"]=True
            for o in new: bpy.data.objects.remove(o,do_unlink=True)
        else:
            report["warnings"].append("GLB reimportado sem objectos novos")
    except Exception as e:
        report["warnings"].append(f"GLB reimportação falhou: {e}")
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
        print(f"ERRO: {cfg_path}", file=sys.stderr); return 1
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERRO JSON: {e}", file=sys.stderr); return 1

    scene_name = cfg.get("scene_name","scene")
    report = _empty_report(scene_name)
    report["camera_mode"]     = cfg.get("camera",{}).get("mode","cinematic")
    report["lighting_preset"] = cfg.get("lighting",{}).get("preset","daylight")
    require_scene_assets: bool = bool(cfg.get("require_scene_assets",False))

    errs = _validate_scene_config(cfg)
    if errs:
        for e in errs: report["errors"].append(f"config: {e}")
        _save_report(report,args.report); return 1

    output_cfg      = cfg.get("output",{})
    resolution      = max(args.resolution, output_cfg.get("resolution",640))
    fps             = output_cfg.get("fps", args.fps)
    max_frames      = min(args.max_frames, output_cfg.get("max_frames",72), 300)
    render_preview  = args.render_preview   or ("png" in output_cfg.get("formats",[]))
    render_anim     = args.render_animation or ("mp4" in output_cfg.get("formats",[]))
    do_glb          = args.export_glb       or ("glb" in output_cfg.get("formats",[]))

    lib_root = (pathlib.Path(args.library_root).resolve()
                if args.library_root else pathlib.Path(__file__).resolve().parents[1])
    resolved = _resolve_assets_from_config(cfg, lib_root, report)
    if report["errors"]:
        _save_report(report,args.report); return 1

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception:
        report["errors"].append(f"erro limpeza cena:\n{traceback.format_exc(limit=5)}")
        _save_report(report,args.report); return 1

    scene = bpy.context.scene
    cols  = _make_collections()
    lighting_preset = cfg.get("lighting",{}).get("preset","daylight")
    env_assets = cfg.get("environment",{}).get("assets",[])

    # ── PASSO 1: Importar e normalizar escala do ambiente ─────────────────
    imported_env_count = 0
    env_import_errors: List[str] = []
    env_objects_all: List[Any] = []

    for env_asset in env_assets:
        asset_id = env_asset["asset_id"]
        position = env_asset.get("position",[0.0,0.0,0.0])
        rot_deg  = env_asset.get("rotation",[0.0,0.0,0.0])
        scale    = env_asset.get("scale",   [1.0,1.0,1.0])

        if asset_id not in resolved:
            err_msg = f"ambiente '{asset_id}' não resolvido"
            env_import_errors.append(err_msg); report["errors"].append(err_msg); continue

        ok, err, objects = _import_glb(resolved[asset_id])
        if not ok:
            err_msg = f"falha ao importar '{asset_id}': {err}"
            env_import_errors.append(err_msg); report["errors"].append(err_msg); continue

        col_name = "ENVIRONMENT"
        name_lo  = asset_id.lower()
        if any(k in name_lo for k in ("road","street")): col_name="ROADS"
        elif any(k in name_lo for k in ("tree","bush","plant","vegetation")): col_name="VEGETATION"
        elif any(k in name_lo for k in ("building","house","edif","tower")): col_name="BUILDINGS"
        elif any(k in name_lo for k in ("prop","barrel","chest","bench")): col_name="PROPS"

        _move_to_collection(objects, cols[col_name])

        # Normalizar escala: detectar cm→m automaticamente
        _auto_scale_objects(objects, asset_id, report, role="building")

        # Posicionar com base em bounds reais pós-escala
        _apply_transform_to_ground(
            objects,
            target_xy=(position[0], position[1]),
            ground_z=position[2],
            rotation_deg=rot_deg,
            scale_extra=scale,
        )

        env_objects_all.extend(objects)
        imported_env_count += 1
        print(f"  [ambiente] '{asset_id}' → coleção {col_name}")

    report["scene_assets_imported"] = imported_env_count

    if require_scene_assets and env_assets and imported_env_count == 0:
        msg = (f"INTEGRIDADE: '{scene_name}' requer ambiente mas nenhum importado. "
               f"Erros: {env_import_errors}")
        report["errors"].append(msg)
        print(f"\n{'='*64}\n{msg}\n{'='*64}\n", file=sys.stderr)
        _save_report(report,args.report); return 1

    # ── PASSO 2: Medir bounds do prédio e determinar o solo ───────────────
    building_bounds: Optional[Tuple] = None
    ground_z: float = 0.0

    if env_objects_all:
        bpy.context.view_layer.update()
        building_bounds = _world_bounds_of(env_objects_all)
        if building_bounds:
            bxmn,bxmx,bymn,bymx,bzmn,bzmx = building_bounds
            ground_z = bzmn
            report["building_world_bounds"] = {
                "min":[round(bxmn,4),round(bymn,4),round(bzmn,4)],
                "max":[round(bxmx,4),round(bymx,4),round(bzmx,4)],
            }
            print(f"  [bounds prédio pós-escala] X=[{bxmn:.3f},{bxmx:.3f}] "
                  f"Y=[{bymn:.3f},{bymx:.3f}] Z=[{bzmn:.3f},{bzmx:.3f}]")

    # ── PASSO 3: Criar chão ao nível do solo real ─────────────────────────
    has_road = any(any(k in a["asset_id"].lower() for k in ("road","street","floor","pavement")) for a in env_assets)
    if not has_road:
        ref_bounds = building_bounds if building_bounds else (-3.0,3.0,-3.0,3.0,0.0,0.0)
        ground_z = _add_ground_plane(scene, ref_bounds, lighting_preset, report)

    report["ground_z"] = round(ground_z, 4)

    # ── PASSO 4: Importar personagens com normalização de escala ──────────
    char_objects_all: List[Any] = []
    char_arm_obj: Optional[Any] = None
    char_action:  Optional[Any] = None

    for char_cfg in cfg.get("characters",[]):
        asset_id  = char_cfg["asset_id"]
        anim_name = char_cfg.get("animation","")

        if asset_id not in resolved:
            report["errors"].append(f"personagem '{asset_id}' não resolvida"); continue

        ok, err, objects = _import_glb(resolved[asset_id])
        if not ok:
            report["errors"].append(f"falha personagem '{asset_id}': {err}"); continue

        _move_to_collection(objects, cols["CHARACTERS"])

        # Normalizar escala do personagem (cm→m se necessário)
        _auto_scale_objects(objects, asset_id, report, role="character")

        # Posicionamento: ao lado do prédio se houver, senão centro
        if building_bounds is not None:
            # Posicionar provisoriamente para medir bounds
            _apply_transform_to_ground(objects, target_xy=(0.0,0.0), ground_z=ground_z,
                                        rotation_deg=[0.0,0.0,0.0], scale_extra=[1.0,1.0,1.0])
            # Posicionar ao lado baseado em bounds reais
            _place_character_beside_building(objects, building_bounds, ground_z, report)
        else:
            _apply_transform_to_ground(objects, target_xy=(0.0,0.0), ground_z=ground_z,
                                        rotation_deg=[0.0,0.0,0.0], scale_extra=[1.0,1.0,1.0])

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
                    print(f"  [personagem] '{asset_id}' → animação '{action.name}'")
                    # Verificar movimento real (sem fcurves — compatível Blender 5.x)
                    fr_s = int(action.frame_range[0])
                    fr_e = int(action.frame_range[1])
                    _check_animation_motion_by_pose(arm_obj, fr_s, fr_e, report)
                except Exception as e:
                    report["warnings"].append(f"animação '{anim_name}' não aplicada: {e}")
            else:
                avail = [a.name for a in bpy.data.actions[:5]]
                report["warnings"].append(f"animação '{anim_name}' não encontrada. Disponíveis: {avail}")

    # ── PASSO 5: Câmara — enquadrar cena completa ─────────────────────────
    bpy.context.view_layer.update()
    all_bounds = _scene_bounds()
    cam_mode   = cfg.get("camera",{}).get("mode","cinematic")
    cam_obj = _place_camera_for_bounds(scene, all_bounds, cam_mode, report)
    for col in list(cam_obj.users_collection):
        col.objects.unlink(cam_obj)
    cols["CAMERAS"].objects.link(cam_obj)

    # ── PASSO 6: Iluminação ───────────────────────────────────────────────
    if lighting_preset == "daylight":
        _setup_daylight(scene, all_bounds, report)
    else:
        _setup_three_point(scene, all_bounds, cam_obj, lighting_preset, report)

    _setup_render(scene, resolution, fps)

    # Ajustar câmara para todos os objectos visíveis
    objects_to_frame = [o for o in (env_objects_all + char_objects_all) if o.type == "MESH"]
    if objects_to_frame:
        _adjust_camera_framing(scene, cam_obj, objects_to_frame,
                                margin=0.08, max_iter=8, report=report)

    # ── PASSO 7: Frames ───────────────────────────────────────────────────
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
        frames_to_render = [fr_start + int(i*step) for i in range(max_frames)]

    scene.frame_start = fr_start; scene.frame_end = fr_end

    # ── PASSO 8: Preview PNG ──────────────────────────────────────────────
    if render_preview:
        mid_frame    = frames_to_render[len(frames_to_render)//2]
        preview_path = output_dir / f"{scene_name}_preview.png"
        scene.frame_set(mid_frame); bpy.context.view_layer.update()
        scene.render.filepath = str(preview_path)
        t0 = time.time()
        try:
            bpy.ops.render.render(write_still=True)
            if preview_path.is_file() and preview_path.stat().st_size > 0:
                report["preview_success"]=True; report["preview_path"]=str(preview_path)
                print(f"  [PNG] {preview_path}")
            else:
                report["errors"].append("PNG não gerado")
        except Exception:
            report["errors"].append(f"erro PNG:\n{traceback.format_exc(limit=5)}")
        report["render_time_s"] = round(time.time()-t0,1)

    # ── PASSO 9: Animação → MP4 ───────────────────────────────────────────
    if render_anim:
        frames_dir = output_dir / "frames_tmp"
        frames_dir.mkdir(parents=True, exist_ok=True)
        rendered: List[pathlib.Path] = []
        t0 = time.time()
        for i, frame in enumerate(frames_to_render):
            fp = frames_dir / f"frame_{i:04d}.png"
            scene.render.filepath = str(fp)
            scene.frame_set(frame); bpy.context.view_layer.update()
            try:
                bpy.ops.render.render(write_still=True)
                if fp.is_file() and fp.stat().st_size > 0:
                    rendered.append(fp)
            except Exception:
                report["warnings"].append(f"frame {frame}:\n{traceback.format_exc(limit=3)}")
                break
        report["frames_rendered"]=len(rendered); report["frames_requested"]=len(frames_to_render)
        report["render_time_s"]=round(time.time()-t0,1)
        if rendered:
            import subprocess
            vp = output_dir / f"{scene_name}.mp4"
            r = subprocess.run(
                ["ffmpeg","-y","-framerate",str(fps),"-i",str(frames_dir/"frame_%04d.png"),
                 "-c:v","libx264","-pix_fmt","yuv420p","-preset","fast","-crf","23",
                 "-movflags","+faststart",str(vp)],
                capture_output=True, text=True, timeout=300)
            if r.returncode==0 and vp.is_file():
                report["video_path"]=str(vp); print(f"  [MP4] {vp}")
            else:
                report["errors"].append(f"FFmpeg: {r.stderr[:500]}")
            import shutil; shutil.rmtree(frames_dir, ignore_errors=True)
        else:
            report["errors"].append("nenhum frame renderizado — MP4 não gerado")

    # ── PASSO 10: GLB ─────────────────────────────────────────────────────
    if do_glb:
        _export_glb(output_dir / f"{scene_name}.glb", report)

    _save_report(report, args.report)
    _print_summary(report)
    return 1 if report["errors"] else 0


def _print_summary(report: Dict[str,Any]) -> None:
    r = report
    chk = lambda b: "✅" if b else "❌"
    print("\n"+"="*64)
    print(f"CENA 3D: {r['scene_name']}  (Blender {r['blender_version']})")
    print("="*64)
    print(f"  escala personagem:    {r.get('character_scale_applied','?')}")
    print(f"  escala prédio:        {r.get('building_scale_applied','?')}")
    bb = r.get("building_world_bounds")
    cb = r.get("character_world_bounds")
    if bb: print(f"  prédio Z:             [{bb['min'][2]:.3f}, {bb['max'][2]:.3f}]  h={(bb['max'][2]-bb['min'][2]):.3f}m")
    if cb: print(f"  personagem Z:         [{cb['min'][2]:.3f}, {cb['max'][2]:.3f}]  h={(cb['max'][2]-cb['min'][2]):.3f}m")
    print(f"  assets ambiente:      {r['scene_assets_imported']}")
    print(f"  personagem:           {chk(r['character_imported'])}")
    print(f"  animação aplicada:    {chk(r['character_animated'])}")
    print(f"  movimento confirmado: {chk(r['animation_motion_confirmed'])}  ossos={r['moving_bones']}")
    print(f"  câmara:               {chk(r['camera_created'])}  {r['camera_mode']}")
    print(f"  enquadramento:        {chk(r['framing_success'])}")
    print(f"  luzes:                {r['lights_created']}")
    print(f"  chão:                 {chk(r['ground_created'])}  z={r.get('ground_z')}")
    if r.get("character_ground_clearance") is not None:
        print(f"  ground clearance:     {r['character_ground_clearance']:.4f} m")
    if r.get("character_building_overlap") is not None:
        print(f"  overlap c/ prédio:    {chk(not r['character_building_overlap'])}")
    if r.get("character_not_above_building") is not None:
        print(f"  não está em cima:     {chk(r['character_not_above_building'])}")
    print(f"  placement validado:   {chk(r.get('placement_validated',False))}")
    print(f"  preview PNG:          {chk(r['preview_success'])}")
    if r.get("frames_requested"):
        print(f"  frames:               {r['frames_rendered']}/{r['frames_requested']}")
    if r.get("video_path"):   print(f"  MP4:                  ✅ {r['video_path']}")
    if r.get("glb_exported"): print(f"  GLB:                  ✅ reimportável={chk(r.get('glb_importable',False))}")
    if r.get("render_time_s"): print(f"  tempo render:         {r['render_time_s']}s")
    if r["errors"]:
        print("\nERROS:")
        for e in r["errors"]: print(f"  ! {str(e)[:300]}")
    if r["warnings"]:
        print("\nAVISOS:")
        for w in r["warnings"]: print(f"  ~ {w}")
    print("="*64+"\n")


if __name__ == "__main__":
    sys.exit(main())
