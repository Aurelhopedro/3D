# Workflows disponíveis

## Como executar pelo telemóvel

1. Abrir o repositório em [github.com/Aurelhopedro/3D](https://github.com/Aurelhopedro/3D)
2. Tocar no separador **Actions**
3. Escolher o workflow na lista à esquerda
4. Tocar em **Run workflow** (botão verde à direita)
5. Preencher os parâmetros e confirmar

---

## Blender 3D Pipeline (`blender.yml`)

**Função**: executar operações 3D no Blender (criar, modificar, renderizar, exportar modelos).

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `job_file` | Ficheiro JSON com o trabalho | `jobs/example.json` |
| `operation` | Substituir a operação do job | (usa a do JSON) |
| `publish_release` | Publicar resultados como Release | false |

**Operações suportadas**: `create_model`, `modify_model`, `apply_material`, `optimize_model`, `animate_model`, `render_model`, `export_model`, `validate_model`

**Onde ver os resultados**: separador Actions → execução → secção Artifacts → `blender-job-<id>`

---

## Library Manager (`library-manager.yml`)

**Função**: gerir o catálogo de assets 3D (CC0). Descarregar, validar e catalogar recursos.

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `operation` | scan / download / validate / catalog / update | scan |
| `category` | Categoria de assets (all = todas) | all |
| `max_downloads` | Máximo de assets por execução (1-20) | 3 |
| `max_total_mb` | Limite de MB por execução (1-500) | 100 |
| `asset_id` | ID de um único asset (opcional) | (vazio) |
| `storage_mode` | temporary (só artefacto) ou persistent (commit) | temporary |

**Fluxo recomendado**:
1. `scan` → ver o que está disponível
2. `download` com `max_downloads=1` → testar um asset
3. `download` com `storage_mode=persistent` → guardar no repositório

---

## Sync Libraries (`sync-libraries.yml`)

**Função**: verificar o estado das ferramentas geradoras e addons em `config/libraries.json`.

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `library_id` | ID de uma biblioteca específica (vazio = todas) | (vazio) |
| `dry_run` | Apenas verificar, sem modificar | true |

**Execução automática**: segunda-feira às 03:00 UTC

---

## Generate Catalog (`generate-catalog.yml`)

**Função**: gerar/atualizar `library/catalog/assets.json` e `catalog/licenses.json`.

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `storage_mode` | temporary ou persistent | temporary |

**Execução automática**: após o Library Manager terminar.

---

## Validate Assets (`validate-assets.yml`)

**Função**: validar assets descarregados — JSON, caminhos, hashes, duplicados, tamanhos.

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `category` | Categoria a validar | all |
| `check_json_configs` | Validar também os JSONs de configuração | true |

**Execução automática**: após o Library Manager terminar.

---

## Workflows de personagens (mpfb-*.yml)

Workflows específicos para geração de personagens com MPFB2/MakeHuman. Ver `scripts/characters/` para os módulos correspondentes.

---

## Workflows de diagnóstico

| Workflow | Função |
|---|---|
| `diagnose-human.yml` | Diagnóstico geral de personagem MPFB |
| `diagnose-mpfb-eyes.yml` | Diagnóstico de olhos |
| `audit-orientation.yml` | Verificar orientação do modelo |
| `validate-3d-assets.yml` | Validação legacy de assets 3D |

---

## Limites e custos (repositório público)

- Minutos GitHub Actions em repositórios públicos: **gratuitos**
- Timeout por execução: **30 minutos** (Blender Pipeline) / **15-20 minutos** (outros)
- Cache do Blender: **ativo** — evita redownload do Blender (~350 MB) em execuções seguintes
- Artefactos: guardados por **30 dias** (Blender) / **7 dias** (Library) / **14 dias** (Validate)
- Tamanho máximo de ficheiro para commit: **50 MB** (aviso GitHub a partir deste valor)
- Total por execução de library em modo persistent: **200 MB**
