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
Não modifica animate_character.py nem nenhum workflow existente.
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

# ── separar argumentos do Blender dos nossos ─────────────────────────────────
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
# SEGURANÇA: diretório base autorizado
# ─────────────────────────────────────────────────────────────────────────────
_LIBRARY_ROOT: Optional[pathlib.Path] = None


def _set_library_root(path: pathlib.Path) -> None:
    global _LIBRARY_ROOT
    _LIBRARY_ROOT = path.resolve()


def _safe_asset_path(rel_or_abs: str) -> pathlib.Path:
    """Valida que o caminho de um asset fica dentro da pasta library/assets/.
    Levanta ValueError se o caminho escapar do sandbox.
    """
    p = pathlib.Path(rel_or_abs).resolve()
    if _LIBRARY_ROOT is None:
        return p  # sem sandbox — aceitamos (modo de teste)
    allowed = (_LIBRARY_ROOT / "assets").resolve()
    try:
        p.relative_to(allowed)
    except ValueError:
        raise ValueError(
            f"Caminho de asset fora do sandbox: {p}\n"
            f"  Sandbox: {allowed}"
        )
    return p


# ─────────────────────────────────────────────────────────────────────────────
# RELATÓRIO
# ─────────────────────────────────────────────────────────────────────────────

def _empty_report(scene_name: str) -> Dict[str, Any]:
    return {
        "scene_name":            scene_name,
        "blender_version":       ".".join(str(v) for v in bpy.app.version),
        "scene_assets_imported": 0,
        "character_imported":    False,
        "character_animated":    False,
        "camera_mode":           "cinematic",
        "camera_created":        False,
        "lighting_preset":       "daylight",
        "lights_created":        0,
        "ground_created":        False,
        "frames_rendered":       0,
        "frames_requested":      0,
        "preview_success":       False,
        "preview_path":          None,
        "video_path":            None,
        "glb_exported":          False,
        "glb_path":              None,
        "glb_importable":        False,
        "render_time_s":         None,
        "framing_success":       False,
        "errors":                [],
        "warnings":              [],
        "asset_details":         [],
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
    """Devolve lista de erros. Lista vazia = válido."""
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
# RESOLVER DE ASSETS — localiza GLB/GLTF na pasta library/assets/
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_asset_glb(asset_id: str, library_root: pathlib.Path,
                       category_hint: str = "") -> Optional[pathlib.Path]:
    """Procura o primeiro GLB/GLTF de um asset_id na biblioteca local.

    Estratégia:
      1. library/assets/<category_hint>/<asset_id>/*.glb   (se category_hint dado)
      2. library/assets/*/<asset_id>/*.glb                 (todos as categorias)
      3. Aceita também .gltf
    """
    assets_root = library_root / "assets"

    def _find_in_dir(adir: pathlib.Path) -> Optional[pathlib.Path]:
        if not adir.is_dir():
            return None
        for ext in (".glb", ".gltf"):
            matches = sorted(adir.rglob(f"*{ext}"))
            if matches:
                return matches[0]
        return None

    # Tentativa 1: categoria hint
    if category_hint:
        p = _find_in_dir(assets_root / category_hint / asset_id)
        if p:
            return p

    # Tentativa 2: varredura de todas as categorias
    if assets_root.is_dir():
        for cat_dir in sorted(assets_root.iterdir()):
            if not cat_dir.is_dir():
                continue
            p = _find_in_dir(cat_dir / asset_id)
            if p:
                return p

    return None


def _resolve_assets_from_config(cfg: Dict[str, Any],
                                library_root: pathlib.Path,
                                report: Dict[str, Any]) -> Dict[str, pathlib.Path]:
    """Devolve mapa asset_id → Path para todos os assets da cena.
    Adiciona avisos/erros ao relatório para assets não encontrados.
    """
    needed: Dict[str, str] = {}  # asset_id → category_hint

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
                f"Execute o download antes de construir a cena."
            )
        else:
            try:
                resolved[asset_id] = _safe_asset_path(str(path))
                report["asset_details"].append({
                    "asset_id": asset_id,
                    "path":     str(path),
                    "found":    True,
                })
            except ValueError as e:
                report["errors"].append(str(e))

    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# COLEÇÕES BLENDER
# ─────────────────────────────────────────────────────────────────────────────

_COLLECTIONS = [
    "SCENE", "ENVIRONMENT", "BUILDINGS", "ROADS",
    "VEGETATION", "PROPS", "CHARACTERS", "LIGHTING", "CAMERAS",
]


