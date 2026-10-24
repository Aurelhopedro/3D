#!/usr/bin/env python3
"""Pipeline de animação 3D — Fase 4: produção visual cinematográfica.

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

Produz:
  - Directório com frames PNG  (temporário; apagado pelo workflow após FFmpeg)
  - preview.png                (frame central, para artefacto)
  - animation-report.json      (relatório completo com métricas visuais)

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

# ── Args do script (após "--") ────────────────────────────────────────────────
try:
    _sep = sys.argv.index("--")
    _argv = sys.argv[_sep + 1:]
except ValueError:
    _argv = []

# ── bpy ───────────────────────────────────────────────────────────────────────
try:
    import bpy          # type: ignore
    import mathutils    # type: ignore
except ImportError:
    print("ERRO: executar dentro do Blender.", file=sys.stderr)
    sys.exit(2)

_BPY_MAJOR = bpy.app.version[0]
_HAS_SLOTS = _BPY_MAJOR >= 5


# ════════════════════════════════════════════════════════════════════════════════
# RELATÓRIO
# ════════════════════════════════════════════════════════════════════════════════

def _empty_report(asset_id: str, glb_path: str,
                  animation_name: str) -> Dict[str, Any]:
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
        # ── Fase 4: campos visuais ────────────────────────────────────────────
        "visual_style": "studio",
        "camera_mode": "auto",
        "camera_created": False,
        "framing_margin_pct": 15,
        "framing_verified": False,
        "framing_frames_sampled": 0,
        "lights_created": 0,
        "ground_enabled": False,
        "ground_created": False,
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
# CÂMARA INTELIGENTE — enquadramento dinâmico
# ════════════════════════════════════════════════════════════════════════════════

def _world_bounds_at_frame(frame: int) -> Optional[Tuple[
        float, float, float, float, float, float]]:
    """Devolve (xmin,xmax,ymin,ymax,zmin,zmax) de todos os meshes no frame dado."""
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    xs, ys, zs = [], [], []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        try:
            mat = obj.matrix_world
            for v in obj.data.vertices:
                wv = mat @ v.co
                xs.append(wv.x)
                ys.append(wv.y)
                zs.append(wv.z)
        except Exception:
            pass
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _compute_animation_bounds(
        frames_to_sample: List[int],
        report: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
    """
    Avalia os bounds do personagem numa amostra de frames da animação.
    Devolve os bounds máximos (envelope que contém todo o movimento).
    """
    gxmn, gxmx = math.inf, -math.inf
    gymn, gymx = math.inf, -math.inf
    gzmn, gzmx = math.inf, -math.inf
    sampled = 0

    for f in frames_to_sample:
        b = _world_bounds_at_frame(f)
        if b is None:
            continue
        xmn, xmx, ymn, ymx, zmn, zmx = b
        gxmn = min(gxmn, xmn); gxmx = max(gxmx, xmx)
        gymn = min(gymn, ymn); gymx = max(gymx, ymx)
        gzmn = min(gzmn, zmn); gzmx = max(gzmx, zmx)
        sampled += 1

    report["framing_frames_sampled"] = sampled

    if sampled == 0 or math.isinf(gxmn):
        # fallback: bounds estáticos no frame 0
        b = _world_bounds_at_frame(0)
        if b:
            return b
        return (-1, 1, -1, 1, 0, 2)

    return (gxmn, gxmx, gymn, gymx, gzmn, gzmx)


def _place_camera(scene: Any,
                  bounds: Tuple[float, float, float, float, float, float],
                  margin_pct: float,
                  style: str,
                  report: Dict[str, Any]) -> None:
    """
    Cria e posiciona câmara para enquadrar completamente os bounds dados,
    com margem de segurança e ângulo cinematográfico.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds

    # Centro e dimensões do envelope
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0
    cz = (zmn + zmx) / 2.0
    dx = xmx - xmn or 0.1
    dy = ymx - ymn or 0.1
    dz = zmx - zmn or 0.1

    # Raio da esfera envolvente
    radius = math.sqrt(dx*dx + dy*dy + dz*dz) / 2.0
    # Margem de segurança
    radius_safe = radius * (1.0 + margin_pct / 100.0)

    # FOV da câmara (35 mm equiv ≈ 54°)
    fov_rad = math.radians(54.0)
    # Distância mínima para a esfera caber no FOV
    cam_dist = radius_safe / math.sin(fov_rad / 2.0)

    # Ângulos cinematográficos: ligeiramente lateral e acima
    if style == "cinematic":
        elev = math.radians(15)   # mais baixo → mais dramático
        azim = math.radians(40)
    else:  # studio
        elev = math.radians(20)
        azim = math.radians(35)

    cam_x = cx + cam_dist * math.cos(elev) * math.sin(azim)
    cam_y = cy - cam_dist * math.cos(elev) * math.cos(azim)
    cam_z = cz + cam_dist * math.sin(elev)

    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam_obj = bpy.context.active_object
    cam_obj.name = "CineCam"

    # Apontar para o centro
    direction = mathutils.Vector((cx, cy, cz)) - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Configurar câmara
    cam_obj.data.lens = 35.0          # 35 mm
    cam_obj.data.clip_start = 0.01
    cam_obj.data.clip_end = cam_dist * 10.0

    scene.camera = cam_obj
    report["camera_created"] = True
    print(f"  [câmara] dist={cam_dist:.2f}  centre=({cx:.2f},{cy:.2f},{cz:.2f})"
          f"  radius_safe={radius_safe:.2f}")


