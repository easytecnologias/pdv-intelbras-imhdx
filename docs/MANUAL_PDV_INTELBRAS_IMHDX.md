# Manual de Integracao PDV Intelbras iMHDX

## Objetivo

Ativar a integracao de ponto de venda entre o sistema WRPDV/Sierra e o gravador
Intelbras iMHDX, permitindo que as vendas aparecam como texto sobreposto no
video e fiquem pesquisaveis em **Ponto de venda** no iMHDX.

## Arquitetura Correta

A integracao funciona em envio direto.

```text
PDV Linux -> iMHDX
```

Cada PDV executa uma ponte local que le os arquivos reais do frente de caixa e
envia UDP direto para o gravador:

```text
Destino: 192.168.24.227:38801
```

Cada PDV usa uma porta UDP propria como origem:

```text
PDV1: 52101
PDV2: 52102
PDV3: 52103
...
PDV12: 52112
```

No iMHDX, cada entrada POS precisa bater com:

```text
IP real do PDV
Porta UDP de origem do PDV
Canal de video correspondente
```

## Equipamentos

- Gravador Intelbras iMHDX: `192.168.24.227`
- Porta UDP do gravador para PDV/POS: `38801`

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

## Configuracao do iMHDX

Acesse o gravador:

```text
http://192.168.24.227
```

Entre em:

```text
Configuracoes > Ponto de venda
```

Configure a porta global UDP do POS/PDV:

```text
UDPPort: 38801
```

Configure cada PDV com:

```text
Ativo: Sim
Tipo de conexao: UDP
Protocolo: General
Codec/Conversao: UTF-8
IP origem: IP real do PDV
Porta origem: 52100 + numero do PDV
IP destino: 192.168.24.227
Porta destino: 38801
Canal vinculado: canal do PDV
Overlay/Sobreposicao: Ativo
Delimitador de linha: ^
Cor do texto: amarelo forte
Tamanho da fonte: 32
```

Exemplos:

```text
PDV1
Nome: PDV1
IP origem: 192.168.24.97
Porta origem: 52101
Canal vinculado: Canal 1

PDV2
Nome: PDV2
IP origem: 192.168.24.173
Porta origem: 52102
Canal vinculado: Canal 2
```

Importante: grave a configuracao POS pela tela web ou pelo endpoint web
`POS.modify`, porque ele preserva `ConnectType: UDP`. Gravar somente pelo SDK
generico pode mostrar campos corretos e ainda deixar o gravador sem aceitar o
UDP do POS.

## Ponte Local nos PDVs

Servico:

```text
pdv-intelbras-bridge.service
```

Arquivos instalados:

```text
/opt/pdv-intelbras-bridge/pdv_intelbras_bridge.py
/etc/pdv-intelbras-bridge.env
/etc/systemd/system/pdv-intelbras-bridge.service
```

Funcionamento:

```text
1. Monitora /home/rpdv/frente/Cm/EspiaoDDMMAA.NNN para itens em tempo real.
2. Se o PDV nao criar Espiao, usa /home/rpdv/frente/Cm/CMDDMMAA.NNN.
3. No arquivo CM, usa TVenda.GravaItem.cmdSql para pegar descricao/valor.
4. O envio pelo CM acontece quando aparece COMANDO ==> REGISTRA ITEM.
5. Tambem monitora /home/rpdv/frente/Log/logAAAAMMDD.NNN para inicio, NFC-e e fechamento.
6. Converte cupom, item, quantidade, valor e forma de pagamento em texto simples.
7. Envia por UDP direto para 192.168.24.227:38801.
```

Configuracao por PDV:

```text
PDV1
Estacao: 1
Log: /home/rpdv/frente/Log/logAAAAMMDD.001
Itens em tempo real: /home/rpdv/frente/Cm/EspiaoDDMMAA.001
Porta UDP origem: 52101
Destino: 192.168.24.227:38801

PDV2
Estacao: 2
Log: /home/rpdv/frente/Log/logAAAAMMDD.002
Itens em tempo real: /home/rpdv/frente/Cm/EspiaoDDMMAA.002
Fallback tempo real: /home/rpdv/frente/Cm/CMDDMMAA.002
Porta UDP origem: 52102
Destino: 192.168.24.227:38801

PDV3 a PDV12
Estacao: numero do PDV
Log: /home/rpdv/frente/Log/logAAAAMMDD.NNN
Itens em tempo real preferencial: /home/rpdv/frente/Cm/EspiaoDDMMAA.NNN
Fallback tempo real: /home/rpdv/frente/Cm/CMDDMMAA.NNN
Porta UDP origem: 52100 + numero do PDV
Destino: 192.168.24.227:38801
```

## Instalacao

Instalacao generica em um PDV:

```sh
sh /tmp/install_bridge_pdv.sh 1 52101 192.168.24.227 38801
```

Para PDV2:

```sh
sh /tmp/install_bridge_pdv.sh 2 52102 192.168.24.227 38801
```

O instalador tambem aceita omitir destino, pois o padrao de producao ja e o
iMHDX:

```sh
sh /tmp/install_bridge_pdv.sh 1 52101
```

## Operacao

Checar status:

```sh
systemctl status pdv-intelbras-bridge.service
journalctl -u pdv-intelbras-bridge.service -n 50 --no-pager
```

Reiniciar somente a ponte, sem reiniciar o computador e sem reiniciar o frente:

```sh
systemctl restart pdv-intelbras-bridge.service
systemctl is-active pdv-intelbras-bridge.service
```

## Correcao da Virada de Dia

Foi corrigido um problema em que a ponte ficava presa no arquivo do dia anterior
quando o arquivo antigo continuava existindo.

Exemplo do problema:

```text
Ponte monitorando Espiao220526.001
Frente criando Espiao240526.001
Ponte continuava presa no arquivo antigo
```

A versao corrigida reavalia o caminho esperado enquanto aguarda novas linhas e
reabre automaticamente o arquivo novo.

## Teste Real

1. Faca uma venda real no PDV.
2. Acesse o iMHDX.
3. Entre em **Ponto de venda > Buscar**.
4. Selecione o periodo correto.
5. Verifique o canal do PDV.

Resultado esperado:

```text
Video criado no horario da venda
Texto da venda sobreposto no video
Texto em amarelo forte
Evento pesquisavel em Ponto de venda
```

## Pontos Importantes

- O texto so aparece se o PDV enviar UDP para `192.168.24.227:38801`.
- A porta `38801` e a porta UDP do gravador.
- As portas `52101`, `52102` etc. sao as portas UDP de origem dos PDVs.
- O IP de origem no iMHDX deve ser o IP real do PDV.
- O codec/conversao do iMHDX precisa estar em `UTF-8`.
- O delimitador de linha usado pela ponte e `^`.
- Se o video for criado mas o texto nao aparecer, confira primeiro codec,
  overlay, delimitador, IP de origem, porta de origem e canal vinculado.

## Nota Historica

Durante o diagnostico inicial foi observado que o modulo CFTV nativo do frente
nao disparava UDP automaticamente para o iMHDX. Por isso foi criada a ponte
local em cada PDV. A arquitetura final de producao e direta: **PDV -> iMHDX**.
