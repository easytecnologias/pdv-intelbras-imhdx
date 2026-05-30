# PDV Intelbras iMHDX

Pacote de integracao entre WRPDV/Sierra e gravador Intelbras iMHDX.

## Conteudo

- `docs/MANUAL_PDV_INTELBRAS_IMHDX.md`: manual operacional completo.
- `scripts/pdv_intelbras_bridge.py`: servico Python instalado nos PDVs Linux.
- `scripts/pdv_camera_auditor_linux.py`: monitor local de camera e eventos do Espiao, sem regras antifraude.
- `scripts/pdv_learning_agent.py`: agente passivo para coletar imagens/contexto e preparar dataset do proximo modelo.
- `scripts/pdv_shadow_antitheft_agent.py`: agente antifurto em modo sombra, sem alertas, para gerar fila de revisao.
- `scripts/pdv_yolo_world_test.py`: teste offline com YOLO-World nas imagens coletadas.
- `scripts/pdv_telegram_assistant.py`: assistente Telegram para consultar caixa, dinheiro, cupom e produtos do PDV.
- `scripts/install_bridge_pdv.sh`: instalador generico da ponte no PDV.
- `services/*.service`: unidades systemd usadas nos PDVs.

## Fluxo

```text
PDV Linux -> iMHDX 192.168.24.227:38801
```

Cada PDV usa porta UDP propria:

```text
PDV1=52101, PDV2=52102, ..., PDV12=52112
```

## Status em 2026-05-24

- Acesso SSH confirmado nos PDVs 1 a 12 com usuario administrativo informado pelo operador.
- `pdv-intelbras-bridge.service` reiniciado nos PDVs 1 a 12 sem reiniciar os computadores.
- PDV6 estava inativo e voltou para `active`.
- `pdv_intelbras_bridge.py` atualizado nos 12 PDVs com correcao para trocar automaticamente de arquivo na virada de dia.
- Backup criado em cada PDV:
  ` /opt/pdv-intelbras-bridge/pdv_intelbras_bridge.py.bak_20260524_1043`

## Modo de producao

O sistema funciona em envio direto. Cada PDV envia UDP para o iMHDX usando sua
propria porta de origem (`52100 + numero do PDV`). No iMHDX, cada entrada POS
deve estar configurada com o IP real do PDV e a porta UDP correspondente.

## Observacao de seguranca

Este pacote nao deve conter senhas, tokens ou dumps completos com credenciais.
Antes de publicar no GitHub, revise qualquer arquivo novo adicionado fora desta pasta.

## Monitor de camera

O monitor do PDV roda no proprio Linux como servico
`pdv-camera-auditor.service`. Ele verifica a saude do snapshot da camera e
registra eventos basicos do arquivo local `EspiaoDDMMAA.001`.

## Agente de aprendizado

O agente `pdv-learning-agent.service` observa a camera e o Espiao sem gerar
alerta. Ele salva amostras em:

```text
/var/log/pdv-learning-agent/AAAAMMDD/images
/var/log/pdv-learning-agent/AAAAMMDD/metadata.jsonl
```

Cada imagem fica com contexto dos eventos recentes do PDV e status
`pending_human_review`, para posterior rotulagem e treino do novo modelo. O
agente tambem mantem:

```text
/var/log/pdv-learning-agent/knowledge/lessons.jsonl
/var/log/pdv-learning-agent/knowledge/future_antitheft_handoff.json
```

## Agente antifurto sombra

O agente `pdv-shadow-antitheft.service` consome o aprendizado ja coletado e
cria hipoteses para revisao humana. Ele nao acusa furto, nao envia Telegram e
nao interfere no PDV.

Saidas:

```text
/var/log/pdv-shadow-antitheft/AAAAMMDD/observations.jsonl
/var/log/pdv-shadow-antitheft/AAAAMMDD/review_queue.jsonl
/var/log/pdv-shadow-antitheft/summary.json
```

## Teste YOLO-World

Teste offline, sem Telegram e sem servico ativo:

```sh
python3 scripts/pdv_yolo_world_test.py \
  --input-dir /var/log/pdv-learning-agent/AAAAMMDD/images \
  --outdir /var/log/pdv-yolo-test \
  --limit 50
```

Saidas:

```text
/var/log/pdv-yolo-test/AAAAMMDD/results.jsonl
/var/log/pdv-yolo-test/AAAAMMDD/summary.json
/var/log/pdv-yolo-test/AAAAMMDD/annotated
```

Eventos relevantes do Espiao:

```text
ABRECUPOM  -> cupom aberto
CSP        -> consulta de produto/preco
VIT        -> item vendido
FIN        -> forma de pagamento
FECHACUPOM -> cupom fechado
```

## Assistente Telegram

O assistente roda separado do auditor, pelo servico
`pdv-telegram-assistant.service`. Ele responde comandos no grupo configurado no
PDV e le o Espiao local do dia.

Quando uma foto de produto ainda nao conhecido e enviada, o bot pergunta o que
aparece na imagem. A resposta humana alimenta:

```text
/var/log/pdv-product-learning/products.json
/var/log/pdv-product-learning/labels.jsonl
```

O botao `Ensinar produtos` procura automaticamente itens vendidos ainda
desconhecidos, envia uma foto por vez e espera a resposta humana antes de
mandar o proximo.

Comandos principais:

```text
/status
/data 24/05/2026
/caixa
/dinheiro
/cupom 216530
/buscar bombom
/foto 216657 arroz
arroz 216657
/ajuda
```

Pelos botoes do Telegram tambem e possivel escolher a data ativa, informar o
numero do cupom, pesquisar produto sem digitar o comando completo e pedir uma
foto do produto no cupom. Para foto, o bot tenta primeiro extrair o quadro da
gravacao do canal do PDV no iMHDX, exatamente no horario do item; se nao
conseguir, informa a falha. A imagem enviada vai com a legenda do PDV escrita
sobre o proprio print.

## Instalador online

No PDV Linux, rode como `root`:

```sh
curl -fsSL https://raw.githubusercontent.com/easytecnologias/pdv-intelbras-imhdx/main/install.sh -o install.sh
chmod +x install.sh
sudo ./install.sh
```

O instalador pergunta os dados do PDV, iMHDX, camera e Telegram.
Ele cria backup da instalacao anterior, copia os scripts para `/opt`, grava os
arquivos `.env` em `/etc`, instala os servicos `systemd`, reinicia tudo e mostra
o status final.

Servicos criados:

```text
pdv-intelbras-bridge.service
pdv-camera-auditor.service
pdv-learning-agent.service
pdv-shadow-antitheft.service
pdv-telegram-assistant.service
```
