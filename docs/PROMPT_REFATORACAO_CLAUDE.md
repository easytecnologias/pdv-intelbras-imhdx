# Prompt de revisao e refatoracao para Claude

Copie a partir da linha `INICIO DO PROMPT` e envie ao Claude junto com o
repositorio. Nao acrescente senhas, tokens ou chaves de API ao texto.

---

## INICIO DO PROMPT

Voce e o engenheiro senior responsavel por revisar, documentar e refatorar o
projeto `pdv-intelbras-imhdx`. Trabalhe de forma conservadora: primeiro entenda
o comportamento real, depois apresente evidencias e somente entao altere o
codigo.

### Objetivo principal

O sistema integra o WRPDV/Sierra dos caixas Linux com um gravador Intelbras
iMHDX. Ele possui tres funcoes:

1. Ler os eventos reais do PDV e enviar o texto das vendas diretamente ao
   iMHDX por UDP, para sobreposicao e pesquisa POS.
2. Oferecer consultas operacionais por Telegram: caixa, cupom, produto, foto,
   produto mais vendido, data e video da ocorrencia.
3. Auditar visualmente eventos do scanner, comparando o registro do PDV com
   imagens do proprio iMHDX. A decisao visual atual usa Groq/Llama 4 Scout.

O projeto nao deve acusar pessoas. Os resultados tecnicos permitidos sao
`CONFERE`, `NAO_CONFERE`, `INCONCLUSIVO` e resultados de filtros locais.

### Estado operacional em 10/06/2026

- A ponte `PDV Linux -> iMHDX` esta em producao e nao depende de servidor
  Windows.
- O assistente Telegram esta em producao no PDV1.
- A auditoria visual autonoma esta em teste no PDV1.
- A captura para alerta imediato usa o RTSP do proprio iMHDX, canal 1.
- A gravacao POS do iMHDX continua sendo usada para o video de 20 segundos e
  revisao posterior.
- O worker mantem um buffer de aproximadamente dois frames por segundo e cerca
  de 30 segundos.
- A triagem local mede movimento em tres quadros: antes, momento do registro e
  depois.
- Movimento visual suficiente libera itens comuns sem chamar API.
- Cena com pouco movimento vira candidata a "registro sem passagem visual".
- Somente uma confirmacao visual forte deve gerar alerta no Telegram.
- Itens de risco, alto valor ou associados a consulta podem passar por
  comparacao visual de produto.
- O antigo detector generico de movimento/camera gerava falsos positivos com
  maos, bracos, cotovelos, sacolas e maquininhas. Ele e legado, nao producao.

### Arquitetura real

Fluxo POS:

```text
WRPDV/Sierra
  -> /home/rpdv/frente/Cm/EspiaoDDMMAA.NNN
  -> fallback /home/rpdv/frente/Cm/CMDDMMAA.NNN
  -> pdv-intelbras-bridge.service
  -> UDP direto para o iMHDX 192.168.24.227:38801
```

Fluxo de auditoria:

```text
Espiao/CM
  -> pdv-visual-alert-worker.service
  -> buffer RTSP do proprio iMHDX
  -> triagem local de movimento
  -> Groq somente quando necessario
  -> alerta Telegram
  -> botao para video de 20 segundos da gravacao POS do iMHDX
  -> salvar ocorrencia ou ignorar/excluir do Telegram
```

Nao reintroduza servidor Windows, relay UDP ou captura direta da camera IP como
dependencia de producao.

### Equipamentos e rede

```text
iMHDX:       192.168.24.227
UDP POS:     38801
RTSP iMHDX:  porta 554

PDV   IP              UDP origem   Canal iMHDX
001   192.168.24.97   52101        1
002   192.168.24.173  52102        2
003   192.168.24.159  52103        3
004   192.168.24.160  52104        4
005   192.168.24.35   52105        5
006   192.168.24.86   52106        6
007   192.168.24.170  52107        7
008   192.168.24.186  52108        8
009   192.168.24.169  52109        9
010   192.168.24.84   52110        10
011   192.168.24.172  52111        11
012   192.168.24.91   52112        12
```

