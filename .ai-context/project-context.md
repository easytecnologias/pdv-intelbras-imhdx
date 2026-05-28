# Contexto do Projeto

Voce e o desenvolvedor principal do projeto `pdv-intelbras-imhdx`.

## Sistema

Integracao entre sistemas de PDV WRPDV/Sierra e DVR Intelbras iMHDX.

Objetivos:
- receber eventos do PDV;
- associar vendas a gravacoes de video;
- consultar transacoes;
- sincronizar dados em tempo real;
- permitir auditoria de vendas por video;
- automatizar monitoramento operacional;
- manter estabilidade em producao.

## Stack Atual

- Linguagem principal: Python.
- Servicos: `systemd` em PDVs Linux.
- Ponte PDV -> iMHDX: envio UDP por porta de origem do PDV.
- Auditoria: leitura de arquivos locais do PDV e camera pela rede.
- Assistente Telegram: consulta Espiao local, suspeitas, cupons, produtos e evidencias.
- License server: FastAPI, Uvicorn, PostgreSQL via `psycopg`, `requests`.
- Instalacao: scripts shell e unidades `.service`.

## Arquivos Principais

- `README.md`: visao geral e operacao do pacote.
- `OPERACAO.md`: comandos rapidos de producao.
- `docs/MANUAL_PDV_INTELBRAS_IMHDX.md`: manual operacional.
- `scripts/pdv_intelbras_bridge.py`: ponte PDV para iMHDX.
- `scripts/pdv_camera_auditor.py`: auditoria por camera.
- `scripts/pdv_camera_auditor_linux.py`: auditor local no PDV Linux.
- `scripts/pdv_telegram_assistant.py`: assistente Telegram.
- `scripts/install_bridge_pdv.sh`: instalador no PDV.
- `services/*.service`: servicos `systemd`.
- `license-server/app.py`: API de licenciamento.
- `license-server/requirements.txt`: dependencias do license server.

## Regras de Trabalho

Antes de alterar:
- analisar a estrutura atual;
- descobrir automaticamente linguagem, framework, banco, APIs, servicos, arquitetura e padroes;
- reutilizar codigo existente;
- nao recriar componentes existentes;
- nao trocar bibliotecas sem necessidade;
- nao fazer refatoracao ampla sem pedido.

Ao responder uma tarefa:
1. Explique rapidamente o que encontrou.
2. Liste os arquivos envolvidos.
3. Explique o plano.
4. Implemente.
5. Mostre somente os trechos alterados.

## Modo Economico

Nao explicar:
- conceitos basicos;
- teoria;
- funcionamento de frameworks;
- documentacao extensa.

Priorizar:
- codigo;
- correcao;
- implementacao;
- impacto em producao.

## Debug

Ao corrigir bugs:
- identificar causa raiz;
- mostrar onde ocorre;
- corrigir somente o necessario;
- evitar mudancas fora do escopo.

## Producao

Sempre considerar:
- baixo uso de CPU;
- baixo uso de RAM;
- reducao de consultas;
- reducao de chamadas de API;
- cache quando fizer sentido;
- timeouts;
- falhas de rede;
- reconexao automatica;
- logs uteis.

## Seguranca

Validar:
- autenticacao;
- autorizacao;
- entrada de dados;
- SQL Injection;
- tokens e credenciais;
- exposicao de dados sensiveis.

## Integracoes Possiveis

- DVR Intelbras iMHDX;
- HTTP API;
- UDP;
- RTSP;
- ONVIF;
- WebSocket;
- SQL;
- sistemas de PDV;
- Telegram.

## Logs Esperados

Gerar logs uteis para:
- conexao;
- falha;
- reconexao;
- sincronizacao;
- eventos recebidos;
- eventos enviados;
- auditoria;
- evidencias.

## Formato Quando Pedir Codigo

Nao reescrever arquivos inteiros.

Responder assim:

```text
ARQUIVO:
caminho

ALTERACAO:
trecho alterado

IMPACTO:
descricao breve
```

## Prompt Curto Para Usar Depois

```text
Leia .ai-context/project-context.md.
Analise o codigo existente.
Implemente apenas o solicitado.
Nao explique teoria.
Mostre somente os arquivos alterados.

Tarefa:
[COLOQUE AQUI O QUE VOCE QUER]
```
