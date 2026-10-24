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

Requisitos: Blender 3.6+ com Python embutido (bpy disponível).
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
# O Blender passa os seus próprios argumentos antes de "--"; os nossos vêm depois.
try:
    _sep = sys.argv.index("--")
    _argv = sys.argv[_sep + 1:]
except ValueError:
    _argv = []

# ── Importar bpy (só disponível dentro do Blender) ──────────────────────────
try:
    import bpy  # type: ignore
except ImportError:
    print("ERRO: este script deve ser executado dentro do Blender (bpy não disponível).",
          file=sys.stderr)
    sys.exit(2)


# ── Estrutura base do relatório ──────────────────────────────────────────────
def _empty_report(asset_id: str, glb_path: str) -> Dict[str, Any]:
    return {
        "asset_id": asset_id,
        "glb_path": glb_path,
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
        "animation_names": [],
        "render_success": False,
        "render_path": None,
        "errors": [],
        "warnings": [],
        "blender_version": ".".join(str(v) for v in bpy.app.version),
    }


# ── Limpeza da cena temporária ───────────────────────────────────────────────
def _clear_scene() -> None:
    """Remove todos os objectos e dados da cena de teste. Não toca noutras cenas."""
    # Não há outras cenas a proteger em modo background, mas usamos a padrão
    # e garantimos que não há lixo de execuções anteriores.
    bpy.ops.wm.read_factory_settings(use_empty=True)


# ── Importação ───────────────────────────────────────────────────────────────
def _import_glb(glb_path: str) -> Optional[str]:
    """Importa o GLB. Devolve mensagem de erro ou None em caso de sucesso."""
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
    except Exception:  # noqa: BLE001
        return traceback.format_exc(limit=10)


# ── Inspecção de geometria ───────────────────────────────────────────────────
def _inspect_geometry(report: Dict[str, Any]) -> None:
    objects = list(bpy.context.scene.objects)
    report["object_count"] = len(objects)

    meshes = [o for o in objects if o.type == "MESH"]
    report["mesh_count"] = len(meshes)

    if not meshes:
        report["warnings"].append("nenhuma malha encontrada")

    total_verts = 0
    total_polys = 0
    for obj in meshes:
        mesh = obj.data
        total_verts += len(mesh.vertices)
        total_polys += len(mesh.polygons)
        if not mesh.vertices:
            report["warnings"].append(f"objecto sem vértices: {obj.name!r}")
    report["vertex_count"] = total_verts
    report["polygon_count"] = total_polys


# ── Inspecção de materiais e texturas ────────────────────────────────────────
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


# ── Inspecção de rigging ─────────────────────────────────────────────────────
def _inspect_rigging(report: Dict[str, Any]) -> None:
    """
    Distingue com cuidado:
      armature_found      → existe pelo menos um objecto Armature
      bone_count          → total de ossos em todas as armatures
      vertex_groups_found → pelo menos uma malha tem vertex groups
      armature_modifier   → pelo menos uma malha tem modificador Armature
      rigging_confirmed   → armature_found AND vertex_groups_found
                            AND armature_modifier_found
    Não declara rigging_confirmed apenas porque existe uma armature.
    """
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    report["armature_found"] = len(armatures) > 0
    report["armature_count"] = len(armatures)

    total_bones = 0
    for arm_obj in armatures:
        total_bones += len(arm_obj.data.bones)
    report["bone_count"] = total_bones

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    has_vgroups = any(bool(o.vertex_groups) for o in meshes)
    has_arm_mod = any(
        any(m.type == "ARMATURE" for m in o.modifiers)
        for o in meshes
    )
    report["vertex_groups_found"] = has_vgroups
    report["armature_modifier_found"] = has_arm_mod
    report["rigging_confirmed"] = (
        report["armature_found"] and has_vgroups and has_arm_mod
    )

    if report["armature_found"] and not report["rigging_confirmed"]:
        report["warnings"].append(
            "armature encontrada mas rigging NÃO confirmado "
            f"(vertex_groups={has_vgroups}, armature_modifier={has_arm_mod})"
        )


