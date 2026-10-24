import json
import logging
import ssl
import time
import uuid

import aiohttp

from . import config
from .cards import BY_ID

log = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

PERSONA = """Ты — Мира, AI-таролог в Telegram. Пользователь знает, что ты искусственный интеллект.
Стиль: тёплый, образный, но конкретный. Обращайся на «ты» и по имени. Короткие абзацы, без списков и заголовков.
Опирайся на значение карты и ситуацию человека, связывай карты между собой, давай мягкий практический совет.
Запрещено: пугать (порча, сглаз, проклятия, «опасность»), обещать гарантированный исход, давать советы по здоровью,
беременности, лекарствам, юридическим вопросам и вложениям денег — в таких случаях мягко советуй обратиться к специалисту.
Никогда не говори, что человеку нужно купить что-то, чтобы избежать беды. Это развлекательный сервис."""


class GigaChat:
    def __init__(self):
        self.token = None
        self.expires_at = 0
        ctx = ssl.create_default_context(cafile=config.GIGACHAT_CA_BUNDLE or None)
        self.connector_ssl = ctx

    async def _auth(self, session):
        if self.token and time.time() < self.expires_at - 60:
            return self.token
        headers = {
            "Authorization": f"Basic {config.GIGACHAT_AUTH_KEY}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        async with session.post(OAUTH_URL, headers=headers, data={"scope": config.GIGACHAT_SCOPE}, ssl=self.connector_ssl) as r:
            r.raise_for_status()
            data = await r.json()
        self.token = data["access_token"]
        self.expires_at = data["expires_at"] / 1000
        return self.token

    async def complete(self, user_prompt, max_tokens=900, temperature=0.9):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
            token = await self._auth(session)
            payload = {
                "model": config.GIGACHAT_MODEL,
                "messages": [
                    {"role": "system", "content": PERSONA},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with session.post(CHAT_URL, headers=headers, json=payload, ssl=self.connector_ssl) as r:
                r.raise_for_status()
                data = await r.json()
        return data["choices"][0]["message"]["content"].strip()


giga = GigaChat()


def _profile(user):
    memory = json.loads(user.get("memory") or "[]")
    lines = [f"Имя: {user.get('name') or 'не указано'}", f"Знак зодиака: {user.get('sign') or 'не указан'}"]
    if user.get("sphere"):
        lines.append(f"Главная тема: {user['sphere']}")
    if user.get("partner_name"):
        lines.append(f"Человек, о котором спрашивает: {user['partner_name']}")
    if memory:
        past = "; ".join(f"{m['d']}: «{m['q']}»" for m in memory[-3:])
        lines.append(f"Прошлые вопросы: {past}")
    return "\n".join(lines)


def _card_line(pos, card_id):
    c = BY_ID[card_id]
    return f"{pos}: {c['name']} ({c['keywords']})"


async def three_card_reading(user, question, card_ids, theme=None):
    """Возвращает три абзаца — по одному на карту. Третий (итог) бесплатным пользователям показываем после оплаты."""
    positions = ["Карта 1 — корень ситуации", "Карта 2 — что происходит сейчас", "Карта 3 — к чему всё ведёт"]
    if theme == "partner":
        positions = ["Карта 1 — что он(а) думает о тебе", "Карта 2 — что чувствует на самом деле", "Карта 3 — чем это закончится"]
    cards = "\n".join(_card_line(p, cid) for p, cid in zip(positions, card_ids))
    prompt = f"""Профиль:
{_profile(user)}

Вопрос: {question or 'общий расклад на ближайшее время'}

Выпавшие карты:
{cards}

Напиши толкование: ровно три части, каждая начинается с метки [1], [2], [3] на отдельной строке.
Каждая часть 2–4 предложения, 300–450 символов. В [1] обратись по имени. Если есть прошлые вопросы, свяжи с ними одной фразой.
В [3] дай вывод и один мягкий совет."""
    text = await giga.complete(prompt)
    parts = []
    for tag in ("[1]", "[2]", "[3]"):
        start = text.find(tag)
        if start == -1:
            break
        end = min([i for i in (text.find(t, start + 3) for t in ("[1]", "[2]", "[3]")) if i > start] or [len(text)])
        parts.append(text[start + 3:end].strip())
    if len(parts) != 3:
        log.warning("Unexpected reading format, falling back to split")
        chunks = [p for p in text.split("\n\n") if p.strip()]
        parts = (chunks + ["", "", ""])[:3]
    return parts


async def daily_card(user, card_id, personal):
    c = BY_ID[card_id]
    who = _profile(user) if personal else f"Знак зодиака: {user['sign']}"
    prompt = f"""{who}

Карта дня: {c['name']} ({c['keywords']}).
Напиши послание на сегодня: 3–4 предложения, до 450 символов. {'Обратись по имени и учти ситуацию человека.' if personal else 'Обращайся ко всем представителям знака.'}
Закончи одной конкретной подсказкой на день."""
    return await giga.complete(prompt, max_tokens=400)
