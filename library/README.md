# Biblioteca 3D (Fase 1)

Infraestrutura isolada para localizar, verificar, descarregar, classificar e catalogar recursos 3D
reutilizáveis. **Não toca** nos scripts, workflows e modelos existentes do projeto. A integração com
Blender/scripts de personagens, roupas, carros e cenas é a Fase 2 (não implementada aqui).

## Como executar (pelo telemóvel)

GitHub → separador **Actions** → **Library Manager (Biblioteca 3D)** → **Run workflow**.

| Operação | O que faz |
|---|---|
| `scan` | Organiza a estrutura, gera o relatório de licenças e consulta as APIs oficiais: lista ficheiros candidatos e hosts de download. Não descarrega modelos. |
| `download` | Descarrega só entradas aprovadas (ver abaixo), valida e escreve metadados. |
| `validate` | Confirma existência, tamanho, sha256, formatos e dependências em falta. |
| `catalog` | Gera `catalog/assets.json`, `catalog/licenses.json` e `reports/catalog_summary.md`. |
| `update` | Compara com a origem; só substitui o que mudou na origem e não foi alterado localmente. |

Entradas: `category` (ou `all`), `max_downloads` (1-20, padrão 3), `max_total_mb` (1-500, padrão 100),
`asset_id` opcional e `storage_mode`.

- **temporary** (padrão): os recursos ficam só no artefacto da execução (7 dias). Nada é commitado.
- **persistent**: um segundo job (com `contents: write`) verifica tamanhos (ficheiro ≤ 50 MB, total ≤ 200 MB),
  copia só `library/assets`, `library/reports` e o catálogo, e só faz commit se houver alterações.
  Sem Git LFS. O `.gitignore` da raiz ignora `*.glb`, `*.obj`, `*.fbx`, `*.png`; o job usa `git add -f`.

O relatório de cada execução aparece no resumo da execução e em `library/reports/` (dentro do artefacto).

## Regra de licenças

Download automático **apenas** para `CC0-1.0` com: licença da entrada verificada (`license_verified: true`),
URL de evidência, fonte com licença de conteúdo verificada, e redistribuição confirmada. Tudo o resto
(CC-BY, MIT, GPL, desconhecida) fica registado e **bloqueado**. A licença de um programa/código não se
aplica aos modelos distribuídos com ele, e "gratuito" não significa CC0.

## Estado das fontes (verificado em 2026-10-20)

| Fonte | Licença do conteúdo | Download automático |
|---|---|---|
| Poly Haven | CC0 (assets). A API tem termos próprios: User-Agent único e crédito "Powered by Poly Haven" ao mostrar conteúdo da API. | Resolver implementado; **esquema `/files` e host de download por confirmar** no SCAN |
| ambientCG | CC0 | Resolver (API v3) implementado; **esquema `downloads` e host/CDN por confirmar** no SCAN |
| Kenney | CC0 segundo a página de suporte; confirmar o ficheiro de licença de cada pacote | Sem API de download documentada (manual) |
| Quaternius | Páginas de pacotes indicam CC0; relatos de licença diferente em alguns pacotes | Verificar pacote a pacote; sem URL direto documentado |
| MakeHuman Community | Pacotes CC0 **e** CC-BY (por pacote/asset) | Verificar por asset; sem URL de pacote confirmado |
| Khronos glTF Samples | Variável por modelo | Só testes de validação; nunca presumir CC0 |

## Como ativar o download de um recurso

1. Executar `scan` para o recurso. O relatório (`scan_report.json`) mostra os ficheiros candidatos
   (`candidates`) e os hosts observados (`hosts_unconfirmed`).
2. Confirmar que os hosts pertencem à fonte oficial e adicioná-los a `allowed_download_hosts` da fonte em
   `catalog/sources.json`.
3. Preencher `selector` na entrada do manifesto (ex.: resolução e formato) até restar um conjunto pequeno e
   inequívoco (limite por recurso: 40 ficheiros).
4. Executar `download` com `asset_id`.

## Estrutura

```
library/
├── catalog/    sources.json (manual), assets.json e licenses.json (gerados)
├── manifests/  um JSON por categoria (declara o que se pretende e a licença)
├── assets/     <categoria>/<id>/ com ficheiros originais, metadata.json e LICENSE.txt
├── presets/    reservado para a Fase 2
├── tools/      common, download_assets, validate_assets, organize_assets, build_catalog, check_licenses
├── tests/      testes automatizados (stdlib)
└── reports/    relatórios gerados
```

Os formatos originais (GLB, glTF, OBJ, FBX, BLEND, VRM...) são preservados; não há conversões nesta fase.
Não se afirma compatibilidade com nenhuma versão do Blender sem testes reais.

## Segurança dos downloads

HTTPS obrigatório · lista de hosts por fonte · redirecionamentos verificados salto a salto · timeouts e
tentativas limitadas · deteção de downloads incompletos (Content-Length/tamanho/md5) · limites por
ficheiro e por execução · ZIP: validação de todas as entradas antes de extrair (ZIP Slip, symlinks, CRC,
tamanho, número de entradas) · extensões fora da lista são ignoradas e reportadas · nada de pacotes
descarregados é executado · o destino é calculado só a partir de categorias permitidas + id validado ·
nunca se apaga nada fora de uma pasta de recurso gerida por esta biblioteca.

## Testes

`python3 -I -m unittest discover -s library/tests` (corre também no início de cada execução do workflow).
Os testes de download usam um servidor HTTP **local**; provam a lógica de segurança, não a integração com
as APIs externas.
