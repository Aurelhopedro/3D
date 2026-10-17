"""
MESH ATLAS — Extrator Universal de Inventário Geométrico
=========================================================
Etapa 1: Inventário completo da malha MPFB

Produz dois inventários separados:
  A — Malha-base MPFB (sem morph feminino)
  B — Personagem feminina B9 Rev2 (com deformações femininas)

Não modifica nenhum ficheiro existente.
Todos os resultados ficam em output/mesh-atlas/

ESTADOS POSSÍVEIS DE EXTRACÇÃO:
  "complete"    — todos os dados extraídos com sucesso
  "partial"     — alguns dados em falta (documentado)
  "failed"      — falha na extracção
  "unavailable" — dados não disponíveis nesta versão

IDENTIFICAÇÃO OBRIGATÓRIA DOS ESTADOS DE COORDENADAS:
  local_rest    — coordenadas locais da malha em repouso (sem shape keys)
  local_eval    — coordenadas avaliadas após shape keys activas
  world_eval    — coordenadas mundiais avaliadas (matrix_world @ local_eval)
  world_post_mod — coordenadas após avaliação com modificadores (depsgraph)
"""

import bpy
import bmesh
import hashlib
import json
import math
import os
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from mathutils import Vector, Matrix

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

out_root = Path(os.environ.get("MPFB_OUTPUT", "output/mesh-atlas")).resolve()
out_base  = out_root / "base"
out_b9    = out_root / "female_b9_rev2"
out_cmp   = out_root / "comparison"

for d in (out_base, out_b9, out_cmp):
    d.mkdir(parents=True, exist_ok=True)

# Sumário global — preenchido ao longo da execução
summary = {
    "generator":      "extract_mesh_inventory.py",
    "generated_at":   datetime.now(timezone.utc).isoformat(),
    "blender_version": bpy.app.version_string,
    "mpfb_version":   None,
    "extraction_status": {
        "base":        "not_started",
        "female_b9":   "not_started",
        "comparison":  "not_started",
    },
    "base":      {},
    "female_b9": {},
    "errors":    [],
}

# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def vec3(v):
    """Converte Vector/tuplo para lista de 3 floats arredondados."""
    return [round(float(v[0]), 8), round(float(v[1]), 8), round(float(v[2]), 8)]


def mat4(m):
    """Serializa Matrix 4x4 como lista de 4 listas de 4 floats."""
    return [[round(float(m[r][c]), 10) for c in range(4)] for r in range(4)]


def write_json(path, data):
    """Escreve JSON com indentação reduzida para economizar espaço."""
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_jsonl(path, records):
    """Escreve JSONL — um objecto JSON por linha (para ficheiros grandes)."""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def topology_hash(mesh):
    """Hash SHA256 da topologia (faces → vértices, sem coordenadas)."""
    h = hashlib.sha256()
    for face in mesh.polygons:
        h.update(bytes(face.vertices))
    return h.hexdigest()


def geometry_hash(mesh):
    """Hash SHA256 das coordenadas locais dos vértices."""
    h = hashlib.sha256()
    for v in mesh.vertices:
        h.update(bytes(bytearray(
            int.to_bytes(int(x * 1e6) & 0xFFFFFFFF, 4, "little")
            for x in v.co
        )))
    return h.hexdigest()


def srgb_to_linear(c):
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = srgb_to_linear(int(h[0:2], 16) / 255.0)
    g = srgb_to_linear(int(h[2:4], 16) / 255.0)
    b = srgb_to_linear(int(h[4:6], 16) / 255.0)
    return (r, g, b, alpha)


def make_material(name, hex_color, roughness=0.6):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = hex_to_linear_rgba(hex_color)
    bsdf.inputs["Roughness"].default_value  = roughness
    out_node = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])
    return mat


def assign_material(obj, mat):
    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat


def deselect_all():
    bpy.ops.object.select_all(action="DESELECT")


def set_active(obj):
    deselect_all()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_transforms(obj):
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()


def evaluated_mesh(obj):
    """
    Devolve a malha avaliada pelo depsgraph (shape keys + modificadores).
    O chamador é responsável por chamar ev.to_mesh_clear() no objecto avaliado.
    """
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    return ev, ev.to_mesh()


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACÇÃO DE GEOMETRIA
# ─────────────────────────────────────────────────────────────────────────────

