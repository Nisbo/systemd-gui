#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="/etc/systemd-gui.env"
SERVICE_FILE="/etc/systemd/system/systemd-gui.service"
NGINX_SITE="/etc/nginx/sites-available/systemd-gui.conf"
NGINX_LINK="/etc/nginx/sites-enabled/systemd-gui.conf"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this installer as root."
  exit 1
fi

apt update
apt install -y git python3 python3-flask gunicorn nginx

SYSTEMD_GUI_HOST="${SYSTEMD_GUI_HOST:-127.0.0.1}"
SYSTEMD_GUI_PORT="${SYSTEMD_GUI_PORT:-8851}"
SYSTEMD_GUI_PUBLIC_PORT="${SYSTEMD_GUI_PUBLIC_PORT:-8850}"
SYSTEMD_GUI_SERVICE="${SYSTEMD_GUI_SERVICE:-systemd-gui}"
SYSTEMD_GUI_PASSWORD="${SYSTEMD_GUI_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')}"
SYSTEMD_GUI_SECRET="${SYSTEMD_GUI_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"

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
systemctl daemon-reload
systemctl enable systemd-gui
systemctl restart systemd-gui
nginx -t
systemctl reload nginx || systemctl restart nginx

echo
echo "Systemd Gui installed."
echo "Open: http://YOUR-SERVER-IP:${SYSTEMD_GUI_PUBLIC_PORT}"
echo "Password: ${SYSTEMD_GUI_PASSWORD}"
echo "Environment file: ${ENV_FILE}"