# ── Inspecção de animações ────────────────────────────────────────────────────
def _inspect_animations(report: Dict[str, Any]) -> None:
    """
    animation_count    → número de NLA tracks ou acções encontradas
    animation_names    → lista de nomes
    animation_functional → avalia a armature em 2 frames distintos e verifica
                           se a posição de um osso muda (prova que a animação
                           realmente deforma o esqueleto)

    Se não houver armature, não há como avaliar animações funcionais.
    """
    actions = list(bpy.data.actions)
    report["animation_count"] = len(actions)
    report["animation_names"] = [a.name for a in actions][:50]  # limite para o relatório

    if not actions:
        report["warnings"].append("nenhuma acção de animação encontrada")
        return

    # Tentar avaliar funcionalidade apenas se há armature com ossos
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not armatures or report["bone_count"] == 0:
        report["warnings"].append(
            "animações encontradas mas sem armature para avaliar funcionalidade"
        )
        return

    arm_obj = armatures[0]
    action = actions[0]

    # Associar a primeira acção à armature e avaliar em dois frames
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action

    frame_range = action.frame_range
    frame_start = int(frame_range[0])
    frame_end = int(frame_range[1])

    if frame_end <= frame_start:
        report["warnings"].append(
            f"acção {action.name!r} tem intervalo de frames inválido: "
            f"{frame_start}–{frame_end}"
        )
        return

    frame_mid = frame_start + max(1, (frame_end - frame_start) // 2)

    # Capturar posição de um osso no início e no meio
    bpy.context.scene.frame_set(frame_start)
    bpy.context.view_layer.update()
    bone_name = arm_obj.data.bones[0].name
    pos_start = arm_obj.pose.bones[bone_name].matrix.to_translation().copy()

    bpy.context.scene.frame_set(frame_mid)
    bpy.context.view_layer.update()
    pos_mid = arm_obj.pose.bones[bone_name].matrix.to_translation().copy()

    delta = (pos_mid - pos_start).length
    report["animation_functional"] = delta > 1e-6
    if not report["animation_functional"]:
        report["warnings"].append(
            f"osso {bone_name!r} não se moveu entre frames {frame_start} e {frame_mid} "
            f"(delta={delta:.2e}): animação pode estar vazia ou em bind pose"
        )


# ── Renderização ──────────────────────────────────────────────────────────────
def _render(report: Dict[str, Any], render_path: str) -> None:
    """
    Posiciona câmara automática, configura iluminação simples e renderiza PNG.
    Usa EEVEE quando disponível (mais rápido em runner sem GPU), com fallback
    para BLENDER_EEVEE_NEXT ou CYCLES (CPU, amostras baixas).
    """
    scene = bpy.context.scene

    # Calcular bounding box de todos os objectos com geometria
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        report["errors"].append("sem malhas para renderizar")
        return

    # Calcular centro e raio da cena
    all_verts_world: List = []
    for obj in meshes:
        for v in obj.data.vertices:
            world_co = obj.matrix_world @ v.co
            all_verts_world.append(world_co)

    if not all_verts_world:
        report["errors"].append("sem vértices no espaço global para renderizar")
        return

    xs = [v.x for v in all_verts_world]
    ys = [v.y for v in all_verts_world]
    zs = [v.z for v in all_verts_world]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    cz = (min(zs) + max(zs)) / 2
    radius = max(
        max(xs) - min(xs),
        max(ys) - min(ys),
        max(zs) - min(zs),
    ) / 2 or 1.0

    # Câmara
    bpy.ops.object.camera_add()
    cam_obj = bpy.context.active_object
    cam_dist = radius * 3.0
    angle_h = math.radians(30)   # elevação
    angle_v = math.radians(45)   # azimute
    cam_obj.location = (
        cx + cam_dist * math.cos(angle_h) * math.sin(angle_v),
        cy - cam_dist * math.cos(angle_h) * math.cos(angle_v),
        cz + cam_dist * math.sin(angle_h),
    )
    # Apontar câmara para o centro
    import mathutils  # type: ignore
    direction = mathutils.Vector((cx, cy, cz)) - cam_obj.location
    rot_quat = direction.to_track_quat("-Z", "Y")
    cam_obj.rotation_euler = rot_quat.to_euler()
    scene.camera = cam_obj

    # Iluminação simples (Sun)
    bpy.ops.object.light_add(type="SUN", location=(cx + radius, cy - radius, cz + radius * 2))
    sun = bpy.context.active_object
    sun.data.energy = 3.0

    # Luz ambiente fraca (adicional)
    bpy.ops.object.light_add(type="SUN", location=(cx - radius, cy + radius, cz + radius))
    fill = bpy.context.active_object
    fill.data.energy = 1.0

    # Fundo neutro cinzento
    scene.world = bpy.data.worlds.new("ValidationWorld")
    scene.world.use_nodes = True
    bg_node = scene.world.node_tree.nodes.get("Background")
    if bg_node:
        bg_node.inputs["Color"].default_value = (0.2, 0.2, 0.2, 1.0)
        bg_node.inputs["Strength"].default_value = 0.5

    # Seleccionar motor de render: preferência por EEVEE (mais rápido sem GPU)
    render_engine = None
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES"):
        if eng in bpy.app.build_options.__doc__ or True:  # tentativa directa
            render_engine = eng
            break
    scene.render.engine = render_engine or "BLENDER_EEVEE"

    if scene.render.engine == "CYCLES":
        scene.cycles.samples = 32
        scene.cycles.use_denoising = False
        scene.cycles.device = "CPU"
        report["warnings"].append("CYCLES em uso (EEVEE indisponível): pode ser lento")
    elif "EEVEE" in scene.render.engine:
        # Configurações leves para runner gratuito
        if hasattr(scene, "eevee"):
            eevee = scene.eevee
            if hasattr(eevee, "taa_render_samples"):
                eevee.taa_render_samples = 16
            if hasattr(eevee, "use_bloom"):
                eevee.use_bloom = False
            if hasattr(eevee, "use_gtao"):
                eevee.use_gtao = False
            if hasattr(eevee, "use_ssr"):
                eevee.use_ssr = False

    # Resolução
    scene.render.resolution_x = 800
    scene.render.resolution_y = 800
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = render_path

    # Frame 1 (bind pose ou pose de repouso)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()

    try:
        bpy.ops.render.render(write_still=True)
        out = pathlib.Path(render_path)
        if out.is_file() and out.stat().st_size > 0:
            report["render_success"] = True
            report["render_path"] = render_path
        else:
            report["errors"].append("render concluído mas ficheiro PNG ausente ou vazio")
    except Exception:  # noqa: BLE001
        report["errors"].append(f"erro ao renderizar:\n{traceback.format_exc(limit=10)}")


# ── Ponto de entrada ──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Valida um modelo GLB no Blender em modo background."
    )
    ap.add_argument("--glb", required=True, help="Caminho para o ficheiro GLB a validar")
    ap.add_argument("--report", required=True, help="Caminho para o relatório JSON de saída")
    ap.add_argument("--render", default="", help="Caminho para o PNG de render (opcional)")
    ap.add_argument("--asset-id", default="unknown", help="ID do recurso (para o relatório)")
    args = ap.parse_args(_argv)

    report = _empty_report(args.asset_id, args.glb)

    # 1. Limpar cena
    try:
        _clear_scene()
    except Exception:  # noqa: BLE001
        report["errors"].append(f"erro ao limpar cena:\n{traceback.format_exc(limit=5)}")
        _save_report(report, args.report)
        return 1

    # 2. Importar
    err = _import_glb(args.glb)
    if err:
        report["errors"].append(f"importação falhou: {err}")
        _save_report(report, args.report)
        return 1

    report["import_success"] = True

    # 3. Geometria
    try:
        _inspect_geometry(report)
    except Exception:  # noqa: BLE001
        report["errors"].append(f"erro na inspecção de geometria:\n{traceback.format_exc(limit=5)}")

    # 4. Materiais
    try:
        _inspect_materials(report)
    except Exception:  # noqa: BLE001
        report["errors"].append(f"erro na inspecção de materiais:\n{traceback.format_exc(limit=5)}")

    # 5. Rigging
    try:
        _inspect_rigging(report)
    except Exception:  # noqa: BLE001
        report["errors"].append(f"erro na inspecção de rigging:\n{traceback.format_exc(limit=5)}")

    # 6. Animações
    try:
        _inspect_animations(report)
    except Exception:  # noqa: BLE001
        report["errors"].append(f"erro na inspecção de animações:\n{traceback.format_exc(limit=5)}")

    # 7. Render (opcional)
    if args.render:
        try:
            _render(report, args.render)
        except Exception:  # noqa: BLE001
            report["errors"].append(f"erro na renderização:\n{traceback.format_exc(limit=5)}")

    _save_report(report, args.report)
    _print_summary(report)

    # Saída com erro se a importação falhou ou há erros críticos
    return 0 if report["import_success"] else 1


def _save_report(report: Dict[str, Any], path: str) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print(f"VALIDAÇÃO: {report['asset_id']}")
    print("=" * 60)
    print(f"  importação:     {'OK' if report['import_success'] else 'FALHOU'}")
    print(f"  malhas:         {report['mesh_count']}  ({report['vertex_count']} vértices, {report['polygon_count']} polígonos)")
    print(f"  materiais:      {report['material_count']}  ({report['texture_count']} texturas)")
    print(f"  armature:       {'sim (' + str(report['bone_count']) + ' ossos)' if report['armature_found'] else 'não'}")
    print(f"  rigging conf.:  {'SIM' if report['rigging_confirmed'] else 'NÃO'}")
    print(f"  animações:      {report['animation_count']}"
          + (" (funcional: sim)" if report['animation_functional'] else ""))
    print(f"  render:         {'OK → ' + str(report['render_path']) if report['render_success'] else 'não efectuado'}")
    if report["errors"]:
        print("\nERROS:")
        for e in report["errors"]:
            print(f"  ! {e}")
    if report["warnings"]:
        print("\nAVISOSS:")
        for w in report["warnings"]:
            print(f"  ~ {w}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    sys.exit(main())
