"""
render.py — Renderização de pré-visualizações em segundo plano.

Operação: "render_model"

Usa Cycles em CPU por omissão — nunca exige GPU dedicada, conforme
requisito do projeto. Motor, dispositivo, amostras e resolução vêm de
config/blender.json, mas podem ser substituídos por parameters do job.
"""

import json
import os
import bpy  # type: ignore
import mathutils  # type: ignore

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "blender.json"
)


def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _ensure_camera_and_light():
    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        bpy.ops.object.camera_add(location=(6, -6, 5))
        cam = bpy.context.active_object
        direction = mathutils.Vector((0, 0, 0)) - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.camera = cam
    elif bpy.context.scene.camera is None:
        bpy.context.scene.camera = next(o for o in bpy.data.objects if o.type == "CAMERA")

    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        bpy.ops.object.light_add(type="SUN", location=(4, -4, 8))
        bpy.context.active_object.data.energy = 3.0


def configure_render(params=None):
    params = params or {}
    config = _load_config()
    scene = bpy.context.scene

    engine = params.get("engine", config.get("render_engine", "CYCLES"))
    scene.render.engine = engine

    if engine == "CYCLES":
        scene.cycles.device = params.get("device", config.get("render_device", "CPU"))
        scene.cycles.samples = int(params.get("samples", config.get("render_samples", 32)))

    width, height = params.get("resolution", config.get("render_resolution", [1280, 720]))
    scene.render.resolution_x = int(width)
    scene.render.resolution_y = int(height)
    scene.render.image_settings.file_format = "PNG"

    _ensure_camera_and_light()


def render_preview(output_path, params=None):
    configure_render(params)
    scene = bpy.context.scene
    scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)
    if not os.path.isfile(output_path):
        raise RuntimeError(f"O render terminou mas o ficheiro não foi criado: {output_path}")
    return output_path


def run(job, state):
    params = job.get("parameters", {})
    output_path = os.path.join(state["output_dir"], params.get("filename", "preview.png"))
    path = render_preview(output_path, params)
    return {"preview_path": path}
