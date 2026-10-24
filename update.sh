#!/usr/bin/env bash
# Обновление бота на сервере: bash /opt/mira/update.sh
set -euo pipefail
cd /opt/mira
git pull -q
venv/bin/pip install -q -r requirements.txt pillow
echo "==> Пересобираю колоду (около минуты)"
venv/bin/python -m tools.make_deck >/dev/null
systemctl restart mira
sleep 3
systemctl --no-pager --lines=5 status mira || true
echo "Готово."
