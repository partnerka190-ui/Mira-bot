"""Рисует иллюстрации колоды через GigaChat (встроенная генерация изображений) и пересобирает карты.

Запуск на сервере:  venv/bin/python -m tools.gen_art            — все 78 карт, уже готовые пропускаются
                     venv/bin/python -m tools.gen_art m00 m17    — только эти карты (перерисовать)
Готовые иллюстрации лежат в assets/art/, превью колоды уходит админу в Telegram.
"""
import asyncio
import io
import re
import sys
import uuid
from pathlib import Path

import aiohttp
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot import config  # noqa: E402
from bot.cards import BY_ID, DECK  # noqa: E402
from bot.llm import CHAT_URL, giga  # noqa: E402
from tools.art_prompts import STYLE, prompt  # noqa: E402

FILES_URL = "https://gigachat.devices.sberbank.ru/api/v1/files/{}/content"
ART = config.ART_DIR


async def draw(session, card):
    raw = await giga_image(session, STYLE, prompt(card))
    Image.open(io.BytesIO(raw)).convert("RGB").save(ART / f"{card['id']}.jpg", quality=95)


async def giga_image(session, system, text):
    """Одна картинка от GigaChat по описанию; возвращает байты JPG."""
    token = await giga._auth(session)
    client = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {token}", "X-Client-ID": client}
    payload = {
        "model": config.GIGACHAT_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "function_call": "auto",
    }
    async with session.post(CHAT_URL, json=payload, headers=headers, ssl=giga.connector_ssl) as r:
        data = await r.json()
        if r.status >= 400:
            raise RuntimeError(f"{r.status}: {data}")
    content = data["choices"][0]["message"]["content"]
    m = re.search(r'<img src="([^"]+)"', content)
    if not m:
        raise RuntimeError(f"нет картинки в ответе: {content[:200]}")
    async with session.get(FILES_URL.format(m.group(1)), headers={**headers, "Accept": "application/jpg"}, ssl=giga.connector_ssl) as r:
        r.raise_for_status()
        return await r.read()


def preview(ids):
    """Лист-превью из готовых карт, 6 в ряд."""
    cards = [Image.open(config.CARDS_DIR / f"{i}.png").resize((300, 500)) for i in ids]
    cols = 6
    rows = -(-len(cards) // cols)
    sheet = Image.new("RGB", (cols * 300, rows * 500), (13, 12, 32))
    for k, im in enumerate(cards):
        sheet.paste(im, ((k % cols) * 300, (k // cols) * 500))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=88)
    return buf.getvalue()


async def send_preview(session, ids, note):
    if not config.ADMIN_ID:
        return
    for start in range(0, len(ids), 24):
        form = aiohttp.FormData()
        form.add_field("chat_id", str(config.ADMIN_ID))
        form.add_field("caption", note if start == 0 else "")
        form.add_field("photo", preview(ids[start:start + 24]), filename="deck.jpg", content_type="image/jpeg")
        async with session.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto", data=form) as r:
            await r.read()


async def main(only):
    ART.mkdir(parents=True, exist_ok=True)
    todo = [BY_ID[i] for i in only] if only else [c for c in DECK if not (ART / f"{c['id']}.jpg").exists()]
    failed = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        for n, card in enumerate(todo, 1):
            for attempt in range(3):
                try:
                    await draw(session, card)
                    print(f"[{n}/{len(todo)}] {card['id']} {card['name']} — готово", flush=True)
                    break
                except Exception as e:
                    print(f"[{n}/{len(todo)}] {card['id']} {card['name']} — ошибка: {e}", flush=True)
                    await asyncio.sleep(5 * (attempt + 1))
            else:
                failed.append(card["id"])
            await asyncio.sleep(2)

        done = [c["id"] for c in todo if c["id"] not in failed]
        if done:
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "tools.make_deck", *done, cwd=ROOT)
            await proc.wait()
            note = f"Новые карты: {len(done)}" + (f". Не получились: {', '.join(failed)}" if failed else "")
            await send_preview(session, done, note)
            # Бот кэширует картинки карт в памяти — перезапускаем, чтобы он взял новые
            restart = await asyncio.create_subprocess_exec("systemctl", "restart", "mira")
            await restart.wait()
    print("Готово." + (f" Не получились: {' '.join(failed)} — запустите для них ещё раз." if failed else ""))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
