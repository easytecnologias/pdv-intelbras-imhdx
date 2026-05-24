# Manual de Integracao PDV Intelbras iMHDX

## Objetivo

Ativar a integracao de ponto de venda entre o sistema WRPDV/Sierra e o gravador Intelbras iMHDX, permitindo que as vendas aparecam como texto sobreposto no video e fiquem pesquisaveis em **Ponto de venda** no iMHDX.

## Equipamentos

- Gravador Intelbras iMHDX: `192.168.24.227`
- Porta UDP do gravador para PDV: `38801`
- PDV1: `192.168.24.97`
- PDV2: `192.168.24.173`
- PDV3 a PDV12: conforme tabela no fim do manual

## Logica da comunicacao

A integracao do iMHDX depende de pacotes UDP chegando ao gravador.

Na solucao aplicada, o PDV le a venda real no proprio log do frente, envia para
o servidor Windows, e o servidor repassa para o iMHDX usando a porta de origem
correta de cada PDV.

```text
PDV -> Servidor Windows -> iMHDX
```

Cada PDV usa uma porta UDP propria de origem:

```text
PDV1: 52101
PDV2: 52102
PDV3: 52103
...
PDV12: 52112
```

No iMHDX, cada entrada de PDV precisa bater com:

```text
IP de origem do servidor Windows
Porta UDP de origem do PDV
Canal de video correspondente
```

Na unidade testada, foi confirmado em captura de rede que o PDV1 envia a venda
para o servidor Windows primeiro, usando TCP `9900` e `20050`. Durante a venda
real observada, nao saiu UDP automatico nem do PDV1 nem do servidor para o
iMHDX. Por isso, quando a venda nao aparece, o problema nao esta no codec do
iMHDX: o texto simplesmente nao esta sendo enviado pelo sistema.

## Configuracao do iMHDX

Acesse o gravador pelo navegador:

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

### PDV1

Configure a entrada PDV1 assim:

```text
Nome: PDV1
Ativo: Sim
Tipo de conexao: UDP
Protocolo: General
Codec/Conversao: UTF-8
IP origem: 192.168.24.174
Porta origem: 52101
IP destino: 192.168.24.227
Porta destino: 38801
Canal vinculado: Canal 1
Overlay/Sobreposicao: Ativo
Delimitador de linha: ^
Cor do texto: amarelo forte
Tamanho da fonte: 32
```

### PDV2

Configure a entrada PDV2 assim:

```text
Nome: PDV2
Ativo: Sim
Tipo de conexao: UDP
Protocolo: General
Codec/Conversao: UTF-8
IP origem: 192.168.24.174
Porta origem: 52102
IP destino: 192.168.24.227
Porta destino: 38801
Canal vinculado: Canal 2
Overlay/Sobreposicao: Ativo
Delimitador de linha: ^
Cor do texto: amarelo forte
Tamanho da fonte: 32
```

Importante: a configuracao do POS no iMHDX deve ser gravada pela tela web ou
pelo endpoint web `POS.modify`, porque ele preserva o campo `ConnectType: UDP`.
Gravar somente pelo SDK generico pode mostrar os campos corretos, mas deixar o
gravador sem aceitar a porta UDP do POS.

## Configuracao no sistema WRPDV/Sierra

Na tela de configuracao do PDV, habilite o CFTV Intelbras.

Para o PDV1:

```text
Utiliza CFTV Intelbras: S
IP CFTV Intelbras: 192.168.024.227
Porta CFTV Intelbras: 38801
Porta UDP CFTV Intelbras: 52101
```

Para o PDV2:

```text
Utiliza CFTV Intelbras: S
IP CFTV Intelbras: 192.168.024.227
Porta CFTV Intelbras: 38801
Porta UDP CFTV Intelbras: 52102
```

Depois de gravar as configuracoes:

```text
Enviar carga para os PDVs
Reiniciar o frente de caixa dos PDVs
```

## Configuracao local dos PDVs

No arquivo de configuracao local do frente de caixa, a unidade `106` deve conter os dados do CFTV Intelbras.

PDV1:

```text
192.168.024.227^38801^52101
```

PDV2:

```text
192.168.024.227^38801^52102
```

O IP deve ficar nesse formato com tres digitos no terceiro bloco:

```text
192.168.024.227
```

Evite formatos quebrados como:

```text
192.168.24 .227
192.168.2  .32
```

## Reinicio dos PDVs

Depois de alterar a configuracao e enviar carga, reinicie o frente de caixa dos PDVs.

O processo esperado em cada PDV deve ficar ativo:

```text
/home/rpdv/frente/erpdv.sh
./frente
```

## Teste real

1. Faca uma venda real no PDV1.
2. Faca uma venda real no PDV2.
3. Acesse o iMHDX.
4. Entre em **Ponto de venda > Buscar**.
5. Selecione o periodo **Hoje**.
6. Verifique:

```text
PDV1: Canal 1
PDV2: Canal 2
```

O resultado esperado e:

```text
Video criado no horario da venda
Texto da venda sobreposto no video
Texto em amarelo forte
Evento pesquisavel em Ponto de venda
```

## Pontos importantes

