#!/usr/bin/env python3
"""Pipeline de animação 3D — Fase 4 Rev2: enquadramento real + chão visível + iluminação corrigida.

Uso (pelo GitHub Actions — Blender em modo background):
  blender --background --python library/tools/animate_character.py -- \
      --glb path/to/model.glb \
      --animation "1H_Melee_Attack_Chop" \
      --fps 24 \
      --max-frames 72 \
      --resolution 640 \
      --frames-dir /tmp/frames \
      --preview path/to/preview.png \
      --report path/to/animation-report.json \
      [--asset-id kaykit_adv_knight] \
      [--visual-style studio|cinematic] \
      [--camera-mode auto] \
      [--ground-enabled true|false] \
      [--lighting-quality medium|low]

Compatível com Blender 3.6 / 4.x / 5.x (inclui Action Slots do 5.x).
Não toca em scripts históricos, modelos B5-B9 nem workflows existentes.
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
    _sep = sys.argv.index("--")
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
_HAS_SLOTS = _BPY_MAJOR >= 5


# ════════════════════════════════════════════════════════════════════════════════
# RELATÓRIO
# ════════════════════════════════════════════════════════════════════════════════

def _empty_report(asset_id: str, glb_path: str, animation_name: str) -> Dict[str, Any]:
    return {
        "asset_id": asset_id,
        "glb_path": glb_path,
        "animation_name_requested": animation_name,
        "animation_name_used": None,
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "import_success": False,
        "bone_count": 0,
        "animation_count": 0,
        "animation_found": False,
        "animation_applied": False,
        "movement_confirmed": False,
        "bones_moved": 0,
        "frame_start": 0,
        "frame_end": 0,
        "frames_rendered": 0,
        "frames_requested": 0,
        "render_fps": 0,
        "render_resolution": 0,
        "preview_success": False,
        "preview_path": None,
        "ffmpeg_success": False,
        "ffmpeg_output": None,
        "video_path": None,
        "video_duration_s": None,
        "video_frames_detected": None,
        # Fase 4
        "visual_style": "studio",
        "camera_mode": "auto",
        "camera_created": False,
        "framing_margin": 0.15,
        "frames_checked": 0,
        "framing_success": False,
        "minimum_projected_x": None,
        "maximum_projected_x": None,
        "minimum_projected_y": None,
        "maximum_projected_y": None,
        "camera_adjustments": 0,
        "framing_frames_sampled": 0,   # mantido por compatibilidade
        "framing_verified": False,     # mantido por compatibilidade
        "lights_created": 0,
        "ground_enabled": False,
        "ground_created": False,
        "ground_z": None,
        "ground_visible": None,
        "render_time_s": None,
        "errors": [],
        "warnings": [],
    }


# ════════════════════════════════════════════════════════════════════════════════
# CENA / IMPORT
# ════════════════════════════════════════════════════════════════════════════════

def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _import_glb(path: str) -> Optional[str]:
    p = pathlib.Path(path)
    if not p.is_file():
        return f"ficheiro não encontrado: {path}"
    if p.stat().st_size == 0:
        return f"ficheiro vazio: {path}"
    try:
        r = bpy.ops.import_scene.gltf(filepath=str(p))
        if r != {"FINISHED"}:
            return f"importação retornou: {r}"
        return None
    except Exception:
        return traceback.format_exc(limit=10)


def _get_armature() -> Optional[Any]:
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            return o
    return None


# ════════════════════════════════════════════════════════════════════════════════
# ACTION SLOTS (Blender 5.x)
# ════════════════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════════════════
# GEOMETRIA DEFORMADA — bounds reais por frame com depsgraph
# ════════════════════════════════════════════════════════════════════════════════

def _get_deformed_world_verts(frame: int) -> List[mathutils.Vector]:
    """
    Obtém os vértices *deformados* (pose aplicada) de todos os meshes
    no espaço mundial, usando o dependency graph do Blender.
    Garante a libertação das malhas temporárias.
    """
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()

    all_verts: List[mathutils.Vector] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        try:
            obj_eval = obj.evaluated_get(depsgraph)
            mesh_eval = obj_eval.to_mesh()
            if mesh_eval is None:
                continue
            mat = obj_eval.matrix_world
            for v in mesh_eval.vertices:
                all_verts.append(mat @ v.co)
            obj_eval.to_mesh_clear()
        except Exception:
            pass
    return all_verts


def _bounds_from_verts(
        verts: List[mathutils.Vector]
) -> Optional[Tuple[float, float, float, float, float, float]]:
    if not verts:
        return None
    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    zs = [v.z for v in verts]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _compute_animation_bounds(
        frames_to_sample: List[int],
        report: Dict[str, Any]
) -> Tuple[float, float, float, float, float, float]:
    """Envelope máximo da geometria deformada ao longo dos frames amostrados."""
    gxmn, gxmx = math.inf, -math.inf
    gymn, gymx = math.inf, -math.inf
    gzmn, gzmx = math.inf, -math.inf
    sampled = 0

    for f in frames_to_sample:
        verts = _get_deformed_world_verts(f)
        b = _bounds_from_verts(verts)
        if b is None:
            continue
        xmn, xmx, ymn, ymx, zmn, zmx = b
        gxmn = min(gxmn, xmn); gxmx = max(gxmx, xmx)
        gymn = min(gymn, ymn); gymx = max(gymx, ymx)
        gzmn = min(gzmn, zmn); gzmx = max(gzmx, zmx)
        sampled += 1

    report["framing_frames_sampled"] = sampled

    if sampled == 0 or math.isinf(gxmn):
        bpy.context.scene.frame_set(frames_to_sample[0] if frames_to_sample else 0)
        bpy.context.view_layer.update()
        verts = _get_deformed_world_verts(frames_to_sample[0] if frames_to_sample else 0)
        b = _bounds_from_verts(verts)
        return b if b else (-1.0, 1.0, -1.0, 1.0, 0.0, 2.0)

    return (gxmn, gxmx, gymn, gymx, gzmn, gzmx)


# ════════════════════════════════════════════════════════════════════════════════
# CÂMARA — posicionamento e ajuste automático por projecção real
# ════════════════════════════════════════════════════════════════════════════════

def _place_camera(scene: Any,
                  bounds: Tuple[float, float, float, float, float, float],
                  margin: float,
                  style: str,
                  report: Dict[str, Any]) -> Any:
    """
    Cria câmara posicionada para enquadrar o envelope completo da animação.
    Distância calculada a partir da esfera envolvente + margem de 30% extra
    para compensar a projecção de vértices extremos.
    Devolve o objecto câmara.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0
    cz = (zmn + zmx) / 2.0
    dx = xmx - xmn or 0.1
    dy = ymx - ymn or 0.1
    dz = zmx - zmn or 0.1

    radius = math.sqrt(dx*dx + dy*dy + dz*dz) / 2.0
    # Margem generosa: envelope + 30% extra (cobre vértices extremos em projecção)
    radius_safe = radius * 1.30

    # FOV 50 mm (mais fechado → menos distorção nos extremos)
    fov_rad = math.radians(46.8)
    cam_dist = radius_safe / math.sin(fov_rad / 2.0)

    if style == "cinematic":
        elev = math.radians(12)
        azim = math.radians(40)
    else:
        elev = math.radians(18)
        azim = math.radians(35)

    cam_x = cx + cam_dist * math.cos(elev) * math.sin(azim)
    cam_y = cy - cam_dist * math.cos(elev) * math.cos(azim)
    cam_z = cz + cam_dist * math.sin(elev)

    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam_obj = bpy.context.active_object
    cam_obj.name = "CineCam"

    direction = mathutils.Vector((cx, cy, cz)) - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    cam_obj.data.lens       = 50.0
    cam_obj.data.clip_start = 0.01
    cam_obj.data.clip_end   = cam_dist * 10.0

    scene.camera = cam_obj
    report["camera_created"] = True
    print(f"  [câmara] dist={cam_dist:.2f}  centro=({cx:.2f},{cy:.2f},{cz:.2f})"
          f"  radius_safe={radius_safe:.2f}  focal=50mm")
    return cam_obj


