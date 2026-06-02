#!/usr/bin/env bash
# install_antitheft.sh — instala agente antifurto com YOLO + LLaVA no PDV
# Uso: sudo bash install_antitheft.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "======================================"
echo " PDV Anti-theft Installer"
echo " YOLO (rapido) + LLaVA (inteligente)"
echo "======================================"
echo ""

# ── Perguntas ──────────────────────────────────────────────────────────────
read -p "Numero do PDV (ex: 001): "               PDV_STATION
read -p "IP da camera ONVIF (ex: 10.10.10.20): "  CAMERA_HOST
read -p "Usuario da camera: "                      CAMERA_USER
read -s -p "Senha da camera: "                     CAMERA_PASS; echo
read -p "Token do Bot Telegram: "                  TELEGRAM_TOKEN
read -p "Chat ID do Telegram: "                    TELEGRAM_CHAT_ID
read -p "Modelo LLaVA [llava:7b / moondream]: "   OLLAMA_MODEL
OLLAMA_MODEL=${OLLAMA_MODEL:-llava:7b}

echo ""
echo "=== [1/6] Instalando Python 3.8 e pip ==="
apt-get update -qq
apt-get install -y python3.8 python3.8-distutils curl ca-certificates
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.8
python3.8 -m pip install "ultralytics>=8.0" "requests>=2.28" "Pillow>=9.0" --quiet
echo "Python 3.8 + ultralytics OK"

echo ""
echo "=== [2/6] Instalando Ollama ==="
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.ai/install.sh | sh
else
    echo "Ollama ja instalado: $(ollama --version)"
fi

# Garante que Ollama sobe como servico
systemctl enable ollama 2>/dev/null || true
systemctl start  ollama 2>/dev/null || ollama serve &
sleep 3

echo ""
echo "=== [3/6] Baixando modelo ${OLLAMA_MODEL} (~4GB, pode demorar) ==="
ollama pull "${OLLAMA_MODEL}"
echo "Modelo ${OLLAMA_MODEL} OK"

echo ""
echo "=== [4/6] Criando diretorios e copiando scripts ==="
mkdir -p /opt/pdv-antitheft
mkdir -p /var/log/pdv-antitheft/{dataset,models,alerts}

cp "$SCRIPT_DIR/pdv_dataset_builder.py"  /opt/pdv-antitheft/
cp "$SCRIPT_DIR/pdv_yolo_trainer.py"     /opt/pdv-antitheft/
cp "$SCRIPT_DIR/pdv_antitheft_agent.py"  /opt/pdv-antitheft/
chmod +x /opt/pdv-antitheft/*.py

echo ""
echo "=== [5/6] Criando /etc/pdv-antitheft-agent.env ==="
cat > /etc/pdv-antitheft-agent.env <<ENV
PDV_STATION=${PDV_STATION}
CAMERA_HOST=${CAMERA_HOST}
CAMERA_USER=${CAMERA_USER}
CAMERA_PASS=${CAMERA_PASS}
TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
PDV_BASE_DIR=/home/rpdv/frente
ANTITHEFT_MODEL=/var/log/pdv-antitheft/models/best.pt
ANTITHEFT_FALLBACK_MODEL=/home/rpdv/yolov8s-world.pt
ANTITHEFT_CONF=0.35
ANTITHEFT_INTERVAL=2.0
ANTITHEFT_EVENT_WINDOW=10.0
ANTITHEFT_COOLDOWN=30.0
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=${OLLAMA_MODEL}
OLLAMA_TIMEOUT=60
YOLO_DEVICE=cpu
DATASET_OUTDIR=/var/log/pdv-antitheft/dataset
TRAINER_OUTDIR=/var/log/pdv-antitheft/models
LEARNING_OUTDIR=/var/log/pdv-learning-agent
YOLO_WORLD_MODEL=/home/rpdv/yolov8s-world.pt
ENV
chmod 600 /etc/pdv-antitheft-agent.env
echo "env OK"

echo ""
echo "=== [6/6] Instalando e iniciando servico ==="
cp "$REPO_ROOT/services/pdv-antitheft-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable pdv-antitheft-agent.service
systemctl start  pdv-antitheft-agent.service
sleep 3
systemctl status pdv-antitheft-agent.service --no-pager

echo ""
echo "============================================"
echo " INSTALACAO CONCLUIDA"
echo "============================================"
echo ""
echo "O agente esta rodando com YOLO + ${OLLAMA_MODEL}."
echo ""
echo "Comandos uteis:"
echo "  Ver alertas em tempo real:"
echo "    journalctl -u pdv-antitheft-agent.service -f"
echo ""
echo "  Construir dataset e treinar modelo proprio (opcional, melhora deteccao):"
echo "    python3.8 /opt/pdv-antitheft/pdv_dataset_builder.py --days 7"
echo "    python3.8 /opt/pdv-antitheft/pdv_yolo_trainer.py"
echo ""
echo "  Ver alertas salvos:"
echo "    ls /var/log/pdv-antitheft/alerts/\$(date +%Y%m%d)/"
echo ""
