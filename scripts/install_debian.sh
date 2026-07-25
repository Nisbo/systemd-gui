#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="/etc/systemd-gui.env"
SERVICE_FILE="/etc/systemd/system/systemd-gui.service"
AVAHI_SERVICE_FILE="/etc/avahi/services/systemd-gui.service"
NGINX_SITE="/etc/nginx/sites-available/systemd-gui.conf"
NGINX_LINK="/etc/nginx/sites-enabled/systemd-gui.conf"
QS_BIN="/usr/local/bin/qs"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this installer as root."
  exit 1
fi

port_80_listeners() {
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltnp 'sport = :80' 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:80 -sTCP:LISTEN 2>/dev/null || true
  else
    return 0
  fi
}

SYSTEMD_GUI_SKIP_NGINX_REQUESTED="${SYSTEMD_GUI_SKIP_NGINX:-auto}"
if [[ "${SYSTEMD_GUI_SKIP_NGINX_REQUESTED}" =~ ^(1|true|yes|on)$ ]]; then
  SYSTEMD_GUI_SKIP_NGINX=1
elif [[ "${SYSTEMD_GUI_SKIP_NGINX_REQUESTED}" =~ ^(0|false|no|off)$ ]]; then
  SYSTEMD_GUI_SKIP_NGINX=0
else
  SYSTEMD_GUI_SKIP_NGINX=0
  PORT_80_LISTENERS="$(port_80_listeners)"
  if [[ -n "${PORT_80_LISTENERS}" ]] && ! grep -qi "nginx" <<<"${PORT_80_LISTENERS}"; then
    echo "Port 80 is already in use by another process:"
    echo "${PORT_80_LISTENERS}"
    echo
    echo "This usually means an existing reverse proxy such as Nginx Proxy Manager, Docker, Caddy or Traefik."
    echo "Choose how Systemd Gui should be installed:"
    echo "  1) Reverse proxy mode: skip Debian nginx and bind Systemd Gui to 0.0.0.0:8851"
    echo "  2) Abort installation"
    echo "  3) Continue with installer-managed nginx anyway"
    if [[ -t 0 ]]; then
      read -r -p "Selection [1]: " SYSTEMD_GUI_INSTALL_MODE
    else
      SYSTEMD_GUI_INSTALL_MODE=1
      echo "Non-interactive install detected. Using reverse proxy mode."
    fi
    case "${SYSTEMD_GUI_INSTALL_MODE:-1}" in
      1|"")
        SYSTEMD_GUI_SKIP_NGINX=1
        ;;
      2)
        echo "Installation aborted."
        exit 1
        ;;
      3)
        SYSTEMD_GUI_SKIP_NGINX=0
        ;;
      *)
        echo "Unknown selection: ${SYSTEMD_GUI_INSTALL_MODE}"
        exit 1
        ;;
    esac
  fi
fi

apt update
APT_PACKAGES=(git python3 python3-flask gunicorn avahi-daemon avahi-utils)
if [[ "${SYSTEMD_GUI_SKIP_NGINX}" != "1" ]]; then
  APT_PACKAGES+=(nginx)
fi
apt install -y "${APT_PACKAGES[@]}"

if [[ "${SYSTEMD_GUI_SKIP_NGINX}" == "1" ]]; then
  SYSTEMD_GUI_HOST="${SYSTEMD_GUI_HOST:-0.0.0.0}"
else
  SYSTEMD_GUI_HOST="${SYSTEMD_GUI_HOST:-127.0.0.1}"
fi
SYSTEMD_GUI_PORT="${SYSTEMD_GUI_PORT:-8851}"
if [[ "${SYSTEMD_GUI_SKIP_NGINX}" == "1" ]]; then
  SYSTEMD_GUI_PUBLIC_PORT="${SYSTEMD_GUI_PUBLIC_PORT:-${SYSTEMD_GUI_PORT}}"
else
  SYSTEMD_GUI_PUBLIC_PORT="${SYSTEMD_GUI_PUBLIC_PORT:-8850}"
fi
SYSTEMD_GUI_SERVICE="${SYSTEMD_GUI_SERVICE:-systemd-gui}"
SYSTEMD_GUI_PASSWORD="${SYSTEMD_GUI_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')}"
SYSTEMD_GUI_SECRET="${SYSTEMD_GUI_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
SYSTEMD_GUI_NODE_ID="${SYSTEMD_GUI_NODE_ID:-$(python3 -c 'import secrets; print(secrets.token_hex(12))')}"
SYSTEMD_GUI_NODE_NAME="${SYSTEMD_GUI_NODE_NAME:-$(hostname)}"
SYSTEMD_GUI_APP_VERSION="${SYSTEMD_GUI_APP_VERSION:-$(python3 -c 'from pathlib import Path; ns={}; exec(Path("'"${APP_DIR}"'/systemd_gui/version.py").read_text(), ns); print(ns["APP_VERSION"])')}"