def _project_verts_to_camera(
        scene: Any,
        cam_obj: Any,
        verts: List[mathutils.Vector]
) -> Tuple[float, float, float, float]:
    """
    Projeta vértices nas coordenadas normalizadas da câmara [0,1].
    Devolve (min_x, max_x, min_y, max_y) dos pontos à frente da câmara.
    """
    min_x, max_x = math.inf, -math.inf
    min_y, max_y = math.inf, -math.inf
    for v in verts:
        co = world_to_camera_view(scene, cam_obj, v)
        # co.z > 0 significa que o ponto está à frente da câmara
        if co.z > 0:
            min_x = min(min_x, co.x)
            max_x = max(max_x, co.x)
            min_y = min(min_y, co.y)
            max_y = max(max_y, co.y)
    if math.isinf(min_x):
        return (0.5, 0.5, 0.5, 0.5)
    return (min_x, max_x, min_y, max_y)


def _verify_and_adjust_framing(
        scene: Any,
        cam_obj: Any,
        frames_to_check: List[int],
        margin: float,
        report: Dict[str, Any],
        max_iterations: int = 8
) -> None:
    """
    Verifica o enquadramento real projetando todos os vértices deformados
    em todos os frames indicados. Afasta a câmara iterativamente se necessário.
    Actualiza report com métricas detalhadas.
    """
    safe_lo = margin          # 0.15
    safe_hi = 1.0 - margin    # 0.85
    adjustments = 0

    for iteration in range(max_iterations + 1):
        all_min_x, all_max_x = math.inf, -math.inf
        all_min_y, all_max_y = math.inf, -math.inf
        frames_ok = 0

        for f in frames_to_check:
            verts = _get_deformed_world_verts(f)
            if not verts:
                continue
            mn_x, mx_x, mn_y, mx_y = _project_verts_to_camera(scene, cam_obj, verts)
            all_min_x = min(all_min_x, mn_x)
            all_max_x = max(all_max_x, mx_x)
            all_min_y = min(all_min_y, mn_y)
            all_max_y = max(all_max_y, mx_y)
            if mn_x >= safe_lo and mx_x <= safe_hi and mn_y >= safe_lo and mx_y <= safe_hi:
                frames_ok += 1

        in_safe = (
            (not math.isinf(all_min_x)) and
            all_min_x >= safe_lo and all_max_x <= safe_hi and
            all_min_y >= safe_lo and all_max_y <= safe_hi
        )

        # Guardar métricas no relatório (valores do melhor estado atingido)
        if not math.isinf(all_min_x):
            report["minimum_projected_x"] = round(all_min_x, 4)
            report["maximum_projected_x"] = round(all_max_x, 4)
            report["minimum_projected_y"] = round(all_min_y, 4)
            report["maximum_projected_y"] = round(all_max_y, 4)

        report["frames_checked"]    = len(frames_to_check)
        report["framing_success"]   = in_safe
        report["framing_verified"]  = in_safe
        report["camera_adjustments"] = adjustments

        if in_safe:
            print(f"  [enquadramento] OK após {adjustments} ajuste(s)  "
                  f"x=[{all_min_x:.3f},{all_max_x:.3f}]  "
                  f"y=[{all_min_y:.3f},{all_max_y:.3f}]  "
                  f"frames_ok={frames_ok}/{len(frames_to_check)}")
            return

        if iteration == max_iterations:
            report["warnings"].append(
                f"enquadramento não garantido após {max_iterations} ajustes: "
                f"x=[{all_min_x:.3f},{all_max_x:.3f}]  "
                f"y=[{all_min_y:.3f},{all_max_y:.3f}]"
            )
            print(f"  [enquadramento] FALHOU após {max_iterations} ajustes")
            return

        # Calcular factor de afastamento necessário
        # Margem de quanto ultrapassou a zona segura em cada eixo
        overshoot = max(
            safe_lo - all_min_x if all_min_x < safe_lo else 0,
            all_max_x - safe_hi if all_max_x > safe_hi else 0,
            safe_lo - all_min_y if all_min_y < safe_lo else 0,
            all_max_y - safe_hi if all_max_y > safe_hi else 0,
        )
        # Factor conservador: afastar 15% por iteração mínimo, até 40% se overshoot grande
        factor = 1.0 + max(0.15, min(0.40, overshoot * 1.5))

        cam_pivot = mathutils.Vector(cam_obj.location)
        # Centro do personagem (médio dos bounds acumulados — aproximação)
        bpy.context.scene.frame_set(frames_to_check[len(frames_to_check)//2])
        bpy.context.view_layer.update()
        verts_mid = _get_deformed_world_verts(frames_to_check[len(frames_to_check)//2])
        b = _bounds_from_verts(verts_mid)
        if b:
            target = mathutils.Vector(((b[0]+b[1])/2, (b[2]+b[3])/2, (b[4]+b[5])/2))
        else:
            target = mathutils.Vector((0, 0, 1))

        direction = (cam_pivot - target).normalized()
        dist_current = (cam_pivot - target).length
        cam_obj.location = target + direction * (dist_current * factor)
        adjustments += 1
        print(f"  [enquadramento] ajuste {adjustments}: factor={factor:.2f}  "
              f"overshoot={overshoot:.3f}  "
              f"x=[{all_min_x:.3f},{all_max_x:.3f}]  "
              f"y=[{all_min_y:.3f},{all_max_y:.3f}]")


# ════════════════════════════════════════════════════════════════════════════════
# CHÃO — posicionamento ao nível dos pés (não da espada)
# ════════════════════════════════════════════════════════════════════════════════

def _estimate_foot_z(arm_obj: Any, frames_sample: List[int]) -> Optional[float]:
    """
    Estima a altura do chão procurando ossos de pé na armature.
    Nomes típicos KayKit: foot, toe, ankle (case-insensitive).
    Se não encontrar, devolve None e o chamador usa o fallback.
    """
    foot_keywords = ("foot", "toe", "ankle", "feet", "pe", "pé")
    foot_bones = [
        pb for pb in arm_obj.pose.bones
        if any(k in pb.name.lower() for k in foot_keywords)
    ]
    if not foot_bones:
        return None

    foot_zs = []
    for f in frames_sample:
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        for pb in foot_bones:
            wm = arm_obj.matrix_world @ pb.matrix
            foot_zs.append(wm.translation.z)

    if not foot_zs:
        return None
    # Usar o mínimo (pé mais baixo durante toda a animação)
    return min(foot_zs)


def _add_ground(scene: Any,
                bounds: Tuple[float, float, float, float, float, float],
                arm_obj: Any,
                frames_sample: List[int],
                style: str,
                report: Dict[str, Any]) -> None:
    """
    Chão posicionado ao nível dos pés (não do ponto mais baixo de toda a geometria).
    O ponto mais baixo pode ser a espada — nesse caso o chão ficaria abaixo dos pés.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0

    # Tentar detectar pés pela armature
    foot_z = _estimate_foot_z(arm_obj, frames_sample)

    if foot_z is not None:
        ground_z = foot_z - 0.005
        method = "ossos de pé detectados"
    else:
        # Fallback: usar o terço inferior da geometria
        # (evita espada abaixada, que pode ser o zmn real)
        char_height = zmx - zmn or 1.0
        ground_z = zmn + char_height * 0.05
        # Se mesmo assim parecer demasiado abaixo (>15% abaixo do zmn global),
        # fixar em zmn
        ground_z = max(ground_z, zmn - 0.01)
        method = "fallback (sem ossos de pé identificados)"

    report["ground_z"] = round(ground_z, 4)

    # Dimensão: 5× a maior dimensão horizontal, mínimo 5 m
    char_width = max(xmx - xmn, ymx - ymn) or 1.0
    plane_size = max(char_width * 5.0, 5.0)

    try:
        bpy.ops.mesh.primitive_plane_add(size=plane_size, location=(cx, cy, ground_z))
        plane = bpy.context.active_object
        plane.name = "Ground"

        mat = bpy.data.materials.new("GroundMat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            if style == "cinematic":
                bsdf.inputs["Base Color"].default_value = (0.08, 0.08, 0.10, 1.0)
            else:
                # Studio: cinzento médio-claro — claramente visível mas não distrai
                bsdf.inputs["Base Color"].default_value = (0.32, 0.32, 0.35, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.90
            for spec_key in ("Specular IOR Level", "Specular"):
                try:
                    bsdf.inputs[spec_key].default_value = 0.03
                    break
                except KeyError:
                    pass

        if plane.data.materials:
            plane.data.materials[0] = mat
        else:
            plane.data.materials.append(mat)

        plane.visible_shadow = True
        report["ground_created"] = True
        report["ground_visible"] = True   # criado e posicionado correctamente
        print(f"  [chão] z={ground_z:.4f}  método={method}  tamanho={plane_size:.2f}")

    except Exception as e:
        report["warnings"].append(f"chão não criado: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# ILUMINAÇÃO — três pontos com energia relativa à distância
# ════════════════════════════════════════════════════════════════════════════════

def _setup_lighting(scene: Any,
                    bounds: Tuple[float, float, float, float, float, float],
                    cam_obj: Any,
                    style: str,
                    quality: str,
                    report: Dict[str, Any]) -> None:
    """
    Três pontos: Key + Fill + Rim.
    A energia é escalada pela distância ao personagem para que a
    iluminação percebida seja consistente independentemente do tamanho do personagem.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0
    cz = (zmn + zmx) / 2.0
    h  = zmx - zmn or 2.0
    r  = max(xmx - xmn, ymx - ymn) or 1.0

    # Tamanho das luzes Area: 80% da altura do personagem
    area_size = max(h * 0.8, 0.5)

    # Distância de referência para calibrar energia
    # (baseada na câmara — luzes serão colocadas a distância similar)
    cam_dist = (mathutils.Vector(cam_obj.location) - mathutils.Vector((cx, cy, cz))).length
    # Escala de energia: ajustada para que a 1 m de distância seja referência
    # Luzes Area em EEVEE: energia em W. A ~3-5 m de distância, 800 W é razoável.
    energy_scale = max(1.0, (cam_dist / 3.0) ** 2)

    lights_ok = 0

    def _add_area(name: str, loc: tuple, base_energy: float,
                  color: tuple = (1.0, 1.0, 1.0)) -> Optional[Any]:
        nonlocal lights_ok
        energy = base_energy * energy_scale
        try:
            bpy.ops.object.light_add(type="AREA", location=loc)
            light = bpy.context.active_object
            light.name = name
            light.data.energy = energy
            light.data.size   = area_size
            light.data.color  = color
            direction = mathutils.Vector((cx, cy, cz)) - light.location
            light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            lights_ok += 1
            print(f"  [luz] {name}  energy={energy:.0f}W  size={area_size:.2f}m")
            return light
        except Exception as e:
            report["warnings"].append(f"luz {name} Area falhou: {e}; fallback Sun")
            try:
                bpy.ops.object.light_add(type="SUN", location=loc)
                light = bpy.context.active_object
                light.name = name
                light.data.energy = max(3.0, base_energy * 0.008)
                light.data.color  = color
                lights_ok += 1
                return light
            except Exception as e2:
                report["warnings"].append(f"luz {name} Sun falhou: {e2}")
                return None

    if style == "cinematic":
        _add_area("KeyLight",  (cx + r*1.5, cy - r*1.2, cz + h*0.9),
                  base_energy=80.0, color=(1.0, 0.97, 0.90))
        _add_area("FillLight", (cx - r*1.8, cy - r*0.5, cz + h*0.4),
                  base_energy=18.0, color=(0.85, 0.90, 1.0))
        _add_area("RimLight",  (cx - r*0.3, cy + r*2.0, cz + h*1.1),
                  base_energy=45.0, color=(0.7, 0.8, 1.0))
    else:  # studio — iluminação equilibrada e clara
        _add_area("KeyLight",  (cx + r*1.4, cy - r*1.0, cz + h*0.8),
                  base_energy=70.0, color=(1.0, 0.98, 0.95))
        _add_area("FillLight", (cx - r*1.6, cy - r*0.4, cz + h*0.5),
                  base_energy=40.0, color=(0.95, 0.97, 1.0))
        _add_area("RimLight",  (cx - r*0.2, cy + r*1.8, cz + h*1.0),
                  base_energy=35.0, color=(1.0, 1.0, 1.0))

    report["lights_created"] = lights_ok


# ════════════════════════════════════════════════════════════════════════════════
# MUNDO / FUNDO
# ════════════════════════════════════════════════════════════════════════════════

def _setup_world(scene: Any, style: str) -> None:
    world = bpy.data.worlds.new("AnimWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        if style == "cinematic":
            bg.inputs["Color"].default_value    = (0.03, 0.03, 0.05, 1.0)
            bg.inputs["Strength"].default_value = 0.05
        else:
            bg.inputs["Color"].default_value    = (0.20, 0.21, 0.24, 1.0)
            bg.inputs["Strength"].default_value = 0.25
    scene.world = world


# ════════════════════════════════════════════════════════════════════════════════
# MOTOR DE RENDER
# ════════════════════════════════════════════════════════════════════════════════

def _setup_render_engine(scene: Any, style: str, quality: str, res: int) -> None:
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = eng
            break
        except Exception:
            continue

    engine = scene.render.engine

    if "CYCLES" in engine:
        scene.cycles.samples       = 32 if quality == "medium" else 16
        scene.cycles.use_denoising = False
        scene.cycles.device        = "CPU"

    elif "EEVEE" in engine and hasattr(scene, "eevee"):
        ev = scene.eevee
        samples = 16 if quality == "medium" else 8
        for attr, val in [
            ("taa_render_samples",  samples),
            ("use_bloom",           False),
            ("use_gtao",            True),
            ("use_ssr",             False),
            ("shadow_cube_size",    "1024"),
            ("shadow_cascade_size", "1024"),
        ]:
            if hasattr(ev, attr):
                try:
                    setattr(ev, attr, val)
                except Exception:
                    pass
        if hasattr(ev, "use_shadow"):
            try:
                ev.use_shadow = True
            except Exception:
                pass

    scene.render.resolution_x          = res
    scene.render.resolution_y          = res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False

    # Gestão de cor: Filmic para maior latitude dinâmica
    try:
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look           = "None"
        scene.view_settings.exposure       = 0.0
        scene.view_settings.gamma          = 1.0
    except Exception:
        pass

    print(f"  [render] motor={engine}  res={res}×{res}  qualidade={quality}")


# ════════════════════════════════════════════════════════════════════════════════
# VERIFICAÇÃO DE MOVIMENTO
# ════════════════════════════════════════════════════════════════════════════════

def _check_movement(arm_obj: Any, frame_start: int, frame_mid: int) -> int:
    bpy.context.scene.frame_set(frame_start)
    bpy.context.view_layer.update()
    m_start = {pb.name: [row[:] for row in pb.matrix] for pb in arm_obj.pose.bones}
    bpy.context.scene.frame_set(frame_mid)
    bpy.context.view_layer.update()
    moved = 0
    for pb in arm_obj.pose.bones:
        ms = m_start.get(pb.name)
        if ms is None:
            continue
        delta = max(abs(ms[ri][ci] - pb.matrix[ri][ci]) for ri in range(4) for ci in range(4))
        if delta > 1e-4:
            moved += 1
    return moved


# ════════════════════════════════════════════════════════════════════════════════
# RENDERIZAÇÃO DE FRAMES
# ════════════════════════════════════════════════════════════════════════════════

def _render_frames(scene: Any,
                   frames_to_render: List[int],
                   frames_dir: pathlib.Path,
                   report: Dict[str, Any]) -> List[pathlib.Path]:
    rendered: List[pathlib.Path] = []
    frames_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    for i, frame in enumerate(frames_to_render):
        out_path = frames_dir / f"frame_{i:04d}.png"
        scene.render.filepath = str(out_path)
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        try:
            bpy.ops.render.render(write_still=True)
            if out_path.is_file() and out_path.stat().st_size > 0:
                rendered.append(out_path)
            else:
                report["warnings"].append(f"frame {frame} renderizado mas PNG ausente")
        except Exception:
            report["errors"].append(
                f"erro ao renderizar frame {frame}:\n{traceback.format_exc(limit=5)}"
            )
            break

    report["render_time_s"] = round(time.time() - t0, 1)
    return rendered


# ════════════════════════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ════════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb",               required=True)
    ap.add_argument("--animation",         default="1H_Melee_Attack_Chop")
    ap.add_argument("--fps",               type=int, default=24)
    ap.add_argument("--max-frames",        type=int, default=72)
    ap.add_argument("--resolution",        type=int, default=640)
    ap.add_argument("--frames-dir",        required=True)
    ap.add_argument("--preview",           default="")
    ap.add_argument("--report",            required=True)
    ap.add_argument("--asset-id",          default="unknown")
    ap.add_argument("--visual-style",      default="studio",
                    choices=["studio", "cinematic"])
    ap.add_argument("--camera-mode",       default="auto")
    ap.add_argument("--ground-enabled",    default="true")
    ap.add_argument("--lighting-quality",  default="medium",
                    choices=["low", "medium"])
    args = ap.parse_args(_argv)

    ground_on = args.ground_enabled.lower() not in ("false", "0", "no")

    report = _empty_report(args.asset_id, args.glb, args.animation)
    report["render_fps"]       = args.fps
    report["render_resolution"] = args.resolution
    report["frames_requested"]  = min(args.max_frames, 72)
    report["visual_style"]      = args.visual_style
    report["camera_mode"]       = args.camera_mode
    report["ground_enabled"]    = ground_on
    report["framing_margin"]    = 0.15

    # 1. Limpar cena
    try:
        _clear_scene()
    except Exception:
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save(report, args.report); return 1

    # 2. Importar GLB
    err = _import_glb(args.glb)
    if err:
        report["errors"].append(f"importação falhou: {err}")
        _save(report, args.report); return 1
    report["import_success"] = True

    # 3. Armature
    arm_obj = _get_armature()
    if arm_obj is None:
        report["errors"].append("armature não encontrada após importação")
        _save(report, args.report); return 1
    report["bone_count"] = len(arm_obj.data.bones)

    # 4. Animações
    actions = list(bpy.data.actions)
    report["animation_count"] = len(actions)
    if not actions:
        report["errors"].append("nenhuma animação encontrada no GLB")
        _save(report, args.report); return 1

    action = _find_action(args.animation)
    if action is None:
        report["warnings"].append(
            f"animação {args.animation!r} não encontrada; a usar a primeira disponível"
        )
        action = actions[0]
    report["animation_found"]     = True
    report["animation_name_used"] = action.name

    # 5. Aplicar animação
    try:
        _assign_action(arm_obj, action)
        report["animation_applied"] = True
    except Exception:
        report["errors"].append(f"erro ao aplicar animação:\n{traceback.format_exc(limit=5)}")
        _save(report, args.report); return 1

    # 6. Intervalo de frames
    frame_range = action.frame_range
    frame_start = int(frame_range[0])
    frame_end   = int(frame_range[1])
    report["frame_start"] = frame_start
    report["frame_end"]   = frame_end

    if frame_end > frame_start:
        frame_mid = frame_start + max(1, (frame_end - frame_start) // 4)
        try:
            moved = _check_movement(arm_obj, frame_start, frame_mid)
            report["bones_moved"]        = moved
            report["movement_confirmed"] = moved > 0
        except Exception:
            report["warnings"].append(f"erro ao verificar movimento:\n{traceback.format_exc(limit=5)}")
    else:
        report["warnings"].append(f"intervalo inválido ({frame_start}–{frame_end})")

    # 7. Selecionar frames a renderizar
    total_anim_frames = max(1, frame_end - frame_start + 1)
    max_frames        = min(args.max_frames, 72)

    if total_anim_frames <= max_frames:
        frames_to_render = list(range(frame_start, frame_end + 1))
    else:
        step = total_anim_frames / max_frames
        frames_to_render = [frame_start + int(i * step) for i in range(max_frames)]
    report["frames_requested"] = len(frames_to_render)

    # 8. Bounds dinâmicos — TODOS os frames a renderizar (animação curta)
    # Para animações longas (>30 frames), amostrar uniformemente.
    if len(frames_to_render) <= 30:
        sample_frames = frames_to_render
    else:
        n_s = 20
        step_s = max(1, len(frames_to_render) // n_s)
        sample_frames = frames_to_render[::step_s][:n_s]
        if frames_to_render[-1] not in sample_frames:
            sample_frames.append(frames_to_render[-1])

    print(f"\n[fase4] a calcular bounds com depsgraph em {len(sample_frames)} frames…")
    bounds = _compute_animation_bounds(sample_frames, report)
    print(f"  bounds: x=[{bounds[0]:.3f},{bounds[1]:.3f}]  "
          f"y=[{bounds[2]:.3f},{bounds[3]:.3f}]  "
          f"z=[{bounds[4]:.3f},{bounds[5]:.3f}]")

    # 9. Configurar cena
    scene = bpy.context.scene
    scene.render.fps = args.fps

    _setup_world(scene, args.visual_style)
    cam_obj = _place_camera(scene, bounds, 0.15, args.visual_style, report)
    _setup_render_engine(scene, args.visual_style, args.lighting_quality, args.resolution)

    # Iluminação após câmara (usa distância da câmara para escalar energia)
    _setup_lighting(scene, bounds, cam_obj, args.visual_style, args.lighting_quality, report)

    if ground_on:
        _add_ground(scene, bounds, arm_obj, sample_frames, args.visual_style, report)

    # 10. Verificar e ajustar enquadramento com projecção real
    print(f"\n[fase4] a verificar enquadramento em {len(sample_frames)} frames…")
    _verify_and_adjust_framing(scene, cam_obj, sample_frames, 0.15, report, max_iterations=8)

    # 11. Renderizar
    frames_dir = pathlib.Path(args.frames_dir)
    rendered   = _render_frames(scene, frames_to_render, frames_dir, report)
    report["frames_rendered"] = len(rendered)

    if not rendered:
        report["errors"].append("nenhum frame foi renderizado")
        _save(report, args.report); return 1

    # 12. Preview PNG
    if args.preview and rendered:
        mid_idx     = len(rendered) // 2
        preview_src = rendered[mid_idx]
        try:
            import shutil
            preview_dst = pathlib.Path(args.preview)
            preview_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(preview_src, preview_dst)
            report["preview_success"] = True
            report["preview_path"]    = args.preview
        except Exception:
            report["warnings"].append(f"erro ao copiar preview:\n{traceback.format_exc(limit=5)}")

    _save(report, args.report)
    _print_summary(report)
    return 0 if rendered else 1


# ════════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ════════════════════════════════════════════════════════════════════════════════

def _save(report: Dict[str, Any], path: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _print_summary(report: Dict[str, Any]) -> None:
    r = report
    print("\n" + "=" * 64)
    print(f"ANIMAÇÃO: {r['asset_id']}  (Blender {r['blender_version']})")
    print("=" * 64)
    print(f"  importação:          {'OK' if r['import_success'] else 'FALHOU'}")
    print(f"  armature:            {r['bone_count']} ossos")
    print(f"  ação encontrada:     {'SIM — ' + str(r['animation_name_used']) if r['animation_found'] else 'NÃO'}")
    print(f"  ação aplicada:       {'SIM' if r['animation_applied'] else 'NÃO'}")
    print(f"  movimento conf.:     {'SIM (' + str(r['bones_moved']) + ' ossos)' if r['movement_confirmed'] else 'NÃO'}")
    print(f"  estilo visual:       {r['visual_style']}")
    print(f"  câmara criada:       {'SIM' if r['camera_created'] else 'NÃO'}")
    print(f"  ajustes câmara:      {r['camera_adjustments']}")
    print(f"  enquadramento ok:    {'SIM' if r['framing_success'] else 'NÃO'}  "
          f"({r['frames_checked']} frames verificados)")
    if r.get("minimum_projected_x") is not None:
        print(f"  proj x:              [{r['minimum_projected_x']:.3f}, {r['maximum_projected_x']:.3f}]")
        print(f"  proj y:              [{r['minimum_projected_y']:.3f}, {r['maximum_projected_y']:.3f}]")
    print(f"  luzes:               {r['lights_created']}")
    print(f"  chão:                {'SIM (z=' + str(r.get('ground_z','?')) + ')' if r['ground_created'] else 'NÃO'}")
    print(f"  frames pedidos:      {r['frames_requested']}")
    print(f"  frames rend.:        {r['frames_rendered']}")
    print(f"  tempo de render:     {r.get('render_time_s', '?')}s")
    print(f"  preview:             {'OK → ' + str(r['preview_path']) if r['preview_success'] else '—'}")
    if r["errors"]:
        print("\nERROS:")
        for e in r["errors"]:
            print(f"  ! {e[:300]}")
    if r["warnings"]:
        print("\nAVISOS:")
        for w in r["warnings"]:
            print(f"  ~ {w}")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    sys.exit(main())
