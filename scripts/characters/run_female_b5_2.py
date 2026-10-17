"""Bootstrap B5.2: instala/ativa MPFB e executa o script original na mesma sessão."""
import os
import runpy
import sys
import traceback
import zipfile
from pathlib import Path

import bpy

OUTPUT = Path(os.environ.get('MPFB_OUTPUT', '/tmp/mpfb_output'))
OUTPUT.mkdir(parents=True, exist_ok=True)

try:
    import addon_utils

    source = Path(os.environ['MPFB_ZIP'])
    if not source.is_file():
        raise FileNotFoundError(f'MPFB ZIP ausente: {source}')

    # O ZIP de distribuição pode conter o manifesto numa subpasta.
    # Reempacotar como extensão Blender com manifesto na raiz.
    repacked = OUTPUT / 'mpfb-extension-install.zip'
    with zipfile.ZipFile(source) as src:
        manifests = [n for n in src.namelist() if n.endswith('blender_manifest.toml')]
        if not manifests:
            raise RuntimeError('ZIP MPFB não contém blender_manifest.toml')
        manifest = min(manifests, key=lambda n: len(Path(n).parts))
        prefix = manifest[:-len('blender_manifest.toml')]
        with zipfile.ZipFile(repacked, 'w', zipfile.ZIP_DEFLATED) as dst:
            for name in src.namelist():
                if name.startswith(prefix) and not name.endswith('/'):
                    relative = name[len(prefix):]
                    if relative and '..' not in Path(relative).parts:
                        dst.writestr(relative, src.read(name))

    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == 'user_default' and r.enabled), None)
    if repo is None:
        repo = repos.new(name='User Default', module='user_default',
                         custom_directory=str(OUTPUT / 'extensions'))
        repo.enabled = True

    module = f'bl_ext.{repo.module}.mpfb'
    print(f'[B5.2-BOOT] Instalando MPFB: {repacked}', flush=True)
    result = bpy.ops.extensions.package_install_files(
        filepath=str(repacked), repo=repo.module, enable_on_install=True)
    if 'FINISHED' not in result:
        raise RuntimeError(f'Instalação MPFB não concluída: {result}')

    if module not in bpy.context.preferences.addons:
        addon_utils.enable(module, default_set=True)
    if module not in bpy.context.preferences.addons:
        raise RuntimeError(f'Extensão não ativada: {module}')

    # poll() testa disponibilidade e contexto do operador sem criar personagem.
    try:
        op = bpy.ops.mpfb.create_human
        available = op.poll()
    except (AttributeError, RuntimeError) as exc:
        raise RuntimeError(f'Operador MPFB indisponível: {exc}') from exc
    if not available:
        raise RuntimeError('Operador mpfb.create_human existe, mas poll() retornou False')

    print(f'[B5.2-BOOT] MPFB ativo: {module}; create_human disponível', flush=True)
    script = Path(__file__).with_name('create_female_b5_2.py')
    if not script.is_file():
        raise FileNotFoundError(f'Script original não encontrado: {script}')
    runpy.run_path(str(script), run_name='__main__')
except BaseException:
    traceback.print_exc()
    sys.exit(1)