def _make_collections() -> Dict[str, Any]:
    """Cria coleções hierárquicas se ainda não existirem."""
    scene = bpy.context.scene
    cols: Dict[str, Any] = {}
    root_col = None

    for name in _COLLECTIONS:
        existing = bpy.data.collections.get(name)
        if existing:
            cols[name] = existing
        else:
            col = bpy.data.collections.new(name)
            cols[name] = col

    # Ligar à cena
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
# IMPORTAÇÃO DE MODELOS
# ─────────────────────────────────────────────────────────────────────────────

def _objects_before(scene: Any) -> set:
    return {o.name for o in scene.objects}


def _import_glb(path: pathlib.Path) -> Tuple[bool, str, List[Any]]:
    """Importa um GLB/GLTF. Devolve (sucesso, erro|"", objetos_importados)."""
    if not path.is_file():
        return False, f"ficheiro não encontrado: {path}", []
    if path.stat().st_size == 0:
        return False, f"ficheiro vazio: {path}", []

    scene   = bpy.context.scene
    before  = _objects_before(scene)
    try:
        r = bpy.ops.import_scene.gltf(filepath=str(path))
        if r != {"FINISHED"}:
            return False, f"importação retornou: {r}", []
    except Exception:
        return False, traceback.format_exc(limit=8), []

    new_objs = [o for o in scene.objects if o.name not in before]
    return True, "", new_objs


def _move_to_collection(objects: List[Any], target_col: Any) -> None:
    """Move objetos para uma coleção específica, removendo das outras."""
    for obj in objects:
        for col in list(obj.users_collection):
            col.objects.unlink(obj)
        target_col.objects.link(obj)


def _apply_transform(objects: List[Any], position: List[float],
                     rotation_deg: List[float], scale: List[float]) -> None:
    """Aplica posição, rotação e escala ao ponto de origem do grupo importado.

    Move o ponto de origem calculando o bounding box coletivo e translacionando
    todos os objetos do grupo de forma que o centro fique em 'position'.
    """
    if not objects:
        return

    # Calcular o centro atual do grupo (bounding box global de todos os objetos)
    min_v = mathutils.Vector((math.inf, math.inf, math.inf))
    max_v = mathutils.Vector((-math.inf, -math.inf, -math.inf))
    for obj in objects:
        if not hasattr(obj, "bound_box"):
            continue
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ mathutils.Vector(corner)
            min_v.x = min(min_v.x, world_co.x)
            min_v.y = min(min_v.y, world_co.y)
            min_v.z = min(min_v.z, world_co.z)
            max_v.x = max(max_v.x, world_co.x)
            max_v.y = max(max_v.y, world_co.y)
            max_v.z = max(max_v.z, world_co.z)

    if math.isinf(min_v.x):
        # Sem bounding box — usar location do primeiro objeto
        center = objects[0].location.copy()
    else:
        center = (min_v + max_v) / 2.0
        # Para posicionamento sobre o chão: usar min_z como base
        center.z = min_v.z

    target = mathutils.Vector(position)
    offset = target - center

    # Aplicar escala (antes de mover para preservar orientação)
    for obj in objects:
        if obj.parent is None:
            obj.scale.x *= scale[0]
            obj.scale.y *= scale[1]
            obj.scale.z *= scale[2]
            obj.rotation_euler.x += math.radians(rotation_deg[0])
            obj.rotation_euler.y += math.radians(rotation_deg[1])
            obj.rotation_euler.z += math.radians(rotation_deg[2])
            obj.location += offset


# ─────────────────────────────────────────────────────────────────────────────
# ANIMAÇÃO (reutiliza lógica de animate_character.py)
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


# ─────────────────────────────────────────────────────────────────────────────
# BOUNDS GLOBAIS DA CENA
# ─────────────────────────────────────────────────────────────────────────────

def _scene_bounds() -> Tuple[float, float, float, float, float, float]:
    """Bounding box de todos os meshes da cena no espaço mundial."""
    min_v = mathutils.Vector((math.inf, math.inf, math.inf))
    max_v = mathutils.Vector((-math.inf, -math.inf, -math.inf))

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mat = obj.matrix_world
        if not hasattr(obj, "bound_box"):
            continue
        for corner in obj.bound_box:
            wc = mat @ mathutils.Vector(corner)
            min_v.x = min(min_v.x, wc.x); max_v.x = max(max_v.x, wc.x)
            min_v.y = min(min_v.y, wc.y); max_v.y = max(max_v.y, wc.y)
            min_v.z = min(min_v.z, wc.z); max_v.z = max(max_v.z, wc.z)

    if math.isinf(min_v.x):
        return (-2.0, 2.0, -2.0, 2.0, 0.0, 2.0)
    return (min_v.x, max_v.x, min_v.y, max_v.y, min_v.z, max_v.z)


