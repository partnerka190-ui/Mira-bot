"""ЮKassa: СБП, ЮMoney и карты на одной платёжной странице. Статусы проверяем опросом, вебхук не нужен."""
import uuid

import aiohttp

from . import config

API = "https://api.yookassa.ru/v3/payments"

DESCRIPTIONS = {
    "trial": f"Мира: {config.TRIAL_DAYS} дней доступа",
    "month": "Мира: подписка на 30 дней",
    "single": "Мира: один ответ карт",
    "deep": "Мира: большой расклад на отношения",
    "forecast": "Мира: прогноз на 30 дней",
}


def _auth():
    return aiohttp.BasicAuth(config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET_KEY)


def price(kind):
    return {"trial": config.PRICE_TRIAL, "month": config.PRICE_MONTH, "single": config.PRICE_SINGLE,
            "deep": config.PRICE_DEEP, "forecast": config.PRICE_FORECAST}[kind]


async def _post(payload):
    headers = {"Idempotence-Key": str(uuid.uuid4())}
    async with aiohttp.ClientSession(auth=_auth(), timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.post(API, json=payload, headers=headers) as r:
            data = await r.json()
            if r.status >= 400:
                raise RuntimeError(f"YooKassa {r.status}: {data}")
            return data


async def create(user_id, kind):
    """Платёж с переходом на страницу ЮKassa. Возвращает (id, ссылка на оплату)."""
    payload = {
        "amount": {"value": f"{price(kind)}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"https://t.me/{config.BOT_USERNAME}"},
        "description": DESCRIPTIONS[kind],
        "metadata": {"user_id": str(user_id), "kind": kind},
    }
    if config.YOOKASSA_AUTOPAY and kind in ("trial", "month"):
        payload["save_payment_method"] = True
    data = await _post(payload)
    return data["id"], data["confirmation"]["confirmation_url"]


async def charge_saved(user_id, payment_method_id):
    """Автосписание продления по сохранённому способу оплаты."""
    payload = {
        "amount": {"value": f"{config.PRICE_MONTH}.00", "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": DESCRIPTIONS["month"],
        "metadata": {"user_id": str(user_id), "kind": "month"},
    }
    return await _post(payload)


async def get(payment_id):
    async with aiohttp.ClientSession(auth=_auth(), timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.get(f"{API}/{payment_id}") as r:
            return await r.json()