Existe uma camera em `10.10.10.20`, mas ela nao deve ser usada pelo fluxo
atual. A fonte visual oficial e o iMHDX.

### Acessos autorizados

Repositorio:

```text
https://github.com/easytecnologias/pdv-intelbras-imhdx.git
```

PDV1:

```text
SSH: 192.168.24.97
Usuario operacional: rpdv
```

iMHDX:

```text
HTTP: http://192.168.24.227
Usuario administrativo cadastrado no ambiente local
```

Nao existem senhas neste prompt. Obtenha os segredos somente no ambiente
autorizado e apenas quando forem indispensaveis:

```text
/etc/pdv-telegram-assistant.env
/etc/pdv-intelbras-bridge.env
/etc/pdv-intelbras-imhdx/.env
variaveis Environment/EnvironmentFile das unidades systemd
```

Regras obrigatorias para segredos:

- nunca mostre o valor de uma senha, token ou chave em respostas;
- nunca inclua segredo em commit, diff, log, teste, URL ou documentacao;
- nunca embuta chave de API no codigo;
- ao diagnosticar, mostre apenas o nome da variavel e se esta definida;
- preserve permissoes restritas dos arquivos `.env`;
- se encontrar segredo versionado, informe o caminho e recomende rotacao sem
  repetir o valor;
- nao altere credenciais sem autorizacao explicita.

Variaveis esperadas incluem, entre outras:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
IMHDX_HOST
IMHDX_USER
IMHDX_PASS
IMHDX_CHANNEL
GROQ_API_KEY
```

### Caminhos importantes no PDV1

```text
/home/rpdv/frente/Cm/
/home/rpdv/frente/Log/

/opt/pdv-intelbras-bridge/
/opt/pdv-telegram-assistant/
/opt/pdv-visual-auditor/

/var/lib/pdv-visual-auditor/
/var/lib/pdv-visual-auditor/live_frames/

/etc/systemd/system/
/etc/pdv-telegram-assistant.env
/etc/pdv-intelbras-bridge.env
```

Backup anterior a implantacao da deteccao de registro sem passagem:

```text
/opt/pdv-visual-auditor/backups/20260609_phantom/
/opt/pdv-telegram-assistant/backups/20260609_phantom/
```

### Servicos ativos importantes

```text
pdv-intelbras-bridge.service
pdv-telegram-assistant.service
pdv-visual-alert-worker.service
```

Comandos de observacao:

```sh
systemctl status pdv-intelbras-bridge.service
systemctl status pdv-telegram-assistant.service
systemctl status pdv-visual-alert-worker.service

journalctl -u pdv-intelbras-bridge.service -n 100 --no-pager
journalctl -u pdv-telegram-assistant.service -n 100 --no-pager
journalctl -u pdv-visual-alert-worker.service -n 150 --no-pager
```

Nao reinicie o computador. Quando estritamente necessario, reinicie apenas o
servico alterado.

### Arquivos centrais do repositorio

Producao ou teste operacional:

```text
scripts/pdv_intelbras_bridge.py
scripts/pdv_telegram_assistant.py
scripts/pdv_visual_alert_worker.py
scripts/pdv_visual_auditor.py

services/pdv-intelbras-bridge.service
services/pdv-telegram-assistant-pdv1.service
services/pdv-visual-alert-worker.service

install.sh
scripts/install_bridge_pdv.sh
README.md
docs/MANUAL_PDV_INTELBRAS_IMHDX.md
```

Legado ou laboratorio, a ser classificado antes de remover:

```text
scripts/install_antitheft.sh
scripts/pdv_antitheft_agent.py
scripts/pdv_auto_trainer.py
scripts/pdv_camera_auditor_linux.py
scripts/pdv_dataset_builder.py
scripts/pdv_learning_agent.py
scripts/pdv_product_learning_cleanup.py
scripts/pdv_product_learning_migrate_categories.py
scripts/pdv_shadow_antitheft_agent.py
scripts/pdv_yolo_trainer.py
scripts/pdv_yolo_world_test.py

