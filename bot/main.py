import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

from . import config, llm, payments
from . import texts as T
from .cards import BY_ID, DECK
from .db import DB, now_iso, parse_iso

log = logging.getLogger("mira")
router = Router()
db = DB()
rng = random.SystemRandom()
TZ = ZoneInfo(config.TZ)

SIGNS = [  # (до месяца, до дня включительно, знак)
    (1, 19, "Козерог"), (2, 18, "Водолей"), (3, 20, "Рыбы"), (4, 19, "Овен"), (5, 20, "Телец"),
    (6, 20, "Близнецы"), (7, 22, "Рак"), (8, 22, "Лев"), (9, 22, "Дева"), (10, 22, "Весы"),
    (11, 21, "Скорпион"), (12, 21, "Стрелец"), (12, 31, "Козерог"),
]
SPHERES = {"love": "Любовь и отношения", "money": "Деньги", "work": "Работа и призвание", "self": "Я и моё состояние"}
CRISIS_RE = re.compile(r"суицид|покончить с собой|не хочу жить|убить себя|самоповрежд|режу себя|повеситься|бьёт меня|бьет меня|насили", re.I)


def zodiac(day, month):
    for m, d, sign in SIGNS:
        if (month, day) <= (m, d):
            return sign
    return "Козерог"


def today():
    return datetime.now(TZ).date().isoformat()


def has_access(user):
    until = parse_iso(user["paid_until"])
    return user["status"] in ("trial", "active") and until and until > datetime.now(timezone.utc)


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=T.MENU_ASK), KeyboardButton(text=T.MENU_PARTNER)],
              [KeyboardButton(text=T.MENU_DAILY), KeyboardButton(text=T.MENU_SUB)]],
    resize_keyboard=True,
)
PAY_KB = kb([(f"Открыть за {config.PRICE_TRIAL} ₽", "pay:trial")], [("Не сейчас", "later")])
PAY_KB_FULL = kb([(f"{config.TRIAL_DAYS} дней за {config.PRICE_TRIAL} ₽", "pay:trial")],
                 [(f"Один расклад — {config.PRICE_SINGLE} ₽", "pay:single")])


async def send_card(bot, chat_id, card_id, caption, back=False):
    path = config.CARDS_DIR / ("back.png" if back else f"{card_id}.png")
    if path.exists():
        await bot.send_photo(chat_id, FSInputFile(path), caption=caption[:1024])
    else:
        await bot.send_message(chat_id, caption)


# ---------- Знакомство ----------

@router.message(CommandStart())
async def start(msg: Message, command: CommandObject):
    user = await db.user(msg.from_user.id)
    if user and user["step"] == "done":
        await msg.answer(f"С возвращением, {user['name']} 🌙", reply_markup=MENU)
        return
    await db.create_user(msg.from_user.id, command.args or "direct")
    await db.update(msg.from_user.id, step="adult", blocked=0)
    await msg.answer(T.WELCOME, reply_markup=kb([("Да, мне есть 18", "adult:yes")], [("Нет", "adult:no")]))


@router.callback_query(F.data.startswith("adult:"))
async def adult(cb: CallbackQuery):
    await cb.answer()
    if cb.data == "adult:no":
        await cb.message.edit_text(T.UNDERAGE)
        return
    await db.update(cb.from_user.id, adult=1, step="name")
    await cb.message.edit_text(T.ASK_NAME)


@router.callback_query(F.data.startswith("sphere:"))
async def sphere(cb: CallbackQuery):
    await cb.answer()
    await db.update(cb.from_user.id, sphere=SPHERES[cb.data.split(":")[1]], step="question")
    await cb.message.edit_text(T.ASK_QUESTION, reply_markup=kb([("Просто расклад на сейчас", "just")]))


@router.callback_query(F.data == "just")
async def just(cb: CallbackQuery):
    await cb.answer()
    await reading(cb.bot, await db.user(cb.from_user.id), "")


# ---------- Меню ----------

@router.message(F.text == T.MENU_ASK)
async def menu_ask(msg: Message):
    user = await db.user(msg.from_user.id)
    if not can_read(user):
        await msg.answer(T.FREE_USED + "\n\n" + T.PAYWALL_GENERIC, reply_markup=PAY_KB_FULL)
        return
    await db.update(user["id"], step="question")
    await msg.answer(T.ASK_QUESTION)


@router.message(F.text == T.MENU_PARTNER)
async def menu_partner(msg: Message):
    user = await db.user(msg.from_user.id)
    if not can_read(user):
        await msg.answer(T.PAYWALL_GENERIC, reply_markup=PAY_KB_FULL)
        return
    await db.update(user["id"], step="partner")
    await msg.answer(T.ASK_PARTNER)