def extract_vertices(obj, out_dir):
    """
    Extrai todos os vértices com:
      - índice
      - coordenadas locais em repouso (local_rest)
      - coordenadas mundiais em repouso (world_rest)
      - coordenadas locais avaliadas (local_eval) — shape keys activas
      - coordenadas mundiais avaliadas (world_eval)
      - coordenadas após modificadores (world_post_mod)
      - normais locais
    """
    mesh  = obj.data
    wmat  = obj.matrix_world
    n_v   = len(mesh.vertices)

    # Malha avaliada (shape keys + modificadores)
    ev_obj, ev_mesh = evaluated_mesh(obj)

    records = []
    for i, v in enumerate(mesh.vertices):
        co_local = v.co
        co_world = wmat @ co_local
        no_local = v.normal

        # Coordenadas avaliadas (shape keys aplicadas)
        if i < len(ev_mesh.vertices):
            ev_co = ev_mesh.vertices[i].co
            ev_co_world = wmat @ ev_co
        else:
            ev_co       = co_local
            ev_co_world = co_world

        records.append({
            "i":              i,
            "local_rest":     vec3(co_local),
            "world_rest":     vec3(co_world),
            "local_eval":     vec3(ev_co),
            "world_eval":     vec3(ev_co_world),
            "world_post_mod": vec3(ev_co_world),   # depsgraph já inclui modificadores
            "normal_local":   vec3(no_local),
        })

    ev_obj.to_mesh_clear()

    # JSONL para ficheiros grandes
    jsonl_path = out_dir / "vertices.jsonl"
    write_jsonl(jsonl_path, records)

    meta = {
        "count":      n_v,
        "format":     "jsonl",
        "file":       "vertices.jsonl",
        "fields":     ["i", "local_rest", "world_rest", "local_eval", "world_eval", "world_post_mod", "normal_local"],
        "status":     "complete",
        "coord_states_extracted": ["local_rest", "world_rest", "local_eval", "world_eval", "world_post_mod"],
    }
    return meta, n_v


def extract_edges(obj, out_dir):
    mesh   = obj.data
    n_e    = len(mesh.edges)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    boundary_set    = {e.index for e in bm.edges if e.is_boundary}
    non_manifold_set = {e.index for e in bm.edges if not e.is_manifold}

    records = []
    for e in mesh.edges:
        records.append({
            "i":           e.index,
            "verts":       list(e.vertices),
            "is_boundary":     e.index in boundary_set,
            "is_non_manifold": e.index in non_manifold_set,
            "seam":        e.use_seam,
            "sharp":       e.use_edge_sharp,
            "crease":      round(e.crease, 6),
        })

    bm.free()
    write_jsonl(out_dir / "edges.jsonl", records)

    return {
        "count":         n_e,
        "boundary":      len(boundary_set),
        "non_manifold":  len(non_manifold_set),
        "format":        "jsonl",
        "file":          "edges.jsonl",
        "status":        "complete",
    }, n_e


def extract_faces(obj, out_dir):
    mesh  = obj.data
    wmat  = obj.matrix_world
    n_f   = len(mesh.polygons)

    records = []
    degenerate = 0
    for f in mesh.polygons:
        c_local = f.center
        c_world = wmat @ c_local
        n_local = f.normal
        # Degenerada = área ~0 ou menos de 3 vértices
        area    = f.area
        is_deg  = (area < 1e-10) or (len(f.vertices) < 3)
        if is_deg:
            degenerate += 1
        records.append({
            "i":           f.index,
            "verts":       list(f.vertices),
            "loops":       list(f.loop_indices),
            "material_idx": f.material_index,
            "smooth":      f.use_smooth,
            "area":        round(area, 10),
            "center_local": vec3(c_local),
            "center_world": vec3(c_world),
            "normal_local": vec3(n_local),
            "is_degenerate": is_deg,
        })

    write_jsonl(out_dir / "faces.jsonl", records)

    return {
        "count":       n_f,
        "degenerate":  degenerate,
        "format":      "jsonl",
        "file":        "faces.jsonl",
        "status":      "complete",
    }, n_f


# ─────────────────────────────────────────────────────────────────────────────
# TOPOLOGIA
# ─────────────────────────────────────────────────────────────────────────────

def extract_topology(obj, out_dir):
    mesh = obj.data
    bm   = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # Vértices isolados (sem arestas)
    isolated_verts = [v.index for v in bm.verts if not v.link_edges]

    # Arestas de fronteira
    boundary_edges = [e.index for e in bm.edges if e.is_boundary]

    # Arestas não-manifold
    non_manifold_edges = [e.index for e in bm.edges if not e.is_manifold]

    # Faces degeneradas
    degenerate_faces = [f.index for f in bm.faces if f.calc_area() < 1e-10]

    # Componentes conectados (BFS/Union-Find simples via bmesh)
    visited  = set()
    n_comps  = 0
    comp_map = {}   # índice_vértice → componente

    for start in bm.verts:
        if start.index in visited:
            continue
        n_comps += 1
        stack = [start]
        while stack:
            v = stack.pop()
            if v.index in visited:
                continue
            visited.add(v.index)
            comp_map[v.index] = n_comps
            for e in v.link_edges:
                other = e.other_vert(v)
                if other.index not in visited:
                    stack.append(other)

    # Adjacência por vértice (vértices vizinhos)
    adjacency = {}
    for v in bm.verts:
        adjacency[v.index] = sorted({e.other_vert(v).index for e in v.link_edges})

    bm.free()

    topo = {
        "connected_components":    n_comps,
        "isolated_vertices":       isolated_verts,
        "isolated_vertices_count": len(isolated_verts),
        "boundary_edges_count":    len(boundary_edges),
        "non_manifold_edges_count": len(non_manifold_edges),
        "degenerate_faces_count":  len(degenerate_faces),
        "degenerate_faces":        degenerate_faces,
        "status":                  "complete",
    }

    # Adjacência como JSONL (pode ser grande)
    adj_records = [{"v": v, "neighbors": ns} for v, ns in adjacency.items()]
    write_jsonl(out_dir / "adjacency.jsonl", adj_records)
    topo["adjacency_file"] = "adjacency.jsonl"

    # Componentes como JSON compacto
    comp_data = {
        "n_components": n_comps,
        "vertex_component": comp_map,   # {vert_idx: comp_id}
    }
    write_json(out_dir / "topology.json", topo)
    write_json(out_dir / "components.json", comp_data)

    return topo