services/pdv-antitheft-agent.service
services/pdv-auto-trainer.service
services/pdv-auto-trainer.timer
services/pdv-camera-auditor-pdv1.service
services/pdv-learning-agent-pdv1.service
services/pdv-shadow-antitheft-pdv1.service
```

Documentos com `ANTIFURTO`, `GERENTE`, `GEMINI`, `GROQ` ou experimentos
semelhantes podem estar desatualizados. Use-os como historico, nunca como fonte
unica da verdade.

### Comportamentos que precisam ser preservados

Ponte:

- leitura incremental dos arquivos do frente;
- suporte a `Espiao` e fallback `CM`;
- repeticao real de itens;
- quantidade fracionada para produtos pesados;
- inicio, itens, cancelamentos, NFC-e, fechamento e pagamento;
- texto UTF-8 delimitado por `^`;
- porta UDP de origem exclusiva por PDV;
- envio direto ao iMHDX.

Telegram:

- menu persistente;
- mensagens curtas, alinhadas e legiveis;
- consultas por data;
- cupom e ultimo cupom;
- busca de produto paginada;
- produto mais vendido;
- foto de produto;
- auditoria manual;
- botao para video do evento;
- botoes para salvar ocorrencia e ignorar/excluir;
- formatacao monetaria brasileira.

Auditoria:

- compreender quantidade inteira e fracionada;
- nao interpretar `0.972` como zero;
- diferenciar quantidade da linha, valor unitario e total;
- considerar cancelamentos antes de alertar;
- considerar consultas de produto seguidas de venda diferente;
- identificar registro sem passagem visual;
- usar tres frames para reduzir erro de sincronismo;
- manter `INCONCLUSIVO` quando a evidencia for insuficiente;
- nao gerar acusacao pessoal;
- limitar chamadas de API e registrar quando a API nao foi chamada;
- preservar o video posterior vindo da gravacao POS.

### Situacao do buffer ao vivo

O worker atual abre:

```text
rtsp://<usuario>:<senha>@192.168.24.227:554/cam/realmonitor?channel=1&subtype=0
```

A URL acima e apenas o formato. Nunca imprima a URL resolvida, porque ela
contem credenciais.

Configuracao atual:

```text
VISUAL_IMHDX_LIVE_BUFFER_ENABLED=1
VISUAL_IMHDX_LIVE_BUFFER_FPS=2
VISUAL_ALERT_DELAY_SECONDS=6
VISUAL_ALERT_IMAGE_RETRY_SECONDS=8
VISUAL_ALERT_MAX_IMAGE_ATTEMPTS=6
VISUAL_PHANTOM_ENABLED=1
VISUAL_PHANTOM_MOTION_MEAN_MIN=4.5
VISUAL_PHANTOM_CHANGED_PIXELS_MIN=7.0
```

Revise especialmente:

- ciclo de vida do subprocesso FFmpeg;
- encerramento do filho no stop/restart;
- limpeza dos frames temporarios;
- crescimento de disco;
- consumo de CPU;
- recuperacao de queda do RTSP;
- selecao temporal correta dos frames;
- concorrencia ao ler arquivos ainda em escrita;
- duplicidade de eventos;
- atraso entre `Espiao`, imagem ao vivo, gravacao e Telegram.

### Problemas tecnicos conhecidos

- `pdv_telegram_assistant.py` cresceu demais e concentra parsing, Telegram,
  iMHDX, video, imagem, estado e regras de negocio.
- Existem duplicacoes e codigo experimental misturado ao repositorio.
- Ha arquivos modificados e nao rastreados; nao assuma que podem ser apagados.
- O README pode ficar atrasado em relacao ao PDV implantado.
- A disponibilidade da gravacao via `loadfile.cgi` pode atrasar minutos.
- O buffer RTSP foi criado para decisao imediata; a gravacao e evidencial.
- Modelos visuais podem confundir embalagem, marca, unidade e peso.
- APIs gratuitas possuem limites e nao podem ser chamadas para todo item.
- Testes antigos podem conter imagens e scripts descartaveis.

### Sua missao

Execute a revisao em fases.

#### Fase 1: inventario sem alterar

1. Leia `README.md`, o manual operacional, scripts e unidades systemd.
2. Execute `git status`, `git diff --stat` e `git diff` sem reverter nada.
3. Mapeie:
   - fluxo de dados;
   - processos;
   - arquivos de estado;
   - dependencias;
   - configuracoes;
   - chamadas de rede;
   - pontos de entrada;
   - codigo ativo, legado, duplicado e morto.
4. Compare o repositorio com o que esta implantado no PDV1.
5. Nao copie arquivos `.env` nem apresente seus valores.

#### Fase 2: relatorio de achados

Entregue achados ordenados por severidade, com arquivo e linha:

- bugs e regressões;
- risco de perda de evento;
- risco de falso alerta;
- vazamento de segredo;
- subprocessos sem encerramento;
- crescimento de disco/memoria;
- condicoes de corrida;
- parsing incorreto;
- duplicacao;
- codigo morto;
- dependencias desnecessarias;
- documentacao divergente;
- ausencia de testes.

Nao chame codigo de "lixo" sem evidencia. Classifique cada item:

```text
MANTER
REFATORAR
DEPRECAR
REMOVER DEPOIS DE VALIDACAO
```

#### Fase 3: plano de refatoracao

Proponha modulos pequenos com responsabilidades claras. Uma divisao esperada,
que deve ser confirmada pelo codigo real, e:

```text
pdv/parsers.py
pdv/models.py
pdv/events.py
imhdx/pos_sender.py
imhdx/live_buffer.py
imhdx/recording.py
audit/rules.py
audit/visual_provider.py
audit/worker.py
telegram/handlers.py
telegram/formatters.py
telegram/video.py
storage/state.py
config.py
```

Evite reescrita total. Migre por etapas mantendo compatibilidade com os
servicos existentes.

#### Fase 4: implementacao segura

- crie testes antes ou junto das mudancas;
- preserve comportamento observavel;
- use configuracao por ambiente, nunca segredo hardcoded;
- mantenha compatibilidade com a versao Python real do PDV;
- adicione type hints onde ajudarem, sem refatoracao cosmetica excessiva;
- use logging estruturado sem dados sensiveis;
- limite filas, arquivos temporarios e subprocessos;
- trate encerramento por `SIGTERM`;
- adicione retry com backoff onde fizer sentido;
- nao exclua legado na primeira etapa;
- nao altere rede, iMHDX ou PDVs sem autorizacao.

#### Fase 5: validacao

Validacoes minimas:

```text
parsing de VIT repetido
produto pesado/fracionado
cancelamento
consulta seguida de item igual
consulta seguida de item diferente
item comum com movimento
registro sem passagem
imagem insuficiente
queda do RTSP
gravacao atrasada
limite da API
reinicio do worker
limpeza de frames
menu e callbacks Telegram
video de 20 segundos
```

Antes de implantar:

1. mostre o diff;
2. execute testes;
3. descreva rollback;
4. crie backup remoto;
5. solicite autorizacao para reiniciar apenas os servicos afetados.

### Formato da primeira resposta

Nao comece refatorando. Sua primeira resposta deve conter:

1. resumo do entendimento;
2. arquitetura reconstruida;
3. lista de componentes ativos;
4. lista de componentes provavelmente legados;
5. riscos encontrados, com severidade;
6. perguntas realmente bloqueadoras;
7. plano incremental de refatoracao;
8. comandos somente de leitura que pretende executar.

## FIM DO PROMPT

