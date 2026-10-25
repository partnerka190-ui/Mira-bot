#!/bin/bash
# Присылает готовые ролики из папки ready/ админу в Telegram.
# Запуск:  bash /opt/mira/send.sh            — все ролики
#          bash /opt/mira/send.sh 01         — только те, что начинаются на 01
cd "$(dirname "$0")" && git pull -q
set -a; . ./.env; set +a
for f in ready/${1}*.mp4; do
  echo "Отправляю $f"
  curl -s -o /dev/null -w "  ответ Telegram: %{http_code}\n" -F chat_id="$ADMIN_ID" -F supports_streaming=true \
       -F caption="$(basename "$f")" -F video=@"$f" "https://api.telegram.org/bot$BOT_TOKEN/sendVideo"
done
