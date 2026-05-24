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

## Causa corrigida em 2026-05-24

A ponte ficava presa no arquivo do dia anterior quando o arquivo antigo continuava existindo.
Exemplo: seguia lendo `Espiao220526.001` mesmo depois da criacao de `Espiao240526.001`.

A versao corrigida reavalia o caminho esperado enquanto aguarda novas linhas e reabre o
arquivo novo quando ele aparece.

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