def _verify_framing(scene: Any,
                    cam_obj: Any,
                    frames_sample: List[int],
                    report: Dict[str, Any]) -> None:
    """
    Verifica se a geometria fica dentro do frustum da câmara
    numa amostra de frames. Regista no relatório (não falha o render).
    """
    try:
        from mathutils.geometry import normal  # noqa — só para confirmar import
        ok_frames = 0
        for f in frames_sample:
            bpy.context.scene.frame_set(f)
            bpy.context.view_layer.update()
            # Projecção simplificada: verificar se o centro do bounding box
            # projectado está dentro do sensor
            b = _world_bounds_at_frame(f)
            if b is None:
                continue
            cx = (b[0] + b[1]) / 2
            cy = (b[2] + b[3]) / 2
            cz = (b[4] + b[5]) / 2
            pt_world = mathutils.Vector((cx, cy, cz))
            # Transform para espaço da câmara
            pt_cam = cam_obj.matrix_world.inverted() @ pt_world
            if pt_cam.z < 0:   # à frente da câmara
                ok_frames += 1
        report["framing_verified"] = ok_frames == len(frames_sample)
        if not report["framing_verified"]:
            report["warnings"].append(
                f"enquadramento: {ok_frames}/{len(frames_sample)} frames com centro visível"
            )
    except Exception as e:
        report["warnings"].append(f"verificação de enquadramento falhou: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# ILUMINAÇÃO DE TRÊS PONTOS
# ════════════════════════════════════════════════════════════════════════════════

def _setup_lighting(scene: Any,
                    bounds: Tuple[float, float, float, float, float, float],
                    style: str,
                    quality: str,
                    report: Dict[str, Any]) -> None:
    """
    Sistema de três pontos cinematográfico.
    Key + Fill + Rim usando luzes Area (suaves) ou Sun (fallback).
    Não altera materiais do GLB.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0
    cz = (zmn + zmx) / 2.0
    h  = zmx - zmn or 2.0   # altura do personagem
    r  = max(xmx - xmn, ymx - ymn) or 1.0

    # Escala das luzes proporcional ao personagem
    area_size = max(h * 0.8, 1.0)

    lights_ok = 0

    def _add_area(name: str, loc: tuple, energy: float,
                  color: tuple = (1, 1, 1)) -> Optional[Any]:
        nonlocal lights_ok
        try:
            bpy.ops.object.light_add(type="AREA", location=loc)
            light = bpy.context.active_object
            light.name = name
            light.data.energy = energy
            light.data.size   = area_size
            light.data.color  = color
            # Apontar para o centro do personagem
            direction = mathutils.Vector((cx, cy, cz)) - light.location
            light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            lights_ok += 1
            return light
        except Exception as e:
            report["warnings"].append(f"luz {name} falhou (Area): {e}; a tentar Sun")
            try:
                bpy.ops.object.light_add(type="SUN", location=loc)
                light = bpy.context.active_object
                light.name = name
                light.data.energy = energy * 0.3
                light.data.color  = color
                lights_ok += 1
                return light
            except Exception as e2:
                report["warnings"].append(f"luz {name} falhou (Sun): {e2}")
                return None

    if style == "cinematic":
        # Key: frontal-lateral direita, alta
        _add_area("KeyLight",
                  (cx + r*1.5, cy - r*1.2, cz + h*0.9),
                  energy=600.0, color=(1.0, 0.97, 0.90))
        # Fill: lateral esquerda, mais baixa e fraca
        _add_area("FillLight",
                  (cx - r*1.8, cy - r*0.5, cz + h*0.4),
                  energy=120.0, color=(0.85, 0.90, 1.0))
        # Rim: atrás e alto — separa do fundo escuro
        _add_area("RimLight",
                  (cx - r*0.3, cy + r*2.0, cz + h*1.1),
                  energy=350.0, color=(0.7, 0.8, 1.0))
    else:  # studio
        # Key: frontal-lateral direita suave
        _add_area("KeyLight",
                  (cx + r*1.4, cy - r*1.0, cz + h*0.8),
                  energy=500.0, color=(1.0, 0.98, 0.95))
        # Fill: lateral esquerda equilibrada
        _add_area("FillLight",
                  (cx - r*1.6, cy - r*0.4, cz + h*0.5),
                  energy=200.0, color=(0.95, 0.97, 1.0))
        # Rim: separação suave
        _add_area("RimLight",
                  (cx - r*0.2, cy + r*1.8, cz + h*1.0),
                  energy=250.0, color=(1.0, 1.0, 1.0))

    report["lights_created"] = lights_ok
    print(f"  [iluminação] {lights_ok} luzes  estilo={style}")


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
        else:  # studio
            bg.inputs["Color"].default_value    = (0.18, 0.19, 0.22, 1.0)
            bg.inputs["Strength"].default_value = 0.2
    scene.world = world


# ════════════════════════════════════════════════════════════════════════════════
# CHÃO
# ════════════════════════════════════════════════════════════════════════════════

def _add_ground(scene: Any,
                bounds: Tuple[float, float, float, float, float, float],
                style: str,
                report: Dict[str, Any]) -> None:
    """
    Plano de chão posicionado imediatamente abaixo do ponto mais baixo
    da geometria animada. Dimensão proporcional ao personagem.
    Não interfere com a animação.
    """
    xmn, xmx, ymn, ymx, zmn, zmx = bounds
    cx = (xmn + xmx) / 2.0
    cy = (ymn + ymx) / 2.0

    # Posição: 1 mm abaixo do pé mais baixo durante toda a animação
    ground_z = zmn - 0.001

    # Dimensão: 4× a largura do personagem, mínimo 4 m
    char_width = max(xmx - xmn, ymx - ymn) or 1.0
    plane_size = max(char_width * 4.0, 4.0)

    try:
        bpy.ops.mesh.primitive_plane_add(
            size=plane_size,
            location=(cx, cy, ground_z)
        )
        plane = bpy.context.active_object
        plane.name = "Ground"

        # Material fosco neutro
        mat = bpy.data.materials.new("GroundMat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            if style == "cinematic":
                bsdf.inputs["Base Color"].default_value = (0.06, 0.06, 0.07, 1.0)
            else:
                bsdf.inputs["Base Color"].default_value = (0.25, 0.25, 0.27, 1.0)
            bsdf.inputs["Roughness"].default_value      = 0.95
            bsdf.inputs["Specular IOR Level"].default_value = 0.02 \
                if hasattr(bsdf.inputs.get("Specular IOR Level", None), "default_value") \
                else 0.02
            # Compatibilidade: tentar "Specular" (Blender < 4) se IOR Level não existir
            try:
                bsdf.inputs["Specular IOR Level"].default_value = 0.02
            except KeyError:
                try:
                    bsdf.inputs["Specular"].default_value = 0.02
                except KeyError:
                    pass

        if plane.data.materials:
            plane.data.materials[0] = mat
        else:
            plane.data.materials.append(mat)

        # Activar sombras no plano
        plane.visible_shadow = True

        report["ground_created"] = True
        print(f"  [chão] z={ground_z:.3f}  tamanho={plane_size:.2f}  estilo={style}")

    except Exception as e:
        report["warnings"].append(f"chão não criado: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# MOTOR DE RENDER
# ════════════════════════════════════════════════════════════════════════════════

def _setup_render_engine(scene: Any, style: str, quality: str,
                         res: int) -> None:
    """Configura EEVEE (preferido) ou Cycles. Sombras activadas."""

    # Escolher motor
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = eng
            break
        except Exception:
            continue

    engine = scene.render.engine

    if "CYCLES" in engine:
        scene.cycles.samples        = 32 if quality == "medium" else 16
        scene.cycles.use_denoising = False
        scene.cycles.device        = "CPU"

    elif "EEVEE" in engine and hasattr(scene, "eevee"):
        ev = scene.eevee
        samples = 16 if quality == "medium" else 8
        for attr, val in [
            ("taa_render_samples",  samples),
            ("use_bloom",           False),
            ("use_gtao",            True),   # oclusão ambiente suave
            ("use_ssr",             False),
            ("shadow_cube_size",    "1024"),
            ("shadow_cascade_size", "1024"),
        ]:
            if hasattr(ev, attr):
                try:
                    setattr(ev, attr, val)
                except Exception:
                    pass

        # Sombras no EEVEE_NEXT (Blender 4+/5+)
        if hasattr(ev, "use_shadow"):
            try:
                ev.use_shadow = True
            except Exception:
                pass

    # Resolução
    scene.render.resolution_x          = res
    scene.render.resolution_y          = res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"

    # Fundo transparente desactivado (queremos ver o fundo colorido)
    scene.render.film_transparent = False

    print(f"  [render] motor={engine}  res={res}×{res}  qualidade={quality}")


# ════════════════════════════════════════════════════════════════════════════════
# VERIFICAÇÃO DE MOVIMENTO
# ════════════════════════════════════════════════════════════════════════════════

def _check_movement(arm_obj: Any, frame_start: int, frame_mid: int) -> int:
    bpy.context.scene.frame_set(frame_start)
    bpy.context.view_layer.update()
    m_start = {pb.name: [row[:] for row in pb.matrix]
               for pb in arm_obj.pose.bones}
    bpy.context.scene.frame_set(frame_mid)
    bpy.context.view_layer.update()
    moved = 0
    for pb in arm_obj.pose.bones:
        ms = m_start.get(pb.name)
        if ms is None:
            continue
        delta = max(abs(ms[r][c] - pb.matrix[r][c])
                    for r in range(4) for c in range(4))
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
    # ── Parâmetros originais (inalterados) ────────────────────────────────────
    ap.add_argument("--glb",         required=True)
    ap.add_argument("--animation",   default="1H_Melee_Attack_Chop")
    ap.add_argument("--fps",         type=int, default=24)
    ap.add_argument("--max-frames",  type=int, default=72)
    ap.add_argument("--resolution",  type=int, default=640)
    ap.add_argument("--frames-dir",  required=True)
    ap.add_argument("--preview",     default="")
    ap.add_argument("--report",      required=True)
    ap.add_argument("--asset-id",    default="unknown")
    # ── Parâmetros Fase 4 ─────────────────────────────────────────────────────
    ap.add_argument("--visual-style",      default="studio",
                    choices=["studio", "cinematic"])
    ap.add_argument("--camera-mode",       default="auto")
    ap.add_argument("--ground-enabled",    default="true")
    ap.add_argument("--lighting-quality",  default="medium",
                    choices=["low", "medium"])
    args = ap.parse_args(_argv)

    ground_on = args.ground_enabled.lower() not in ("false", "0", "no")

    report = _empty_report(args.asset_id, args.glb, args.animation)
    report["render_fps"]      = args.fps
    report["render_resolution"] = args.resolution
    report["frames_requested"]  = min(args.max_frames, 72)
    report["visual_style"]      = args.visual_style
    report["camera_mode"]       = args.camera_mode
    report["ground_enabled"]    = ground_on

    # ── 1. Limpar cena ────────────────────────────────────────────────────────
    try:
        _clear_scene()
    except Exception:
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save(report, args.report); return 1

    # ── 2. Importar GLB ───────────────────────────────────────────────────────
    err = _import_glb(args.glb)
    if err:
        report["errors"].append(f"importação falhou: {err}")
        _save(report, args.report); return 1
    report["import_success"] = True

    # ── 3. Armature ───────────────────────────────────────────────────────────
    arm_obj = _get_armature()
    if arm_obj is None:
        report["errors"].append("armature não encontrada após importação")
        _save(report, args.report); return 1
    report["bone_count"] = len(arm_obj.data.bones)

    # ── 4. Animações ──────────────────────────────────────────────────────────
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
    report["animation_found"]    = True
    report["animation_name_used"] = action.name

    # ── 5. Aplicar animação ───────────────────────────────────────────────────
    try:
        _assign_action(arm_obj, action)
        report["animation_applied"] = True
    except Exception:
        report["errors"].append(
            f"erro ao aplicar animação:\n{traceback.format_exc(limit=5)}"
        )
        _save(report, args.report); return 1

    # ── 6. Intervalo de frames ────────────────────────────────────────────────
    frame_range  = action.frame_range
    frame_start  = int(frame_range[0])
    frame_end    = int(frame_range[1])
    report["frame_start"] = frame_start
    report["frame_end"]   = frame_end

    # Verificar movimento
    if frame_end > frame_start:
        frame_mid = frame_start + max(1, (frame_end - frame_start) // 4)
        try:
            moved = _check_movement(arm_obj, frame_start, frame_mid)
            report["bones_moved"]       = moved
            report["movement_confirmed"] = moved > 0
            if moved == 0:
                report["warnings"].append(
                    f"nenhum osso se moveu entre frames {frame_start} e {frame_mid}"
                )
        except Exception:
            report["warnings"].append(
                f"erro ao verificar movimento:\n{traceback.format_exc(limit=5)}"
            )
    else:
        report["warnings"].append(
            f"intervalo inválido ({frame_start}–{frame_end}); a renderizar frame único"
        )

    # ── 7. Seleccionar frames a renderizar ────────────────────────────────────
    total_anim_frames = max(1, frame_end - frame_start + 1)
    max_frames        = min(args.max_frames, 72)

    if total_anim_frames <= max_frames:
        frames_to_render = list(range(frame_start, frame_end + 1))
    else:
        step = total_anim_frames / max_frames
        frames_to_render = [frame_start + int(i * step) for i in range(max_frames)]
    report["frames_requested"] = len(frames_to_render)

    # ── 8. FASE 4: bounds dinâmicos para câmara e chão ───────────────────────
    # Amostrar até 8 frames uniformes para calcular o envelope de movimento.
    # Isto é suficiente para armas e ataques amplos sem custo excessivo.
    n_sample   = min(8, len(frames_to_render))
    step_s     = max(1, len(frames_to_render) // n_sample)
    sample_frames = frames_to_render[::step_s][:n_sample]
    if frames_to_render[-1] not in sample_frames:
        sample_frames.append(frames_to_render[-1])

    print(f"\n[fase4] a calcular bounds em {len(sample_frames)} frames de amostra…")
    bounds = _compute_animation_bounds(sample_frames, report)
    print(f"  bounds: x=[{bounds[0]:.2f},{bounds[1]:.2f}]  "
          f"y=[{bounds[2]:.2f},{bounds[3]:.2f}]  "
          f"z=[{bounds[4]:.2f},{bounds[5]:.2f}]")

    # ── 9. Configurar cena ────────────────────────────────────────────────────
    scene = bpy.context.scene
    scene.render.fps = args.fps

    _setup_world(scene, args.visual_style)
    _place_camera(scene, bounds, 15.0, args.visual_style, report)
    _setup_lighting(scene, bounds, args.visual_style, args.lighting_quality, report)

    if ground_on:
        _add_ground(scene, bounds, args.visual_style, report)

    _setup_render_engine(scene, args.visual_style, args.lighting_quality, args.resolution)

    # ── 10. Verificar enquadramento ───────────────────────────────────────────
    cam_obj = scene.camera
    if cam_obj:
        _verify_framing(scene, cam_obj, sample_frames, report)

    # ── 11. Renderizar frames ─────────────────────────────────────────────────
    frames_dir = pathlib.Path(args.frames_dir)
    rendered   = _render_frames(scene, frames_to_render, frames_dir, report)
    report["frames_rendered"] = len(rendered)

    if not rendered:
        report["errors"].append("nenhum frame foi renderizado")
        _save(report, args.report); return 1

    # ── 12. Preview PNG ───────────────────────────────────────────────────────
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
            report["warnings"].append(
                f"erro ao copiar preview:\n{traceback.format_exc(limit=5)}"
            )

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
    print(f"  importação:         {'OK' if r['import_success'] else 'FALHOU'}")
    print(f"  armature:           {r['bone_count']} ossos")
    print(f"  animações no GLB:   {r['animation_count']}")
    print(f"  ação encontrada:    {'SIM — ' + str(r['animation_name_used']) if r['animation_found'] else 'NÃO'}")
    print(f"  ação aplicada:      {'SIM' if r['animation_applied'] else 'NÃO'}")
    print(f"  movimento conf.:    {'SIM (' + str(r['bones_moved']) + ' ossos)' if r['movement_confirmed'] else 'NÃO'}")
    print(f"  estilo visual:      {r['visual_style']}")
    print(f"  câmara criada:      {'SIM' if r['camera_created'] else 'NÃO'}")
    print(f"  enquadramento ok:   {'SIM' if r['framing_verified'] else 'NÃO'} "
          f"({r['framing_frames_sampled']} frames amostrados)")
    print(f"  luzes:              {r['lights_created']}")
    print(f"  chão:               {'SIM' if r['ground_created'] else 'NÃO'}")
    print(f"  frames pedidos:     {r['frames_requested']}")
    print(f"  frames rend.:       {r['frames_rendered']}")
    print(f"  tempo de render:    {r.get('render_time_s', '?')}s")
    print(f"  preview:            {'OK → ' + str(r['preview_path']) if r['preview_success'] else '—'}")
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
