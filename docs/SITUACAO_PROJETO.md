# PDV Intelbras iMHDX — Situação do Projeto
**Data:** 06/06/2026 | **PDV de referência:** PDV1 (192.168.24.97)

---

## O que é este sistema

Sistema de monitoramento de caixas de supermercado com dois módulos independentes:

1. **Bridge PDV → iMHDX** — envia eventos de venda do PDV Linux em tempo real como overlay de texto no gravador Intelbras via UDP
2. **Vision Monitor** — câmera IP analisa o caixa a cada 20s usando IA local, descreve o que o atendente está fazendo e alerta no Telegram quando há movimento suspeito sem registro no PDV

---

## Arquitetura atual

```
PDV Linux (Ubuntu 18.04, i3-12100T, 7.4GB RAM)
│
├── WRPDV/Sierra ──────────────────────────────────► Arquivo Espião
│   (software de frente de caixa)                    /home/rpdv/frente/Cm/EspiaoDD MMAA.NNN
│
├── pdv-intelbras-bridge (systemd)
│   Lê Espião → UDP porta 52101 ──────────────────► iMHDX 192.168.24.227:38801
│                                                    (overlay de texto no vídeo)
│
├── pdv-antitheft-agent (systemd)
│   Câmera 10.10.10.20 → snapshot a cada 20s
│   → Ollama/moondream (local, sem internet) ──────► Descrição em PT-BR
│   → Compara com Espião (VIT últimos 25s)
│   → Telegram: alerta + botão 📷 Ver ao vivo
│
└── Ollama v0.1.48 + moondream (1.8B params, 1.7GB)
    Modelos em /home/rpdv/.ollama/models
```

---

## O que está funcionando

### Bridge PDV → iMHDX ✅
- PDV1 e PDV12 com bridge ativa
- PDV1 transmite para 192.168.24.227:38801
- PDV12 transmite para 192.168.24.174:52112 (destino diferente)
- Troca automática de arquivo à meia-noite funcionando

### Vision Monitor no PDV1 ✅
- Serviço ativo e reinicia automaticamente
- Ollama sobe sozinho no reboot (ExecStartPre no systemd)
- Moondream descreve ações do atendente em português a cada 20s
- Exemplos reais que funcionaram bem:
  - `"passando pelo scanner uma garrafa de refrigerante"` → PDV: Refri Guarana Antarctica 2l
  - `"manuseando uma garrafa de ketchup"` → PDV: Catchup Tambau 380g
  - `"ensacando salsicha"` → PDV: Salsicha Perdigao

### Telegram Bot ✅
- Alertas automáticos quando há atividade sem VIT no PDV
- Botão **📷 Ver caixa agora** envia foto + descrição ao vivo
- Comandos: `/ver`, `/foto`, `/caixa`

### Log de atividade ✅
- `/var/log/pdv-antitheft/alerts/YYYYMMDD/activity.jsonl`
- Registra: hora, ação, VIT, permanente

---

## Problemas conhecidos

### 1. Moondream inventa nomes de produtos
**Problema:** O modelo é pequeno (1.8B parâmetros) e chuta nomes genéricos como "salgadinho", "chips" para qualquer embalagem, mesmo quando não há salgadinho na imagem.

**Causa:** Modelo não foi treinado para identificar produtos de supermercado brasileiro. Visão geral do modelo é boa (vê ação, postura, objeto), mas nomeação específica é imprecisa.

**Status:** Prompt já foi ajustado para pedir forma/cor do objeto em vez do nome. Aguardando validação.

**Solução definitiva:** Usar modelo maior (ver seção de recomendações).

---

### 2. Translation inglês → português incompleta
**Problema:** Algumas expressões em inglês escapam da tradução e aparecem no Telegram/log.

**Exemplos:** `"verde spray bottle"`, `"at a supermarket"`, `"self-service terminal"`

**Causa:** Dicionário de tradução cobre ~95% dos casos mas não 100%.

**Impacto:** Baixo — texto ainda é compreensível.

**Solução:** Ampliar dicionário conforme aparecem novos padrões.

---

### 3. Vision Monitor instalado apenas no PDV1
**Problema:** PDV2 a PDV12 não têm o monitor de visão instalado.

**Status:** Script de instalação (`install_antitheft.sh`) pronto e testado para Ubuntu 18.04. Pronto para deploy.

---

### 4. Bridge sem redundância
**Problema:** Se o iMHDX ficar offline, a bridge tenta reconectar mas os eventos do período são perdidos.

