#!/usr/bin/env bash
# install_antitheft.sh — instala PDV Vision Monitor (Groq API) no PDV
# Uso: sudo bash install_antitheft.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "======================================"
echo " PDV Vision Monitor — Instalacao"
echo " Groq API (Llama 4 Scout com visao)"
echo "======================================"
echo ""

# ── Perguntas ──────────────────────────────────────────────────────────────
read -p "Numero do PDV (ex: 001): "               PDV_STATION
read -p "IP da camera ONVIF (ex: 10.10.10.20): "  CAMERA_HOST
read -p "Usuario da camera: "                      CAMERA_USER
read -s -p "Senha da camera: "                     CAMERA_PASS; echo
read -p "Token do Bot Telegram: "                  TELEGRAM_TOKEN
read -p "Chat ID do Telegram: "                    TELEGRAM_CHAT_ID
read -p "Chave Groq API (console.groq.com): "      GROQ_API_KEY

echo ""
echo "=== [1/3] Instalando Python 3.8 e requests ==="
apt-get update -qq
apt-get install -y python3.8 python3.8-distutils curl ca-certificates
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.8
python3.8 -m pip install "requests>=2.28" --quiet
echo "Python 3.8 + requests OK"

echo ""
echo "=== [2/3] Instalando script e servico ==="
mkdir -p /opt/pdv-antitheft
mkdir -p /var/log/pdv-antitheft/alerts

cp "$SCRIPT_DIR/pdv_antitheft_agent.py" /opt/pdv-antitheft/
chmod +x /opt/pdv-antitheft/pdv_antitheft_agent.py

cat > /etc/pdv-antitheft-agent.env <<ENV
PDV_STATION=${PDV_STATION}
CAMERA_HOST=${CAMERA_HOST}
CAMERA_USER=${CAMERA_USER}
CAMERA_PASS=${CAMERA_PASS}
TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
PDV_BASE_DIR=/home/rpdv/frente
GROQ_API_KEY=${GROQ_API_KEY}
GROQ_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
ANTITHEFT_INTERVAL=20.0
ANTITHEFT_VIT_WINDOW=25.0
ANTITHEFT_OUTDIR=/var/log/pdv-antitheft/alerts
ENV
chmod 600 /etc/pdv-antitheft-agent.env
echo "Env OK"

cp "$REPO_ROOT/services/pdv-antitheft-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable pdv-antitheft-agent.service

echo ""
echo "=== [3/3] Iniciando servico ==="
systemctl start pdv-antitheft-agent.service
sleep 3
systemctl status pdv-antitheft-agent.service --no-pager

echo ""
echo "============================================"
echo " INSTALACAO CONCLUIDA"
echo "============================================"
echo ""
echo "O agente esta rodando com Groq API."
echo "Analise a cada 20 segundos."
echo ""
echo "Comandos uteis:"
echo "  Ver descricoes em tempo real:"
echo "    journalctl -u pdv-antitheft-agent -f"
echo ""
echo "  Ver log de atividade do dia:"
echo "    cat /var/log/pdv-antitheft/alerts/\$(date +%Y%m%d)/activity.jsonl"
echo ""