- O texto so aparece se algum equipamento enviar UDP para `192.168.24.227:38801`.
- No teste manual, texto enviado por UDP apareceu no iMHDX depois de ajustar codec/conversao para `UTF-8`.
- Confirmado no canal 1: mensagem manual enviada pelo servidor com origem UDP `52101` apareceu no iMHDX.
- Em venda real do PDV1, foi visto trafego do PDV1 para o servidor, mas nao foi visto UDP para o iMHDX.
- Se o sistema estiver configurado para o PDV enviar direto, o IP configurado no iMHDX deve ser o IP do PDV.
- Se o sistema estiver configurado para o servidor reenviar, o IP configurado no iMHDX deve ser o IP do servidor.
- A porta `38801` e a porta UDP do gravador.
- As portas `52101`, `52102` etc. sao as portas UDP de cada PDV.
- O codec/conversao do iMHDX precisa estar correto. Neste caso, o texto funcionou com `UTF-8`.
- Se o video for criado mas o texto nao aparecer, confira primeiro codec/conversao, overlay e delimitador.

## Diagnostico confirmado no PDV1

Durante venda real no PDV1:

```text
PDV1 192.168.24.97 -> Servidor 192.168.24.174 TCP 9900
PDV1 192.168.24.97 -> Servidor 192.168.24.174 TCP 20050
```

Nao foi visto:

```text
PDV1 192.168.24.97 -> iMHDX 192.168.24.227 UDP 38801
Servidor 192.168.24.174 -> iMHDX 192.168.24.227 UDP 38801
```

Ou seja: o PDV1 esta vendendo e comunicando com o servidor, mas o modulo CFTV
nao esta disparando o pacote UDP para o gravador.

## Ponte instalada nos PDVs

Como o modulo CFTV nativo do frente nao disparou UDP para o iMHDX, foi
instalada uma ponte local em cada PDV.

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
1. O servico monitora /home/rpdv/frente/Cm/EspiaoDDMMAA.NNN para itens em tempo real.
2. Cada linha VIT do Espiao e enviada assim que o produto passa no caixa.
3. Se o PDV nao criar Espiao, o servico usa /home/rpdv/frente/Cm/CMDDMMAA.NNN.
4. No arquivo CM, o servico usa TVenda.GravaItem.cmdSql para pegar descricao/valor.
5. O envio pelo CM so acontece quando aparece COMANDO ==> REGISTRA ITEM.
6. O servico tambem monitora /home/rpdv/frente/Log/logAAAAMMDD.NNN para inicio, NFC-e e fechamento.
7. Converte cupom, item, quantidade, valor e forma de pagamento em texto simples.
8. Envia por UDP para o servidor Windows 192.168.24.174.
9. O servidor repassa para o iMHDX em 192.168.24.227:38801.
```

Configuracao instalada:

```text
PDV1
Estacao: 1
Log: /home/rpdv/frente/Log/logAAAAMMDD.001
Itens em tempo real: /home/rpdv/frente/Cm/EspiaoDDMMAA.001
Porta UDP origem: 52101
Destino: 192.168.24.174:52101

PDV2
Estacao: 2
Log: /home/rpdv/frente/Log/logAAAAMMDD.002
Itens em tempo real: /home/rpdv/frente/Cm/EspiaoDDMMAA.002
Fallback tempo real: /home/rpdv/frente/Cm/CMDDMMAA.002
Porta UDP origem: 52102
Destino: 192.168.24.174:52102

PDV3 a PDV12
Estacao: numero do PDV
Log: /home/rpdv/frente/Log/logAAAAMMDD.NNN
Itens em tempo real preferencial: /home/rpdv/frente/Cm/EspiaoDDMMAA.NNN
Fallback tempo real: /home/rpdv/frente/Cm/CMDDMMAA.NNN
Porta UDP origem: 52100 + numero do PDV
Destino: 192.168.24.174:mesma porta UDP do PDV
```

Comandos uteis:

```text
systemctl status pdv-intelbras-bridge.service
journalctl -u pdv-intelbras-bridge.service -n 50 --no-pager
systemctl restart pdv-intelbras-bridge.service
```

## Relay instalado no servidor Windows

Como o iMHDX foi configurado para receber os dois PDVs vindo do servidor, o
servidor Windows faz o repasse UDP.

Servidor:

```text
192.168.24.174
```

Arquivos:

```text
C:\PDVIntelbrasRelay\pdv-intelbras-relay.ps1
C:\PDVIntelbrasRelay\relay.log
```

Tarefa agendada:

```text
PDVIntelbrasRelay
```

Portas escutadas:

```text
52101 a 52112 -> repassa para 192.168.24.227:38801
```

Comandos uteis no servidor:

```text
Get-ScheduledTask -TaskName PDVIntelbrasRelay
Get-Content C:\PDVIntelbrasRelay\relay.log -Tail 50
netstat -ano -p udp | findstr "5210 5211"
```

## Configuracao final aplicada

```text
iMHDX
IP: 192.168.24.227
UDP POS: 38801

Servidor Windows
IP: 192.168.24.174
Relay UDP atual: 52101 e 52102

iMHDX ativo atualmente
PDV1: 192.168.24.174:52101 -> canal 1
PDV2: 192.168.24.174:52102 -> canal 2

iMHDX desativado atualmente
PDV3 a PDV12: entradas POS desabilitadas para validacao um por um
```

## Tabela dos PDVs Linux

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

## Validacao final

Depois de gravar o POS pelo endpoint web `POS.modify`, o iMHDX retornou eventos
no proprio **Ponto de venda > Buscar** entre `15:09` e `15:12` do dia
`22/05/2026`, nos canais 1 e 2. Isso confirma que o gravador passou a aceitar e
indexar o POS em UDP.