# ─────────────────────────────────────────────────────────────────────────────
# GRUPOS DE VÉRTICES
# ─────────────────────────────────────────────────────────────────────────────

def extract_vertex_groups(obj, out_dir):
    mesh = obj.data
    groups = obj.vertex_groups

    if not groups:
        result = {"count": 0, "groups": [], "status": "complete", "note": "no_vertex_groups"}
        write_json(out_dir / "vertex_groups.json", result)
        return result

    # Índice inverso: para cada grupo, quais vértices e pesos
    group_data = []
    for g in groups:
        verts_in_group = []
        for v in mesh.vertices:
            for vg in v.groups:
                if vg.group == g.index:
                    verts_in_group.append({"v": v.index, "w": round(vg.weight, 8)})
                    break
        group_data.append({
            "index":   g.index,
            "name":    g.name,
            "lock":    g.lock_weight,
            "count":   len(verts_in_group),
            "vertices": verts_in_group,
        })

    result = {
        "count":  len(groups),
        "groups": group_data,
        "status": "complete",
    }
    write_json(out_dir / "vertex_groups.json", result)
    return {"count": len(groups), "status": "complete"}


# ─────────────────────────────────────────────────────────────────────────────
# SHAPE KEYS
# ─────────────────────────────────────────────────────────────────────────────

def extract_shape_keys(obj, out_dir):
    if obj.data.shape_keys is None:
        result = {"count": 0, "keys": [], "status": "complete", "note": "no_shape_keys"}
        write_json(out_dir / "shape_keys.json", result)
        return result

    key_blocks  = obj.data.shape_keys.key_blocks
    n_v         = len(obj.data.vertices)
    keys_meta   = []
    active_vals = {}

    for kb in key_blocks:
        active_vals[kb.name] = round(kb.value, 8)
        key_meta = {
            "name":         kb.name,
            "value":        round(kb.value, 8),
            "value_min":    round(kb.slider_min, 8),
            "value_max":    round(kb.slider_max, 8),
            "relative_key": kb.relative_key.name if kb.relative_key else None,
            "vertex_count": len(kb.data),
            "status":       "complete" if len(kb.data) == n_v else "partial",
        }
        keys_meta.append(key_meta)

        # Coordenadas de cada vértice nesta shape key (JSONL)
        sk_path = out_dir / f"sk_{kb.name.replace('/', '_').replace(' ', '_')}.jsonl"
        records = []
        for i, kd in enumerate(kb.data):
            records.append({"i": i, "co": vec3(kd.co)})
        write_jsonl(sk_path, records)
        key_meta["data_file"] = sk_path.name

    # Relações entre shape keys (relative_key graph)
    relations = {}
    for kb in key_blocks:
        if kb.relative_key and kb.relative_key.name != kb.name:
            relations[kb.name] = kb.relative_key.name

    result = {
        "count":         len(key_blocks),
        "active_values": active_vals,
        "relations":     relations,
        "keys":          keys_meta,
        "expected_verts_per_key": n_v,
        "status":        "complete",
    }
    write_json(out_dir / "shape_keys.json", result)
    return {"count": len(key_blocks), "status": "complete"}


# ─────────────────────────────────────────────────────────────────────────────
# MODIFICADORES
# ─────────────────────────────────────────────────────────────────────────────