@router.message(F.text == T.MENU_DAILY)
async def menu_daily(msg: Message):
    user = await db.user(msg.from_user.id)
    await send_daily(msg.bot, user)


@router.message(F.text == T.MENU_SUB)
async def menu_sub(msg: Message):
    user = await db.user(msg.from_user.id)
    if has_access(user):
        until = parse_iso(user["paid_until"]).astimezone(TZ).strftime("%d.%m.%Y")
        auto = "включено" if user["autopay"] and user["payment_method_id"] else "выключено"
        buttons = [[("Отключить автопродление", "autopay:off")]] if auto == "включено" else [[(f"Продлить за {config.PRICE_MONTH} ₽", "pay:month")]]
        await msg.answer(T.SUB_ACTIVE.format(until=until, auto=auto), reply_markup=kb(*buttons))
    elif user["status"] == "expired":
        await msg.answer(T.SUB_EXPIRED, reply_markup=kb([(f"30 дней за {config.PRICE_MONTH} ₽", "pay:month")]))
    else:
        await msg.answer(T.SUB_FREE + "\n\n" + T.PAYWALL_GENERIC, reply_markup=PAY_KB_FULL)


@router.callback_query(F.data == "autopay:off")
async def autopay_off(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    await db.update(user["id"], autopay=0)
    until = parse_iso(user["paid_until"]).astimezone(TZ).strftime("%d.%m.%Y")
    await cb.message.edit_text(T.AUTOPAY_OFF.format(until=until))


@router.callback_query(F.data == "later")
async def later(cb: CallbackQuery):
    await cb.answer("Хорошо, я сохраню твою карту 🤍")


# ---------- Свободный текст по шагам ----------

@router.message(F.text)
async def text(msg: Message):
    user = await db.user(msg.from_user.id)
    if not user:
        await msg.answer("Нажми /start, чтобы начать 🌙")
        return
    if CRISIS_RE.search(msg.text):
        await msg.answer(T.CRISIS)
        return
    step = user["step"]
    if step == "adult":
        await msg.answer(T.WELCOME, reply_markup=kb([("Да, мне есть 18", "adult:yes")], [("Нет", "adult:no")]))
    elif step == "name":
        name = msg.text.strip().split()[0][:30].capitalize()
        await db.update(user["id"], name=name, step="birth")
        await msg.answer(T.ASK_BIRTH.format(name=name))
    elif step == "birth":
        m = re.fullmatch(r"\s*(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{4})\s*", msg.text)
        try:
            birth = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
        except ValueError:
            birth = None
        if not birth or not 1930 <= birth.year <= datetime.now().year - 18:
            await msg.answer(T.BAD_BIRTH)
            return
        sign = zodiac(birth.day, birth.month)
        await db.update(user["id"], birth=birth.date().isoformat(), sign=sign, step="sphere")
        await msg.answer(T.ASK_SPHERE.format(sign=sign), reply_markup=kb(
            [("❤️ Любовь", "sphere:love"), ("💰 Деньги", "sphere:money")],
            [("💼 Работа", "sphere:work"), ("🌿 Я и моё состояние", "sphere:self")]))
    elif step == "partner":
        await db.update(user["id"], partner_name=msg.text.strip()[:40], step="done")
        user = await db.user(user["id"])
        await reading(msg.bot, user, f"Что {user['partner_name']} чувствует ко мне?", theme="partner")
    elif step in ("question", "done"):
        await reading(msg.bot, user, msg.text.strip())


# ---------- Расклад ----------

def can_read(user):
    return has_access(user) or user["single_credits"] > 0 or not user["free_reading_used"]


async def reading(bot, user, question, theme=None):
    uid = user["id"]
    if not can_read(user):
        await bot.send_message(uid, T.FREE_USED + "\n\n" + T.PAYWALL_GENERIC, reply_markup=PAY_KB_FULL)
        return
    access = has_access(user)
    if access:
        count = user["questions_today"] if user["questions_date"] == today() else 0
        if count >= config.QUESTIONS_PER_DAY:
            await bot.send_message(uid, T.LIMIT)
            return
        await db.update(uid, questions_date=today(), questions_today=count + 1)
    elif user["single_credits"] > 0:
        await db.update(uid, single_credits=user["single_credits"] - 1)
        access = True
    else:
        await db.update(uid, free_reading_used=1)

    status = await bot.send_message(uid, T.SHUFFLE[0])
    cards = [c["id"] for c in rng.sample(DECK, 3)]
    task = asyncio.create_task(llm.three_card_reading(user, question, cards, theme))
    for frame in T.SHUFFLE[1:]:
        await asyncio.sleep(0.9)
        await status.edit_text(frame)
    try:
        parts = await task
    except Exception:
        log.exception("reading failed")
        if not access:
            await db.update(uid, free_reading_used=0)
        await status.edit_text(T.ERROR)
        return
    await status.delete()

    titles = T.TITLES_PARTNER if theme == "partner" else T.TITLES
    for i in range(2):
        await send_card(bot, uid, cards[i], T.CARD_CAPTION.format(title=titles[i], name=BY_ID[cards[i]]["name"], text=parts[i]))
        await asyncio.sleep(1.2)
    await db.remember(uid, question or "общий расклад", [BY_ID[c]["name"] for c in cards])
    await db.update(uid, step="done", last_question_at=now_iso(), followup_sent=0 if question else 1)

    final = T.FINAL_REVEAL.format(title=titles[2], name=BY_ID[cards[2]]["name"], text=parts[2])
    if access:
        await send_card(bot, uid, cards[2], final)
        await bot.send_message(uid, "Можешь задать уточняющий вопрос — я помню контекст 🌙", reply_markup=MENU)
    else:
        await db.update(uid, pending_final=f"{cards[2]}|{final}")
        await send_card(bot, uid, cards[2], T.PAYWALL.format(title=titles[2]), back=True)
        await bot.send_message(uid, "👇", reply_markup=PAY_KB)
        await bot.send_message(uid, "Меню всегда под рукой.", reply_markup=MENU)


# ---------- Оплата ----------

@router.callback_query(F.data.startswith("pay:"))
async def pay(cb: CallbackQuery):
    await cb.answer()
    kind = cb.data.split(":")[1]
    try:
        pid, url = await payments.create(cb.from_user.id, kind)
    except Exception:
        log.exception("payment create failed")
        await cb.message.answer(T.PAY_ERROR)
        return
    await db.add_payment(pid, cb.from_user.id, kind, payments.price(kind), "pending")
    await cb.message.answer(T.PAY_LINK, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оплатить {payments.price(kind)} ₽", url=url)],
        [InlineKeyboardButton(text="Я оплатил(а)", callback_data=f"check:{pid}")]]))


