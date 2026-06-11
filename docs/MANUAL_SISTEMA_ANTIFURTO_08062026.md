# Manual do Sistema Antifurto PDV — Estado atual
**Data:** 08/06/2026 | **PDV em teste:** 001 (192.168.24.97)

---

## O que o sistema é

Um sistema de monitoramento de caixa de supermercado que combina três fontes de informação:

1. **Câmera IP** (Intelbras, 10.10.10.20) — vê o que acontece fisicamente no caixa
2. **Espião PDV** (arquivo local no Linux do PDV) — registra cada produto escaneado em tempo real
3. **IA de visão** (Groq / Llama 4 Scout) — analisa a imagem e decide se há suspeita

O sistema roda como serviço Linux (`pdv-antitheft-agent.service`) direto no PDV1 e envia alertas pelo Telegram.

---

## Componentes instalados no PDV1

| Serviço | Arquivo | Status |
|---|---|---|
| Agente antifurto | `/opt/pdv-antitheft/pdv_antitheft_agent.py` | ✅ rodando |
| Assistente Telegram | `/opt/pdv-telegram-assistant/pdv_telegram_assistant.py` | ✅ rodando |
| Env do agente | `/etc/pdv-antitheft-agent.env` | ✅ configurado |
| Env do assistente | `/etc/pdv-telegram-assistant.env` | ✅ configurado |

---

## O que o agente antifurto faz — fluxo atual

```
A cada 20 segundos:

1. Tira um snapshot da câmera (HTTP Digest Auth)

2. Lê o arquivo Espião do dia:
   /home/rpdv/frente/Cm/EspiaoDDMMAA.001

3. Verifica se houve VIT (item escaneado) nos últimos 25 segundos

   SE HOUVE VIT → loga "venda normal: [produto]" e dorme
   (Groq NÃO é chamado — economia de cota)

   SE NÃO HOUVE VIT → chama a IA com a imagem

4. IA analisa a imagem e responde JSON:
   {"suspeito": true/false, "tipo": "tipo_do_furto", "motivo": "..."}

5. SE suspeito = true:
   → Salva imagem em /var/log/pdv-antitheft/alerts/YYYYMMDD/
   → Salva log em activity.jsonl com tipo_furto e motivo
   → Envia alerta no Telegram com foto e 3 botões
```

---

## O alerta no Telegram

Quando a IA detecta suspeita, o supervisor recebe:

```
⚠️ Caixa 001 — 14:32:10
Tipo: produto_escondido
Operador empurrou item para baixo do balcão sem registrar.
PDV: SEM REGISTRO

[ ✅ Fraude real ]  [ ❌ Falso positivo ]  [ 📹 Ver vídeo ]
```

**Botão ✅ Fraude real** → grava em `confirmed.jsonl` com tipo de furto

**Botão ❌ Falso positivo** → grava em `dismissed.jsonl` com tipo de furto

**Botão 📹 Ver vídeo** → baixa clipe de 25 segundos do iMHDX, converte para MP4 e envia no Telegram

---

## Tipos de furto que a IA detecta

### Pelo operador de caixa
| Código | O que é |
|---|---|
| `passada_fantasma` | Produto passa sem ser lido no scanner |
| `produto_escondido` | Item empurrado para baixo do balcão antes de registrar |
| `troca_produto` | Item diferente do registrado colocado na sacola |
| `conluio_operador` | Produto visível sendo ignorado pelo operador |

### Pelo cliente
| Código | O que é |
|---|---|
| `bolso_bolsa_cliente` | Produto colocado no bolso/bolsa sem scanner |
| `embaixo_carrinho` | Item na prateleira inferior do carrinho não retirado |
| `crianca_produto` | Produto dado para criança no colo sem registrar |
| `consumo_loja` | Cliente consumindo produto antes de registrar |
| `sacola_propria` | Item em sacola própria do cliente sem scanner |
| `embalagem_dentro_embalagem` | Produto menor escondido dentro de maior |

---

## Onde ficam os logs

```
/var/log/pdv-antitheft/alerts/
  └── YYYYMMDD/
        ├── activity.jsonl        ← registro de cada análise
        └── alert_001_*.jpg       ← imagem salva de cada suspeita

/var/log/pdv-antitheft/feedback/
  ├── confirmed.jsonl             ← alertas confirmados como fraude real
  └── dismissed.jsonl            ← alertas descartados como falso positivo
```

### Exemplo de registro em activity.jsonl
```json
{
  "time": "2026-06-08 14:32:10",
  "acao": "suspeita detectada",
  "vit": "nenhum",
  "tipo_furto": "produto_escondido",
  "motivo": "operador empurrou item para baixo do balcão sem registrar"
}
```