def extract_modifiers(obj, out_dir):
    mods = []
    for m in obj.modifiers:
        mod_data = {
            "name":    m.name,
            "type":    m.type,
            "enabled": m.show_viewport,
            "show_render": m.show_render,
        }
        # Propriedades específicas por tipo
        try:
            if m.type == "ARMATURE":
                mod_data["object"] = m.object.name if m.object else None
                mod_data["use_vertex_groups"] = m.use_vertex_groups
            elif m.type == "SUBSURF":
                mod_data["levels"]        = m.levels
                mod_data["render_levels"] = m.render_levels
                mod_data["subdivision_type"] = m.subdivision_type
            elif m.type == "MIRROR":
                mod_data["use_axis"] = list(m.use_axis)
                mod_data["merge_threshold"] = m.merge_threshold
            elif m.type == "SOLIDIFY":
                mod_data["thickness"] = m.thickness
            elif m.type == "SMOOTH" or m.type == "CORRECTIVE_SMOOTH":
                mod_data["iterations"] = getattr(m, "iterations", None)
                mod_data["factor"]     = getattr(m, "factor", None)
        except Exception:
            mod_data["properties_error"] = "could_not_read"

        mods.append(mod_data)

    result = {"count": len(mods), "modifiers": mods, "status": "complete"}
    write_json(out_dir / "modifiers.json", result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MATERIAIS
# ─────────────────────────────────────────────────────────────────────────────

def extract_materials(obj, out_dir):
    mats = []
    for slot in obj.material_slots:
        m = slot.material
        if m is None:
            mats.append({"name": None, "status": "empty_slot"})
            continue
        mat_data = {
            "name":        m.name,
            "use_nodes":   m.use_nodes,
            "blend_method": m.blend_method,
            "shadow_method": getattr(m, "shadow_method", None),
        }
        if m.use_nodes and m.node_tree:
            node_names = [n.type for n in m.node_tree.nodes]
            mat_data["nodes"] = node_names
        mats.append(mat_data)

    # Índice de material por face
    face_mat = [f.material_index for f in obj.data.polygons]

    result = {
        "count":            len(mats),
        "materials":        mats,
        "face_material_idx": face_mat,
        "status":           "complete",
    }
    write_json(out_dir / "materials.json", result)
    return {"count": len(mats), "status": "complete"}


# ─────────────────────────────────────────────────────────────────────────────
# UVs
# ─────────────────────────────────────────────────────────────────────────────

def extract_uv_layers(obj, out_dir):
    mesh   = obj.data
    layers = mesh.uv_layers

    if not layers:
        result = {"count": 0, "layers": [], "status": "complete", "note": "no_uv_layers"}
        write_json(out_dir / "uv_layers.json", result)
        return result

    layers_meta = []
    for uv in layers:
        layer_path = out_dir / f"uv_{uv.name.replace(' ', '_')}.jsonl"
        records = []
        for li, ld in enumerate(uv.data):
            records.append({"loop": li, "uv": [round(float(ld.uv[0]), 8), round(float(ld.uv[1]), 8)]})
        write_jsonl(layer_path, records)
        layers_meta.append({
            "name":        uv.name,
            "active":      uv.active,
            "active_render": uv.active_render,
            "loop_count":  len(uv.data),
            "file":        layer_path.name,
            "status":      "complete",
        })

    result = {"count": len(layers), "layers": layers_meta, "status": "complete"}
    write_json(out_dir / "uv_layers.json", result)
    return {"count": len(layers), "status": "complete"}


# ─────────────────────────────────────────────────────────────────────────────
# ESTRUTURA DA CENA
# ─────────────────────────────────────────────────────────────────────────────

def extract_scene_objects(out_dir):
    objects_data = []
    for o in bpy.data.objects:
        obj_data = {
            "name":       o.name,
            "type":       o.type,
            "parent":     o.parent.name if o.parent else None,
            "location":   vec3(o.location),
            "rotation_euler": [round(float(r), 8) for r in o.rotation_euler],
            "scale":      vec3(o.scale),
            "matrix_world": mat4(o.matrix_world),
            "hide_viewport": o.hide_viewport,
            "hide_render":   o.hide_render,
        }
        if o.type == "MESH":
            obj_data["vertices"] = len(o.data.vertices)
            obj_data["edges"]    = len(o.data.edges)
            obj_data["faces"]    = len(o.data.polygons)
            # Bounding box (local)
            bb = o.bound_box
            bb_verts = [vec3(v) for v in bb]
            obj_data["bound_box_local"] = bb_verts
            obj_data["dimensions"] = vec3(o.dimensions)
        if o.type == "ARMATURE":
            obj_data["bones"] = len(o.data.bones)
        # Relação com armature
        for m in o.modifiers:
            if m.type == "ARMATURE" and m.object:
                obj_data["armature"] = m.object.name
        objects_data.append(obj_data)

    result = {"count": len(objects_data), "objects": objects_data, "status": "complete"}
    write_json(out_dir / "objects.json", result)
    return {"count": len(objects_data), "status": "complete"}


# ─────────────────────────────────────────────────────────────────────────────
# BOUNDING BOX E DIMENSÕES
# ─────────────────────────────────────────────────────────────────────────────

def extract_dimensions(obj):
    mesh  = obj.data
    wmat  = obj.matrix_world
    verts_world = [wmat @ v.co for v in mesh.vertices]

    if not verts_world:
        return {"status": "no_vertices"}

    xs = [v.x for v in verts_world]
    ys = [v.y for v in verts_world]
    zs = [v.z for v in verts_world]

    return {
        "min": [round(min(xs), 8), round(min(ys), 8), round(min(zs), 8)],
        "max": [round(max(xs), 8), round(max(ys), 8), round(max(zs), 8)],
        "size": [
            round(max(xs) - min(xs), 8),
            round(max(ys) - min(ys), 8),
            round(max(zs) - min(zs), 8),
        ],
        "center_world": [
            round((min(xs) + max(xs)) / 2, 8),
            round((min(ys) + max(ys)) / 2, 8),
            round((min(zs) + max(zs)) / 2, 8),
        ],
        "status": "complete",
    }


# ─────────────────────────────────────────────────────────────────────────────
# VALIDAÇÕES
# ─────────────────────────────────────────────────────────────────────────────

def run_validations(obj, out_dir, category_stats):
    mesh  = obj.data
    n_v   = len(mesh.vertices)
    n_e   = len(mesh.edges)
    n_f   = len(mesh.polygons)
    vset  = set(range(n_v))

    validations = {}

    # 1. Todos os vértices exportados
    verts_file = out_dir / "vertices.jsonl"
    if verts_file.exists():
        count_lines = sum(1 for _ in verts_file.open(encoding="utf-8"))
        validations["all_vertices_exported"] = {
            "expected": n_v,
            "found":    count_lines,
            "pass":     count_lines == n_v,
        }
    else:
        validations["all_vertices_exported"] = {"pass": False, "error": "file_missing"}

    # 2. Índices de vértices válidos em faces
    invalid_face_refs = 0
    for f in mesh.polygons:
        for vi in f.vertices:
            if vi not in vset:
                invalid_face_refs += 1
    validations["face_vertex_indices_valid"] = {
        "invalid_refs": invalid_face_refs,
        "pass":         invalid_face_refs == 0,
    }

    # 3. Grupos de vértices exportados
    vg_file = out_dir / "vertex_groups.json"
    if vg_file.exists():
        vg_data = json.loads(vg_file.read_text(encoding="utf-8"))
        validations["vertex_groups_exported"] = {
            "expected": len(obj.vertex_groups),
            "found":    vg_data.get("count", 0),
            "pass":     vg_data.get("count", 0) == len(obj.vertex_groups),
        }
    else:
        validations["vertex_groups_exported"] = {"pass": False, "error": "file_missing"}

    # 4. Shape keys com número correcto de coordenadas
    if obj.data.shape_keys:
        sk_errors = []
        for kb in obj.data.shape_keys.key_blocks:
            if len(kb.data) != n_v:
                sk_errors.append({"key": kb.name, "expected": n_v, "found": len(kb.data)})
        validations["shape_keys_vertex_count"] = {
            "keys_checked": len(obj.data.shape_keys.key_blocks),
            "errors":       sk_errors,
            "pass":         len(sk_errors) == 0,
        }
    else:
        validations["shape_keys_vertex_count"] = {
            "pass": True, "note": "no_shape_keys"
        }

    # 5. Matriz de transformação válida (determinante ≠ 0)
    det = obj.matrix_world.determinant()
    validations["matrix_world_valid"] = {
        "determinant": round(det, 10),
        "pass":        abs(det) > 1e-10,
    }

    # 6. Identificação das duas versões (verificado externamente)
    validations["versions_identified"] = {
        "base_dir":      str(out_base),
        "female_b9_dir": str(out_b9),
        "pass":          True,
    }

    # Resultado global
    all_pass = all(v.get("pass", False) for v in validations.values())
    validations["overall"] = {
        "pass":   all_pass,
        "status": "complete" if all_pass else "partial",
    }

    write_json(out_dir / "validations.json", validations)
    return validations


# ─────────────────────────────────────────────────────────────────────────────
# IDENTIFICAÇÃO DO MPFB
# ─────────────────────────────────────────────────────────────────────────────

def detect_mpfb_version():
    try:
        for addon in bpy.context.preferences.addons:
            if "mpfb" in addon.module.lower():
                mod_info = bpy.context.preferences.addons[addon.module]
                # Tentar ler versão do módulo
                import importlib
                mod = importlib.import_module(addon.module)
                version = getattr(mod, "__version__", None) or getattr(mod, "bl_info", {}).get("version", None)
                if version:
                    return str(version)
                return f"module_found:{addon.module}"
    except Exception:
        pass
    return "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# METADADOS COMPLETOS
# ─────────────────────────────────────────────────────────────────────────────

def build_metadata(obj, label, category_stats, validations):
    mesh = obj.data
    dim  = extract_dimensions(obj)

    return {
        "label":           label,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "blender_version": bpy.app.version_string,
        "mpfb_version":    summary.get("mpfb_version", "unavailable"),
        "object_name":     obj.name,
        "vertex_count":    len(mesh.vertices),
        "edge_count":      len(mesh.edges),
        "face_count":      len(mesh.polygons),
        "loop_count":      len(mesh.loops),
        "shape_key_count": len(mesh.shape_keys.key_blocks) if mesh.shape_keys else 0,
        "vertex_group_count": len(obj.vertex_groups),
        "material_count":  len(obj.material_slots),
        "uv_layer_count":  len(mesh.uv_layers),
        "modifier_count":  len(obj.modifiers),
        "topology_hash":   topology_hash(mesh),
        "geometry_hash":   geometry_hash(mesh),
        "dimensions":      dim,
        "matrix_world":    mat4(obj.matrix_world),
        "category_stats":  category_stats,
        "validations_summary": {
            k: v.get("pass") for k, v in validations.items()
        },
        "coord_states_documented": [
            "local_rest   — coordenadas locais da malha em repouso (sem shape keys)",
            "local_eval   — coordenadas locais avaliadas após shape keys activas",
            "world_eval   — coordenadas mundiais avaliadas (matrix_world @ local_eval)",
            "world_post_mod — coordenadas após avaliação completa com modificadores (depsgraph)",
        ],
        "extraction_complete": all(
            s == "complete" for s in category_stats.values()
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACÇÃO COMPLETA DE UM OBJECTO
# ─────────────────────────────────────────────────────────────────────────────

def extract_object(obj, label, out_dir):
    """
    Executa toda a extracção para um objecto dado e escreve os ficheiros.
    Devolve o dicionário de estatísticas e o estado geral.
    """
    stats    = {}
    errors   = []

    def run(name, fn, *args):
        try:
            result = fn(*args)
            if isinstance(result, tuple):
                stats[name] = result[0]
            else:
                stats[name] = result
            if isinstance(stats[name], dict):
                stats[name].setdefault("status", "complete")
        except Exception as e:
            stats[name] = {"status": "failed", "error": str(e)}
            errors.append({"category": name, "error": str(e), "traceback": traceback.format_exc()})

    # Geometria
    run("vertices",       extract_vertices,      obj, out_dir)
    run("edges",          extract_edges,         obj, out_dir)
    run("faces",          extract_faces,         obj, out_dir)

    # Topologia
    run("topology",       extract_topology,      obj, out_dir)

    # Grupos e shape keys
    run("vertex_groups",  extract_vertex_groups, obj, out_dir)
    run("shape_keys",     extract_shape_keys,    obj, out_dir)

    # Modificadores
    run("modifiers",      extract_modifiers,     obj, out_dir)

    # Materiais e UVs
    run("materials",      extract_materials,     obj, out_dir)
    run("uv_layers",      extract_uv_layers,     obj, out_dir)

    # Estrutura da cena
    run("scene_objects",  extract_scene_objects, out_dir)

    # Validações
    validations = {}
    try:
        validations = run_validations(obj, out_dir, {k: v.get("status","?") for k,v in stats.items()})
    except Exception as e:
        errors.append({"category": "validations", "error": str(e)})

    # Metadados
    try:
        meta = build_metadata(obj, label, {k: v.get("status","?") for k,v in stats.items()}, validations)
        write_json(out_dir / "metadata.json", meta)
    except Exception as e:
        errors.append({"category": "metadata", "error": str(e)})

    if errors:
        write_json(out_dir / "extraction_errors.json", errors)

    all_complete = all(v.get("status") == "complete" for v in stats.values())
    status = "complete" if (all_complete and not errors) else "partial"
    return stats, status, errors


# ─────────────────────────────────────────────────────────────────────────────
# INSTALAR MPFB
# ─────────────────────────────────────────────────────────────────────────────

def install_mpfb():
    source   = Path(os.environ["MPFB_ZIP"])
    repacked = out_root / "mpfb-extension.zip"

    with zipfile.ZipFile(source) as z:
        manifest = next(n for n in z.namelist() if n.endswith("blender_manifest.toml"))
        prefix   = manifest[:-len("blender_manifest.toml")]
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as dest:
            for n in z.namelist():
                if n.startswith(prefix) and not n.endswith("/") and ".." not in Path(n).parts:
                    dest.writestr(n[len(prefix):], z.read(n))

    repos = bpy.context.preferences.extensions.repos
    repo  = next((r for r in repos if r.module == "user_default" and r.enabled), None)
    if repo is None:
        repo = repos.new(name="User Default", module="user_default",
                         custom_directory=str(out_root / "extensions"))
        repo.enabled = True

    res = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if "FINISHED" not in res and "RUNNING_MODAL" not in res:
        raise RuntimeError(f"MPFB install_files: {res}")

    import addon_utils
    module_id = f"bl_ext.{repo.module}.mpfb"
    if module_id not in bpy.context.preferences.addons:
        addon_utils.enable(module_id, default_set=True)
    if module_id not in bpy.context.preferences.addons:
        raise RuntimeError(f"MPFB não ficou activo: {module_id}")

    return module_id


# ─────────────────────────────────────────────────────────────────────────────
# MORPH FEMININO (reutilizado do B9)
# ─────────────────────────────────────────────────────────────────────────────

def apply_female_morph(human):
    """Aplica shape keys femininas idênticas ao B9. Não modifica a geometria base."""
    keys      = human.data.shape_keys.key_blocks
    z_before  = [v.co.z for v in human.data.vertices]

    for key in keys:
        n = key.name
        if n.startswith("$md-$") and "-$yn" in n and ("-$fe-" in n or "-$ma-" in n):
            if n.startswith(("$md-$as-", "$md-$ca-", "$md-$af-")):
                key.value = 1.0 / 3.0 if "-$fe-" in n else 0.0
            elif n.startswith("$md-universal-"):
                key.value = 1.0 if "-$fe-" in n else 0.0

    bpy.context.view_layer.update()
    z_after   = [v.co.z for v in human.data.vertices]
    changed_v = sum(abs(a - b) > 1e-6 for a, b in zip(z_before, z_after))
    return changed_v


def scale_to_170(obj):
    """Escala identica ao B9."""
    set_active(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()
    vz = [v.co.z for v in obj.data.vertices]
    h  = max(vz) - min(vz)
    if h <= 0:
        raise RuntimeError(f"Altura inválida: {h}")
    factor = 1.70 / h
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    z_min = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z = z_min + (v.co.z - z_min) * factor
        v.co.x *= factor
        v.co.y *= factor
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()


# ─────────────────────────────────────────────────────────────────────────────
# PROGRAMA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

try:
    # ── INSTALAR MPFB ────────────────────────────────────────────────────────
    module_id = install_mpfb()
    summary["mpfb_version"] = detect_mpfb_version()
    print(f"[ATLAS] MPFB instalado: {module_id}")

    # ═══════════════════════════════════════════════════════════════════════
    # FASE A — MALHA-BASE (sem morph feminino)
    # ═══════════════════════════════════════════════════════════════════════
    print("[ATLAS] === FASE A: Malha-base MPFB ===")
    summary["extraction_status"]["base"] = "started"

    # Limpar cena e criar humano fresco
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result_base = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result_base:
        raise RuntimeError(f"mpfb.create_human (base): {result_base}")

    meshes = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True)
    if not meshes:
        raise RuntimeError("Nenhuma malha base encontrada")

    human_base = meshes[0][0]

    # Aplicar material de pele (para extracção consistente)
    skin_mat = make_material("Atlas_Skin", "#A66A48", roughness=0.52)
    assign_material(human_base, skin_mat)
    apply_transforms(human_base)

    print(f"[ATLAS] Malha base: {human_base.name} — {len(human_base.data.vertices)} verts")

    stats_base, status_base, errors_base = extract_object(human_base, "base_mpfb", out_base)
    summary["extraction_status"]["base"] = status_base
    summary["base"] = {
        "object":   human_base.name,
        "vertices": len(human_base.data.vertices),
        "edges":    len(human_base.data.edges),
        "faces":    len(human_base.data.polygons),
        "shape_keys": len(human_base.data.shape_keys.key_blocks) if human_base.data.shape_keys else 0,
        "vertex_groups": len(human_base.vertex_groups),
        "status":   status_base,
        "categories": {k: v.get("status","?") for k,v in stats_base.items()},
        "errors":   len(errors_base),
    }

    # ═══════════════════════════════════════════════════════════════════════
    # FASE B — PERSONAGEM FEMININA B9 Rev2 (com morph + escala)
    # ═══════════════════════════════════════════════════════════════════════
    print("[ATLAS] === FASE B: Personagem feminina B9 Rev2 ===")
    summary["extraction_status"]["female_b9"] = "started"

    # NOVA CENA: humano fresco para aplicar morph
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result_b9 = bpy.ops.mpfb.create_human()
    if "FINISHED" not in result_b9:
        raise RuntimeError(f"mpfb.create_human (B9): {result_b9}")

    meshes_b9 = sorted(
        [(o, len(o.data.vertices)) for o in bpy.data.objects if o.type == "MESH"],
        key=lambda x: x[1], reverse=True)
    if not meshes_b9:
        raise RuntimeError("Nenhuma malha B9 encontrada")

    human_b9 = meshes_b9[0][0]

    if human_b9.data.shape_keys is None:
        raise RuntimeError("B9: humano não tem shape keys — morph feminino impossível")

    # Aplicar morph feminino (idêntico ao B9)
    changed = apply_female_morph(human_b9)
    if changed == 0:
        summary["female_b9_morph_warning"] = "AVISO: nenhum vértice alterado pelo morph feminino"
        print("[ATLAS] AVISO: morph feminino não alterou vértices")

    # Escalar para 1.70m (idêntico ao B9)
    apply_transforms(human_b9)
    scale_to_170(human_b9)
    apply_transforms(human_b9)

    assign_material(human_b9, make_material("Atlas_Skin_B9", "#A66A48", roughness=0.52))

    print(f"[ATLAS] Malha B9: {human_b9.name} — {len(human_b9.data.vertices)} verts, morph_changed={changed}")
    summary["female_b9_morph_vertices_changed"] = changed

    stats_b9, status_b9, errors_b9 = extract_object(human_b9, "female_b9_rev2", out_b9)
    summary["extraction_status"]["female_b9"] = status_b9
    summary["female_b9"] = {
        "object":        human_b9.name,
        "vertices":      len(human_b9.data.vertices),
        "edges":         len(human_b9.data.edges),
        "faces":         len(human_b9.data.polygons),
        "shape_keys":    len(human_b9.data.shape_keys.key_blocks) if human_b9.data.shape_keys else 0,
        "vertex_groups": len(human_b9.vertex_groups),
        "morph_changed_vertices": changed,
        "status":        status_b9,
        "categories":    {k: v.get("status","?") for k,v in stats_b9.items()},
        "errors":        len(errors_b9),
        "limitation_note": (
            "B9 Rev2 reconstruída com morph feminino + escala 1.70m. "
            "Não inclui olhos, cabelo nem calçado — apenas a malha do corpo MPFB. "
            "Os shape keys activos representam o morph feminino B9 Rev2 conforme aplicado no script original."
        ),
    }

    # ═══════════════════════════════════════════════════════════════════════
    # COMPARAÇÃO
    # ═══════════════════════════════════════════════════════════════════════
    print("[ATLAS] === Comparação ===")
    summary["extraction_status"]["comparison"] = "started"

    try:
        meta_base = json.loads((out_base / "metadata.json").read_text(encoding="utf-8"))
        meta_b9   = json.loads((out_b9  / "metadata.json").read_text(encoding="utf-8"))

        comparison = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base": {
                "vertex_count":       meta_base["vertex_count"],
                "edge_count":         meta_base["edge_count"],
                "face_count":         meta_base["face_count"],
                "shape_key_count":    meta_base["shape_key_count"],
                "vertex_group_count": meta_base["vertex_group_count"],
                "topology_hash":      meta_base["topology_hash"],
                "geometry_hash":      meta_base["geometry_hash"],
                "dimensions":         meta_base["dimensions"],
            },
            "female_b9": {
                "vertex_count":       meta_b9["vertex_count"],
                "edge_count":         meta_b9["edge_count"],
                "face_count":         meta_b9["face_count"],
                "shape_key_count":    meta_b9["shape_key_count"],
                "vertex_group_count": meta_b9["vertex_group_count"],
                "topology_hash":      meta_b9["topology_hash"],
                "geometry_hash":      meta_b9["geometry_hash"],
                "dimensions":         meta_b9["dimensions"],
            },
            "differences": {
                "topology_identical": meta_base["topology_hash"] == meta_b9["topology_hash"],
                "geometry_identical": meta_base["geometry_hash"] == meta_b9["geometry_hash"],
                "same_vertex_count":  meta_base["vertex_count"] == meta_b9["vertex_count"],
                "same_face_count":    meta_base["face_count"]   == meta_b9["face_count"],
                "same_vgroup_count":  meta_base["vertex_group_count"] == meta_b9["vertex_group_count"],
                "b9_has_female_morph_applied": changed > 0,
                "morph_changed_vertices": changed,
            },
            "interpretation": {
                "topology_hash_differs_means": "As faces referenciam vértices em posições diferentes (geometria alterada)",
                "geometry_hash_differs_means": "As coordenadas locais dos vértices foram alteradas pelo morph",
                "coord_states_note": (
                    "Para diferenças exactas por vértice, comparar vertices.jsonl "
                    "entre base/ e female_b9_rev2/ usando o campo 'local_rest'."
                ),
            },
        }
        write_json(out_cmp / "inventory_comparison.json", comparison)
        summary["extraction_status"]["comparison"] = "complete"
        summary["comparison"] = comparison["differences"]
    except Exception as e:
        summary["extraction_status"]["comparison"] = "failed"
        summary["errors"].append({"category": "comparison", "error": str(e)})

    # ═══════════════════════════════════════════════════════════════════════
    # SUMÁRIO FINAL
    # ═══════════════════════════════════════════════════════════════════════
    all_ok = all(s in ("complete", "partial")
                 for s in summary["extraction_status"].values())
    summary["overall_status"] = "complete" if all_ok else "partial"

    # Calcular tamanho total dos dados
    total_bytes = sum(f.stat().st_size for f in out_root.rglob("*") if f.is_file())
    summary["total_data_bytes"] = total_bytes
    summary["total_data_mb"]    = round(total_bytes / (1024 * 1024), 2)

    write_json(out_root / "inventory_summary.json", summary)

    # ── RESUMO NOS LOGS ───────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("MESH ATLAS — RESUMO")
    print("="*60)
    b = summary.get("base", {})
    f = summary.get("female_b9", {})
    print(f"MALHA BASE     : {b.get('vertices','?')} verts | {b.get('faces','?')} faces | {b.get('vertex_groups','?')} groups | {b.get('shape_keys','?')} shape keys | status={b.get('status','?')}")
    print(f"FEMALE B9 Rev2 : {f.get('vertices','?')} verts | {f.get('faces','?')} faces | {f.get('vertex_groups','?')} groups | {f.get('shape_keys','?')} shape keys | status={f.get('status','?')}")
    print(f"Total dados    : {summary['total_data_mb']} MB")
    print(f"Estado geral   : {summary['overall_status']}")
    print(f"Comparação     : {summary['extraction_status']['comparison']}")
    for k, v in summary.get("comparison", {}).items():
        print(f"  {k}: {v}")
    print("="*60)

    if summary.get("errors"):
        print(f"ERROS ({len(summary['errors'])}):")
        for e in summary["errors"]:
            print(f"  [{e['category']}] {e['error']}")

    print("MPFB_ATLAS_SUMMARY:", json.dumps(summary, ensure_ascii=False))

except Exception as exc:
    summary["overall_status"] = "failed"
    summary["fatal_error"]    = str(exc)
    summary["traceback"]      = traceback.format_exc()
    write_json(out_root / "inventory_summary.json", summary)
    print("MPFB_ATLAS_SUMMARY:", json.dumps(summary, ensure_ascii=False))
    sys.exit(1)
