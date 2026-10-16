"""
animate.py — Demonstração funcional de animação por keyframes.

Operação: "animate_model"

Insere keyframes de localização / rotação / escala ao longo de um intervalo
de frames. Sem "keyframes" explícitos no job, faz uma rotação de 360° em Z
como demonstração funcional. A arquitetura fica preparada para animações
mais complexas (rigs, shape keys, curvas de easing custom) — ver
docs/ARCHITECTURE.md, secção "Trabalho futuro".
"""

import math
import bpy  # type: ignore


def run(job, state):
    params = job.get("parameters", {})
    names = params.get("objects")
    targets = (
        [bpy.data.objects[n] for n in names if n in bpy.data.objects]
        if names
        else list(bpy.data.objects)
    )
    if not targets:
        raise ValueError("Nenhum objeto encontrado para animar.")

    frame_start = int(params.get("frame_start", 1))
    frame_end = int(params.get("frame_end", 48))
    keyframes = params.get("keyframes")

    scene = bpy.context.scene
    scene.frame_start = frame_start
    scene.frame_end = frame_end

    animated = []

    if keyframes:
        # Lista explícita: [{"frame": 1, "location": [0,0,0]}, {"frame": 48, "location": [0,0,5]}]
        for obj in targets:
            for kf in keyframes:
                scene.frame_set(int(kf["frame"]))
                if "location" in kf:
                    obj.location = tuple(kf["location"])
                    obj.keyframe_insert(data_path="location")
                if "rotation_degrees" in kf:
                    rx, ry, rz = kf["rotation_degrees"]
                    obj.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
                    obj.keyframe_insert(data_path="rotation_euler")
                if "scale" in kf:
                    s = kf["scale"]
                    obj.scale = (s, s, s) if isinstance(s, (int, float)) else tuple(s)
                    obj.keyframe_insert(data_path="scale")
            animated.append(obj.name)
    else:
        for obj in targets:
            scene.frame_set(frame_start)
            obj.rotation_euler = (0, 0, 0)
            obj.keyframe_insert(data_path="rotation_euler")

            scene.frame_set(frame_end)
            obj.rotation_euler = (0, 0, math.radians(360))
            obj.keyframe_insert(data_path="rotation_euler")

            if obj.animation_data and obj.animation_data.action:
                for fcurve in obj.animation_data.action.fcurves:
                    for kp in fcurve.keyframe_points:
                        kp.interpolation = "LINEAR"
            animated.append(obj.name)

    scene.frame_set(frame_start)

    return {
        "objects_animated": animated,
        "frame_range": [frame_start, frame_end],
        "mode": "explicit_keyframes" if keyframes else "demo_rotation",
    }
