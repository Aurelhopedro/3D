#!/usr/bin/env python3
"""Pipeline de animação 3D — renderiza uma sequência de frames e gera MP4.

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
      [--asset-id kaykit_adv_knight]

Produz:
  - Directório com frames PNG  (temporário; apagado pelo workflow após FFmpeg)
  - preview.png                (frame central, para artefacto)
  - animation-report.json      (relatório completo)

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
import traceback
from typing import Any, Dict, List, Optional

# ── Args do script (após "--") ────────────────────────────────────────────────
try:
    _sep = sys.argv.index("--")
    _argv = sys.argv[_sep + 1:]
except ValueError:
    _argv = []

# ── bpy ───────────────────────────────────────────────────────────────────────
try:
    import bpy  # type: ignore
    import mathutils  # type: ignore
except ImportError:
    print("ERRO: executar dentro do Blender.", file=sys.stderr)
    sys.exit(2)

_BPY_MAJOR = bpy.app.version[0]
_HAS_SLOTS = _BPY_MAJOR >= 5  # Blender 5.x: Action Slots API


# ── Relatório base ────────────────────────────────────────────────────────────
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
        "errors": [],
        "warnings": [],
    }


# ── Limpeza ───────────────────────────────────────────────────────────────────
def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


# ── Importação ────────────────────────────────────────────────────────────────
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


# ── Encontrar armature e ossos ────────────────────────────────────────────────
def _get_armature() -> Optional[Any]:
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            return o
    return None


# ── Action Slots (Blender 5.x) ───────────────────────────────────────────────
def _assign_action(arm_obj: Any, action: Any) -> None:
    """Atribui Action à armature com suporte a Slots do Blender 5.x."""
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
                pass  # API pode variar entre builds do 5.x


def _find_action(name: str) -> Optional[Any]:
    """Procura Action por nome exacto, depois parcial (case-insensitive)."""
    for a in bpy.data.actions:
        if a.name == name:
            return a
    name_l = name.lower()
    for a in bpy.data.actions:
        if name_l in a.name.lower():
            return a
    return None


# ── Verificar movimento ───────────────────────────────────────────────────────
def _check_movement(arm_obj: Any, frame_start: int, frame_mid: int) -> int:
    """Devolve o nº de ossos cujas matrizes mudaram entre frame_start e frame_mid."""
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


# ── Câmara e cena ─────────────────────────────────────────────────────────────
def _setup_scene(scene: Any, res: int) -> None:
    """Câmara automática, iluminação e motor EEVEE leve."""
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        return

    verts: List = []
    for obj in meshes:
        for v in obj.data.vertices:
            verts.append(obj.matrix_world @ v.co)
    if not verts:
        return

    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    zs = [v.z for v in verts]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    cz = (min(zs) + max(zs)) / 2
    radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0

    # Câmara ligeiramente lateral para mostrar a animação
    bpy.ops.object.camera_add()
    cam = bpy.context.active_object
    cam_dist = radius * 2.8
    elev = math.radians(20)
    azim = math.radians(35)
    cam.location = (
        cx + cam_dist * math.cos(elev) * math.sin(azim),
        cy - cam_dist * math.cos(elev) * math.cos(azim),
        cz + cam_dist * math.sin(elev),
    )
    direction = mathutils.Vector((cx, cy, cz)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    # Luzes
    bpy.ops.object.light_add(type="SUN",
                              location=(cx + radius, cy - radius, cz + radius * 2))
    bpy.context.active_object.data.energy = 5.0
    bpy.ops.object.light_add(type="SUN",
                              location=(cx - radius * 0.5, cy + radius * 0.5, cz + radius))
    bpy.context.active_object.data.energy = 2.0

    # Fundo
    world = bpy.data.worlds.new("AnimWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.15, 0.15, 0.18, 1.0)
        bg.inputs["Strength"].default_value = 0.3
    scene.world = world

    # Motor: EEVEE preferido (mais rápido sem GPU)
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = eng
            break
        except Exception:
            continue

    if "CYCLES" in scene.render.engine:
        scene.cycles.samples = 24
        scene.cycles.use_denoising = False
        scene.cycles.device = "CPU"

    if "EEVEE" in scene.render.engine and hasattr(scene, "eevee"):
        ev = scene.eevee
        for attr, val in [
            ("taa_render_samples", 8),
            ("use_bloom", False),
            ("use_gtao", False),
            ("use_ssr", False),
        ]:
            if hasattr(ev, attr):
                setattr(ev, attr, val)

    scene.render.resolution_x = res
    scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"


# ── Renderização dos frames ───────────────────────────────────────────────────
def _render_frames(scene: Any, frames_to_render: List[int],
                   frames_dir: pathlib.Path, report: Dict[str, Any]) -> List[pathlib.Path]:
    """Renderiza a lista de frames e devolve os ficheiros criados."""
    rendered: List[pathlib.Path] = []
    frames_dir.mkdir(parents=True, exist_ok=True)

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
            break  # interrompe se o render falhar (evitar loop de erros)

    return rendered


# ── Ponto de entrada ──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--animation", default="1H_Melee_Attack_Chop")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--max-frames", type=int, default=72)
    ap.add_argument("--resolution", type=int, default=640)
    ap.add_argument("--frames-dir", required=True,
                    help="Directório para os frames PNG intermédios")
    ap.add_argument("--preview", default="",
                    help="Caminho para o PNG de preview (frame central)")
    ap.add_argument("--report", required=True)
    ap.add_argument("--asset-id", default="unknown")
    args = ap.parse_args(_argv)

    report = _empty_report(args.asset_id, args.glb, args.animation)
    report["render_fps"] = args.fps
    report["render_resolution"] = args.resolution
    report["frames_requested"] = min(args.max_frames, 72)

    # 1. Limpar cena
    try:
        _clear_scene()
    except Exception:
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save(report, args.report)
        return 1

    # 2. Importar GLB
    err = _import_glb(args.glb)
    if err:
        report["errors"].append(f"importação falhou: {err}")
        _save(report, args.report)
        return 1
    report["import_success"] = True

    # 3. Encontrar armature
    arm_obj = _get_armature()
    if arm_obj is None:
        report["errors"].append("armature não encontrada após importação")
        _save(report, args.report)
        return 1
    report["bone_count"] = len(arm_obj.data.bones)

    # 4. Listar animações
    actions = list(bpy.data.actions)
    report["animation_count"] = len(actions)
    if not actions:
        report["errors"].append("nenhuma animação encontrada no GLB")
        _save(report, args.report)
        return 1

    # 5. Encontrar a animação pedida
    action = _find_action(args.animation)
    if action is None:
        report["warnings"].append(
            f"animação {args.animation!r} não encontrada; a usar a primeira disponível"
        )
        action = actions[0]
    report["animation_found"] = True
    report["animation_name_used"] = action.name

    # 6. Aplicar animação
    try:
        _assign_action(arm_obj, action)
        report["animation_applied"] = True
    except Exception:
        report["errors"].append(
            f"erro ao aplicar animação:\n{traceback.format_exc(limit=5)}"
        )
        _save(report, args.report)
        return 1

    # 7. Verificar movimento (antes de renderizar — falha rápida se inútil)
    frame_range = action.frame_range
    frame_start = int(frame_range[0])
    frame_end = int(frame_range[1])
    report["frame_start"] = frame_start
    report["frame_end"] = frame_end

    if frame_end > frame_start:
        frame_mid = frame_start + max(1, (frame_end - frame_start) // 4)
        try:
            moved = _check_movement(arm_obj, frame_start, frame_mid)
            report["bones_moved"] = moved
            report["movement_confirmed"] = moved > 0
            if moved == 0:
                report["warnings"].append(
                    f"nenhum osso se moveu entre frames {frame_start} e {frame_mid} "
                    f"(threshold=1e-4) — renderização continua mas animação pode estar vazia"
                )
        except Exception:
            report["warnings"].append(
                f"erro ao verificar movimento:\n{traceback.format_exc(limit=5)}"
            )
    else:
        report["warnings"].append(
            f"intervalo de frames inválido ({frame_start}–{frame_end}); "
            f"a renderizar frame único"
        )

    # 8. Seleccionar frames a renderizar
    total_anim_frames = max(1, frame_end - frame_start + 1)
    max_frames = min(args.max_frames, 72)  # limite absoluto de segurança

    if total_anim_frames <= max_frames:
        frames_to_render = list(range(frame_start, frame_end + 1))
    else:
        # Amostrar uniformemente sem alterar a animação original
        step = total_anim_frames / max_frames
        frames_to_render = [
            frame_start + int(i * step) for i in range(max_frames)
        ]
    report["frames_requested"] = len(frames_to_render)

    # 9. Configurar cena e renderizar
    scene = bpy.context.scene
    scene.render.fps = args.fps
    _setup_scene(scene, args.resolution)

    frames_dir = pathlib.Path(args.frames_dir)
    rendered = _render_frames(scene, frames_to_render, frames_dir, report)
    report["frames_rendered"] = len(rendered)

    if not rendered:
        report["errors"].append("nenhum frame foi renderizado")
        _save(report, args.report)
        return 1

    # 10. Preview PNG (frame central)
    if args.preview and rendered:
        mid_idx = len(rendered) // 2
        preview_src = rendered[mid_idx]
        try:
            import shutil
            preview_dst = pathlib.Path(args.preview)
            preview_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(preview_src, preview_dst)
            report["preview_success"] = True
            report["preview_path"] = args.preview
        except Exception:
            report["warnings"].append(
                f"erro ao copiar preview:\n{traceback.format_exc(limit=5)}"
            )

    _save(report, args.report)
    _print_summary(report)

    # Exit code: 0 se pelo menos 1 frame foi renderizado
    return 0 if rendered else 1


def _save(report: Dict[str, Any], path: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 64)
    print(f"ANIMAÇÃO: {report['asset_id']}  (Blender {report['blender_version']})")
    print("=" * 64)
    r = report
    print(f"  importação:        {'OK' if r['import_success'] else 'FALHOU'}")
    print(f"  armature:          {r['bone_count']} ossos")
    print(f"  animações no GLB:  {r['animation_count']}")
    print(f"  ação encontrada:   {'SIM — ' + str(r['animation_name_used']) if r['animation_found'] else 'NÃO'}")
    print(f"  ação aplicada:     {'SIM' if r['animation_applied'] else 'NÃO'}")
    print(f"  movimento conf.:   {'SIM (' + str(r['bones_moved']) + ' ossos)' if r['movement_confirmed'] else 'NÃO'}")
    print(f"  frames pedidos:    {r['frames_requested']}")
    print(f"  frames rend.:      {r['frames_rendered']}")
    print(f"  preview:           {'OK → ' + str(r['preview_path']) if r['preview_success'] else '—'}")
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
