# PDV Intelbras iMHDX — Contexto do Projeto

## O que é
Bridge entre PDVs Linux (WRPDV/Sierra) e gravador Intelbras iMHDX via UDP, com bot Telegram e auditoria visual via Groq.

## Rede e Acesso
- **PDV1**: `192.168.24.97`, user `rpdv`, SSH via PuTTY plink
- **iMHDX**: `192.168.24.227:38801` (UDP overlay), stream RTSP canal 1
- SSH Windows: `"C:/Program Files/PuTTY/plink" -ssh -pw <senha> rpdv@192.168.24.97 <cmd>`
- sudo no PDV1 precisa de `-t` (pty) + senha via stdin (`sudo -S`)
- `journalctl` funciona sem sudo para o user rpdv

## Serviços no PDV1 (todos active)
| Serviço | Função |
|---|---|
| `pdv-intelbras-bridge` | Lê arquivo Espião → UDP → iMHDX |
| `pdv-telegram-assistant` | Bot Telegram: status/caixa/cupom/foto/auditar |
| `pdv-visual-alert-worker` | RTSP → triagem local → Groq llama-4-scout → alerta |

## Scripts (em `/opt/.../*.py`, espelhados em `scripts/`)
- `pdv_intelbras_bridge.py` — bridge principal, PDV porta UDP 52100+N
- `pdv_telegram_assistant.py` — bot Telegram
- `pdv_visual_alert_worker.py` — worker Groq + buffer RTSP (ImhdxLiveBuffer)
- `pdv_visual_auditor.py` — auditoria sob demanda via bot

## Arquitetura de dados
```
/home/rpdv/frente/Cm/EspiaoDDMMAA.NNN  (fallback: /Log/)
  → pdv-intelbras-bridge → UDP → iMHDX 192.168.24.227:38801
```
Cada PDV tem porta própria: PDV1=52101, PDV2=52102, ...

## Deploy (working tree == produção)
Os scripts locais estão em md5sum idêntico ao PDV1. Para atualizar:
```
scp scripts/pdv_*.py rpdv@192.168.24.97:/opt/pdv-intelbras-imhdx/scripts/
ssh rpdv@192.168.24.97 "sudo systemctl restart pdv-intelbras-bridge pdv-telegram-assistant pdv-visual-alert-worker"
```

## Riscos conhecidos (não corrigidos ainda)
- credenciais RTSP visíveis via `ps aux` em pdv_visual_alert_worker.py ~L95
- ffmpeg do ImhdxLiveBuffer sem SIGTERM handler → processos órfãos
- `pending_cm_items` na bridge sem TTL
- `docs/SITUACAO_PROJETO.md` (não rastreado) pode conter senha — nunca commitar

## Regras do projeto
- Nunca expor URL RTSP com credenciais em texto claro em logs ou ps aux
- Testar no PDV1 antes de replicar para outros PDVs
- Commitar antes de fazer deploy

## Localização
`C:\PROJETOS\Mikrotik\pdv-intelbras-imhdx\`
