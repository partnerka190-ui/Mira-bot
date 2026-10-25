#!/bin/bash
# Присылает готовые ролики из папки ready/ админу в Telegram.
# Запуск:  bash /opt/mira/send.sh            — все ролики
#          bash /opt/mira/send.sh 01         — только те, что начинаются на 01
cd "$(dirname "$0")" && git pull -q
set -a; . ./.env; set +a
if [ -z "$ADMIN_ID" ]; then
  echo "В .env не задан ADMIN_ID — твой числовой Telegram ID."
  echo "Узнай его у бота @userinfobot и добавь строку:  echo ADMIN_ID=123456789 >> /opt/mira/.env"
  exit 1
fi
for f in ready/${1}*.mp4; do
  echo "Отправляю $f → $ADMIN_ID"
  resp=$(curl -s -F chat_id="$ADMIN_ID" -F supports_streaming=true -F caption="$(basename "$f")" \
              -F video=@"$f" "https://api.telegram.org/bot$BOT_TOKEN/sendVideo")
  case "$resp" in
    *'"ok":true'*) echo "  готово, проверь Telegram" ;;
    *) echo "  ошибка: $(echo "$resp" | grep -o '"description":"[^"]*"')" ;;
  esac
done
