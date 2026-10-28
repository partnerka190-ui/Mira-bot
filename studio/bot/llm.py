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
    lines = [f"Имя: {user.get('name') or 'не указано'}"]
    if user.get("sign"):
        lines.append(f"Знак зодиака: {user['sign']}")
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


def _split(text, tags):
    """Режет ответ модели по меткам вида [1], [Q]. Возвращает список частей в порядке tags."""
    found = sorted((text.find(t), t) for t in tags if text.find(t) != -1)
    parts = {}
    for k, (pos, tag) in enumerate(found):
        end = found[k + 1][0] if k + 1 < len(found) else len(text)
        parts[tag] = text[pos + len(tag):end].strip()
    if len(parts) < len(tags):
        log.warning("Unexpected format: %s", text[:200])
        chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
        for t, chunk in zip(tags, chunks):
            parts.setdefault(t, chunk)
    return [parts.get(t, "") for t in tags]


async def bait_message(card_id, topic=None):
    """Короткое послание карты — «плюшка» на входе в бота."""
    c = BY_ID[card_id]
    prompt = f"""Человек только что открыл карту {c['name']} ({c['keywords']}){f' в ответ на вопрос «{topic}»' if topic else ''}.
Напиши послание этой карты: 3 предложения, до 350 символов, на «ты», образно и лично, как будто карта видит его ситуацию.
Не задавай вопросов и не предлагай расклад."""
    return await giga.complete(prompt, max_tokens=250)


async def three_card_reading(user, question, card_ids, positions, key_card=None):
    """Толкование трёх карт. С key_card дополнительно пишет интригующий вопрос [Q] и скрытый ответ [A] через карту-ключ."""
    cards = "\n".join(_card_line(f"Карта {i + 1} — {p.lower()}", cid) for i, (p, cid) in enumerate(zip(positions, card_ids)))
    hook = ""
    tags = ["[1]", "[2]", "[3]"]
    if key_card:
        k = BY_ID[key_card]
        tags += ["[Q]", "[A]"]
        hook = f"""
[Q] — интрига: 1–2 предложения от лица Миры. Скажи, что в раскладе есть ещё одна важная деталь, которую карты показали,
и задай вопрос, на который человеку очень захочется узнать ответ. Деталь вытекает из карт и вопроса: например, скрытые чувства
другого человека, решение, которое он откладывает, человек или событие из прошлого, конкретный срок перемен, шанс, который скоро появится.
Будь конкретной и лично про эту ситуацию. Не раскрывай ответ. Без страха и угроз: никаких порчи, болезней, смерти, измены как факта.
Закончи вопросом вида «Хочешь узнать…?».
[A] — ответ на этот вопрос через карту-ключ {k['name']} ({k['keywords']}): 4–6 предложений, 500–750 символов, конкретно и тепло,
с примерным сроком и одним практическим советом."""
    prompt = f"""Профиль:
{_profile(user)}

Вопрос: {question or 'общий расклад на ближайшее время'}

Выпавшие карты:
{cards}

Напиши толкование. Каждая часть начинается с метки на отдельной строке: {', '.join(tags)}.
[1], [2], [3] — по 2–3 предложения, 250–380 символов, про свою карту и позицию. В [1] обратись по имени.
Если есть прошлые вопросы, свяжи с ними одной фразой. В [3] дай вывод.{hook}"""
    text = await giga.complete(prompt, max_tokens=1300 if key_card else 900)
    return _split(text, tags)


async def key_answer(user, question, card_id):
    c = BY_ID[card_id]
    prompt = f"""Профиль:
{_profile(user)}

Вопрос: {question}
Карта-ключ: {c['name']} ({c['keywords']}).
Ответь на вопрос через эту карту: 4–6 предложений, 500–750 символов, конкретно и тепло, с примерным сроком и одним практическим советом."""
    return await giga.complete(prompt, max_tokens=600)


async def big_reading(user, question, card_ids, positions):
    """Премиум-расклад: по абзацу на карту и итог."""
    cards = "\n".join(_card_line(f"Карта {i + 1} — {p.lower()}", cid) for i, (p, cid) in enumerate(zip(positions, card_ids)))
    tags = [f"[{i + 1}]" for i in range(len(card_ids))] + ["[И]"]
    prompt = f"""Профиль:
{_profile(user)}

Вопрос: {question or 'будущее отношений'}

Расклад:
{cards}

Напиши глубокое толкование. Каждая часть начинается с метки на отдельной строке: {', '.join(tags)}.
Части с номерами — по 2–3 предложения про карту в её позиции. [И] — итог расклада: 4–5 предложений, примерные сроки и два практических совета."""
    text = await giga.complete(prompt, max_tokens=1800)
    return _split(text, tags)


async def yes_no(user, question, card_id):
    c = BY_ID[card_id]
    prompt = f"""Профиль:
{_profile(user)}

Вопрос «да или нет»: {question}
Карта: {c['name']} ({c['keywords']}).
Первой строкой напиши одно из: «Да», «Скорее да», «Скорее нет», «Нет», «Пока неясно» — по смыслу карты.
Дальше 2–3 предложения, почему, и что поможет."""
    return await giga.complete(prompt, max_tokens=300)


async def daily_card(user, card_id, personal):
    c = BY_ID[card_id]
    who = _profile(user) if personal else "Общая карта дня для всех читателей."
    prompt = f"""{who}

Карта дня: {c['name']} ({c['keywords']}).
Напиши послание на сегодня: 3–4 предложения, до 450 символов. {'Обратись по имени и учти ситуацию человека.' if personal else 'Обращайся к читательнице на «ты».'}
Закончи одной конкретной подсказкой на день."""
    return await giga.complete(prompt, max_tokens=400)