cat > "${ENV_FILE}" <<EOF
SYSTEMD_GUI_PASSWORD=${SYSTEMD_GUI_PASSWORD}
SYSTEMD_GUI_SECRET=${SYSTEMD_GUI_SECRET}
SYSTEMD_GUI_SERVICE=${SYSTEMD_GUI_SERVICE}
SYSTEMD_GUI_HOST=${SYSTEMD_GUI_HOST}
SYSTEMD_GUI_PORT=${SYSTEMD_GUI_PORT}
SYSTEMD_GUI_PUBLIC_PORT=${SYSTEMD_GUI_PUBLIC_PORT}
SYSTEMD_GUI_ALLOW_PROTECTED=0
EOF
chmod 600 "${ENV_FILE}"

mkdir -p "${APP_DIR}/data"

if [[ -f "${APP_DIR}/data/nodes.json" ]]; then
  EXISTING_NODE_ID="$(python3 -c 'import json, sys; data=json.load(open(sys.argv[1])); print(data.get("settings", {}).get("node_id", ""))' "${APP_DIR}/data/nodes.json" 2>/dev/null || true)"
  EXISTING_NODE_NAME="$(python3 -c 'import json, sys; data=json.load(open(sys.argv[1])); print(data.get("settings", {}).get("node_name", ""))' "${APP_DIR}/data/nodes.json" 2>/dev/null || true)"
  SYSTEMD_GUI_NODE_ID="${EXISTING_NODE_ID:-${SYSTEMD_GUI_NODE_ID}}"
  SYSTEMD_GUI_NODE_NAME="${EXISTING_NODE_NAME:-${SYSTEMD_GUI_NODE_NAME}}"
fi

if [[ ! -f "${APP_DIR}/data/nodes.json" ]]; then
cat > "${APP_DIR}/data/nodes.json" <<EOF
{
  "settings": {
    "node_id": "${SYSTEMD_GUI_NODE_ID}",
    "node_name": "${SYSTEMD_GUI_NODE_NAME}",
    "announce_enabled": true
  },
  "nodes": []
}
EOF
chmod 600 "${APP_DIR}/data/nodes.json"
fi

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Systemd Gui
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/gunicorn -w 2 -b \${SYSTEMD_GUI_HOST}:\${SYSTEMD_GUI_PORT} 'systemd_gui:create_app()'
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

if [[ "${SYSTEMD_GUI_SKIP_NGINX}" != "1" ]]; then
cat > "${NGINX_SITE}" <<EOF
server {
    listen ${SYSTEMD_GUI_PUBLIC_PORT};
    server_name _;

    location / {
        proxy_pass http://${SYSTEMD_GUI_HOST}:${SYSTEMD_GUI_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf "${NGINX_SITE}" "${NGINX_LINK}"
fi

cat > "${QS_BIN}" <<EOF
#!/usr/bin/env sh
export SYSTEMD_GUI_ROOT="${APP_DIR}"
export SYSTEMD_GUI_DATA_DIR="${APP_DIR}/data"
exec /usr/bin/python3 "${APP_DIR}/scripts/quick_shell.py" "\$@"
EOF
chmod 755 "${QS_BIN}"

mkdir -p "$(dirname "${AVAHI_SERVICE_FILE}")"
cat > "${AVAHI_SERVICE_FILE}" <<EOF
<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${SYSTEMD_GUI_NODE_NAME} on %h</name>
  <service>
    <type>_systemd-gui._tcp</type>
    <port>${SYSTEMD_GUI_PUBLIC_PORT}</port>
    <txt-record>node_id=${SYSTEMD_GUI_NODE_ID}</txt-record>
    <txt-record>app=systemd-gui</txt-record>
    <txt-record>version=${SYSTEMD_GUI_APP_VERSION}</txt-record>
    <txt-record>path=/</txt-record>
    <txt-record>https=0</txt-record>
  </service>
</service-group>
EOF

systemctl daemon-reload
systemctl enable systemd-gui
systemctl restart systemd-gui
systemctl enable avahi-daemon
systemctl restart avahi-daemon
if [[ "${SYSTEMD_GUI_SKIP_NGINX}" != "1" ]]; then
  nginx -t
  systemctl reload nginx || systemctl restart nginx
fi

echo
echo "Systemd Gui installed."
if [[ "${SYSTEMD_GUI_SKIP_NGINX}" == "1" ]]; then
  echo "Nginx setup: skipped"
  echo "Gunicorn bind: ${SYSTEMD_GUI_HOST}:${SYSTEMD_GUI_PORT}"
  echo "Open direct or proxy this target from your existing reverse proxy."
else
  echo "Open: http://YOUR-SERVER-IP:${SYSTEMD_GUI_PUBLIC_PORT}"
fi
echo "Password: ${SYSTEMD_GUI_PASSWORD}"
echo "Environment file: ${ENV_FILE}"
echo "Quick Shell: qs"
echo "LAN discovery: enabled via Avahi/mDNS"
