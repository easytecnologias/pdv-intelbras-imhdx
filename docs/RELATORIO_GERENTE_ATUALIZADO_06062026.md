# Atualização para o Gerente — PDV Vision Monitor
**Data:** 06/06/2026 — tarde | **PDV:** 001 (192.168.24.97)

---

## Situação atual — todas as 3 perguntas respondidas e implementadas

---

### Ponto 1 ✅ Groq agora só é chamado quando não há VIT

**Implementado.** O fluxo passou a ser:

```
1. Snapshot a cada 20s
2. Lê Espião (VIT dos últimos 25s)
3. SE há venda registrada → loga "OK venda normal" e dorme (Groq NÃO chamado)
4. SE não há venda → chama Groq para analisar suspeita
5. SE suspeito → alerta no Telegram
```

**Evidência nos logs:**
```
11:34:24 OK PDV001 venda normal: Esteira De Praia C Alca 90cm   ← Groq NÃO chamado
11:35:25 OK PDV001 venda normal: Gelo De Agua De Coco           ← Groq NÃO chamado
11:35:44 O atendente não está manuseando nenhum produto...      ← Groq chamado (sem VIT)
11:38:47 OK PDV001 venda normal: Batata Pringles Original 35g   ← Groq NÃO chamado
11:39:08 OK PDV001 venda normal: Ling Calab Perdigao Def Grossa ← Groq NÃO chamado
```

**Redução de chamadas:** ~80% menos. Groq só é acionado em momentos sem registro de venda.

---

### Ponto 2 ✅ YOLO removido — confirmado

**Não existe mais.** O fluxo antigo (YOLO → Groq) foi substituído por (pré-filtro VIT → Groq). Sem dependência de modelo local, sem ultralytics, sem GPU.

---

### Ponto 3 ✅ Botões de feedback implementados

**Implementado.** Todo alerta de suspeita agora inclui dois botões:

```
Caixa 001 | 14:32:10
O atendente está passando produto sem escanear.
PDV: SEM REGISTRO

[ ✅ Fraude real ]  [ ❌ Falso positivo ]
```

Quando o operador clica:
- **Fraude real** → grava em `/var/log/pdv-antitheft/feedback/confirmed.jsonl`
- **Falso positivo** → grava em `/var/log/pdv-antitheft/feedback/dismissed.jsonl`
- Telegram responde imediatamente: `"✅ Registrado. Obrigado!"`

Formato gravado:
```json
{"alert_id": "001_20260606_143210", "image": "/var/log/.../alert_001_20260606_143210.jpg", "time": "2026-06-06 14:32:10", "pdv": "001"}
```

Imagem do alerta também é salva em disco para referência futura.

---

## Estado consolidado do sistema

| Item | Status |
|---|---|
| Bridge PDV → iMHDX (PDV1, PDV12) | ✅ Produção |
| Vision Monitor PDV1 | ✅ Produção — Groq + pré-filtro VIT |
| Groq só quando sem VIT | ✅ Implementado hoje |
| Botões Fraude real / Falso positivo | ✅ Implementado hoje |
| Gravação confirmed.jsonl / dismissed.jsonl | ✅ Implementado hoje |
| Bot Telegram 📷 Ver caixa agora | ✅ Funcionando |
| Log activity.jsonl | ✅ Funcionando |
| Vision Monitor PDV2-12 | ⏳ Pendente — script pronto |

---

## Problema em aberto — cota Groq gratuita

A cota gratuita do Groq (500 req/dia por conta) foi esgotada nos testes de hoje. O sistema está em 429 até meia-noite.

**Ação necessária:** Ativar plano pago no Groq (~R$3/mês para 1 PDV em produção). Sem isso o sistema fica cego nos dias de teste intenso.

---

## O que fazer agora

| Ação | Responsável | Prazo |
|---|---|---|
| Ativar plano pago no Groq | Financeiro + TI | Esta semana |
| Instalar Vision Monitor nos outros PDVs | TI | Próximas 2 semanas |
| Monitorar qualidade dos alertas por 3 dias | Supervisor de loja | Contínuo |
| Analisar confirmed.jsonl após 1 semana | Gerência | 1 semana |
