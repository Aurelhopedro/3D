# Arquitetura técnica

## Visão geral

O sistema é composto por três camadas:

1. **Runner efémero (GitHub Actions)** — uma máquina Linux temporária que
   só existe durante a execução do workflow. Nada é persistido nela entre
   execuções, exceto o cache do binário do Blender (`actions/cache`), que
   é uma otimização de velocidade e não uma dependência funcional — o
   sistema funciona igualmente bem com cache vazio (apenas mais lento).
2. **Orquestrador Python (`scripts/main.py`)** — corre dentro do
   interpretador Python embutido no Blender (acedido via `bpy`) e trata
   cada pedido como um **job**: um objeto JSON autocontido que descreve
   *o quê* fazer e *que outputs* produzir.
3. **Módulos de operação (`scripts/*.py`)** — cada operação do domínio
   (criar, modificar, materiais, otimizar, animar, renderizar, exportar,
   validar) é isolada no seu próprio ficheiro, com uma função `run(job,
   state)` como ponto de entrada único. Isto permite adicionar operações
   novas sem tocar nas existentes.

## Ciclo de vida de uma execução

```
workflow_dispatch (API/MCP ou UI)
        │
        ▼
checkout do repo → ler config/blender.json → validar job_file
        │
        ▼
cache hit? ── não ──► descarregar blender-X.Y.Z-linux-x64.tar.xz
    │                  + verificar checksum SHA-256 oficial
    │ sim                + extrair para blender_bin/
    ▼                          │
        ◄────────────────────┘
        ▼
blender --background --factory-startup --python scripts/main.py -- job.json output/<job_id>
        │
        ├─ main.py lê e valida o job
        ├─ main.py despacha para o módulo da operação pedida
        ├─ main.py gera outputs pedidos (blend/glb/obj/fbx/preview)
        └─ main.py escreve output/<job_id>/metadata.json (SEMPRE)
        ▼
upload-artifact (sempre) ──► opcional: gh release create/upload
        ▼
job falha explicitamente se run_job.outcome != success
```

`--factory-startup` garante que a execução não depende de preferências
locais de utilizador (determinismo entre execuções).

## O modelo de job

Um job é um objeto JSON com quatro campos:

- `job_id` (string, obrigatório) — identificador único, usado no nome da
  pasta de output, do artefacto e da tag do Release.
- `operation` (string, obrigatório) — uma chave do dicionário `OPERATIONS`
  em `scripts/main.py`.
- `parameters` (objeto, obrigatório) — parâmetros específicos da operação.
- `outputs` (objeto, opcional) — que ficheiros gerar depois da operação
  principal: `blend` (omissão `true`), `glb`, `obj`, `fbx`, `preview`.

Esta separação entre "operação principal" e "outputs pedidos" existe
porque nem toda a operação implica necessariamente gerar todos os
formatos — por exemplo, um job de `optimize_model` pode só querer o
relatório e o `.blend`, sem gastar tempo a renderizar uma pré-visualização.

## Porquê sem Docker e sem GPU

- **Sem Docker**: o runner `ubuntu-latest` já fornece um ambiente Linux
  limpo e descartável; adicionar uma camada de contentor só aumentaria o
  tempo de arranque sem benefício de isolamento adicional neste contexto
  (o runner em si já é efémero).
- **Sem GPU**: os runners padrão do GitHub Actions não têm GPU dedicada.
  `render.py` usa Cycles em modo `CPU` deliberadamente, para que o sistema
  funcione em qualquer runner `ubuntu-latest` sem configuração extra.

## Porquê o Blender nunca é guardado no histórico Git

O binário do Blender (~350 MB) é descarregado em cada execução (ou obtido
do cache do GitHub Actions, que é uma camada de infraestrutura separada
do histórico do repositório) a partir das fontes oficiais em
`download.blender.org`. Guardá-lo no Git infletiria o tamanho do
repositório permanentemente e tornaria impossível trocar de versão sem
reescrever o histórico. A versão a usar é controlada inteiramente por
`config/blender.json`.

## Segurança

- O workflow só executa código Python que já está commitado no
  repositório (`scripts/*.py`) — nunca código recebido diretamente de um
  pedido externo. Os jobs JSON são **dados**, não código: `main.py`
  interpreta campos conhecidos (`type`, `size`, `color`, …), nunca faz
  `eval`/`exec` sobre conteúdo do job.
- O caminho do ficheiro de job é validado no workflow para impedir
  sequências `..` (travessia de diretórios).
- O `GITHUB_TOKEN` automático é usado com `permissions: contents: write`
  — o mínimo necessário para publicar Releases — sem exigir um Personal
  Access Token.
- `timeout-minutes: 30` ao nível do job evita execuções presas a
  consumirem minutos indefinidamente.
- Nenhum segredo é impresso nos logs; os passos que usam `GH_TOKEN`
  fazem-no apenas como variável de ambiente para o `gh` CLI.

## Correlação entre pedido e execução

`workflow_dispatch` não devolve o ID da execução (`run_id`) de forma
síncrona — ver `docs/API.md` para a estratégia de correlação recomendada
(poll por `job_id` no nome do artefacto / tag do Release).

## Trabalho futuro

- Geradores de modelos mais avançados (ex.: a partir de descrições
  textuais, usando um algoritmo ou modelo dedicado — atualmente fora do
  âmbito desta primeira versão).
- Suporte a mais modificadores em `modify_model.py` (array, mirror,
  boolean).
- Animações mais complexas: rigs/armatures, shape keys, curvas de easing
  customizadas.
- Execução de scripts de IA gerados dinamicamente dentro do Blender —
  **requer** uma política de segurança e isolamento dedicada (sandboxing
  do código, lista de operações `bpy` permitidas) antes de ser introduzida;
  não está implementada nesta versão.
- Upload direto para serviços de armazenamento de modelos 3D (além de
  GitHub Releases).