def _character_bounds(char_objects: List[Any]) -> Tuple[float, float, float, float, float, float]:
    """Bounding box apenas dos objetos do personagem."""
    min_v = mathutils.Vector((math.inf, math.inf, math.inf))
    max_v = mathutils.Vector((-math.inf, -math.inf, -math.inf))
    found = False
    for obj in char_objects:
        if obj.type != "MESH" or not hasattr(obj, "bound_box"):
            continue
        mat = obj.matrix_world
        for corner in obj.bound_box:
            wc = mat @ mathutils.Vector(corner)
            min_v.x = min(min_v.x, wc.x); max_v.x = max(max_v.x, wc.x)
            min_v.y = min(min_v.y, wc.y); max_v.y = max(max_v.y, wc.y)
            min_v.z = min(min_v.z, wc.z); max_v.z = max(max_v.z, wc.z)
        found = True
    if not found:
        return (-1.0, 1.0, -1.0, 1.0, 0.0, 2.0)
    return (min_v.x, max_v.x, min_v.y, max_v.y, min_v.z, max_v.z)


# ─────────────────────────────────────────────────────────────────────────────
# CÂMARA — três modos
# ─────────────────────────────────────────────────────────────────────────────

def _place_camera_for_bounds(
        scene: Any,
        bounds: Tuple[float, float, float, float, float, float],
        mode: str,
        report: Dict[str, Any],
) -> Any:
    """Cria e posiciona câmara. Modos: character_focus | scene_overview | cinematic."""
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0
    cz = (zmn + zmx) / 2.0
    dx = xmx - xmn or 0.1
    dy = ymx - ymn or 0.1
    dz = zmx - zmn or 0.1

    radius       = math.sqrt(dx*dx + dy*dy + dz*dz) / 2.0
    radius_safe  = radius * 1.35
    fov_rad      = math.radians(46.8)   # 50 mm equiv.
    cam_dist     = radius_safe / math.sin(fov_rad / 2.0)

    if mode == "scene_overview":
        elev = math.radians(35)
        azim = math.radians(45)
        cam_dist *= 1.4   # mais afastado para mostrar cenário completo
    elif mode == "character_focus":
        elev = math.radians(15)
        azim = math.radians(30)
        cam_dist *= 0.85  # mais próximo — foco no personagem
    else:  # cinematic (padrão)
        elev = math.radians(12)
        azim = math.radians(40)

    cam_x = cx + cam_dist * math.cos(elev) * math.sin(azim)
    cam_y = cy - cam_dist * math.cos(elev) * math.cos(azim)
    cam_z = cz + cam_dist * math.sin(elev)

    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam_obj = bpy.context.active_object
    cam_obj.name = "SceneCam"

    target_pt = mathutils.Vector((cx, cy, cz))
    direction  = target_pt - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    cam_obj.data.lens       = 50.0
    cam_obj.data.clip_start = 0.01
    cam_obj.data.clip_end   = cam_dist * 12.0
    scene.camera = cam_obj

    report["camera_created"] = True
    print(f"  [câmara] modo={mode}  dist={cam_dist:.2f}  "
          f"centro=({cx:.2f},{cy:.2f},{cz:.2f})")
    return cam_obj


