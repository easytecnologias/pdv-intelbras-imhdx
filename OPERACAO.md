# Operacao Rapida

## Checar servico em um PDV

```sh
systemctl status pdv-intelbras-bridge.service
journalctl -u pdv-intelbras-bridge.service -n 50 --no-pager
```

## Reiniciar somente a ponte

Nao reinicia o computador nem o frente de caixa.

```sh
systemctl restart pdv-intelbras-bridge.service
systemctl is-active pdv-intelbras-bridge.service
```

## Arquivos monitorados

```text
/home/rpdv/frente/Cm/EspiaoDDMMAA.NNN
/home/rpdv/frente/Cm/CMDDMMAA.NNN
/home/rpdv/frente/Log/logAAAAMMDD.NNN
```

## Monitor de camera no PDV1

O monitor roda dentro do proprio PDV1 Linux pelo servico
`pdv-camera-auditor.service`, lendo o Espiao local e validando snapshot da
camera pela rede. Nao existem regras antifraude ativas neste servico.

```text
CSP = consulta de produto/preco
VIT = item registrado na venda
FIN = pagamento
```

Comandos no PDV1:

```sh
systemctl status pdv-camera-auditor.service
journalctl -u pdv-camera-auditor.service -n 80 --no-pager
tail -n 20 /var/log/pdv-camera-auditor/events.jsonl
```

## Agente de aprendizado no PDV1

Servico:

```sh
systemctl status pdv-learning-agent.service
journalctl -u pdv-learning-agent.service -n 80 --no-pager
```

Arquivos gerados:

```sh
find /var/log/pdv-learning-agent -type f | tail
tail -n 5 /var/log/pdv-learning-agent/$(date +%Y%m%d)/metadata.jsonl
```

Ele nao envia Telegram e nao toma decisao. Apenas coleta imagens com contexto
do Espiao para rotulagem humana e treino futuro.

## Assistente Telegram no PDV1

Servico:

```sh
systemctl status pdv-telegram-assistant.service
journalctl -u pdv-telegram-assistant.service -n 80 --no-pager
```

Comandos no grupo:

```text
/status
/data 24/05/2026
/caixa
/dinheiro
/cupom 216530
/buscar bombom
/foto 216657 arroz
/ajuda
```

Exemplos:

```text
/dinheiro
/buscar bombom
/cupom 216657
/foto 216657 bombom
bombom 216657
produto bombom do cupom 216657
```

O botao `Data` define a data ativa da consulta. Depois disso, `Caixa`,
`Dinheiro`, `Cupom` e `Buscar produto` usam essa data ate que outra data seja
selecionada.

No comando de foto, o assistente tenta primeiro baixar a gravacao do canal do
PDV no iMHDX, no horario do item, e extrair um quadro. A foto enviada no
Telegram ja sai com a legenda do PDV sobreposta no print.

## Instalacao online

Em um PDV Linux novo ou em manutencao, use:

```sh
curl -fsSL https://raw.githubusercontent.com/easytecnologias/pdv-intelbras-imhdx/main/install.sh -o install.sh
chmod +x install.sh
sudo ./install.sh
```

O instalador cria backup automatico em `/var/backups/pdv-intelbras-imhdx`,
instala os scripts em `/opt`, grava as configuracoes em `/etc` e ativa os tres
servicos principais.

## Causa corrigida em 2026-05-24

A ponte ficava presa no arquivo do dia anterior quando o arquivo antigo continuava existindo.
Exemplo: seguia lendo `Espiao220526.001` mesmo depois da criacao de `Espiao240526.001`.

A versao corrigida reavalia o caminho esperado enquanto aguarda novas linhas e reabre o
arquivo novo quando ele aparece.

## Fluxo de rede

```text
PDV -> iMHDX 192.168.24.227:38801
```

Nao ha equipamento intermediario no fluxo. Cada PDV deve sair com sua porta UDP
propria:

```text
PDV001: origem 52101 -> destino 192.168.24.227:38801
PDV002: origem 52102 -> destino 192.168.24.227:38801
...
PDV012: origem 52112 -> destino 192.168.24.227:38801
```

## PDVs

```text
PDV   IP              Porta UDP   Canal iMHDX
001   192.168.24.97   52101       1
002   192.168.24.173  52102       2
003   192.168.24.159  52103       3
004   192.168.24.160  52104       4
005   192.168.24.35   52105       5
006   192.168.24.86   52106       6
007   192.168.24.170  52107       7
008   192.168.24.186  52108       8
009   192.168.24.169  52109       9
010   192.168.24.84   52110       10
011   192.168.24.172  52111       11
012   192.168.24.91   52112       12
```
