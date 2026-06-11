# Relatório para o Gerente — PDV Vision Monitor
**Data:** 06/06/2026 | **Preparado por:** Time de Arquitetura

---

## Resumo Executivo

O sistema de monitoramento de caixas está **operacional no PDV1** e passou por uma evolução completa hoje. O modelo de visão foi migrado de um modelo local limitado (moondream) para o **Groq API com Llama 4 Scout** — o mesmo que o gerente solicitou. O resultado é imediato: descrições precisas em português sem erros de produto.

---

## O que foi entregue hoje

### ✅ Migração Groq API concluída e em produção

**Antes (moondream local):**
```
"O atendente está manuseando um pacote de salgadinho"  ← produto inventado
"O atendente está manuseando um pacote de salgadinho"  ← repetia errado
"O atendente está manuseando um pacote de salgadinho"  ← sempre salgadinho
```

**Depois (Groq / Llama 4 Scout):**
```
"O atendente está manipulando o saco plástico e embalando os produtos do cliente."
"O atendente está segurando um desodorante (em spray ou roll-on) enquanto opera o caixa."
"O atendente está passando um produto pelo scanner do caixa."
```

A diferença é estrutural: o Llama 4 Scout é um modelo de 17 bilhões de parâmetros com visão real. O moondream era 1.8 bilhões sem contexto de supermercado.

---

## Arquitetura atual (após mudanças de hoje)

```
PDV Linux (Ubuntu 18.04)
│
├── Câmera IP (10.10.10.20)
│   └── snapshot JPEG a cada 20s
│
├── Arquivo Espião (local)
│   └── eventos VIT em tempo real
│
├── pdv-antitheft-agent (systemd)
│   ├── Captura snapshot
│   ├── Lê VIT dos últimos 25s
│   ├── Envia para Groq API ──────────────► Llama 4 Scout (nuvem)
│   │   Recebe: descrição em português
│   ├── Loga em activity.jsonl
│   └── Telegram: alerta + botão 📷 Ver ao vivo
│
└── RAM utilizada: ~50MB (antes: ~1.7GB com Ollama)
```

---

## Estado dos sistemas

| Sistema | Status | Observação |
|---|---|---|
| Bridge PDV → iMHDX | ✅ Produção | PDV1 e PDV12 ativos |
| Vision Monitor PDV1 | ✅ Produção | Groq ativo desde hoje |
| Bot Telegram (botão) | ✅ Funcionando | `/ver`, `/foto`, `/caixa` |
| Log de atividade | ✅ Funcionando | JSONL diário |
| Outros PDVs (2-12) | ⏳ Pendente | Script de instalação pronto |

---

## O que o Groq custa

O gerente perguntou sobre custo. Aqui estão os números reais:

**Modelo:** `meta-llama/llama-4-scout-17b-16e-instruct`

| Uso | Chamadas/dia | Custo estimado |
|---|---|---|
| 1 PDV, intervalo 20s, 8h/dia | ~1.440 | ~$0,05/dia = **R$0,30/mês** |
| 12 PDVs | ~17.280 | ~$0,60/dia = **R$3,60/mês** |

> Valores baseados no pricing público do Groq. Pode variar.

**Limite gratuito:** 500 req/dia por conta. Para uso contínuo em PDV, o plano pago é necessário.

**Recomendação:** Usar 1 conta Groq por PDV ou criar projetos separados para controlar o uso individualmente.

---

## Problemas resolvidos hoje

| Problema | Situação |
|---|---|
| Moondream inventava produtos (salgadinho para tudo) | ✅ Resolvido — Groq nomeia corretamente |
| 200 linhas de tradução inglês→PT frágil | ✅ Removido — Groq responde em PT nativo |
| Ollama consumindo 1.7GB RAM no PDV | ✅ Removido — PDV liberou memória |
| Autostart do Ollama no reboot | ✅ Irrelevante agora — Groq é API |
| Código com imports mortos (PIL, BytesIO, torch) | ✅ Removido na refatoração |

---

## Problemas que ainda existem

### 1. Vision Monitor só no PDV1
**Situação:** 11 PDVs sem monitoramento de visão.
**Solução:** Script de instalação pronto (`install_antitheft.sh`). Cada instalação leva ~10 minutos.
**Próximo passo:** Agendar instalação nos PDVs de maior movimento.

---

### 2. Qualidade da descrição ainda não é 100%
**Exemplos de falhas residuais:**
- `"O atendente está checking out um cliente em supermarket"` — mistura inglês/português
- Às vezes o contexto do VIT não é usado adequadamente

**Causa:** O prompt pode ser refinado. O modelo é bom — o problema é a instrução.
**Solução:** Ajuste de prompt (sem custo, sem código novo).

---

### 3. Sem análise retroativa
**Situação:** Os logs existem mas não há ferramenta para analisar padrões.
**Impacto:** Para auditoria, é necessário ler o `.jsonl` manualmente.
**Solução futura:** Dashboard web simples para visualizar a timeline do dia.

---

## Recomendações prioritárias

### Prioridade 1 — Refinamento do prompt (esta semana, gratuito)
O prompt atual pede "uma frase curta". Ajustar para especificar melhor o formato e evitar mistura de idiomas.
**Responsável:** Time de arquitetura. **Prazo:** 2 dias.

### Prioridade 2 — Instalar nos PDVs de maior movimento (próximas 2 semanas)
Identificar os 3-5 PDVs com maior volume de vendas e instalar primeiro.
```bash
# Em cada PDV via SSH:
scp scripts/install_antitheft.sh rpdv@192.168.24.X:/tmp/
ssh rpdv@192.168.24.X "sudo bash /tmp/install_antitheft.sh"
```
**Responsável:** Time de TI. **Prazo:** 2 semanas.

### Prioridade 3 — Criar conta Groq paga (esta semana, ~R$20/mês)
Conta gratuita tem 500 req/dia — suficiente para testar, insuficiente para produção em múltiplos PDVs.
Criar conta em [console.groq.com](https://console.groq.com) e adicionar créditos.
**Responsável:** Financeiro + TI. **Prazo:** 1 semana.

---

## Acesso e monitoramento

**Ver o sistema funcionando em tempo real:**
```bash
ssh rpdv@192.168.24.97
journalctl -u pdv-antitheft-agent -f
```

**Ver log do dia:**
```bash
cat /var/log/pdv-antitheft/alerts/$(date +%Y%m%d)/activity.jsonl \
  | python3 -c "
import sys, json
for l in sys.stdin:
    d = json.loads(l)
    print(d['time'], '|', d['acao'], '| PDV:', d['vit'])
"
```

**Telegram:** Pressionar o botão **📷 Ver caixa agora** no chat do bot a qualquer momento.

---

## Arquivos entregues

| Arquivo | Descrição |
|---|---|
| `scripts/pdv_antitheft_agent.py` | Script principal — Groq API, 280 linhas |
| `scripts/install_antitheft.sh` | Instalador para novos PDVs |
| `services/pdv-antitheft-agent.service` | Systemd unit limpa |
| `docs/SITUACAO_PROJETO.md` | Diagnóstico geral do projeto |
| `docs/RELATORIO_GERENTE_06062026.md` | Este documento |
