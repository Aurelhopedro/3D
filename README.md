# 3D — Blender na nuvem via GitHub Actions

Ambiente de produção 3D que executa o Blender inteiramente dentro de
runners temporários do GitHub Actions — sem instalar nada localmente, sem
Docker e sem guardar o executável do Blender no repositório. Construído
para ser operado por agentes de IA através de um MCP GitHub já existente.

## Objetivo

Este repositório permite criar, modificar, otimizar, animar, renderizar e
exportar modelos 3D enviando um simples ficheiro de trabalho em JSON. Todo
o processamento pesado acontece num runner `ubuntu-latest` descartável; o
teu telemóvel ou computador nunca precisa de correr o Blender.

## Como funciona

1. Um pedido de trabalho (**job**) é escrito como JSON (ver `jobs/example.json`).
2. O workflow `.github/workflows/blender.yml` é acionado (manualmente, ou
   via API/MCP com `workflow_dispatch`).
3. O runner descarrega a versão oficial do Blender definida em
   `config/blender.json`, verifica o checksum SHA-256 quando disponível, e
   corre-o em modo `--background`.
4. `scripts/main.py` lê o job, chama o módulo correspondente à operação
   pedida, gera os outputs pedidos (`.blend`, `.glb`, `preview.png`, …) e
   escreve sempre um `metadata.json` com o resultado real da execução.
5. Os resultados ficam disponíveis como **artefactos do workflow** e,
   opcionalmente, como **GitHub Release**.

## Estrutura do repositório

```
3D/
├── .github/workflows/blender.yml   # pipeline GitHub Actions
├── scripts/
│   ├── main.py                     # orquestrador: lê o job, dispatch, metadata
│   ├── create_model.py             # create_model
│   ├── modify_model.py             # modify_model
│   ├── materials.py                # apply_material
│   ├── optimize.py                 # optimize_model
│   ├── animate.py                  # animate_model
│   ├── render.py                   # render_model
│   ├── export.py                   # export_model
│   └── validate.py                 # validate_model + metadata.json
├── jobs/example.json               # job de exemplo (cubo vermelho)
├── config/blender.json             # versão do Blender e definições de render
├── docs/API.md                     # como disparar/consultar workflows via API
├── docs/ARCHITECTURE.md            # arquitetura técnica detalhada
├── requirements.txt
├── .gitignore
├── LICENSE
└── README.md
```

## Operações suportadas

| Operação         | O que faz |
|------------------|-----------|
| `create_model`   | Cria primitivas paramétricas (cube, sphere, cylinder, cone, plane, torus) ou composições simples |
| `modify_model`   | Carrega um `.blend` existente e aplica escala/rotação/posição/modificadores/material, preservando o original |
| `apply_material` | Aplica cor, metallic, roughness e transparência a um objeto |
| `optimize_model` | Reduz polígonos (Decimate), não destrutivo por omissão, com relatório de vértices/faces |
| `animate_model`  | Insere keyframes (demonstração de rotação, ou lista explícita de keyframes) |
| `render_model`   | Renderiza uma pré-visualização PNG com Cycles em CPU |
| `export_model`   | Exporta para BLEND/GLB (garantidos) e OBJ/FBX (se disponíveis nesta versão) |
| `validate_model` | Inspeciona a cena e produz um relatório |

Cada operação é um módulo independente em `scripts/`, o que permite
acrescentar novas operações sem reescrever o sistema — basta criar o
ficheiro e registá-lo no dicionário `OPERATIONS` de `scripts/main.py`.

## Como executar um trabalho

### Pela interface do GitHub
1. Separador **Actions** → workflow **Blender 3D Pipeline** → **Run workflow**.
2. Preenche `job_file` (por omissão `jobs/example.json`), opcionalmente
   `operation` (para forçar uma operação diferente da que está no JSON) e
   `publish_release` (para publicar os resultados num GitHub Release).

### Formato de um ficheiro de trabalho (job)
```json
{
  "job_id": "test-001",
  "operation": "create_model",
  "parameters": {
    "type": "cube",
    "size": 2,
    "color": "#FF0000"
  },
  "outputs": {
    "blend": true,
    "glb": true,
    "preview": true
  }
}
```
`outputs.blend` é `true` por omissão mesmo que o campo não exista no JSON.
`outputs.glb`, `outputs.obj`, `outputs.fbx` e `outputs.preview` só são
gerados se explicitamente pedidos.

### Consultar os resultados
- **Artefactos**: separador Actions → a execução → secção *Artifacts* →
  `blender-job-<job_id>` (contém `model.blend`, `model.glb`, `preview.png`,
  `metadata.json`, conforme pedido).
- **Release** (se `publish_release=true` e o job tiver sucesso): tag
  `3d-model-<job_id>` na secção *Releases*.
- `metadata.json` é sempre gerado — mesmo quando o job falha — e contém
  `success`, `errors`, `warnings`, contagem de objetos/vértices/faces e o
  estado de cada ficheiro de output.

## Integração com o MCP GitHub

Este repositório foi preparado para ser operado por agentes de IA através
de um MCP GitHub já ligado — ver `docs/API.md` para o detalhe de como
disparar `workflow_dispatch`, correlacionar o pedido com a execução
(`workflow run`), consultar o estado e recuperar os ficheiros publicados.

## Limitações atuais

- `create_model` gera apenas primitivas paramétricas — não interpreta
  descrições textuais livres como modelos realistas (isso exigiria um
  gerador/algoritmo de IA dedicado, ainda não implementado).
- `modify_model` suporta um conjunto inicial de modificadores
  (`subdivision`, `bevel`, `decimate`, `solidify`).
- A exportação OBJ/FBX depende dos exportadores estarem disponíveis na
  versão do Blender instalada — o relatório `metadata.json`/`warnings`
  indica claramente quando um formato não pôde ser exportado.
- A renderização usa Cycles em CPU; não há suporte a GPU (os runners
  `ubuntu-latest` do GitHub Actions não têm GPU dedicada).
- Execução de scripts de IA mais avançados dentro do Blender (geração
  procedural complexa, texto→3D) fica para uma fase futura, com uma
  política de segurança própria — ver `docs/ARCHITECTURE.md`.

## Custos e limites do GitHub Actions

- Em repositórios **públicos**, minutos do GitHub Actions em
  `ubuntu-latest` são gratuitos.
- Em repositórios **privados**, consomem a quota mensal de minutos da
  conta/plano (consulta a tua faturação em Settings → Billing).
- Cada execução tem um limite de `timeout-minutes: 30` definido no
  workflow (ajustável em `.github/workflows/blender.yml`).
- Artefactos ficam guardados por `retention-days: 30` por omissão.
- O download do Blender (~350 MB) é feito uma vez e fica em cache
  (`actions/cache`) para acelerar execuções seguintes com a mesma versão.

## Como adicionar novas funcionalidades

1. Cria `scripts/<nova_operacao>.py` com uma função `run(job, state)` que
   devolve um `dict` com o resultado.
2. Regista-a em `OPERATIONS` dentro de `scripts/main.py`.
3. Se a operação precisar de uma nova opção no `workflow_dispatch`
   (`operation`), acrescenta-a à lista `options` em
   `.github/workflows/blender.yml`.
4. Documenta o novo comportamento neste README e em `docs/ARCHITECTURE.md`.