def _adjust_camera_framing(
        scene: Any,
        cam_obj: Any,
        char_objects: List[Any],
        margin: float = 0.12,
        max_iter: int = 6,
        report: Dict[str, Any] = {},
) -> None:
    """Ajusta câmara iterativamente para garantir que o personagem aparece enquadrado."""
    if not char_objects:
        return

    safe_lo = margin
    safe_hi = 1.0 - margin

    for iteration in range(max_iter + 1):
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        all_min_x, all_max_x = math.inf, -math.inf
        all_min_y, all_max_y = math.inf, -math.inf

        for obj in char_objects:
            if obj.type != "MESH":
                continue
            try:
                obj_eval = obj.evaluated_get(depsgraph)
                mesh_eval = obj_eval.to_mesh()
                if mesh_eval is None:
                    continue
                mat = obj_eval.matrix_world
                for v in mesh_eval.vertices:
                    co = world_to_camera_view(scene, cam_obj, mat @ v.co)
                    if co.z > 0:
                        all_min_x = min(all_min_x, co.x)
                        all_max_x = max(all_max_x, co.x)
                        all_min_y = min(all_min_y, co.y)
                        all_max_y = max(all_max_y, co.y)
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
                f"enquadramento do personagem não garantido após {max_iter} ajustes"
            )
            return

        overshoot = max(
            safe_lo - all_min_x if all_min_x < safe_lo else 0,
            all_max_x - safe_hi if all_max_x > safe_hi else 0,
            safe_lo - all_min_y if all_min_y < safe_lo else 0,
            all_max_y - safe_hi if all_max_y > safe_hi else 0,
        )
        factor = 1.0 + max(0.15, min(0.40, overshoot * 1.5))

        # Centro do personagem
        bounds = _character_bounds(char_objects)
        target = mathutils.Vector((
            (bounds[0]+bounds[1])/2,
            (bounds[2]+bounds[3])/2,
            (bounds[4]+bounds[5])/2,
        ))
        direction = (mathutils.Vector(cam_obj.location) - target).normalized()
        dist = (mathutils.Vector(cam_obj.location) - target).length
        cam_obj.location = target + direction * (dist * factor)
        print(f"  [enquadramento] ajuste {iteration+1}: factor={factor:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# ILUMINAÇÃO — preset daylight / studio / cinematic
# ─────────────────────────────────────────────────────────────────────────────

def _setup_daylight(scene: Any,
                    bounds: Tuple[float, float, float, float, float, float],
                    report: Dict[str, Any]) -> None:
    """
    Preset 'daylight': Sol + Céu HDRI nativo do Blender + luz de preenchimento.
    Não requer ficheiros externos.
    Cria sombras coerentes e fundo de céu.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn+xmx)/2; cy = (ymn+ymx)/2
    h  = zmx - zmn or 2.0

    lights_ok = 0

    # ── Sol (Key Light) ──────────────────────────────────────────────────────
    try:
        bpy.ops.object.light_add(type="SUN", location=(cx + 5, cy - 3, zmz + h*2.5)
                                  if False else (cx + 5, cy - 3, zmn + h*2.5))
        sun = bpy.context.active_object
        sun.name = "Sun_Key"
        sun.data.energy = 3.5
        sun.data.color  = (1.0, 0.97, 0.88)
        sun.data.angle  = math.radians(3.0)  # tamanho angular do sol → sombras suaves
        direction = mathutils.Vector((cx, cy, zmn + h*0.5)) - sun.location
        sun.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        lights_ok += 1
    except Exception as e:
        report["warnings"].append(f"Sol principal falhou: {e}")

    # ── Luz ambiente (Fill) ──────────────────────────────────────────────────
    try:
        bpy.ops.object.light_add(type="AREA", location=(cx - 4, cy + 2, zmn + h*1.5))
        fill = bpy.context.active_object
        fill.name = "Sky_Fill"
        fill.data.energy = 200.0
        fill.data.size   = max(h * 2.0, 4.0)
        fill.data.color  = (0.55, 0.70, 1.0)  # cor do céu
        direction = mathutils.Vector((cx, cy, zmn + h*0.5)) - fill.location
        fill.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        lights_ok += 1
    except Exception as e:
        report["warnings"].append(f"Fill Sky falhou: {e}")

    # ── Fundo: céu Nishita (nativo Blender 3.6+) ────────────────────────────
    try:
        world = bpy.data.worlds.new("DaylightWorld")
        world.use_nodes = True
        nt = world.node_tree
        bg_node = nt.nodes.get("Background")
        if bg_node is None:
            bg_node = nt.nodes.new("ShaderNodeBackground")

        # Tentar nó SkyTexture Nishita
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

        out_node = nt.nodes.get("World Output")
        if out_node is None:
            out_node = nt.nodes.new("ShaderNodeOutputWorld")
        nt.links.new(bg_node.outputs["Background"], out_node.inputs["Surface"])

        scene.world = world
        report["warnings"].append("Céu Nishita ativado (daylight)")
    except Exception as e:
        # Fallback: céu azul simples
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
        report["warnings"].append(f"Nishita não disponível: {e}. Fallback azul simples usado.")

    report["lights_created"] = lights_ok
    print(f"  [iluminação] preset=daylight  luzes={lights_ok}")


def _setup_three_point(scene: Any,
                       bounds: Tuple[float, float, float, float, float, float],
                       cam_obj: Any,
                       preset: str,
                       report: Dict[str, Any]) -> None:
    """Iluminação de três pontos (studio / cinematic)."""
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
        energy = base_e * e_scale
        try:
            bpy.ops.object.light_add(type="AREA", location=loc)
            lt = bpy.context.active_object
            lt.name = name
            lt.data.energy = energy
            lt.data.size   = area_size
            lt.data.color  = color
            direction = mathutils.Vector((cx,cy,cz)) - lt.location
            lt.rotation_euler = direction.to_track_quat("-Z","Y").to_euler()
            lights_ok += 1
        except Exception as e:
            report["warnings"].append(f"luz {name} falhou: {e}")

    if preset == "cinematic":
        _add_area("KeyLight",  (cx+r*1.5, cy-r*1.2, cz+h*0.9), 80.0, (1.0,0.97,0.90))
        _add_area("FillLight", (cx-r*1.8, cy-r*0.5, cz+h*0.4), 18.0, (0.85,0.90,1.0))
        _add_area("RimLight",  (cx-r*0.3, cy+r*2.0, cz+h*1.1), 45.0, (0.7,0.8,1.0))
        # Fundo escuro cinematic
        world = bpy.data.worlds.new("CinWorld")
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value    = (0.03, 0.03, 0.05, 1.0)
            bg.inputs["Strength"].default_value = 0.05
        scene.world = world
    else:  # studio
        _add_area("KeyLight",  (cx+r*1.4, cy-r*1.0, cz+h*0.8), 70.0, (1.0,0.98,0.95))
        _add_area("FillLight", (cx-r*1.6, cy-r*0.4, cz+h*0.5), 40.0, (0.95,0.97,1.0))
        _add_area("RimLight",  (cx-r*0.2, cy+r*1.8, cz+h*1.0), 35.0, (1.0,1.0,1.0))
        world = bpy.data.worlds.new("StudioWorld")
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value    = (0.20, 0.21, 0.24, 1.0)
            bg.inputs["Strength"].default_value = 0.25
        scene.world = world

    report["lights_created"] = lights_ok
    print(f"  [iluminação] preset={preset}  luzes={lights_ok}")


# ─────────────────────────────────────────────────────────────────────────────
# CHÃO
# ─────────────────────────────────────────────────────────────────────────────

def _add_ground_plane(scene: Any,
                      bounds: Tuple[float, float, float, float, float, float],
                      style: str,
                      report: Dict[str, Any]) -> None:
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn+xmx)/2; cy = (ymn+ymx)/2
    ground_z   = zmn - 0.005
    plane_size = max((xmx-xmn)*2.0, (ymx-ymn)*2.0, 6.0)

    try:
        bpy.ops.mesh.primitive_plane_add(size=plane_size,
                                         location=(cx, cy, ground_z))
        plane = bpy.context.active_object
        plane.name = "SceneGround"
        mat = bpy.data.materials.new("GroundMat_Scene")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            if style == "daylight":
                bsdf.inputs["Base Color"].default_value = (0.22, 0.20, 0.16, 1.0)  # terra
            elif style == "cinematic":
                bsdf.inputs["Base Color"].default_value = (0.06, 0.06, 0.08, 1.0)
            else:
                bsdf.inputs["Base Color"].default_value = (0.28, 0.28, 0.30, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.92
            for k in ("Specular IOR Level", "Specular"):
                try:
                    bsdf.inputs[k].default_value = 0.02
                    break
                except KeyError:
                    pass
        if plane.data.materials:
            plane.data.materials[0] = mat
        else:
            plane.data.materials.append(mat)
        plane.visible_shadow = True
        report["ground_created"] = True
        print(f"  [chão] z={ground_z:.4f}  tamanho={plane_size:.2f}m")
    except Exception as e:
        report["warnings"].append(f"chão não criado: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE RENDER
# ─────────────────────────────────────────────────────────────────────────────

def _setup_render(scene: Any, resolution: int, fps: int) -> None:
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = eng
            break
        except Exception:
            continue
    engine = scene.render.engine

    if "EEVEE" in engine and hasattr(scene, "eevee"):
        ev = scene.eevee
        for attr, val in [
            ("taa_render_samples", 16),
            ("use_bloom",          False),
            ("use_gtao",           True),
            ("use_ssr",            False),
        ]:
            if hasattr(ev, attr):
                try:
                    setattr(ev, attr, val)
                except Exception:
                    pass
    elif "CYCLES" in engine:
        scene.cycles.samples       = 32
        scene.cycles.use_denoising = False
        scene.cycles.device        = "CPU"

    scene.render.resolution_x          = resolution
    scene.render.resolution_y          = resolution
    scene.render.resolution_percentage = 100
    scene.render.fps                    = fps
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent       = False

    try:
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look           = "None"
    except Exception:
        pass

    print(f"  [render] motor={engine}  res={resolution}×{resolution}  fps={fps}")


# ─────────────────────────────────────────────────────────────────────────────
# EXPORTAÇÃO GLB
# ─────────────────────────────────────────────────────────────────────────────

def _export_glb(output_path: pathlib.Path, report: Dict[str, Any]) -> bool:
    """Exporta a cena para GLB e verifica se o ficheiro foi criado."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.export_scene.gltf(
            filepath      = str(output_path),
            export_format = "GLB",
            export_animations = True,
            export_skins      = True,
            export_materials  = "EXPORT",
            export_cameras    = False,
            export_lights     = False,
            use_selection     = False,
        )
    except Exception:
        report["errors"].append(
            f"exportação GLB falhou:\n{traceback.format_exc(limit=6)}"
        )
        return False

    if not output_path.is_file() or output_path.stat().st_size < 100:
        report["errors"].append(
            f"GLB exportado mas parece inválido: {output_path}"
        )
        return False

    size_kb = output_path.stat().st_size // 1024
    report["glb_exported"] = True
    report["glb_path"]     = str(output_path)
    print(f"  [GLB] exportado: {output_path}  ({size_kb} KB)")

    # Teste de reimportação (verifica se o GLB é legível)
    try:
        before = {o.name for o in bpy.context.scene.objects}
        bpy.ops.import_scene.gltf(filepath=str(output_path))
        new_objs = [o for o in bpy.context.scene.objects if o.name not in before]
        if new_objs:
            report["glb_importable"] = True
            # Limpar objetos da reimportação de teste
            for o in new_objs:
                bpy.data.objects.remove(o, do_unlink=True)
            print(f"  [GLB] reimportação OK — {len(new_objs)} objetos validados")
        else:
            report["warnings"].append("GLB reimportado mas sem objetos novos")
    except Exception as e:
        report["warnings"].append(f"GLB exportado mas reimportação de teste falhou: {e}")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# PONTO DE ENTRADA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scene-config",     required=True,
                    help="Caminho para o JSON de configuração da cena")
    ap.add_argument("--output-dir",       required=True,
                    help="Diretório para PNG, MP4 e GLB")
    ap.add_argument("--report",           required=True,
                    help="Caminho para o relatório JSON de saída")
    ap.add_argument("--library-root",     default="",
                    help="Raiz da biblioteca (ex: /github/workspace/library)")
    ap.add_argument("--render-preview",   action="store_true",
                    help="Renderizar imagem PNG de prévia")
    ap.add_argument("--render-animation", action="store_true",
                    help="Renderizar animação e gerar MP4")
    ap.add_argument("--export-glb",       action="store_true",
                    help="Exportar cena para GLB")
    ap.add_argument("--resolution",       type=int, default=640)
    ap.add_argument("--max-frames",       type=int, default=26)
    ap.add_argument("--fps",              type=int, default=24)
    args = ap.parse_args(_argv)

    # Configurar sandbox
    if args.library_root:
        _set_library_root(pathlib.Path(args.library_root))

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Carregar JSON de cena ─────────────────────────────────────────────
    cfg_path = pathlib.Path(args.scene_config)
    if not cfg_path.is_file():
        print(f"ERRO: ficheiro de cena não encontrado: {cfg_path}", file=sys.stderr)
        return 1

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERRO: JSON inválido em {cfg_path}: {e}", file=sys.stderr)
        return 1

    scene_name = cfg.get("scene_name", "scene")
    report = _empty_report(scene_name)
    report["camera_mode"]     = cfg.get("camera", {}).get("mode", "cinematic")
    report["lighting_preset"] = cfg.get("lighting", {}).get("preset", "daylight")

    # ── Validar configuração ──────────────────────────────────────────────
    errs = _validate_scene_config(cfg)
    if errs:
        for e in errs:
            report["errors"].append(f"config inválida: {e}")
        _save_report(report, args.report)
        return 1

    # ── Usar parâmetros CLI se superiores aos do JSON ─────────────────────
    output_cfg  = cfg.get("output", {})
    resolution  = max(args.resolution, output_cfg.get("resolution", 640))
    fps         = output_cfg.get("fps", args.fps)
    max_frames  = min(args.max_frames, output_cfg.get("max_frames", 72), 300)

    render_preview   = args.render_preview   or ("png"  in output_cfg.get("formats", []))
    render_animation = args.render_animation or ("mp4"  in output_cfg.get("formats", []))
    do_export_glb    = args.export_glb       or ("glb"  in output_cfg.get("formats", []))

    # ── Resolver assets ───────────────────────────────────────────────────
    lib_root = (pathlib.Path(args.library_root).resolve()
                if args.library_root
                else pathlib.Path(__file__).resolve().parents[1])
    resolved = _resolve_assets_from_config(cfg, lib_root, report)

    if report["errors"]:
        _save_report(report, args.report)
        return 1

    # ── Limpar cena e criar coleções ──────────────────────────────────────
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception:
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save_report(report, args.report)
        return 1

    scene = bpy.context.scene
    cols  = _make_collections()

    # ── Importar assets de ambiente ───────────────────────────────────────
    lighting_preset = cfg.get("lighting", {}).get("preset", "daylight")
    env_assets = cfg.get("environment", {}).get("assets", [])
    imported_env_count = 0

    for env_asset in env_assets:
        asset_id  = env_asset["asset_id"]
        position  = env_asset.get("position", [0.0, 0.0, 0.0])
        rot_deg   = env_asset.get("rotation", [0.0, 0.0, 0.0])
        scale     = env_asset.get("scale",    [1.0, 1.0, 1.0])

        if asset_id not in resolved:
            report["errors"].append(f"asset de ambiente '{asset_id}' não resolvido")
            continue

        glb_path = resolved[asset_id]
        ok, err, objects = _import_glb(glb_path)
        if not ok:
            report["errors"].append(f"falha ao importar '{asset_id}': {err}")
            continue

        # Determinar coleção destino baseada no nome do asset
        col_name = "ENVIRONMENT"
        name_lo  = asset_id.lower()
        if any(k in name_lo for k in ("road", "street", "estrada")):
            col_name = "ROADS"
        elif any(k in name_lo for k in ("tree", "bush", "plant", "arvore", "vegetation")):
            col_name = "VEGETATION"
        elif any(k in name_lo for k in ("building", "house", "edif", "tower")):
            col_name = "BUILDINGS"
        elif any(k in name_lo for k in ("prop", "barrel", "chest", "bench")):
            col_name = "PROPS"

        _move_to_collection(objects, cols[col_name])
        _apply_transform(objects, position, rot_deg, scale)

        imported_env_count += 1
        print(f"  [ambiente] '{asset_id}' importado → coleção {col_name}  "
              f"pos={position}")

    report["scene_assets_imported"] = imported_env_count

    # ── Importar personagens ──────────────────────────────────────────────
    char_objects_all: List[Any] = []
    char_arm_obj: Optional[Any] = None
    char_action:  Optional[Any] = None

    for char_cfg in cfg.get("characters", []):
        asset_id  = char_cfg["asset_id"]
        position  = char_cfg.get("position",  [0.0, 0.0, 0.0])
        anim_name = char_cfg.get("animation", "")

        if asset_id not in resolved:
            report["errors"].append(f"personagem '{asset_id}' não resolvida")
            continue

        ok, err, objects = _import_glb(resolved[asset_id])
        if not ok:
            report["errors"].append(f"falha ao importar personagem '{asset_id}': {err}")
            continue

        _move_to_collection(objects, cols["CHARACTERS"])
        _apply_transform(objects, position, [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])

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
                    print(f"  [personagem] '{asset_id}' animada com '{action.name}'")
                except Exception as e:
                    report["warnings"].append(
                        f"animação '{anim_name}' não aplicada: {e}"
                    )
            else:
                avail = [a.name for a in bpy.data.actions[:5]]
                report["warnings"].append(
                    f"animação '{anim_name}' não encontrada. "
                    f"Disponíveis (5 primeiras): {avail}"
                )

    # ── Bounds para câmara e iluminação ──────────────────────────────────
    all_bounds  = _scene_bounds()
    char_bounds = (_character_bounds(char_objects_all)
                   if char_objects_all else all_bounds)

    # ── Câmara ────────────────────────────────────────────────────────────
    cam_mode = cfg.get("camera", {}).get("mode", "cinematic")

    # Para cinematic/character_focus: usar bounds do personagem para enquadramento
    # Para scene_overview: usar bounds de toda a cena
    bounds_for_cam = (char_bounds if cam_mode in ("cinematic", "character_focus")
                      else all_bounds)
    cam_obj = _place_camera_for_bounds(scene, bounds_for_cam, cam_mode, report)

    # Mover câmara para coleção CAMERAS
    for col in list(cam_obj.users_collection):
        col.objects.unlink(cam_obj)
    cols["CAMERAS"].objects.link(cam_obj)

    # ── Iluminação ────────────────────────────────────────────────────────
    bounds_for_light = all_bounds

    if lighting_preset == "daylight":
        _setup_daylight(scene, bounds_for_light, report)
    else:
        _setup_three_point(scene, bounds_for_light, cam_obj, lighting_preset, report)

    # Chão — só se não houver assets de pavimento na cena
    has_road_assets = any(
        any(k in a["asset_id"].lower() for k in ("road","street","floor","floor","pavement"))
        for a in env_assets
    )
    if not has_road_assets:
        _add_ground_plane(scene, all_bounds, lighting_preset, report)

    # ── Render engine ─────────────────────────────────────────────────────
    _setup_render(scene, resolution, fps)

    # ── Ajuste de enquadramento do personagem ─────────────────────────────
    if char_objects_all:
        _adjust_camera_framing(scene, cam_obj, char_objects_all,
                                margin=0.12, max_iter=6, report=report)

    # ── Selecionar frames ─────────────────────────────────────────────────
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

    scene.frame_start = fr_start
    scene.frame_end   = fr_end

    # ── Renderizar preview PNG ────────────────────────────────────────────
    if render_preview:
        mid_frame  = frames_to_render[len(frames_to_render) // 2]
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
            report["errors"].append(
                f"erro ao renderizar preview:\n{traceback.format_exc(limit=5)}"
            )
        report["render_time_s"] = round(time.time() - t0, 1)

    # ── Renderizar animação → frames → MP4 ───────────────────────────────
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
                report["warnings"].append(
                    f"frame {frame} não renderizado:\n{traceback.format_exc(limit=3)}"
                )
                break

        report["frames_rendered"]  = len(rendered_paths)
        report["frames_requested"] = len(frames_to_render)
        report["render_time_s"]    = round(time.time() - t0, 1)

        if rendered_paths:
            import subprocess
            video_path = output_dir / f"{scene_name}.mp4"
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", str(frames_dir / "frame_%04d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "fast",
                "-crf", "23",
                "-movflags", "+faststart",
                str(video_path),
            ]
            try:
                r = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=300)
                if r.returncode == 0 and video_path.is_file():
                    report["video_path"] = str(video_path)
                    print(f"  [MP4] gerado: {video_path}")
                else:
                    report["errors"].append(f"FFmpeg falhou: {r.stderr[:500]}")
            except Exception as e:
                report["errors"].append(f"FFmpeg não executou: {e}")

            # Limpar frames temporários
            import shutil
            try:
                shutil.rmtree(frames_dir, ignore_errors=True)
            except Exception:
                pass
        else:
            report["errors"].append("nenhum frame renderizado — MP4 não gerado")

    # ── Exportar GLB ──────────────────────────────────────────────────────
    if do_export_glb:
        glb_path = output_dir / f"{scene_name}.glb"
        _export_glb(glb_path, report)

    # ── Guardar relatório e imprimir sumário ──────────────────────────────
    _save_report(report, args.report)
    _print_summary(report)

    failed = bool(report["errors"])
    return 1 if failed else 0


