# Fontes e Bibliotecas 3D

## Filosofia

Este repositório usa exclusivamente fontes com licença **CC0-1.0** para assets estáticos (descarregáveis automaticamente). Para ferramentas geradoras e addons Blender, a licença do código é avaliada separadamente da licença dos assets produzidos.

**Regra fundamental**: a licença do código de um gerador (MIT, AGPL, etc.) **não** se aplica automaticamente aos modelos que ele gera. A geometria gerada pelo utilizador pertence ao utilizador.

---

## Assets estáticos CC0 (descarregáveis automaticamente)

Ver `library/catalog/sources.json` para o estado completo e detalhado.

### KayKit (Kay Lousberg)
- **Licença**: CC0-1.0 ✅ verificada
- **Fonte**: repositórios GitHub oficiais (codeload.github.com)
- **Categorias**: personagens, ambientes urbanos, dungeon, veículos
- **Packs integrados**:
  - KayKit-Character-Pack-Adventures-1.0 — 5 personagens com 75 animações
  - KayKit-City-Builder-Bits-1.0 — tileset urbano (edifícios, estradas, carros)
  - KayKit-Dungeon-Remastered-1.0 — 200+ assets de dungeon/medieval

### Poly Haven
- **Licença**: CC0-1.0 ✅ verificada
- **Fonte**: API oficial (api.polyhaven.com / dl.polyhaven.org)
- **Categorias**: veículos, materiais, HDRI, ambientes
- **Requisitos da API**: User-Agent identificado; crédito "Powered by Poly Haven" ao mostrar conteúdo da API

### 100Avatars (Polygonal Mind)
- **Licença**: CC0-1.0 ✅ verificada (coleções r1, r2, r3)
- **Fonte**: Arweave (arweave.net) — armazenamento permanente descentralizado
- **Formato**: VRM 0.x (compatível com Blender via addon VRM ou como GLTF base)
- **Nota**: coleções NFT e vipe-heroes-genesis excluídas (CC-BY ou não avaliadas)

### ambientCG
- **Licença**: CC0-1.0 ✅ verificada
- **Fonte**: API v3 (ambientcg.com/api/v3)
- **Categorias**: materiais PBR (texturas, mapas normais, roughness, AO)
- **Estado**: esquema de download por confirmar via SCAN

---

## Ferramentas geradoras (não redistribuem assets)

Ver `config/libraries.json` para o estado completo.

### MPFB2 (MakeHuman Plugin for Blender)
- **Licença código**: AGPL-3.0
- **Estado**: ✅ **integrado** no pipeline de personagens
- **Workflows**: `mpfb-*.yml`
- **Atenção AGPL**: verificar obrigações de divulgação de código-fonte se distribuir software que usa este addon

### Building Tools (ranjian0)
- **Licença código**: MIT (não verificada no ficheiro LICENSE)
- **Estado**: registado, não integrado
- **Uso**: gerador procedural de edifícios — janelas, portas, telhados
- **Para integrar**: verificar licença, confirmar compatibilidade com Blender 5.2.1

### BPY-City (bowber)
- **Licença código**: MIT (não verificada)
- **Estado**: registado, não integrado
- **Uso**: gerador procedural de cidades via bpy
- **Para integrar**: verificar licença; executar num job sem segredos

### Procgen Maps (Beneking102)
- **Licença código**: desconhecida ❌
- **Estado**: registado, BLOQUEADO até confirmar licença
- **Nota**: sem ficheiro LICENSE visível no repositório = All Rights Reserved por defeito

---

## Fontes bloqueadas ou manuais

### Kenney
- **Licença conteúdo**: CC0 segundo página de suporte, mas não verificada por pacote
- **Estado**: BLOQUEADO — sem API de download documentada
- **Alternativa**: download manual em kenney.nl/assets

### Quaternius
- **Estado**: EXCLUÍDO por instrução do proprietário do repositório

### Khronos glTF Samples
- **Licença**: variável por modelo
- **Uso**: apenas para testes de validação de formatos

---

## Como adicionar uma nova fonte

1. Verificar a licença no repositório original (ficheiro LICENSE, não apenas a página web).
2. Confirmar separação entre licença do código e licença dos assets.
3. Adicionar entrada em `library/catalog/sources.json` (assets estáticos) ou `config/libraries.json` (ferramentas).
4. Para assets CC0: criar entrada no manifesto de categoria em `library/manifests/<categoria>.json`.
5. Executar `scan` pelo Library Manager para confirmar candidatos e hosts.
6. Só depois executar `download`.

---

## Limitações conhecidas

- ambientCG: esquema de resposta `/downloads` por confirmar via SCAN antes do primeiro download
- KayKit veículos: nomes de ficheiro GLB a confirmar via SCAN (car_taxi.glb, etc.)
- 100Avatars VRM: addon VRM necessário no Blender para rigging completo; GLTF base funciona sem addon
