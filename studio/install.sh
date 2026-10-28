#!/usr/bin/env bash
# Установка бота «Мира» на чистый Ubuntu 22/24. Запуск от root:
#   curl -sSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash -s <owner>/<repo>
set -euo pipefail

REPO="${1:?Укажите репозиторий: owner/repo}"
DIR=/opt/mira

echo "==> Пакеты"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-venv ffmpeg curl >/dev/null

echo "==> Код"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q
else
  git clone -q "https://github.com/$REPO.git" "$DIR"
fi
cd "$DIR"

echo "==> Python"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt pillow

echo "==> Сертификат Минцифры для GigaChat"
if [ ! -s russian_trusted_root_ca.cer ]; then
  curl -sSLk -o russian_trusted_root_ca.cer https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt || true
fi

echo "==> Колода"
[ -f assets/cards/back.png ] || venv/bin/python tools/make_deck.py

if [ ! -f .env ]; then
  echo "==> Ключи (вводятся здесь и остаются только на сервере)"
  read -rp "Токен бота от @BotFather: " BOT_TOKEN </dev/tty
  read -rp "Ключ авторизации GigaChat: " GIGA </dev/tty
  read -rp "Ваш Telegram ID для уведомлений (можно пусто): " ADMIN </dev/tty
  cp .env.example .env
  sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=$BOT_TOKEN|; s|^GIGACHAT_AUTH_KEY=.*|GIGACHAT_AUTH_KEY=$GIGA|; s|^ADMIN_ID=.*|ADMIN_ID=${ADMIN:-0}|; s|^GIGACHAT_CA_BUNDLE=.*|GIGACHAT_CA_BUNDLE=$DIR/russian_trusted_root_ca.cer|" .env
  chmod 600 .env
fi

echo "==> Проверка GigaChat"
venv/bin/python -m tools.check_giga || echo "!! GigaChat не ответил — бот запустится, но расклады работать не будут. Пришлите текст ошибки."

echo "==> Служба"
cp mira.service /etc/systemd/system/mira.service
systemctl daemon-reload
systemctl enable -q mira
systemctl restart mira
sleep 3
systemctl --no-pager --lines=15 status mira || true
echo
echo "Готово. Логи: journalctl -u mira -f"
