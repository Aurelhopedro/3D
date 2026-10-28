# Licenças dos assets

## Regra geral

Apenas assets com licença **CC0-1.0** verificada são descarregados automaticamente. Cada pasta de asset em `library/assets/<categoria>/<id>/` contém um ficheiro `LICENSE.txt` gerado automaticamente com:

- Nome da licença
- URL do texto legal
- URL de evidência da verificação
- Data de verificação
- Fonte original
- Informação de atribuição (CC0 = não obrigatória)

## Fontes aprovadas para download automático

| Fonte | Licença assets | Verificada | Evidência |
|---|---|---|---|
| KayKit (Kay Lousberg) | CC0-1.0 | ✅ 2026-10-21 | Ficheiro LICENSE nos repositórios GitHub |
| Poly Haven | CC0-1.0 | ✅ 2026-10-20 | https://polyhaven.com/license |
| 100Avatars (Polygonal Mind) — r1, r2, r3 | CC0-1.0 | ✅ 2026-10-27 | projects.json no repositório oficial |
| ambientCG | CC0-1.0 | ✅ 2026-10-20 | https://docs.ambientcg.com/license/ |

## Fontes bloqueadas

| Fonte | Motivo do bloqueio |
|---|---|
| Kenney | Sem API de download; licença CC0 por confirmar por pacote |
| Quaternius | Excluído por instrução do proprietário |
| Khronos glTF Samples | Licença variável por modelo |
| MakeHuman Community | Licença por pacote não verificada (CC0 e CC-BY misturados) |

## Uso dos assets CC0

Assets CC0 podem ser usados:
- Em projetos comerciais ✅
- Sem atribuição obrigatória ✅
- Modificados e redistribuídos ✅
- Em jogos, filmes, apps ✅

Crédito voluntário é sempre apreciado pelas fontes.

## Nota sobre ferramentas geradoras

A licença de uma ferramenta geradora (MIT, AGPL, etc.) **não se aplica** à geometria que ela gera. A geometria criada pelo utilizador pertence ao utilizador. Ver `licenses/THIRD_PARTY.md` para detalhes sobre as ferramentas.
