#!/usr/bin/env bash
# Первичная настройка сервера Oracle Cloud (Ubuntu 22.04/24.04, ARM Ampere A1) под стенд ШЕКАРЫ.
# Запуск на сервере:  curl -fsSL https://raw.githubusercontent.com/Falconwu17/shekara/main/deploy/bootstrap.sh | bash
set -euo pipefail

REPO="https://github.com/Falconwu17/shekara.git"
DIR="$HOME/shekara"

echo "== Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "== Порты 80/443 во встроенном файрволе образа Oracle"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$port" -j ACCEPT
done
sudo apt-get install -y netfilter-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "== Код"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "$REPO" "$DIR"; fi

echo "== Ключи и адрес (deploy/.env)"
ENV="$DIR/deploy/.env"
if [ ! -f "$ENV" ]; then
  IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
  cat > "$ENV" <<EOF
SHEKARA_DOMAIN=${IP//./-}.sslip.io
SHEKARA_ADMIN_TOKEN=$(openssl rand -hex 16)
SHEKARA_OPERATOR_TOKEN=$(openssl rand -hex 8)
SHEKARA_SPEED=600
EOF
  chmod 600 "$ENV"
fi
cat "$ENV"
echo
echo "Готово. Залейте данные с компьютера (deploy/push.ps1), затем сайт: https://$(grep SHEKARA_DOMAIN "$ENV" | cut -d= -f2)"
echo "Перелогиньтесь (exit и ssh снова), чтобы docker работал без sudo."