**Impacto:** Baixo para operação normal. Relevante apenas se houver queda de rede frequente.

---

### 5. Sem dashboard — monitoramento só via Telegram e log
**Problema:** Não há interface web para visualizar histórico de atividade, filtrar por horário, ver padrões.

**Impacto:** Para auditoria retroativa é necessário ler o arquivo `.jsonl` manualmente.

---

## Recomendações — o que fazer agora

### Prioridade 1 — Validar qualidade das descrições (esta semana)
Com o novo prompt (forma/cor em vez de nome), monitorar por 2-3 dias e avaliar:
- As descrições fazem sentido com o que está na câmera?
- Os alertas no Telegram são pertinentes?
- A taxa de falsos positivos caiu?

**Custo:** Zero. **Tempo:** 3 dias de observação.

---

### Prioridade 2 — Instalar Vision Monitor nos outros PDVs (próximas 2 semanas)
O PDV1 é o PDV de testes. Para produção real, instalar nos PDVs de maior movimento.

```bash
# Em cada PDV (SSH como root):
scp scripts/install_antitheft.sh rpdv@192.168.24.X:/tmp/
ssh rpdv@192.168.24.X "sudo bash /tmp/install_antitheft.sh"
```

**Custo:** Zero. **Tempo:** ~30 min por PDV.

---

### Prioridade 3 — Modelo de visão melhor (decisão de investimento)

O moondream tem limitações estruturais por ser pequeno. Opções:

| Opção | Custo | Qualidade | Complexidade |
|---|---|---|---|
| Continuar com moondream | R$0/mês | Regular | Zero |
| LLaVA 7B local (Ollama) | R$0/mês | Boa | Precisa testar RAM (7.4GB disponível, 6-7GB necessário) |
| Groq API paga | ~R$3/mês | Muito boa | Baixa |
| Gemini com billing | ~R$1/mês | Muito boa | Baixa |

**Recomendação:** Testar LLaVA 7B local primeiro — mesmo PDV, sem custo. Se a RAM não aguentar, partir para Groq pago.

---

### Prioridade 4 — Dashboard web simples (futuro)
Um script Python que lê os `.jsonl` do dia e exibe uma timeline das atividades com foto por horário. Útil para auditoria.

**Custo:** Zero (desenvolvimento interno). **Tempo estimado:** 1-2 dias.

---

## Arquivos importantes

| Arquivo | Descrição |
|---|---|
| `scripts/pdv_antitheft_agent.py` | Script principal do Vision Monitor |
| `scripts/pdv_intelbras_bridge.py` | Bridge PDV → iMHDX |
| `scripts/install_antitheft.sh` | Instalador para novos PDVs |
| `services/pdv-antitheft-agent.service` | Systemd unit (inclui autostart do Ollama) |
| `/etc/pdv-antitheft-agent.env` | Configuração por PDV (câmera, Telegram, etc.) |
| `/var/log/pdv-antitheft/alerts/` | Logs de atividade e alertas |
| `/opt/pdv-antitheft/` | Scripts em produção no PDV |

---

## Acesso ao PDV1 (testes)

```
IP:     192.168.24.97
Host:   pdv101
User:   rpdv
Pass:   rpdv@#
OS:     Ubuntu 18.04 (GLIBC 2.27)
```

Ver logs ao vivo:
```bash
journalctl -u pdv-antitheft-agent -f
```

Ver atividade do dia:
```bash
cat /var/log/pdv-antitheft/alerts/$(date +%Y%m%d)/activity.jsonl | python3 -c "
import sys, json
for l in sys.stdin:
    d = json.loads(l)
    print(d['time'], '|', d['acao'], '| PDV:', d['vit'])
"
```

---

## Resumo para o gerente

**O que já funciona e pode ir para produção:**
- Bridge PDV → iMHDX: **pronto, em produção no PDV1 e PDV12**
- Vision Monitor: **funcional no PDV1, pronto para escalar para os outros PDVs**
- Bot Telegram com botão ao vivo: **funcionando**

**O que ainda não está perfeito:**
- Moondream erra o nome do produto às vezes — faz sentido visual mas nomeia errado
- Só PDV1 tem o Vision Monitor

**Decisão principal que o gerente precisa tomar:**
> Ficar com moondream local (gratuito, qualidade regular) ou investir ~R$3/mês no Groq API (qualidade profissional)?

A escolha define se o sistema é "útil para ver o que está acontecendo" ou "confiável para detectar fraudes".