@router.callback_query(F.data.startswith("check:"))
async def check(cb: CallbackQuery):
    pid = cb.data.split(":", 1)[1]
    if not await sync_payment(cb.bot, pid):
        await cb.answer(T.PAY_PENDING, show_alert=True)
    else:
        await cb.answer()


async def sync_payment(bot, pid):
    """Сверяет статус с ЮKassa и выдаёт доступ. True, если платёж прошёл."""
    record = await db.payment(pid)
    if not record or record["status"] == "succeeded":
        return bool(record)
    data = await payments.get(pid)
    status = data.get("status")
    if status == "canceled":
        await db.set_payment_status(pid, "canceled")
    if status != "succeeded":
        return False
    await db.set_payment_status(pid, "succeeded")
    await grant(bot, record["user_id"], record["kind"], data.get("payment_method"))
    return True


async def grant(bot, uid, kind, method=None):
    user = await db.user(uid)
    now = datetime.now(timezone.utc)
    fields = {}
    if method and method.get("saved"):
        fields["payment_method_id"] = method["id"]
    if kind == "single":
        fields["single_credits"] = user["single_credits"] + 1
        msg = T.PAID_SINGLE
    else:
        base = max(now, parse_iso(user["paid_until"]) or now)
        days = config.TRIAL_DAYS if kind == "trial" else 30
        fields.update(status=kind if kind == "trial" else "active", paid_until=(base + timedelta(days=days)).isoformat(),
                      renew_reminded=0, autopay=1)
        msg = T.PAID_TRIAL if kind == "trial" else T.PAID_MONTH
    await db.update(uid, **fields)
    await bot.send_message(uid, msg, reply_markup=MENU)
    if config.ADMIN_ID:
        await safe_send(bot, config.ADMIN_ID, f"💰 Оплата {kind}: {payments.price(kind)} ₽ от {uid} (источник: {user['source']})")
    if user["pending_final"] and kind != "single":
        card_id, final = user["pending_final"].split("|", 1)
        await send_card(bot, uid, card_id, final)
        await db.update(uid, pending_final=None)


# ---------- Карта дня ----------

