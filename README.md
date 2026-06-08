# PDV Intelbras iMHDX

Integracao entre WRPDV/Sierra, PDVs Linux e gravador Intelbras iMHDX.

O objetivo principal do projeto e fazer as vendas do PDV aparecerem como texto
sobreposto no video do iMHDX e ficarem pesquisaveis em **Ponto de venda**.

## Estado Real do Projeto

Status em `08/06/2026`:

- Em producao: ponte local `PDV Linux -> iMHDX`, sem servidor intermediario.
- Em producao: assistente Telegram para consultar caixa, cupom, produto e foto
  do item pela gravacao do iMHDX.
- Removido do fluxo ativo: antifurto por movimento/camera, porque gerava falso
  positivo com mao, braco, cotovelo, maquininha, sacola e movimentos normais.
- Em estudo: auditoria visual de scanner, baseada em evidencias reais
  `item do Espiao + horario + imagem do iMHDX`.

Este repositorio ainda contem arquivos antigos/experimentais de camera,
aprendizado e antifurto. Eles ficam como historico de pesquisa e **nao devem ser
tratados como producao** sem nova validacao.

## Arquitetura de Producao

```text
WRPDV/Sierra
  -> /home/rpdv/frente/Cm/EspiaoDDMMAA.NNN
  -> pdv-intelbras-bridge.service
  -> UDP direto para iMHDX 192.168.24.227:38801
```

Cada PDV envia com porta UDP propria:

```text
PDV1:  52101
PDV2:  52102
PDV3:  52103
...
PDV12: 52112
```

No iMHDX, cada entrada POS deve bater com:

```text
IP real do PDV
Porta UDP de origem do PDV
Canal de video correspondente
```

Nao ha dependencia do servidor Windows para o envio POS em producao.

## Componentes Ativos

### Ponte PDV -> iMHDX

Arquivo:

```text
scripts/pdv_intelbras_bridge.py
```

Servico no PDV:

```text
pdv-intelbras-bridge.service
```

O que faz:

- le o arquivo `EspiaoDDMMAA.NNN` do frente de caixa;
- usa fallback no arquivo `CMDDMMAA.NNN` quando necessario;
- identifica item vendido, abertura/fechamento de cupom, NFC-e e pagamento;
- converte os dados em texto simples com delimitador `^`;
- envia UDP para o iMHDX pela porta de origem do PDV.

### Assistente Telegram

Arquivo:

```text
scripts/pdv_telegram_assistant.py
```

Servico no PDV:

```text
pdv-telegram-assistant.service
```

O que faz:

- mostra status do caixa;
- consulta resumo do dia;
- busca cupom;
- busca produto;
- lista produto mais vendido;
- busca foto de item na gravacao do iMHDX;
- permite escolher data ativa pelo Telegram.

Menu atual:

```text
Status              Caixa
Cupom               Ultimo cupom
Buscar produto      Foto produto
Produto mais vendido Data
```

## Componentes Legados ou Experimentais

Os itens abaixo nao representam o fluxo atual de producao:

```text
scripts/pdv_antitheft_agent.py
scripts/install_antitheft.sh
scripts/pdv_learning_agent.py
scripts/pdv_shadow_antitheft_agent.py
scripts/pdv_yolo_world_test.py
services/pdv-antitheft-agent.service
services/pdv-learning-agent.service
services/pdv-shadow-antitheft.service
services/pdv-auto-trainer.service
```

Eles devem ser considerados material de laboratorio. O caminho novo para IA nao
e detectar "movimento suspeito"; e responder uma pergunta objetiva:

```text
O que foi passado pelo scanner corresponde ao que foi registrado no PDV?
```

## Auditoria Visual: Proximo Caminho Correto

Antes de ligar qualquer IA em producao, o teste correto e montar um conjunto de
evidencias reais:

```text
cupom
horario do item
produto registrado
quantidade registrada
valor
imagem do iMHDX no momento do item
recorte da area do scanner
```

Cada caso deve ser classificado como:

```text
CONFERE
NAO_CONFERE
INCONCLUSIVO
```

So depois disso faz sentido testar Gemini, OpenAI, Groq ou qualquer outro modelo
de visao. A IA deve confirmar compatibilidade visual, nao acusar funcionario.

## Configuracao do iMHDX

No gravador:

```text
Configuracoes > Ponto de venda
UDPPort: 38801
```

Para cada PDV:

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
```

Exemplo PDV1:

```text
IP origem: 192.168.24.97
Porta origem: 52101
Canal: 1
```

Exemplo PDV2:

```text
IP origem: 192.168.24.173
Porta origem: 52102
Canal: 2
```

## Instalacao

Instalacao limpa da ponte em um PDV:

```sh
sh /tmp/install_bridge_pdv.sh 1 52101 192.168.24.227 38801
```

Tambem existe um instalador online:

```sh
curl -fsSL https://raw.githubusercontent.com/easytecnologias/pdv-intelbras-imhdx/main/install.sh -o install.sh
chmod +x install.sh
sudo ./install.sh
```

O `install.sh` instala apenas os componentes de producao:

```text
pdv-intelbras-bridge.service
pdv-telegram-assistant.service
```

Durante a instalacao ele tambem desativa servicos legados de camera,
aprendizado e antifurto, se existirem no PDV.

## Operacao

Checar ponte:

```sh
systemctl status pdv-intelbras-bridge.service
journalctl -u pdv-intelbras-bridge.service -n 80 --no-pager
```

Reiniciar ponte sem reiniciar o computador:

```sh
systemctl restart pdv-intelbras-bridge.service
systemctl is-active pdv-intelbras-bridge.service
```

Checar Telegram:

```sh
systemctl status pdv-telegram-assistant.service
journalctl -u pdv-telegram-assistant.service -n 80 --no-pager
```

## Comandos Uteis do Telegram

```text
/status
/data 24/05/2026
/caixa
/cupom 216530
/buscar bombom
/foto 216657 arroz
/ajuda
```

Tambem e possivel usar os botoes do menu fixo.

## Tabela de PDVs

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

## Documentacao

Manual operacional principal:

```text
docs/MANUAL_PDV_INTELBRAS_IMHDX.md
```

Documentos com `ANTIFURTO`, `GERENTE`, `GROQ`, `GEMINI` ou similares sao
historico de testes e nao devem ser usados como promessa de producao.

## Seguranca

Nao publicar no GitHub:

```text
senhas
tokens Telegram
chaves Gemini/Groq/OpenAI
credenciais do iMHDX
credenciais de camera
prints contendo dados sensiveis
```

Arquivos `.env` reais devem ficar apenas no PDV ou no ambiente de producao.
