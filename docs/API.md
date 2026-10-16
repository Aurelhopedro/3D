# API — como um MCP (ou qualquer cliente) dispara e consulta o pipeline

Este documento explica como o MCP GitHub já ligado (ou qualquer outro
cliente da API REST do GitHub) pode iniciar um trabalho neste repositório,
acompanhar a sua execução e recuperar os resultados.

> Não é criado nenhum MCP novo neste repositório — esta documentação
> descreve apenas como usar a API do GitHub que o teu MCP existente já
> sabe falar.

## 1. Iniciar um workflow (`workflow_dispatch`)

Endpoint:

```
POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches
```

- `owner`: `Aurelhopedro`
- `repo`: `3D`
- `workflow_id`: `blender.yml` (ou o ID numérico devolvido por
  `GET /repos/{owner}/{repo}/actions/workflows`)

Corpo do pedido:

```json
{
  "ref": "main",
  "inputs": {
    "job_file": "jobs/example.json",
    "operation": "",
    "publish_release": "false"
  }
}
```

Esta chamada devolve **204 No Content** em caso de sucesso — **não**
devolve o `run_id` da execução criada. Isto é intencional por parte da
API do GitHub, e é a razão da secção seguinte.

## 2. Correlacionar o pedido com a execução (`run_id`)

Como o `dispatch` não devolve o `run_id` diretamente, usa esta estratégia:

1. Regista o instante (UTC) imediatamente antes de chamar `dispatches`.
2. Faz polling a:
   ```
   GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs
       ?event=workflow_dispatch&branch=main&per_page=5
   ```
3. Entre os runs devolvidos, escolhe o mais recente cujo `created_at` seja
   igual ou posterior ao instante registado no passo 1.
4. Se precisares de uma correlação mais forte quando há vários jobs
   disparados quase ao mesmo tempo, usa como desempate o `job_id` que
   definiste no teu `job_file` — ele aparece no nome do artefacto
   (`blender-job-<job_id>`) e, se `publish_release=true`, na tag do
   Release (`3d-model-<job_id>`) assim que a execução terminar.

Não assumas que o primeiro run devolvido é sempre o teu: outro trigger
manual pode ter acontecido entre os dois passos.

## 3. Consultar o estado da execução

```
GET /repos/{owner}/{repo}/actions/runs/{run_id}
```

Campos relevantes:
- `status`: `queued` | `in_progress` | `completed`
- `conclusion` (só quando `status == completed`): `success` | `failure` |
  `cancelled` | `timed_out` | …

Faz polling deste endpoint (ex.: a cada 10–15 segundos) até
`status == "completed"`.

## 4. Consultar erros / logs

```
GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs
```
devolve os jobs da execução e os respetivos `steps[].conclusion`. Para
logs em texto:
```
GET /repos/{owner}/{repo}/actions/runs/{run_id}/logs
```
(devolve um ZIP com os logs de todos os steps).

O passo **"Mostrar metadata.json"** do workflow imprime sempre o conteúdo
de `metadata.json` nos logs, mesmo quando o job falha — é o primeiro
sítio a olhar para perceber o que correu mal.

## 5. Recuperar os resultados (artefactos)

```
GET /repos/{owner}/{repo}/actions/runs/{run_id}/artifacts
```
Localiza o artefacto `blender-job-<job_id>` e usa:
```
GET /repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip
```
para descarregar um ZIP com `model.blend`, `model.glb`, `preview.png`,
`metadata.json` (conforme o que foi pedido em `outputs`). Este endpoint
exige autenticação mesmo em repositórios públicos.

## 6. Recuperar os resultados (GitHub Release, se publicado)

Se o job foi disparado com `publish_release=true` e terminou com sucesso:

```
GET /repos/{owner}/{repo}/releases/tags/3d-model-{job_id}
```

A resposta inclui `assets[].browser_download_url` para cada ficheiro
publicado — estes links são públicos (sem autenticação) se o repositório
for público.

## 7. Resumo do fluxo completo para um MCP

```
1. POST .../dispatches                         (inicia o job)
2. Poll GET .../runs?event=workflow_dispatch    (encontra o run_id)
3. Poll GET .../runs/{run_id}                   (espera status=completed)
4. GET .../runs/{run_id}/artifacts              (lista artefactos)
5. GET .../artifacts/{artifact_id}/zip          (descarrega metadata.json
                                                  + ficheiros gerados)
   — ou, se publish_release=true e sucesso —
   GET .../releases/tags/3d-model-{job_id}      (links diretos de download)
```

Nunca assumas sucesso só porque o `dispatch` devolveu 204 — esse código
confirma apenas que o pedido de execução foi aceite, não que o job
terminou nem que teve êxito. Confirma sempre `conclusion == "success"` no
passo 3 e consulta `metadata.json` para o resultado funcional real.
