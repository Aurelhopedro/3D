#!/usr/bin/env python3
"""Validação de modelos 3D no Blender em modo background.

Uso (pelo GitHub Actions):
  blender --background --python library/tools/validate_blender.py -- \
      --glb path/to/model.glb \
      --report path/to/validation-report.json \
      [--render path/to/render.png] \
      [--asset-id kaykit_adv_knight]

Produz:
  - validation-report.json  (sempre)
  - render.png              (se --render for especificado e a importação tiver êxito)

Não modifica nenhum modelo histórico (B5, B7, B8, B9) nem os scripts de produção.
Usa uma cena temporária isolada que é limpa no início.

Requisitos: Blender 3.6+ / 4.x / 5.x com Python embutido (bpy disponível).
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

# ── Separar argumentos do Blender dos nossos ─────────────────────────────────
try:
    _sep = sys.argv.index("--")
    _argv = sys.argv[_sep + 1:]
except ValueError:
    _argv = []

# ── Importar bpy ─────────────────────────────────────────────────────────────
try:
    import bpy  # type: ignore
    import mathutils  # type: ignore
except ImportError:
    print("ERRO: este script deve ser executado dentro do Blender (bpy não disponível).",
          file=sys.stderr)
    sys.exit(2)

# Detectar se estamos no Blender 5.x (Action Slots API)
_BPY_MAJOR = bpy.app.version[0]
_BPY_MINOR = bpy.app.version[1]
_HAS_SLOTS = _BPY_MAJOR >= 5  # Blender 5.x introduziu Action Slots


# ── Estrutura base do relatório ───────────────────────────────────────────────
def _empty_report(asset_id: str, glb_path: str) -> Dict[str, Any]:
    return {
        "asset_id": asset_id,
        "glb_path": glb_path,
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "import_success": False,
        "object_count": 0,
        "mesh_count": 0,
        "vertex_count": 0,
        "polygon_count": 0,
        "material_count": 0,
        "texture_count": 0,
        "armature_found": False,
        "armature_count": 0,
        "bone_count": 0,
        "vertex_groups_found": False,
        "armature_modifier_found": False,
        "rigging_confirmed": False,
        "animation_count": 0,
        "animation_functional": False,
        "animation_bones_moved": 0,
        "animation_tested_action": None,
        "animation_names": [],
        "render_success": False,
        "render_path": None,
        "errors": [],
        "warnings": [],
    }


# ── Limpeza da cena ───────────────────────────────────────────────────────────
def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


# ── Importação ────────────────────────────────────────────────────────────────
def _import_glb(glb_path: str) -> Optional[str]:
    path = pathlib.Path(glb_path)
    if not path.is_file():
        return f"ficheiro não encontrado: {glb_path}"
    if path.stat().st_size == 0:
        return f"ficheiro vazio: {glb_path}"
    try:
        result = bpy.ops.import_scene.gltf(filepath=str(path))
        if result != {"FINISHED"}:
            return f"importação retornou: {result}"
        return None
    except Exception:
        return traceback.format_exc(limit=10)


# ── Geometria ─────────────────────────────────────────────────────────────────
def _inspect_geometry(report: Dict[str, Any]) -> None:
    objects = list(bpy.context.scene.objects)
    report["object_count"] = len(objects)
    meshes = [o for o in objects if o.type == "MESH"]
    report["mesh_count"] = len(meshes)
    if not meshes:
        report["warnings"].append("nenhuma malha encontrada")
    total_verts = total_polys = 0
    for obj in meshes:
        m = obj.data
        total_verts += len(m.vertices)
        total_polys += len(m.polygons)
        if not m.vertices:
            report["warnings"].append(f"objecto sem vértices: {obj.name!r}")
    report["vertex_count"] = total_verts
    report["polygon_count"] = total_polys


# ── Materiais ─────────────────────────────────────────────────────────────────
def _inspect_materials(report: Dict[str, Any]) -> None:
    mat_names: set = set()
    tex_count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            mat_names.add(mat.name)
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image is not None:
                        tex_count += 1
    report["material_count"] = len(mat_names)
    report["texture_count"] = tex_count


# ── Rigging ───────────────────────────────────────────────────────────────────
def _inspect_rigging(report: Dict[str, Any]) -> None:
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    report["armature_found"] = bool(armatures)
    report["armature_count"] = len(armatures)
    report["bone_count"] = sum(len(a.data.bones) for a in armatures)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    has_vgroups = any(bool(o.vertex_groups) for o in meshes)
    has_arm_mod = any(
        any(m.type == "ARMATURE" for m in o.modifiers) for o in meshes
    )
    report["vertex_groups_found"] = has_vgroups
    report["armature_modifier_found"] = has_arm_mod
    report["rigging_confirmed"] = bool(armatures) and has_vgroups and has_arm_mod
    if armatures and not report["rigging_confirmed"]:
        report["warnings"].append(
            f"armature encontrada mas rigging NÃO confirmado "
            f"(vertex_groups={has_vgroups}, armature_modifier={has_arm_mod})"
        )


# ── Animações — API Blender 5.x compatível ────────────────────────────────────

def _assign_action_blender5(arm_obj: Any, action: Any) -> bool:
    """Atribui uma Action à armature usando a API de Slots do Blender 5.x.

    O Blender 5.x exige que cada Action seja atribuída via um Slot específico
    para o tipo de objecto (OBJECT → slot 'Object', ARMATURE → slot 'Bone').
    Sem slot correcto, a Action é ignorada e os ossos ficam em bind pose.

    Devolve True se a atribuição teve êxito.
    """
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    anim_data = arm_obj.animation_data

    # Blender 5.x: Action tem .slots (lista de ActionSlot)
    if hasattr(action, "slots") and action.slots:
        # Preferir slot do tipo ARMATURE / OBJECT
        chosen_slot = None
        for slot in action.slots:
            # slot.target_id_type: 'ARMATURE', 'OBJECT', etc.
            tid = getattr(slot, "target_id_type", "") or ""
            if tid in ("ARMATURE", "OBJECT", ""):
                chosen_slot = slot
                break
        if chosen_slot is None:
            chosen_slot = action.slots[0]  # fallback: primeiro slot disponível

        anim_data.action = action
        # Blender 5.x: anim_data.action_slot para seleccionar o slot
        if hasattr(anim_data, "action_slot"):
            anim_data.action_slot = chosen_slot
        return True

    # Sem slots (Blender < 4.4 ou Action vazia)
    anim_data.action = action
    return True


def _assign_action_legacy(arm_obj: Any, action: Any) -> bool:
    """Atribui Action em Blender 3.x / 4.x (sem Slots)."""
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action
    return True


def _assign_action(arm_obj: Any, action: Any) -> bool:
    if _HAS_SLOTS:
        return _assign_action_blender5(arm_obj, action)
    return _assign_action_legacy(arm_obj, action)


def _matrix_delta(m1: Any, m2: Any, threshold: float = 1e-4) -> float:
    """Diferença máxima entre dois elementos de duas matrizes 4×4."""
    d = 0.0
    for r in range(4):
        for c in range(4):
            d = max(d, abs(m1[r][c] - m2[r][c]))
    return d


def _test_action_movement(arm_obj: Any, action: Any) -> Dict[str, Any]:
    """
    Avalia se uma Action produz movimento real em pelo menos um osso.

    Estratégia (corrige o bug do validador anterior que só testava translação
    de um único osso):
      1. Atribuir a Action ao objecto Armature com slot correcto (Blender 5.x).
      2. Avaliar a pose em frame_start e num frame intermédio.
      3. Comparar a MATRIZ COMPLETA de cada osso (captura translação,
         rotação e escala).
      4. Registar quantos ossos se moveram acima do limiar numérico.
      5. Ignorar diferenças < threshold (flutuação de vírgula flutuante).

    Devolve dict com: bones_moved, bones_total, frame_start, frame_mid,
    action_name, functional.
    """
    frame_range = action.frame_range
    frame_start = int(frame_range[0])
    frame_end = int(frame_range[1])

    result = {
        "action_name": action.name,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frame_mid": frame_start,
        "bones_moved": 0,
        "bones_total": len(arm_obj.data.bones),
        "functional": False,
        "warning": None,
    }

    if frame_end <= frame_start:
        result["warning"] = f"intervalo de frames inválido: {frame_start}–{frame_end}"
        return result

    frame_mid = frame_start + max(1, (frame_end - frame_start) // 4)
    result["frame_mid"] = frame_mid

    _assign_action(arm_obj, action)

    # Snapshot em frame_start
    bpy.context.scene.frame_set(frame_start)
    bpy.context.view_layer.update()
    matrices_start = {}
    for pb in arm_obj.pose.bones:
        matrices_start[pb.name] = [row[:] for row in pb.matrix]

    # Snapshot em frame_mid
    bpy.context.scene.frame_set(frame_mid)
    bpy.context.view_layer.update()

    bones_moved = 0
    for pb in arm_obj.pose.bones:
        m_start = matrices_start.get(pb.name)
        if m_start is None:
            continue
        delta = _matrix_delta(m_start, [row[:] for row in pb.matrix])
        if delta > 1e-4:
            bones_moved += 1

    result["bones_moved"] = bones_moved
    result["functional"] = bones_moved > 0
    return result


def _find_action_by_name(name: str) -> Optional[Any]:
    """Procura uma Action pelo nome, com correspondência parcial (case-insensitive)."""
    name_lower = name.lower()
    # Correspondência exacta primeiro
    for a in bpy.data.actions:
        if a.name == name:
            return a
    # Correspondência parcial
    for a in bpy.data.actions:
        if name_lower in a.name.lower():
            return a
    return None


def _inspect_animations(report: Dict[str, Any], preferred_action: str = "") -> None:
    """
    Avalia animações com comparação de matrizes completas em todos os ossos.
    Testa primeiro a ação preferida; se não existir, testa a primeira disponível.
    """
    actions = list(bpy.data.actions)
    report["animation_count"] = len(actions)
    report["animation_names"] = [a.name for a in actions][:80]

    if not actions:
        report["warnings"].append("nenhuma acção de animação encontrada")
        return

    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not armatures or report["bone_count"] == 0:
        report["warnings"].append(
            "animações encontradas mas sem armature para avaliar funcionalidade"
        )
        return

    arm_obj = armatures[0]

    # Escolher a acção a testar
    action_to_test = None
    if preferred_action:
        action_to_test = _find_action_by_name(preferred_action)
        if action_to_test is None:
            report["warnings"].append(
                f"acção preferida {preferred_action!r} não encontrada; "
                f"a testar a primeira disponível"
            )

    if action_to_test is None and actions:
        # Preferir uma acção com "attack" ou "walk" no nome (mais provável de ter movimento)
        for candidate_name in ("attack", "walk", "run", "jump", "idle"):
            for a in actions:
                if candidate_name in a.name.lower():
                    action_to_test = a
                    break
            if action_to_test:
                break
        if action_to_test is None:
            action_to_test = actions[0]

    result = _test_action_movement(arm_obj, action_to_test)
    report["animation_functional"] = result["functional"]
    report["animation_bones_moved"] = result["bones_moved"]
    report["animation_tested_action"] = result["action_name"]

    if result.get("warning"):
        report["warnings"].append(f"acção {result['action_name']!r}: {result['warning']}")

    if not result["functional"]:
        report["warnings"].append(
            f"acção {result['action_name']!r}: {result['bones_moved']}/{result['bones_total']} "
            f"ossos moveram-se entre frames {result['frame_start']} e {result['frame_mid']} "
            f"(matrizes completas comparadas, threshold=1e-4)"
        )
    else:
        print(
            f"  ✓ animação funcional: {result['bones_moved']}/{result['bones_total']} ossos "
            f"moveram-se em {result['action_name']!r} "
            f"(frames {result['frame_start']}→{result['frame_mid']})"
        )


# ── Câmara e cena ─────────────────────────────────────────────────────────────
def _setup_scene(scene: Any, res: int = 640) -> tuple:
    """Configura câmara, luzes e fundo. Devolve (cx, cy, cz, radius)."""
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        return (0.0, 0.0, 0.0, 1.0)

    all_verts: List = []
    for obj in meshes:
        for v in obj.data.vertices:
            all_verts.append(obj.matrix_world @ v.co)

    if not all_verts:
        return (0.0, 0.0, 0.0, 1.0)

    xs = [v.x for v in all_verts]
    ys = [v.y for v in all_verts]
    zs = [v.z for v in all_verts]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    cz = (min(zs) + max(zs)) / 2
    radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0

    # Câmara
    bpy.ops.object.camera_add()
    cam_obj = bpy.context.active_object
    cam_dist = radius * 3.0
    elev = math.radians(25)
    azim = math.radians(45)
    cam_obj.location = (
        cx + cam_dist * math.cos(elev) * math.sin(azim),
        cy - cam_dist * math.cos(elev) * math.cos(azim),
        cz + cam_dist * math.sin(elev),
    )
    direction = mathutils.Vector((cx, cy, cz)) - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam_obj

    # Luzes
    bpy.ops.object.light_add(type="SUN",
                              location=(cx + radius, cy - radius, cz + radius * 2))
    bpy.context.active_object.data.energy = 4.0
    bpy.ops.object.light_add(type="SUN",
                              location=(cx - radius, cy + radius, cz + radius))
    bpy.context.active_object.data.energy = 1.5

    # Fundo
    scene.world = bpy.data.worlds.new("AnimWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.18, 0.18, 0.22, 1.0)
        bg.inputs["Strength"].default_value = 0.4

    # Motor de render — EEVEE (mais rápido sem GPU)
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = eng
            break
        except Exception:
            continue

    if "CYCLES" in scene.render.engine:
        scene.cycles.samples = 32
        scene.cycles.use_denoising = False
        scene.cycles.device = "CPU"

    if "EEVEE" in scene.render.engine and hasattr(scene, "eevee"):
        ev = scene.eevee
        for attr, val in [
            ("taa_render_samples", 16),
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

    return (cx, cy, cz, radius)


# ── Renderização de um frame ──────────────────────────────────────────────────
def _render(report: Dict[str, Any], render_path: str, res: int = 640) -> None:
    scene = bpy.context.scene
    _setup_scene(scene, res)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    scene.render.filepath = render_path
    try:
        bpy.ops.render.render(write_still=True)
        out = pathlib.Path(render_path)
        if out.is_file() and out.stat().st_size > 0:
            report["render_success"] = True
            report["render_path"] = render_path
        else:
            report["errors"].append("render concluído mas PNG ausente ou vazio")
    except Exception:
        report["errors"].append(f"erro ao renderizar:\n{traceback.format_exc(limit=10)}")


# ── Ponto de entrada ──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--render", default="")
    ap.add_argument("--asset-id", default="unknown")
    ap.add_argument("--preferred-action", default="",
                    help="Nome preferencial da animação a testar (correspondência parcial)")
    args = ap.parse_args(_argv)

    report = _empty_report(args.asset_id, args.glb)

    try:
        _clear_scene()
    except Exception:
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save_report(report, args.report)
        return 1

    err = _import_glb(args.glb)
    if err:
        report["errors"].append(f"importação falhou: {err}")
        _save_report(report, args.report)
        return 1
    report["import_success"] = True

    for fn, label in [
        (_inspect_geometry, "geometria"),
        (_inspect_materials, "materiais"),
        (_inspect_rigging, "rigging"),
    ]:
        try:
            fn(report)
        except Exception:
            report["errors"].append(
                f"erro na inspecção de {label}:\n{traceback.format_exc(limit=5)}"
            )

    try:
        _inspect_animations(report, preferred_action=args.preferred_action)
    except Exception:
        report["errors"].append(
            f"erro na inspecção de animações:\n{traceback.format_exc(limit=5)}"
        )

    if args.render:
        try:
            _render(report, args.render)
        except Exception:
            report["errors"].append(f"erro na renderização:\n{traceback.format_exc(limit=5)}")

    _save_report(report, args.report)
    _print_summary(report)
    return 0 if report["import_success"] else 1


def _save_report(report: Dict[str, Any], path: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 64)
    print(f"VALIDAÇÃO: {report['asset_id']}  (Blender {report['blender_version']})")
    print("=" * 64)
    s = report
    print(f"  importação:      {'OK' if s['import_success'] else 'FALHOU'}")
    print(f"  malhas:          {s['mesh_count']}  ({s['vertex_count']} vértices, {s['polygon_count']} polígonos)")
    print(f"  materiais:       {s['material_count']}  ({s['texture_count']} texturas)")
    print(f"  armature:        {'sim (' + str(s['bone_count']) + ' ossos)' if s['armature_found'] else 'não'}")
    print(f"  rigging conf.:   {'SIM' if s['rigging_confirmed'] else 'NÃO'}")
    print(f"  animações:       {s['animation_count']}")
    if s.get("animation_tested_action"):
        print(f"  ação testada:    {s['animation_tested_action']!r}  "
              f"({'FUNCIONAL — ' + str(s['animation_bones_moved']) + ' ossos' if s['animation_functional'] else 'NÃO funcional'})")
    print(f"  render:          {'OK → ' + str(s['render_path']) if s['render_success'] else '—'}")
    if s["errors"]:
        print("\nERROS:")
        for e in s["errors"]:
            print(f"  ! {e[:300]}")
    if s["warnings"]:
        print("\nAVISOS:")
        for w in s["warnings"]:
            print(f"  ~ {w}")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    sys.exit(main())