---

## O assistente Telegram (bot separado)

Além do agente antifurto, há um segundo serviço que responde comandos:

| Comando / Botão | O que faz |
|---|---|
| `Status` | Resumo do dia: cupons fechados, itens, total vendido |
| `Caixa` | Detalhe de vendas e formas de pagamento |
| `Cupom` | Digite o número do cupom para ver os itens |
| `Último cupom` | Mostra o último cupom com movimento |
| `Buscar produto` | Pesquisa um produto no Espião do dia |
| `Foto produto` | Busca a foto do item no iMHDX (ex: `216657 arroz`) |
| `Produto mais vendido` | Ranking dos 10 produtos mais vendidos |
| `🧠 IA — O que aprendi` | Mostra estatísticas do modelo de aprendizado |
| `/data 08/06/2026` | Muda a data da consulta |

---

## Configuração — variáveis de ambiente

**Agente antifurto** (`/etc/pdv-antitheft-agent.env`):
```
CAMERA_HOST=10.10.10.20
CAMERA_USER=admin
CAMERA_PASS=ab112233
PDV_STATION=001
PDV_BASE_DIR=/home/rpdv/frente
GEMINI_API_KEY=...        ← API de visão (ATUAL: Gemini — NÃO FUNCIONA)
GROQ_API_KEY=...          ← API de visão (FUNCIONA — precisa ativar)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
IMHDX_HOST=192.168.24.227
IMHDX_USER=admin
IMHDX_PASS=...
IMHDX_CHANNEL=1
ANTITHEFT_INTERVAL=20.0   ← segundos entre análises
ANTITHEFT_VIT_WINDOW=25.0 ← segundos de janela sem VIT para acionar IA
```

---

## O que está funcionando hoje

| Função | Status | Observação |
|---|---|---|
| Snapshot da câmera | ✅ | HTTP Digest Auth na Intelbras |
| Leitura do Espião | ✅ | Arquivo local, tempo real |
| Pré-filtro por VIT | ✅ | Não chama IA se há venda ativa |
| IA de visão | ❌ | Gemini com limit:0 — precisa trocar para Groq |
| Alerta Telegram com foto | ✅ | Quando IA funciona |
| Botões de feedback | ✅ | confirmed/dismissed com tipo_furto |
| Botão Ver vídeo | ✅ | Baixa do iMHDX, converte com ffmpeg |
| Detecção de tipo de furto | ✅ | 10 tipos classificados |
| Assistente Telegram | ✅ | Todos os comandos funcionando |

---

## O que está pendente (próximos passos)

### Urgente
- **Trocar Gemini por Groq** no agente — Groq testado e funcionando

### Próxima evolução do modelo
O sistema atual só detecta furto **quando não há VIT** nos últimos 25 segundos (caixa parado). Isso pega o cenário mais óbvio mas perde os mais sofisticados.

A próxima versão planeja detectar também:
- **Substituição de código** — operador registra produto barato mas o que passou no scanner era caro
- **VIT após SBT** — itens adicionados depois do subtotal (anomalia de sequência)
- **PLU genérico** — código `0000...` usado para registrar produto diferente

O gatilho muda de "sem VIT por X segundos" para "VIT suspeito foi registrado → tira foto → IA verifica se o produto visível bate com o que foi registrado".

---

## Como reiniciar os serviços

```bash
# No PDV1 (192.168.24.97) via SSH

# Reiniciar agente antifurto
sudo systemctl restart pdv-antitheft-agent.service

# Ver logs em tempo real
sudo journalctl -u pdv-antitheft-agent.service -f

# Ver últimos logs
sudo journalctl -u pdv-antitheft-agent.service -n 30 --no-pager

# Status dos dois serviços
sudo systemctl is-active pdv-antitheft-agent.service pdv-telegram-assistant.service
```

---

## Câmera PDV1 — observações técnicas

- **Posição:** acima e atrás da operadora
- **Ângulo:** visão top-down do balcão
- **Resolução stream principal:** 1920×1080, H.265, 30fps, 2048 kbps
- **Resolução sub-stream:** 704×480, H.265, 30fps, 512 kbps
- **Melhor para detectar:** comportamento do cliente, produtos no balcão
- **Limitação:** passada fantasma pelo operador é difícil de ver (corpo obstrui scanner)

---

## Resumo em uma linha

> O sistema tira foto do caixa a cada 20 segundos. Se não houve venda nos últimos 25 segundos, manda a foto para a IA que decide se há suspeita e qual tipo de furto. Se sim, alerta no Telegram com botões de feedback e opção de ver o vídeo do iMHDX.
