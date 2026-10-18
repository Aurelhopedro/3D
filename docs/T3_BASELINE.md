# T3 — Ponto de partida oficial dos modelos femininos MPFB

`scripts/characters/create_female_t3.py` é a base oficial para novas versões.
Workflow: `.github/workflows/mpfb-t3.yml` (manual, `workflow_dispatch`).
`create_female_t4.py` é a T3 + madeixas frontais movidas para as laterais (aplica um patch de texto sobre a T3, por isso a T3 tem de ficar no repo).

## Problema

Nas versões B7, T1 e T2 os olhos apareciam por cima da testa, junto à linha do cabelo, em vez de nas órbitas.

## Causa (confirmada pelo `diagnostic_report.json` da T3)

1. **Shape keys não aplicadas.** O morph feminino era feito só com os valores das shape keys, mas as posições eram lidas de `human.data.vertices`, que é a malha base (sem o mix). O GLB é exportado com `export_apply=True`, ou seja, com o morph aplicado.
   Medido: o centro do olho deslocava-se **−6,07 cm em Z** (e +1,48 cm em Y) entre a malha neutra e a malha com morph (`bake_effect`). Os olhos eram postos na cabeça "neutra" e a cara visível era a "feminina".
2. **Globo à frente da pele (T1/T2).** A esfera era colocada à frente da superfície da pele via raycast, e ficava a flutuar fora da cara.
3. **Touca de cabelo grande demais (B7/T1/T2).** Esfera de ~13 cm com a borda abaixo da linha dos olhos: cobria testa e sobrancelhas.
4. **Madeixas frontais sobre a cara (B7, tratado na T4).** Nasciam na linha do cabelo e caíam à frente do rosto (y ≈ −0,13 m, x ≈ 0…±0,04 m), a tapar os olhos (x = ±0,03 m). Isto foi inferido por cálculo a partir das medidas.

## Correção na T3

| # | Mudança | Onde |
|---|---------|------|
| 1 | `bake_shape_keys()`: grava o mix na malha (`shape_key_add(from_mix=True)` + `shape_key_clear()`) antes de escalar e medir | pipeline |
| 2 | Escala para 1,70 m, medições e BVH só depois do bake | pipeline |
| 3 | Olho = esfera no centro (bbox) do grupo `helper-l-eye` / `helper-r-eye` da malha final; raio da bbox limitado a 9,5–14,5 mm | `build_eyes()` |
| 4 | Íris e pupila são calotes esféricas coladas ao globo, viradas para −Y | `build_eye_cap()` |
| 5 | Raycast só verifica; se a pele tapar o globo, ele avança no máximo 12 mm | `build_eyes()` |
| 6 | Touca em elipsoide ajustada à cabeça real; sem faces frontais abaixo da linha do cabelo (`eye_z + 6 cm`) | `build_hair()` |
| 7 | Fallback (altura 0,935 do corpo, x = ±31 mm) se os helpers não existirem ou saírem dos limites anatómicos | `build_eyes()` |

## Valores medidos na execução da T3

- Altura final 1,70 m; olhos a 0,933 da altura; separação 5,96 cm; raio 14,5 mm.
- `skin_state`: `abertura_ou_pele_atras_do_centro` (sem pele a tapar o globo).
- Grupos usados: `helper-l-eye` / `helper-r-eye` (72 vértices cada).

## Como correr

1. GitHub → Actions → **MPFB T3** → Run workflow.
2. Descarregar o artefacto `mpfb-t3-olhos-nas-orbitas`.
3. Validar visualmente: `t3_view_front.png`, `t3_view_eyes_closeup.png`, `mpfb-female-t3.glb`.
4. Diagnóstico: `diagnostic_report.json` (campos `bake`, `eyes.bake_effect`, `eyes.placement_*`).

## Regra para versões futuras

Se o script mexer em shape keys ou morphs, **bake antes de medir qualquer posição**. Qualquer coisa ancorada em `data.vertices` com shape keys ativas fica na cabeça errada.

## Limitações conhecidas

- Cabelo em curvas NURBS, sem partículas.
- Calçado em prisma simples.
- Sem rigging, pose T, sem roupa.
- Morph feminino experimental (não usa presets oficiais do MPFB).
