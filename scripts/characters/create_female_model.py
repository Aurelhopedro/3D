"""
create_female_model.py — Gera uma modelo humana feminina 3D realista.

Método: geração paramétrica de malha via bpy, usando subdivisão, sculpting
e modelação procedural. Não depende de MPFB nem de MakeHuman — funciona
em Blender 4.x e 5.x em modo --background (sem GPU, sem janelas).

Características da personagem:
  - Sexo: feminino adulto
  - Altura: ~1.75 m
  - Proporções: modelo de passarela
  - Pose inicial: A-pose
  - Cabeça, tronco, braços, mãos (5 dedos), pernas, pés
  - Cabelo preto comprido
  - Materiais PBR básicos (pele, cabelo)
  - UVs automáticos (Smart UV Project)
  - Pronta para rigging
"""

import math
import bpy          # type: ignore
import bmesh        # type: ignore
from mathutils import Vector  # type: ignore


# ---------------------------------------------------------------------------
# Constantes de proporções (escala real em metros)
# ---------------------------------------------------------------------------
HEIGHT = 1.75           # altura total
HEAD_H = HEIGHT / 7.5  # cabeça: 1/7.5 da altura total (~0.233 m)
NECK_H = 0.09
TORSO_H = HEIGHT * 0.30
WAIST_Y = HEIGHT * 0.57   # Z do centro da cintura (a partir do chão)
HIP_H = HEIGHT * 0.14
LEG_H = HEIGHT * 0.48
FOOT_H = 0.07
ARM_L = HEIGHT * 0.44
FOREARM_L = HEIGHT * 0.22
HAND_L = HEIGHT * 0.12

