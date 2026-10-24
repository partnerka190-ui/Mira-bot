"""Аватарка для канала через GigaChat: несколько вариантов уходят админу в Telegram.

Запуск на сервере:  venv/bin/python -m tools.gen_avatar
Файлы сохраняются в data/avatar_1.jpg … avatar_4.jpg.
"""
import asyncio
import io
import sys
from pathlib import Path

import aiohttp
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot import config  # noqa: E402
from tools.gen_art import giga_image  # noqa: E402

STYLE = (
    "Ты — художник-иллюстратор. Рисуешь аватарку для канала о картах Таро. Стиль: утончённая иллюстрация ар-нуво, "
    "глубокий полуночно-синий и фиолетовый фон, акценты сусального золота, мягкий лунный свет, звёзды. "
    "Квадратная композиция, главный объект строго в центре и целиком помещается в круг, крупно, с запасом по краям. "
    "Без текста, без букв и цифр, без рамки."
)
VARIANTS = [
    "Нарисуй красивую загадочную девушку-гадалку с длинными тёмными волосами, вид по плечи, в волосах полумесяц и звёзды, "
    "она держит у груди карту Таро рубашкой к зрителю, спокойный тёплый взгляд",
    "Нарисуй золотой полумесяц, внутри которого сияет восьмиконечная звезда, вокруг тонкий орнамент ар-нуво и мелкие звёзды",
    "Нарисуй руку с тонкими золотыми кольцами, которая держит три карты Таро веером рубашкой к зрителю, над картами светится полумесяц",
    "Нарисуй профиль девушки с закрытыми глазами, её силуэт заполнен звёздным небом и созвездиями, над головой золотой полумесяц",
]


async def main():
    out = Path(config.DB_PATH).parent
    out.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        for n, text in enumerate(VARIANTS, 1):
            try:
                raw = await giga_image(session, STYLE, text)
            except Exception as e:
                print(f"Вариант {n}: ошибка — {e}", flush=True)
                continue
            img = ImageOps.fit(Image.open(io.BytesIO(raw)).convert("RGB"), (1024, 1024), Image.LANCZOS)
            path = out / f"avatar_{n}.jpg"
            img.save(path, quality=95)
            print(f"Вариант {n}: {path}", flush=True)
            if config.ADMIN_ID:
                form = aiohttp.FormData()
                form.add_field("chat_id", str(config.ADMIN_ID))
                form.add_field("caption", f"Аватарка, вариант {n}. Сохраните картинку и поставьте на канал.")
                form.add_field("photo", path.read_bytes(), filename=path.name, content_type="image/jpeg")
                async with session.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto", data=form) as r:
                    await r.read()
            await asyncio.sleep(2)
    print("Готово.")


if __name__ == "__main__":
    asyncio.run(main())
