# PDV License Server

Servidor de licencas do PDV Intelbras iMHDX.

## Render

Crie um Web Service apontando para este repositorio:

```text
Root Directory: license-server
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

Variaveis de ambiente:

```text
DATABASE_URL=<Internal Database URL do Render Postgres>
ADMIN_TOKEN=<token grande para chamadas administrativas>
LICENSE_SECRET=<segredo grande para assinatura das licencas>
ASAAS_WEBHOOK_TOKEN=<token configurado no webhook do Asaas>
```

## Endpoints

```text
GET  /health
POST /admin/pdvs
GET  /admin/pdvs
POST /admin/renew
POST /licenses/check
POST /webhooks/asaas
```

Todas as rotas `/admin/*` exigem o header:

```text
X-Admin-Token: <ADMIN_TOKEN>
```

O webhook do Asaas exige o header:

```text
asaas-access-token: <ASAAS_WEBHOOK_TOKEN>
```

## Criar PDV

```sh
curl -X POST https://seu-servico.onrender.com/admin/pdvs \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{
    "customer_name": "Mercado Exemplo",
    "store_name": "Matriz",
    "pdv_number": "001",
    "payment_reference": "mercado-exemplo-pdv001",
    "initial_days": 7
  }'
```

Use `payment_reference` como `externalReference` na cobranca do Asaas. Quando o
Pix for confirmado, o webhook renova a licenca encontrada por essa referencia
por mais 30 dias.

## Verificar Licenca

```sh
curl -X POST https://seu-servico.onrender.com/licenses/check \
  -H "Content-Type: application/json" \
  -d '{
    "license_key": "pdv_xxxxx",
    "device_id": "pdv001-maquina"
  }'
```