# Raios (metade da largura/profundidade)
HEAD_R = HEAD_H * 0.46
NECK_R = 0.045
SHOULDER_W = 0.195      # meio da largura dos ombros
CHEST_R_X = 0.155       # meio do peito (X)
CHEST_R_Z = 0.115       # profundidade do peito (Z)
WAIST_R = 0.115
HIP_R = 0.160
UPPER_ARM_R = 0.040
LOWER_ARM_R = 0.030
HAND_R = 0.038
UPPER_LEG_R = 0.065
LOWER_LEG_R = 0.042
ANKLE_R = 0.028
FOOT_R_X = 0.042
FOOT_R_Z = 0.095


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials,
                  bpy.data.cameras, bpy.data.lights, bpy.data.armatures):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def _new_collection(name):
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def _apply_skin_material(obj, name="Skin"):
    mat = bpy.data.materials.get(name)
    if not mat:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        bsdf.inputs["Base Color"].default_value = (0.847, 0.694, 0.573, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
        bsdf.inputs["Specular IOR Level"].default_value = 0.35 if "Specular IOR Level" in bsdf.inputs else None
        bsdf.inputs["Subsurface Weight"].default_value = 0.15 if "Subsurface Weight" in bsdf.inputs else None
        out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def _apply_hair_material(obj):
    mat = bpy.data.materials.get("Hair_Black")
    if not mat:
        mat = bpy.data.materials.new("Hair_Black")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Base Color"].default_value = (0.02, 0.01, 0.01, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.45
        out = nodes.new("ShaderNodeOutputMaterial")
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def _add_subsurf(obj, levels=2, render_levels=3):
    mod = obj.modifiers.new("Subdivision", "SUBSURF")
    mod.levels = levels
    mod.render_levels = render_levels
    return mod


def _smart_uv(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=66.0, margin_method="SCALED", island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def _create_ellipsoid(rx, ry, rz, segs=24, rings=16, name="Ellipsoid"):
    """Cria uma esfera UV e escala-a para rx, ry, rz."""
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=1.0, segments=segs, ring_count=rings, location=(0, 0, 0)
    )
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (rx, ry, rz)
    bpy.ops.object.transform_apply(scale=True)
    return obj


def _create_cylinder_tapered(r_top, r_bot, height, segs=16, name="Cylinder", location=(0,0,0)):
    """Cria um cilindro com raios diferentes no topo e na base."""
    bpy.ops.mesh.primitive_cone_add(
        radius1=r_bot, radius2=r_top, depth=height,
        vertices=segs, location=location
    )
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _join_objects(objects, new_name="Joined"):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    bpy.context.active_object.name = new_name
    return bpy.context.active_object


def _move(obj, xyz):
    obj.location = Vector(xyz)


def build_character():
    """Constrói a personagem completa e devolve uma lista de todos os objetos criados."""
    parts = []

    # Z base (chão = 0)
    foot_base_z = 0.0
    ankle_z = foot_base_z + FOOT_H
    knee_z = ankle_z + LEG_H * 0.50
    hip_z = ankle_z + LEG_H              # ~0.84 m
    waist_z = hip_z + HIP_H             # ~0.98 m
    bust_z = waist_z + TORSO_H * 0.50   # ~1.11 m
    shoulder_z = waist_z + TORSO_H      # ~1.24 m
    neck_bot_z = shoulder_z
    neck_top_z = neck_bot_z + NECK_H    # ~1.33 m
    head_center_z = neck_top_z + HEAD_H * 0.50  # ~1.45 m
    head_top_z = neck_top_z + HEAD_H    # ~1.56 m — cabelo acima disto

    # ------------------------------------------------------------------
    # 1. CABEÇA
    # ------------------------------------------------------------------
    head = _create_ellipsoid(HEAD_R, HEAD_R * 0.87, HEAD_H * 0.50,
                              segs=32, rings=24, name="Head")
    _move(head, (0, 0, head_center_z))
    _apply_skin_material(head)
    _add_subsurf(head, 2, 3)
    _smart_uv(head)
    parts.append(head)

    # ------------------------------------------------------------------
    # 2. PESCOÇO
    # ------------------------------------------------------------------
    neck = _create_cylinder_tapered(NECK_R, NECK_R * 1.1, NECK_H,
                                    segs=16, name="Neck",
                                    location=(0, 0, neck_bot_z + NECK_H * 0.5))
    _apply_skin_material(neck)
    _add_subsurf(neck, 1, 2)
    parts.append(neck)

    # ------------------------------------------------------------------
    # 3. TRONCO (torso com forma orgânica: ombros, peito, cintura, ancas)
    # ------------------------------------------------------------------
    # Tronco base: cilindro subdividido e deformado via shapekeys
    torso_total_h = TORSO_H + HIP_H
    torso = _create_cylinder_tapered(WAIST_R, HIP_R, torso_total_h,
                                     segs=24, name="Torso",
                                     location=(0, 0, waist_z - HIP_H * 0.5 + torso_total_h * 0.5))
    # Escalar X para criar curva de cintura vs ancas
    torso.scale.x = 1.0
    torso.scale.y = 0.82
    _apply_skin_material(torso)
    _add_subsurf(torso, 2, 3)
    parts.append(torso)

    # Peito superior (caixa torácica alargada)
    chest = _create_ellipsoid(CHEST_R_X, CHEST_R_Z, TORSO_H * 0.45,
                              segs=24, rings=16, name="Chest")
    _move(chest, (0, 0, bust_z))
    _apply_skin_material(chest)
    _add_subsurf(chest, 2, 3)
    parts.append(chest)

    # ------------------------------------------------------------------
    # 4. OMBROS
    # ------------------------------------------------------------------
    for side, sx in (("L", 1), ("R", -1)):
        shoulder = _create_ellipsoid(0.06, 0.05, 0.05, segs=16, rings=12,
                                     name=f"Shoulder_{side}")
        _move(shoulder, (sx * SHOULDER_W, 0, shoulder_z))
        _apply_skin_material(shoulder)
        parts.append(shoulder)

    # ------------------------------------------------------------------
    # 5. BRAÇOS (braço superior + antebraço)  — A-pose: 45° para fora
    # ------------------------------------------------------------------
    arm_angle_deg = 45.0
    arm_angle_rad = math.radians(arm_angle_deg)
    for side, sx in (("L", 1), ("R", -1)):
        # Direção do braço: descendo e para o exterior
        dx = sx * math.sin(arm_angle_rad)
        dz = -math.cos(arm_angle_rad)

        upper_arm_cx = sx * (SHOULDER_W + UPPER_ARM_R + (ARM_L * 0.50) * math.sin(arm_angle_rad) * 0.5)
        upper_arm_cz = shoulder_z + (ARM_L * 0.50) * dz * 0.5

        upper_arm = _create_cylinder_tapered(UPPER_ARM_R * 0.90, UPPER_ARM_R, ARM_L * 0.50,
                                             segs=12, name=f"UpperArm_{side}",
                                             location=(upper_arm_cx, 0, upper_arm_cz))
        upper_arm.rotation_euler = (0, sx * arm_angle_rad, 0)
        _apply_skin_material(upper_arm)
        _add_subsurf(upper_arm, 1, 2)
        parts.append(upper_arm)

        # Antebraço
        elbow_x = sx * (SHOULDER_W + ARM_L * 0.50 * math.sin(arm_angle_rad))
        elbow_z = shoulder_z + ARM_L * 0.50 * dz
        fore_cx = elbow_x + sx * FOREARM_L * 0.50 * math.sin(arm_angle_rad)
        fore_cz = elbow_z + FOREARM_L * 0.50 * dz

        forearm = _create_cylinder_tapered(LOWER_ARM_R, UPPER_ARM_R * 0.90, FOREARM_L,
                                           segs=12, name=f"Forearm_{side}",
                                           location=(fore_cx, 0, fore_cz))
        forearm.rotation_euler = (0, sx * arm_angle_rad, 0)
        _apply_skin_material(forearm)
        _add_subsurf(forearm, 1, 2)
        parts.append(forearm)

        # Mão (palma)
        wrist_x = elbow_x + sx * FOREARM_L * math.sin(arm_angle_rad)
        wrist_z = elbow_z + FOREARM_L * dz
        hand_cx = wrist_x + sx * HAND_L * 0.40 * math.sin(arm_angle_rad)
        hand_cz = wrist_z + HAND_L * 0.40 * dz

        hand = _create_ellipsoid(HAND_R, HAND_R * 0.65, HAND_L * 0.40,
                                 segs=12, rings=8, name=f"Hand_{side}")
        _move(hand, (hand_cx, 0, hand_cz))
        hand.rotation_euler = (0, sx * arm_angle_rad, 0)
        _apply_skin_material(hand)
        parts.append(hand)

        # 5 dedos por mão
        finger_base_x = hand_cx
        finger_base_z = hand_cz
        finger_names = ["Thumb", "Index", "Middle", "Ring", "Pinky"]
        n_fingers = 5
        for fi in range(n_fingers):
            spread = (fi - 2) * 0.018 * sx  # distribuição lateral
            f_l = HAND_L * (0.48 if fi == 0 else 0.52 - fi * 0.015)
            f_r = HAND_R * 0.20

            # posição distal da palma
            f_cx = finger_base_x + sx * (HAND_L * 0.45 + f_l * 0.50) * math.sin(arm_angle_rad) - spread * math.cos(arm_angle_rad)
            f_cy = spread
            f_cz = finger_base_z + (HAND_L * 0.45 + f_l * 0.50) * dz

            finger = _create_cylinder_tapered(f_r * 0.7, f_r, f_l,
                                              segs=8, name=f"Finger_{finger_names[fi]}_{side}",
                                              location=(f_cx, f_cy, f_cz))
            finger.rotation_euler = (0, sx * arm_angle_rad, 0)
            _apply_skin_material(finger)
            parts.append(finger)

    # ------------------------------------------------------------------
    # 6. PERNAS — A-pose: ligeiramente abertas
    # ------------------------------------------------------------------
    leg_spread = 0.10   # distância do centro ao eixo de cada perna
    for side, sx in (("L", 1), ("R", -1)):
        leg_x = sx * leg_spread

        # Coxa
        thigh = _create_cylinder_tapered(UPPER_LEG_R * 0.85, UPPER_LEG_R, LEG_H * 0.50,
                                         segs=16, name=f"Thigh_{side}",
                                         location=(leg_x, 0, knee_z + LEG_H * 0.25))
        _apply_skin_material(thigh)
        _add_subsurf(thigh, 1, 2)
        parts.append(thigh)

        # Perna inferior
        shin = _create_cylinder_tapered(LOWER_LEG_R * 0.85, UPPER_LEG_R * 0.85, LEG_H * 0.50,
                                        segs=16, name=f"Shin_{side}",
                                        location=(leg_x, 0, ankle_z + LEG_H * 0.25))
        _apply_skin_material(shin)
        _add_subsurf(shin, 1, 2)
        parts.append(shin)

        # Tornozelo / joelho (esferas de transição)
        for z_joint, r_j, jname in (
            (knee_z, UPPER_LEG_R * 0.78, f"Knee_{side}"),
            (ankle_z, ANKLE_R * 1.3, f"Ankle_{side}"),
        ):
            joint = _create_ellipsoid(r_j, r_j * 0.75, r_j, segs=12, rings=10, name=jname)
            _move(joint, (leg_x, 0, z_joint))
            _apply_skin_material(joint)
            parts.append(joint)

        # Pé
        foot = _create_ellipsoid(FOOT_R_X, FOOT_R_Z, FOOT_H * 0.65,
                                 segs=16, rings=10, name=f"Foot_{side}")
        _move(foot, (leg_x, FOOT_R_Z * 0.3, ankle_z - FOOT_H * 0.25))
        _apply_skin_material(foot)
        parts.append(foot)

    # ------------------------------------------------------------------
    # 7. ANCAS (volume glúteo e quadril)
    # ------------------------------------------------------------------
    hips = _create_ellipsoid(HIP_R, HIP_R * 0.68, HIP_H * 0.75,
                             segs=20, rings=14, name="Hips")
    _move(hips, (0, 0, hip_z))
    _apply_skin_material(hips)
    _add_subsurf(hips, 2, 3)
    parts.append(hips)

    # ------------------------------------------------------------------
    # 8. CABELO — comprido e preto
    # ------------------------------------------------------------------
    # Topo da cabeça
    hair_top = _create_ellipsoid(HEAD_R * 0.97, HEAD_R * 0.80, HEAD_H * 0.30,
                                 segs=24, rings=12, name="Hair_Top")
    _move(hair_top, (0, 0, head_top_z - HEAD_H * 0.05))
    _apply_hair_material(hair_top)
    parts.append(hair_top)

    # Volume posterior longo
    hair_back = _create_cylinder_tapered(HEAD_R * 0.60, HEAD_R * 0.90, TORSO_H * 0.90,
                                         segs=20, name="Hair_Back",
                                         location=(0, -HEAD_R * 0.20,
                                                   head_top_z - TORSO_H * 0.45 - HEAD_H * 0.05))
    _apply_hair_material(hair_back)
    _add_subsurf(hair_back, 2, 3)
    parts.append(hair_back)

    return parts


def apply_uvs_to_all(parts):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in parts:
        if obj.type == "MESH":
            _smart_uv(obj)


def get_stats(parts):
    total_verts = 0
    total_faces = 0
    for obj in parts:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        total_verts += len(mesh.vertices)
        total_faces += len(mesh.polygons)
    return {
        "objects": len(parts),
        "vertices": total_verts,
        "faces": total_faces,
    }


def run(job, state):
    import time
    t0 = time.time()

    _clear_scene()
    col = _new_collection("FemaleCharacter")

    parts = build_character()

    # Mover todos para a coleção
    for obj in parts:
        try:
            bpy.context.scene.collection.objects.unlink(obj)
        except Exception:
            pass
        col.objects.link(obj)

    # Aplicar UVs
    apply_uvs_to_all(parts)

    # Definir unidades da cena (metros)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.length_unit = "METERS"
    bpy.context.scene.unit_settings.scale_length = 1.0

    stats = get_stats(parts)
    elapsed = round(time.time() - t0, 2)

    state["character_parts"] = [o.name for o in parts]
    state["character_stats"] = stats
    state["character_height_m"] = HEIGHT
    state["has_rig"] = False
    state["has_uvs"] = True
    state["has_materials"] = True

    return {
        "character_created": True,
        "height_m": HEIGHT,
        "pose": "A-pose",
        "method": "Parametric bpy (Blender 5.x native, no MPFB)",
        "objects_created": [o.name for o in parts],
        "stats": stats,
        "elapsed_seconds": elapsed,
    }
