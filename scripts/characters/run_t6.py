"""T6 patch: substitui BLENDER_EEVEE_NEXT por deteccao automatica."""
import bpy, sys, runpy
from pathlib import Path

# Ler o script T6 original, corrigir o motor e executar
src_path = Path(__file__).parent / "create_female_t6.py"
src = src_path.read_text(encoding="utf-8")

OLD = '    scene.render.engine = "BLENDER_EEVEE_NEXT"'
NEW = '''    _eevee_ok = None
    for _e in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            bpy.context.scene.render.engine = _e
            if bpy.context.scene.render.engine == _e:
                _eevee_ok = _e
                break
        except Exception:
            pass
    if _eevee_ok is None:
        raise RuntimeError("Motor EEVEE nao encontrado nesta versao do Blender.")
    report["engine_used"] = _eevee_ok'''

if OLD not in src:
    raise RuntimeError(f"Trecho nao encontrado em {src_path}")

src = src.replace(OLD, NEW)
# tambem actualizar o campo 'engine' no report inicial
src = src.replace('"engine": "BLENDER_EEVEE_NEXT"', '"engine": "auto_detect"')

exec(compile(src, str(src_path), "exec"), {"__name__": "__main__", "__file__": str(src_path)})
