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

## Auditoria de camera no PDV1

O prototipo de auditoria compara movimento na area do scanner com eventos reais
do PDV. Em producao/teste correto, ele roda dentro do proprio PDV1 Linux pelo
servico `pdv-camera-auditor.service`, lendo o Espiao local e a camera pela rede.
Para evitar falso positivo quando o operador demora com o produto parado porque
esta consultando o sistema, o auditor tambem le eventos `CSP` no Espiao.

```text
CSP = consulta de produto/preco
VIT = item registrado na venda
FIN = pagamento
```

Regra pratica:

```text
movimento + VIT dentro da janela       -> casou
movimento + CSP recente                -> aguarda consulta
movimento + pagamento/fim              -> ignora
movimento sem VIT depois da espera     -> suspeita
```

Comandos no PDV1:

```sh
systemctl status pdv-camera-auditor.service
journalctl -u pdv-camera-auditor.service -n 80 --no-pager
tail -n 20 /var/log/pdv-camera-auditor/events.jsonl
find /var/log/pdv-camera-auditor/evidencias -type f | tail
```

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
