"""
materials.py — Criação e aplicação de materiais básicos.

Operação: "apply_material"

Suporta: cor base, metallic, roughness e transparência (alpha), usando o nó
"Principled BSDF" do sistema de materiais do Blender (compatível com Cycles
e Eevee).
"""

import bpy  # type: ignore


def _hex_to_rgba(hex_color, alpha=1.0):
    hex_color = (hex_color or "#CCCCCC").lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    if len(hex_color) != 6:
        raise ValueError(f"Cor hexadecimal inválida: '{hex_color}'")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0

    def to_linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (to_linear(r), to_linear(g), to_linear(b), alpha)


def apply_color(obj, hex_color, metallic=0.0, roughness=0.5, alpha=1.0, name=None):
    """Cria (ou reutiliza) um material Principled BSDF e aplica-o ao objeto."""
    mat_name = name or f"{obj.name}_material"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    mat.use_nodes = True

    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        raise RuntimeError("Nó 'Principled BSDF' não encontrado no material.")

    rgba = _hex_to_rgba(hex_color, alpha)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Metallic"].default_value = float(metallic)
    bsdf.inputs["Roughness"].default_value = float(roughness)

    if alpha < 1.0:
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = float(alpha)
        mat.blend_method = "BLEND"
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = "HASHED"

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return mat


def run(job, state):
    """Entry point para a operação 'apply_material' standalone sobre um job."""
    params = job.get("parameters", {})
    object_name = params.get("object")

    if not object_name:
        if not bpy.data.objects:
            raise ValueError("Nenhum objeto na cena para aplicar material.")
        obj = bpy.context.active_object or bpy.data.objects[0]
    else:
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ValueError(f"Objeto '{object_name}' não encontrado na cena.")

    mat = apply_color(
        obj,
        params.get("color", "#CCCCCC"),
        params.get("metallic", 0.0),
        params.get("roughness", 0.5),
        params.get("alpha", 1.0),
    )
    return {"object": obj.name, "material": mat.name}