def _print_summary(report: Dict[str, Any]) -> None:
    r = report
    chk = lambda b: "✅" if b else "❌"
    print("\n" + "=" * 64)
    print(f"CENA 3D: {r['scene_name']}  (Blender {r['blender_version']})")
    print("=" * 64)
    print(f"  assets de ambiente:  {r['scene_assets_imported']}")
    print(f"  personagem:          {chk(r['character_imported'])}")
    print(f"  animação:            {chk(r['character_animated'])}")
    print(f"  câmara ({r['camera_mode']}):  {chk(r['camera_created'])}")
    print(f"  enquadramento:       {chk(r['framing_success'])}")
    print(f"  iluminação ({r['lighting_preset']}): {r['lights_created']} luzes")
    print(f"  chão:                {chk(r['ground_created'])}")
    print(f"  preview PNG:         {chk(r['preview_success'])}")
    if r.get("frames_requested"):
        print(f"  frames:              {r['frames_rendered']}/{r['frames_requested']}")
    if r.get("video_path"):
        print(f"  MP4:                 ✅ {r['video_path']}")
    if r.get("glb_exported"):
        print(f"  GLB:                 ✅ {r['glb_path']}")
        print(f"  GLB reimportável:    {chk(r.get('glb_importable', False))}")
    if r["errors"]:
        print("\nERROS:")
        for e in r["errors"]:
            print(f"  ! {str(e)[:300]}")
    if r["warnings"]:
        print("\nAVISOS:")
        for w in r["warnings"]:
            print(f"  ~ {w}")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    sys.exit(main())
