#!/bin/sh
set -eu

REPO_RAW_BASE="${REPO_RAW_BASE:-https://raw.githubusercontent.com/easytecnologias/pdv-intelbras-imhdx/main}"
INSTALL_ROOT="/opt"
BRIDGE_DIR="$INSTALL_ROOT/pdv-intelbras-bridge"
AUDITOR_DIR="$INSTALL_ROOT/pdv-camera-auditor"
ASSISTANT_DIR="$INSTALL_ROOT/pdv-telegram-assistant"
BACKUP_ROOT="/var/backups/pdv-intelbras-imhdx"
TMP_DIR=""

say() {
    printf '%s\n' "$*"
}

die() {
    say "ERRO: $*" >&2
    exit 1
}

need_root() {
    [ "$(id -u)" = "0" ] || die "rode como root: sudo ./install.sh"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

download() {
    url="$1"
    dest="$2"
    if command_exists curl; then
        curl -fsSL "$url" -o "$dest"
    elif command_exists wget; then
        wget -q "$url" -O "$dest"
    else
        die "curl ou wget ausente para baixar arquivos online"
    fi
}

ask() {
    label="$1"
    default="$2"
    var_name="$3"
    if [ -n "$default" ]; then
        printf '%s [%s]: ' "$label" "$default"
    else
        printf '%s: ' "$label"
    fi
    read answer
    if [ -z "$answer" ]; then
        answer="$default"
    fi
    eval "$var_name=\$answer"
}

ask_secret() {
    label="$1"
    var_name="$2"
    printf '%s: ' "$label"
    stty -echo 2>/dev/null || true
    read answer
    stty echo 2>/dev/null || true
    printf '\n'
    eval "$var_name=\$answer"
}

yes_no() {
    label="$1"
    default="$2"
    printf '%s [%s]: ' "$label" "$default"
    read answer
    answer="${answer:-$default}"
    case "$answer" in
        s|S|sim|SIM|y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

station3() {
    printf '%03d' "$1"
}

port_for_station() {
    printf '%d' "$((52100 + $1))"
}

prepare_source() {
    if [ -f "./scripts/pdv_intelbras_bridge.py" ]; then
        SOURCE_DIR="$(pwd)"
        return
    fi

    TMP_DIR="$(mktemp -d /tmp/pdv-intelbras-imhdx.XXXXXX)"
    mkdir -p "$TMP_DIR/scripts"
    say "Baixando arquivos do GitHub..."
    download "$REPO_RAW_BASE/scripts/pdv_intelbras_bridge.py" "$TMP_DIR/scripts/pdv_intelbras_bridge.py"
    download "$REPO_RAW_BASE/scripts/pdv_camera_auditor_linux.py" "$TMP_DIR/scripts/pdv_camera_auditor_linux.py"
    download "$REPO_RAW_BASE/scripts/pdv_telegram_assistant.py" "$TMP_DIR/scripts/pdv_telegram_assistant.py"
    SOURCE_DIR="$TMP_DIR"
}

backup_existing() {
    stamp="$(date +%Y%m%d_%H%M%S)"
    backup_dir="$BACKUP_ROOT/$stamp"
    mkdir -p "$backup_dir"

    for path in \
        "$BRIDGE_DIR" \
        "$AUDITOR_DIR" \
        "$ASSISTANT_DIR" \
        /etc/pdv-intelbras-bridge.env \
        /etc/pdv-camera-auditor.env \
        /etc/pdv-telegram-assistant.env \
        /etc/systemd/system/pdv-intelbras-bridge.service \
        /etc/systemd/system/pdv-camera-auditor.service \
        /etc/systemd/system/pdv-telegram-assistant.service
    do
        if [ -e "$path" ]; then
            cp -a "$path" "$backup_dir/"
        fi
    done
    say "Backup criado em: $backup_dir"
}

install_packages() {
    if ! yes_no "Instalar/validar dependencias pelo apt? python3-requests python3-pil ffmpegthumbnailer" "s"; then
        return
    fi
    if command_exists apt-get; then
        apt-get update
        apt-get install -y python3 python3-requests python3-pil ffmpegthumbnailer
    else
        say "apt-get nao encontrado. Verifique manualmente: python3, requests, PIL e ffmpegthumbnailer."
    fi
}

install_files() {
    mkdir -p "$BRIDGE_DIR" "$AUDITOR_DIR" "$ASSISTANT_DIR"
    install -m 755 "$SOURCE_DIR/scripts/pdv_intelbras_bridge.py" "$BRIDGE_DIR/pdv_intelbras_bridge.py"
    install -m 755 "$SOURCE_DIR/scripts/pdv_camera_auditor_linux.py" "$AUDITOR_DIR/pdv_camera_auditor_linux.py"
    install -m 755 "$SOURCE_DIR/scripts/pdv_telegram_assistant.py" "$ASSISTANT_DIR/pdv_telegram_assistant.py"
}

write_bridge_env() {
    cat >/etc/pdv-intelbras-bridge.env <<EOF
PDV_STATION=$PDV_STATION
PDV_SRC_PORT=$PDV_SRC_PORT
PDV_BASE_DIR=$PDV_BASE_DIR
IMHDX_IP=$IMHDX_HOST
IMHDX_PORT=$IMHDX_POS_PORT
EOF
    chmod 600 /etc/pdv-intelbras-bridge.env
}

write_auditor_env() {
    cat >/etc/pdv-camera-auditor.env <<EOF
CAMERA_HOST=$CAMERA_HOST
CAMERA_USER=$CAMERA_USER
CAMERA_PASS=$CAMERA_PASS
PDV_STATION=$PDV_STATION
PDV_BASE_DIR=$PDV_BASE_DIR
AUDITOR_OUTDIR=/var/log/pdv-camera-auditor
AUDITOR_DURATION=0
AUDITOR_SPY_TAIL=350
AUDITOR_SNAPSHOT_INTERVAL=10.0
EOF
    chmod 600 /etc/pdv-camera-auditor.env
}

write_assistant_env() {
    cat >/etc/pdv-telegram-assistant.env <<EOF
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
PDV_STATION=$PDV_STATION
PDV_BASE_DIR=$PDV_BASE_DIR
AUDITOR_EVENTS_FILE=/var/log/pdv-camera-auditor/events.jsonl
BOT_STATE_DIR=/var/lib/pdv-telegram-assistant
IMHDX_HOST=$IMHDX_HOST
IMHDX_USER=$IMHDX_USER
IMHDX_PASS=$IMHDX_PASS
IMHDX_CHANNEL=$IMHDX_CHANNEL
IMHDX_WINDOW_BEFORE=2
IMHDX_WINDOW_AFTER=8
FFMPEGTHUMBNAILER=ffmpegthumbnailer
EOF
    chmod 600 /etc/pdv-telegram-assistant.env
}

write_services() {
    cat >/etc/systemd/system/pdv-intelbras-bridge.service <<'EOF'
[Unit]
Description=PDV Intelbras iMHDX POS bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/pdv-intelbras-bridge.env
ExecStart=/usr/bin/python3 /opt/pdv-intelbras-bridge/pdv_intelbras_bridge.py --station ${PDV_STATION} --src-port ${PDV_SRC_PORT} --dest-ip ${IMHDX_IP} --dest-port ${IMHDX_PORT} --base-dir ${PDV_BASE_DIR} --log-dir ${PDV_BASE_DIR}/Log
Restart=always
RestartSec=2
WorkingDirectory=/opt/pdv-intelbras-bridge
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    cat >/etc/systemd/system/pdv-camera-auditor.service <<'EOF'
[Unit]
Description=PDV Camera Auditor
After=network-online.target pdv-intelbras-bridge.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/pdv-camera-auditor.env
ExecStart=/usr/bin/python3 /opt/pdv-camera-auditor/pdv_camera_auditor_linux.py
Restart=always
RestartSec=5
User=root
WorkingDirectory=/opt/pdv-camera-auditor

[Install]
WantedBy=multi-user.target
EOF

    cat >/etc/systemd/system/pdv-telegram-assistant.service <<'EOF'
[Unit]
Description=PDV Telegram Assistant
After=network-online.target pdv-camera-auditor.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/pdv-telegram-assistant.env
ExecStart=/usr/bin/python3 /opt/pdv-telegram-assistant/pdv_telegram_assistant.py
Restart=always
RestartSec=5
User=root
WorkingDirectory=/opt/pdv-telegram-assistant

[Install]
WantedBy=multi-user.target
EOF
}

validate_python() {
    python3 -m py_compile \
        "$BRIDGE_DIR/pdv_intelbras_bridge.py" \
        "$AUDITOR_DIR/pdv_camera_auditor_linux.py" \
        "$ASSISTANT_DIR/pdv_telegram_assistant.py"
}

enable_services() {
    systemctl daemon-reload
    systemctl enable pdv-intelbras-bridge.service
    systemctl enable pdv-camera-auditor.service
    systemctl enable pdv-telegram-assistant.service
    systemctl restart pdv-intelbras-bridge.service
    systemctl restart pdv-camera-auditor.service
    systemctl restart pdv-telegram-assistant.service
}

show_status() {
    say ""
    say "Status final:"
    systemctl --no-pager --full status pdv-intelbras-bridge.service || true
    systemctl --no-pager --full status pdv-camera-auditor.service || true
    systemctl --no-pager --full status pdv-telegram-assistant.service || true
    say ""
    say "Comandos uteis:"
    say "  journalctl -u pdv-intelbras-bridge.service -n 80 --no-pager"
    say "  journalctl -u pdv-camera-auditor.service -n 80 --no-pager"
    say "  journalctl -u pdv-telegram-assistant.service -n 80 --no-pager"
}

collect_config() {
    say "Instalador PDV Intelbras iMHDX"
    say ""
    ask "Numero do PDV" "1" PDV_NUMBER
    PDV_STATION="$(station3 "$PDV_NUMBER")"
    PDV_SRC_PORT="$(port_for_station "$PDV_NUMBER")"
    DEFAULT_CHANNEL="$PDV_NUMBER"

    ask "Diretorio do frente" "/home/rpdv/frente" PDV_BASE_DIR
    ask "IP do iMHDX" "192.168.24.227" IMHDX_HOST
    ask "Porta POS UDP do iMHDX" "38801" IMHDX_POS_PORT
    ask "Canal do iMHDX para este PDV" "$DEFAULT_CHANNEL" IMHDX_CHANNEL
    ask "Usuario do iMHDX" "admin" IMHDX_USER
    ask_secret "Senha do iMHDX" IMHDX_PASS

    ask "IP da camera do PDV" "10.10.10.20" CAMERA_HOST
    ask "Usuario da camera/ONVIF" "" CAMERA_USER
    ask_secret "Senha da camera/ONVIF" CAMERA_PASS

    ask "Token do bot Telegram" "" TELEGRAM_BOT_TOKEN
    ask "ID do grupo Telegram" "" TELEGRAM_CHAT_ID

    say ""
    say "Resumo:"
    say "  PDV: $PDV_STATION"
    say "  Porta origem UDP: $PDV_SRC_PORT"
    say "  iMHDX: $IMHDX_HOST:$IMHDX_POS_PORT canal $IMHDX_CHANNEL"
    say "  Camera: $CAMERA_HOST"
    say "  Frente: $PDV_BASE_DIR"
    say ""
    yes_no "Confirmar instalacao?" "s" || die "instalacao cancelada"
}

cleanup() {
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}

trap cleanup EXIT

need_root
prepare_source
collect_config
backup_existing
install_packages
install_files
write_bridge_env
write_auditor_env
write_assistant_env
write_services
validate_python
enable_services
show_status

say "Instalacao concluida."
