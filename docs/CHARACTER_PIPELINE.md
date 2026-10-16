# Importação de personagem feminina — fase 1

Este módulo **não gera uma pessoa realista do zero**. Importa uma malha humana existente, preserva a geometria e os materiais, guarda `.blend` e `.glb` e renderiza quatro imagens de inspeção. É uma primeira etapa verificável para o projeto de desfiles.

## Ficheiro necessário

Colocar uma personagem adulta licenciada para reutilização em `assets/characters/base_female.glb` (ou `.fbx`, `.obj`, `.gltf`). Preferir modelo em A-pose ou T-pose, com UVs, texturas e rig. A licença deve permitir os usos pretendidos e a **redistribuição pública**, se for incluída no repositório público. Caso não permita, **não a adicionar ao repositório público**; usar um repositório privado e executar aí este workflow.

## Execução

No GitHub: **Actions → Character Model Import → Run workflow**. Deixar `source_file` como `assets/characters/base_female.glb`, ou indicar o caminho real dentro de `assets/characters/`.

## Resultados

Descarregar o artifact `female-character-import`: `female-model.blend`, `female-model.glb`, `front.png`, `side.png`, `back.png`, `perspective.png`, `metadata.json`.

## Limitações

- A altura é reportada nas unidades do Blender; não é redimensionada automaticamente para 1,75 m para evitar quebrar rigs e roupas.
- A qualidade da personagem depende do ficheiro de origem.
- Rigging e animação não são criados por este script.
- O resultado não foi testado em GitHub Actions nesta entrega; é necessário executar e inspecionar as imagens.
- O workflow existente `.github/workflows/blender.yml` não é alterado.
