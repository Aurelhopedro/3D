# Addons Blender

Esta pasta contém addons Blender geridos pelo pipeline.

## Addons integrados

- **MPFB2** — instalado dinamicamente pelos workflows de personagens (`mpfb-*.yml`). Não está armazenado aqui — é descarregado do repositório oficial em cada execução.

## Como adicionar um addon

1. Verifica a licença do addon em `config/libraries.json`.
2. Confirma compatibilidade com a versão do Blender em `config/blender.json`.
3. Cria um workflow dedicado ou adapta um existente para instalar o addon antes de executar scripts.
4. Nunca executa código de addons externos com segredos disponíveis no ambiente.

## Nota de segurança

Addons de terceiros são tratados como código não confiável. A instalação é sempre feita em jobs sem acesso a `secrets.GITHUB_TOKEN` com permissões de escrita, exceto quando estritamente necessário.
