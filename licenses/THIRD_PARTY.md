# Licenças de terceiros

Este ficheiro regista as licenças de todo o código de terceiros incorporado neste repositório.

## Código de terceiros incorporado

Nenhum código de terceiros está incorporado diretamente no repositório neste momento. As ferramentas externas são instaladas dinamicamente nos runners do GitHub Actions durante a execução dos workflows.

---

## Ferramentas instaladas dinamicamente (não incorporadas)

### Blender

- **Versão**: ver `config/blender.json`
- **Licença**: GPL-2.0-or-later
- **Fonte**: download.blender.org (oficial)
- **Nota**: o Blender é executado como processo externo. Os scripts Python em `scripts/` correm dentro do Blender via `--python` e são código deste repositório (MIT).

### MPFB2 (MakeHuman Plugin for Blender 2)

- **Repositório**: https://github.com/makehumancommunity/mpfb2
- **Licença código**: AGPL-3.0
- **Assets MakeHuman base**: CC0-1.0
- **Nota**: o MPFB2 é instalado no runner em cada execução dos workflows `mpfb-*.yml`. Não está incluído no repositório.

---

## Assets de terceiros descarregados (biblioteca)

Os assets descarregados pelo Library Manager ficam em `library/assets/` com um ficheiro `LICENSE.txt` gerado automaticamente em cada pasta de asset. As licenças são:

### CC0-1.0 (Creative Commons Zero)

Assets com esta licença podem ser usados livremente, sem atribuição obrigatória:

- **KayKit / Kay Lousberg** — personagens, tileset urbano, dungeon
  - Evidência: ficheiros LICENSE incluídos nos repositórios GitHub oficiais
  - Crédito voluntário: Kay Lousberg / kaylousberg.com

- **Poly Haven** — veículos, materiais, HDRIs
  - Evidência: https://polyhaven.com/license
  - Requisito da API: indicar "Powered by Poly Haven" ao mostrar conteúdo obtido pela API

- **100Avatars (Polygonal Mind)** — avatares VRM (coleções r1, r2, r3)
  - Evidência: campo `"license": "CC0"` em https://github.com/ToxSam/open-source-avatars/blob/main/data/projects.json
  - Crédito voluntário: Polygonal Mind / polygonal.mind

- **ambientCG** — materiais PBR
  - Evidência: https://docs.ambientcg.com/license/

---

## Código deste repositório

O código original deste repositório (`scripts/`, `library/tools/`, workflows) é disponibilizado sob a licença indicada no ficheiro `LICENSE` na raiz do repositório.
