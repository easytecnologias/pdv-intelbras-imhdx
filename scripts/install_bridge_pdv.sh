#!/bin/sh
set -eu

station="$1"
src_port="$2"
dest_ip="${3:-192.168.24.174}"
dest_port="${4:-$src_port}"

mkdir -p /opt/pdv-intelbras-bridge
install -m 755 /tmp/pdv_intelbras_bridge.py /opt/pdv-intelbras-bridge/pdv_intelbras_bridge.py
install -m 644 /tmp/pdv-intelbras-bridge.service /etc/systemd/system/pdv-intelbras-bridge.service

cat >/etc/pdv-intelbras-bridge.env <<EOF
PDV_STATION=$station
PDV_SRC_PORT=$src_port
IMHDX_IP=$dest_ip
IMHDX_PORT=$dest_port
EOF

systemctl daemon-reload
systemctl enable --now pdv-intelbras-bridge.service
systemctl restart pdv-intelbras-bridge.service
systemctl --no-pager --full status pdv-intelbras-bridge.service
