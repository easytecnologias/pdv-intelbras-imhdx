# Atualização para o Gerente — Detecção de Tipos de Furto
**Data:** 06/06/2026 — noite | **PDV:** 001 (192.168.24.97)

---

## O que mudou hoje

O sistema de câmera inteligente no caixa foi atualizado. Antes, quando havia suspeita, o Telegram recebia apenas uma mensagem genérica tipo "atividade suspeita detectada". Agora o sistema **identifica o tipo exato de furto** observado na imagem.

---

## Como o alerta chegava antes

```
Caixa 001 | 14:32:10
Atividade suspeita detectada.
PDV: SEM REGISTRO

[ ✅ Fraude real ]  [ ❌ Falso positivo ]
```

---

## Como o alerta chega agora

```
⚠️ Caixa 001 — 14:32:10
Tipo: produto_escondido
Operador empurrou item para baixo do balcão sem registrar.
PDV: SEM REGISTRO

[ ✅ Fraude real ]  [ ❌ Falso positivo ]
```

---

## Tipos de furto que o sistema detecta

### Furto pelo operador de caixa

| Código | O que é |
|---|---|
| `passada_fantasma` | Produto passa na frente do scanner sem ser lido — mão cobrindo o código de barras, produto virado ou gesto rápido sem scan |
| `produto_escondido` | Item empurrado para baixo do balcão ou colocado na sacola antes de ser registrado |
| `troca_produto` | Item diferente do que foi registrado colocado na sacola |
| `conluio_operador` | Produto claramente visível sendo ignorado pelo operador |

### Furto pelo cliente

| Código | O que é |
|---|---|
| `bolso_bolsa_cliente` | Produto colocado no bolso ou bolsa do cliente sem passar no scanner |
| `embaixo_carrinho` | Item na prateleira inferior do carrinho não retirado para o scan |
| `crianca_produto` | Produto entregue para criança no colo ou no carrinho sem registrar |
| `consumo_loja` | Cliente consumindo produto (comida ou bebida) antes de registrar |
| `sacola_propria` | Item colocado em sacola trazida pelo cliente sem passar no scanner |
| `embalagem_dentro_embalagem` | Produto menor escondido dentro de embalagem maior |

---

## O que é gravado a cada alerta

Além do Telegram, cada suspeita é registrada automaticamente em arquivo de log com o tipo identificado:

```json
{
  "time": "2026-06-06 14:32:10",
  "acao": "suspeita detectada",
  "vit": "nenhum",
  "tipo_furto": "produto_escondido",
  "motivo": "operador empurrou item para baixo do balcão sem registrar"
}
```

Quando o supervisor clica em **Fraude real** ou **Falso positivo**, o feedback também registra o tipo:

```json
{
  "alert_id": "001_20260606_143210",
  "image": "/var/log/pdv-antitheft/alerts/20260606/alert_001_20260606_143210.jpg",
  "time": "2026-06-06 14:32:10",
  "pdv": "001",
  "tipo_furto": "produto_escondido"
}
```

Isso permite gerar, futuramente, um relatório de quais tipos de furto são mais comuns e quantos alertas cada tipo gerou por semana.

---

## O que o sistema NÃO faz — regras de proteção

O sistema foi programado com regras obrigatórias para evitar acusações injustas:

- Nunca acusa com base em postura, etnia, aparência ou nervosismo
- Só emite alerta quando há evidência visual clara e objetiva
- Em caso de dúvida, retorna "sem suspeita"
- Se houver venda registrada nos últimos 25 segundos, o sistema nem analisa a imagem

---

## Estado atual

| Item | Status |
|---|---|
| Detecção de tipos de furto | ✅ Ativo no PDV1 desde hoje à noite |
| Alerta com tipo no Telegram | ✅ Funcionando |
| Log `activity.jsonl` com `tipo_furto` | ✅ Funcionando |
| Feedback com `tipo_furto` (confirmed / dismissed) | ✅ Funcionando |
| Vision Monitor PDV2–12 | ⏳ Pendente |

---

## Próximos passos

| Ação | Responsável | Prazo |
|---|---|---|
| Monitorar qualidade dos alertas com os novos tipos por 3 dias | Supervisor de loja | Esta semana |
| Ativar plano pago no Groq se cota gratuita esgotar novamente | Financeiro + TI | Esta semana |
| Gerar relatório de tipos de furto após 1 semana de dados | Gerência + TI | 13/06/2026 |
| Instalar nos demais PDVs | TI | A definir |