async def send_daily(bot, user):
    day = today()
    personal = has_access(user)
    key = str(user["id"]) if personal else user["sign"]
    cached = await db.cached(day, key)
    if cached:
        card_id, body = cached
    else:
        card_id = rng.choice(DECK)["id"]
        body = await llm.daily_card(user, card_id, personal)
        await db.cache(day, key, card_id, body)
    name = BY_ID[card_id]["name"]
    if personal:
        caption = T.DAILY_PERSONAL.format(user=user["name"], name=name, text=body)
    else:
        caption = T.DAILY_FREE.format(sign=user["sign"], name=name, text=body) + T.DAILY_FREE_CTA.format(sign=user["sign"])
    await send_card(bot, user["id"], card_id, caption)
    await db.update(user["id"], last_card_date=day)


# ---------- Планировщик ----------

async def safe_send(bot, uid, text, **kw):
    try:
        await bot.send_message(uid, text, **kw)
        await asyncio.sleep(0.05)
    except TelegramForbiddenError:
        await db.update(uid, blocked=1)
    except Exception:
        log.exception("send failed to %s", uid)


async def tick(bot):
    now = datetime.now(timezone.utc)
    local = now.astimezone(TZ)

    for p in await db.pending_payments():
        try:
            await sync_payment(bot, p["id"])
        except Exception:
            log.exception("payment sync failed")

    if local.hour >= config.DAILY_CARD_HOUR:
        for user in await db.users_where("step='done' AND (last_card_date IS NULL OR last_card_date<>?)", (today(),)):
            try:
                await send_daily(bot, user)
                await asyncio.sleep(0.05)
            except TelegramForbiddenError:
                await db.update(user["id"], blocked=1)
            except Exception:
                log.exception("daily card failed for %s", user["id"])

    # Прогрев тех, кто получил бесплатный расклад, но не оплатил: через 20 ч, 3 и 5 дней
    for user in await db.users_where("status='free' AND free_reading_used=1 AND nurture_stage<3"):
        hours = (now - parse_iso(user["created_at"])).total_seconds() / 3600
        stage = user["nurture_stage"] + 1
        if hours >= {1: 20, 2: 72, 3: 120}[stage] and 10 <= local.hour < 21:
            await safe_send(bot, user["id"], T.NURTURE[stage].format(name=user["name"]), reply_markup=PAY_KB_FULL)
            await db.update(user["id"], nurture_stage=stage)

    # Продление и окончание доступа
    for user in await db.users_where("status IN ('trial','active')"):
        until = parse_iso(user["paid_until"])
        auto = config.YOOKASSA_AUTOPAY and user["autopay"] and user["payment_method_id"]
        if until - now < timedelta(days=1) and not user["renew_reminded"]:
            if auto:
                await safe_send(bot, user["id"], T.RENEW_SOON.format(price=config.PRICE_MONTH))
            else:
                await safe_send(bot, user["id"], T.RENEW_MANUAL.format(price=config.PRICE_MONTH),
                                reply_markup=kb([(f"Продлить за {config.PRICE_MONTH} ₽", "pay:month")]))
            await db.update(user["id"], renew_reminded=1)
        if until <= now:
            if auto:
                try:
                    data = await payments.charge_saved(user["id"], user["payment_method_id"])
                    await db.add_payment(data["id"], user["id"], "month", config.PRICE_MONTH, "pending")
                    if data["status"] in ("succeeded", "pending", "waiting_for_capture"):
                        # Пока банк подтверждает списание, даём 2 часа отсрочки, чтобы не списать повторно
                        await db.update(user["id"], paid_until=(now + timedelta(hours=2)).isoformat())
                        await sync_payment(bot, data["id"])
                        continue
                except Exception:
                    log.exception("autopay failed for %s", user["id"])
            await db.update(user["id"], status="expired")
            await safe_send(bot, user["id"], T.EXPIRED, reply_markup=kb([(f"Вернуться за {config.PRICE_MONTH} ₽", "pay:month")]))

    # «Сбылось?» через 4 дня после вопроса — только подписчикам
    for user in await db.users_where("followup_sent=0 AND status IN ('trial','active')"):
        asked = parse_iso(user["last_question_at"])
        if asked and now - asked > timedelta(days=4) and 11 <= local.hour < 20:
            memory = json.loads(user["memory"] or "[]")
            if memory:
                await safe_send(bot, user["id"], T.FOLLOWUP.format(name=user["name"], q=memory[-1]["q"]))
            await db.update(user["id"], followup_sent=1)


async def scheduler(bot):
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(60)


async def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    await db.connect()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    if not config.BOT_USERNAME:
        config.BOT_USERNAME = (await bot.get_me()).username
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
