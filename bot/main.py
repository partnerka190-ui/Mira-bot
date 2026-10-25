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
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

from . import broadcast, config, images, llm, oferta, payments, stats
from . import texts as T
from .cards import BY_ID, DECK
from .db import DB, now_iso, parse_iso

log = logging.getLogger("mira")
router = Router()
db = DB()
rng = random.SystemRandom()
TZ = ZoneInfo(config.TZ)
CRISIS_RE = re.compile(r"суицид|покончить с собой|не хочу жить|убить себя|самоповрежд|режу себя|повеситься|бьёт меня|бьет меня|насили", re.I)
ONBOARDING = ("bait", "sphere_wait", "sphere", "name", "his_name", "question", "ritual")


def load_reels():
    """Ролики из reels/*.json: по метке ?start=r01 бот продолжает тему ролика."""
    reels = {}
    for path in sorted((config.BASE_DIR / "reels").glob("*.json")):
        data = json.loads(path.read_text())
        reels["r" + path.name[:2]] = {
            "format": data["format"],
            "topic": data["scenes"][0].get("text", "").replace("\n", " "),
            "cards": [s["card"] for s in data["scenes"] if s["type"] == "reveal"],
            "sphere": data.get("sphere"),
        }
    return reels


REELS = load_reels()


def today():
    return datetime.now(TZ).date().isoformat()


TESTER_IDS = {int(t) for t in config.TESTERS if t.isdigit()}


def mark_tester(tg_user):
    if tg_user and tg_user.username and tg_user.username.lower() in config.TESTERS:
        TESTER_IDS.add(tg_user.id)
    return tg_user and tg_user.id in TESTER_IDS


async def tester_middleware(handler, event, data):
    if not mark_tester(event.from_user) and event.from_user:
        await db.seen(event.from_user.id)
    return await handler(event, data)


async def track(uid, event, detail=None):
    """Пишет шаг воронки в журнал для /stats. Тестировщиков не считаем."""
    if uid in TESTER_IDS:
        return
    try:
        await db.log(uid, event, detail)
    except Exception:
        log.exception("event log failed")


def has_access(user):
    if user["id"] in TESTER_IDS:
        return True
    until = parse_iso(user["paid_until"])
    return user["status"] in ("trial", "active") and until and until > datetime.now(timezone.utc)


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=T.MENU_ASK), KeyboardButton(text=T.MENU_PARTNER)],
              [KeyboardButton(text=T.MENU_YESNO), KeyboardButton(text=T.MENU_DAILY)],
              [KeyboardButton(text=T.MENU_PREMIUM), KeyboardButton(text=T.MENU_SUB)]],
    resize_keyboard=True,
)
LOCKED_KB = kb([(T.LOCKED_BTN, "pay:trial")])


def photo(data, name="mira.jpg"):
    return BufferedInputFile(data, name)


async def card_photo(bot, chat_id, card_id, caption, **kw):
    """Одна карта с подписью; длинный текст уходит отдельным сообщением, чтобы не обрезался."""
    path = config.CARDS_DIR / f"{card_id}.png"
    if len(caption) <= 1024:
        return await bot.send_photo(chat_id, FSInputFile(path), caption=caption, **kw)
    await bot.send_photo(chat_id, FSInputFile(path))
    return await bot.send_message(chat_id, caption, **kw)


def key_of(user):
    """Карта-ключ, если она ещё не закрылась."""
    if not user.get("pending_key"):
        return None
    key = json.loads(user["pending_key"])
    if datetime.now(timezone.utc) - parse_iso(key["at"]) > timedelta(days=config.KEY_TTL_DAYS):
        return None
    return key


def is_partner_topic(user):
    return user.get("topic") in ("love", "ex")


# ---------- Вход: плюшка ----------

@router.message(CommandStart())
async def start(msg: Message, command: CommandObject):
    payload = (command.args or "").strip()
    if payload == "oferta":
        await send_oferta(msg.bot, msg.chat.id)
        return
    if msg.from_user.id in TESTER_IDS:
        await db.delete_user(msg.from_user.id)  # тестировщик каждый раз проходит воронку с нуля
    user = await db.user(msg.from_user.id)
    if user:
        await track(user["id"], "start", payload or "direct")
    if user and has_access(user):
        await msg.answer(T.WELCOME_BACK.format(name=user["name"] or ""), reply_markup=MENU)
        return
    if user and user["step"] not in ONBOARDING and user["free_reading_used"]:
        await msg.answer(T.WELCOME_BACK.format(name=user["name"] or ""), reply_markup=MENU)
        if key_of(user):
            await send_key_offer(msg.bot, user, key_of(user)["q"])
        return
    await db.create_user(msg.from_user.id, payload or "direct")
    if not user:
        await track(msg.from_user.id, "start", payload or "direct")
    await db.update(msg.from_user.id, step="bait", blocked=0)
    await send_bait(msg.bot, msg.from_user.id, payload)


async def send_bait(bot, uid, payload):
    reel_key, _, pick = payload.partition("_")
    reel = REELS.get(reel_key)
    if reel and reel["sphere"]:
        await db.update(uid, topic=reel["sphere"], sphere=T.SPHERES[reel["sphere"]][1])
    if reel and reel["format"] == "pick3" and len(reel["cards"]) == 3:
        if pick in ("1", "2", "3"):
            await reveal_bait(bot, uid, reel["cards"][int(pick) - 1], reel["topic"])
            return
        data = await asyncio.to_thread(images.spread, reel["cards"], ["1", "2", "3"], (0, 1, 2))
        await bot.send_photo(uid, photo(data), caption=T.BAIT_REEL.format(topic=reel["topic"]),
                             reply_markup=kb([(str(i + 1), f"bait:{reel_key}:{i}") for i in range(3)]))
    elif reel and reel["format"] == "sign" and reel["cards"]:
        await bot.send_photo(uid, FSInputFile(config.CARDS_DIR / "back.png"), caption=T.BAIT_SIGN,
                             reply_markup=kb([("✨ Открыть карту", f"bait:{reel_key}:0")]))
    else:
        data = await asyncio.to_thread(images.spread, ["back"] * 3, ["1", "2", "3"], (0, 1, 2))
        await bot.send_photo(uid, photo(data), caption=T.BAIT_GENERIC,
                             reply_markup=kb([(str(i + 1), f"bait:x:{i}") for i in range(3)]))


@router.callback_query(F.data.startswith("bait:"))
async def bait(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    if not user or user["step"] != "bait":
        return
    await db.update(user["id"], step="sphere_wait")
    _, reel_key, idx = cb.data.split(":")
    reel = REELS.get(reel_key)
    card_id = reel["cards"][int(idx)] if reel and reel["cards"] else rng.choice(DECK)["id"]
    try:
        await cb.message.edit_caption(caption=T.BAIT_OPENING)
    except TelegramBadRequest:
        pass
    await reveal_bait(cb.bot, user["id"], card_id, reel["topic"] if reel else None)


async def reveal_bait(bot, uid, card_id, topic):
    await db.update(uid, step="sphere_wait", bait_card=card_id)
    await track(uid, "bait", card_id)
    await bot.send_chat_action(uid, "upload_photo")
    try:
        text = await llm.bait_message(card_id, topic)
    except Exception:
        log.exception("bait message failed")
        text = T.BAIT_FALLBACK.format(keywords=BY_ID[card_id]["keywords"])
    await card_photo(bot, uid, card_id, T.BAIT_REVEAL.format(name=BY_ID[card_id]["name"], text=text))
    await asyncio.sleep(2.5)
    await bot.send_message(uid, T.BAIT_HOOK, reply_markup=kb([(T.BAIT_HOOK_BTN, "go")]))


# ---------- Знакомство ----------

@router.callback_query(F.data == "go")
async def go(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=None)
    await track(user["id"], "go")
    if user["topic"]:
        await db.update(user["id"], step="name")
        await cb.message.answer(T.ASK_NAME)
    else:
        await ask_sphere(cb.message)


async def ask_sphere(msg):
    await db.update(msg.chat.id, step="sphere")
    s = T.SPHERES
    await msg.answer(T.ASK_SPHERE, reply_markup=kb(
        [(s["love"][0], "sphere:love")], [(s["ex"][0], "sphere:ex")],
        [(s["future"][0], "sphere:future"), (s["money"][0], "sphere:money")]))


@router.callback_query(F.data.startswith("sphere:"))
async def sphere(cb: CallbackQuery):
    await cb.answer()
    key = cb.data.split(":")[1]
    await db.update(cb.from_user.id, topic=key, sphere=T.SPHERES[key][1], step="name")
    await track(cb.from_user.id, "sphere", key)
    await cb.message.edit_text(T.ASK_NAME)


@router.callback_query(F.data == "skiphis")
async def skip_his(cb: CallbackQuery):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await ask_question(cb.bot, await db.user(cb.from_user.id))


async def ask_question(bot, user):
    await db.update(user["id"], step="question")
    questions = T.QUESTIONS.get(user["topic"] or "future", T.QUESTIONS["future"])
    await bot.send_message(user["id"], T.ASK_QUESTION.format(name=user["name"]),
                           reply_markup=kb(*[[(q, f"q:{i}")] for i, q in enumerate(questions)]))


@router.callback_query(F.data.startswith("q:"))
async def preset_question(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    if user["step"] != "question":
        return
    q = T.QUESTIONS.get(user["topic"] or "future", T.QUESTIONS["future"])[int(cb.data[2:])]
    await cb.message.edit_reply_markup(reply_markup=None)
    await start_ritual(cb.bot, user, q, "free")


# ---------- Ритуал: перемешать и выбрать три карты ----------

async def start_ritual(bot, user, question, flow, full=True):
    await db.update(user["id"], pending_q=question, picks="[]", flow=flow, step="ritual")
    await track(user["id"], "question" if flow == "free" else f"ask_{flow}")
    if full:
        await bot.send_message(user["id"], T.RITUAL.format(q=question), reply_markup=kb([(T.RITUAL_BTN, "shuffle")]))
    else:
        await send_fan(bot, user["id"])


@router.callback_query(F.data == "shuffle")
async def shuffle(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    if user["step"] != "ritual":
        return
    for frame in T.SHUFFLE:
        try:
            await cb.message.edit_text(frame)
        except TelegramBadRequest:
            pass
        await asyncio.sleep(0.6)
    await cb.message.delete()
    await send_fan(cb.bot, user["id"])


def fan_kb(picks):
    mark = lambda i: "✦" if i in picks else str(i + 1)  # noqa: E731
    return kb([(mark(i), f"pick:{i}") for i in range(4)], [(mark(i), f"pick:{i}") for i in range(4, 7)])


async def send_fan(bot, uid):
    data = await asyncio.to_thread(images.fan, 7)
    await bot.send_photo(uid, photo(data), caption=T.PICK.format(n=0), reply_markup=fan_kb([]))


@router.callback_query(F.data.startswith("pick:"))
async def pick(cb: CallbackQuery):
    user = await db.user(cb.from_user.id)
    if user["step"] != "ritual":
        await cb.answer()
        return
    picks = json.loads(user["picks"] or "[]")
    i = int(cb.data[5:])
    if i in picks:
        await cb.answer("Эта карта уже выбрана")
        return
    picks.append(i)
    await db.update(user["id"], picks=json.dumps(picks))
    await cb.answer()
    if len(picks) < 3:
        await cb.message.edit_caption(caption=T.PICK.format(n=len(picks)), reply_markup=fan_kb(picks))
        return
    await db.update(user["id"], step="reading")
    await cb.message.edit_caption(caption=T.PICK_DONE, reply_markup=None)
    await do_reading(cb.bot, await db.user(user["id"]))


@router.callback_query(F.data == "retry")
async def retry(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    if user["step"] == "reading":
        await cb.message.delete()
        await do_reading(cb.bot, user)


# ---------- Расклад ----------

def spend(user):
    """Списывает один расклад из лимита. Возвращает, чем заплатили, или None, если расклад недоступен."""
    if user["id"] in TESTER_IDS:
        return {}, {}
    if has_access(user):
        count = user["questions_today"] if user["questions_date"] == today() else 0
        if count >= config.QUESTIONS_PER_DAY:
            return None
        return {"questions_date": today(), "questions_today": count + 1}, {"questions_today": count}
    if user["single_credits"] > 0:
        return {"single_credits": user["single_credits"] - 1}, {"single_credits": user["single_credits"]}
    return None


async def do_reading(bot, user):
    uid, flow, question = user["id"], user["flow"], user["pending_q"]
    refund = None
    if flow != "free":
        spent = spend(user)
        if not spent:
            await db.update(uid, step="done")
            await bot.send_message(uid, T.LIMIT if has_access(user) else T.LOCKED, reply_markup=None if has_access(user) else LOCKED_KB)
            return
        await db.update(uid, **spent[0])
        refund = spent[1]
    partner = flow == "partner" or (flow == "free" and is_partner_topic(user))
    positions = T.POSITIONS_PARTNER if partner else T.POSITIONS
    drawn = [c["id"] for c in rng.sample(DECK, 4)]
    cards, key_card = drawn[:3], drawn[3] if flow == "free" else None

    status = await bot.send_message(uid, T.READING_WAIT[0])
    task = asyncio.create_task(llm.three_card_reading(user, question, cards, positions, key_card))
    for frame in T.READING_WAIT[1:]:
        await asyncio.sleep(2.5)
        if task.done():
            break
        await status.edit_text(frame)
    try:
        parts = await task
        image = await asyncio.to_thread(images.spread, cards, T.LABELS_PARTNER if partner else T.LABELS)
    except Exception:
        log.exception("reading failed")
        if refund:
            await db.update(uid, **refund)
        await status.edit_text(T.ERROR, reply_markup=kb([("🔁 Попробовать ещё раз", "retry")]))
        return
    await status.delete()

    await bot.send_photo(uid, photo(image), caption=T.SPREAD_CAPTION.format(q=question))
    names = [BY_ID[c]["name"] for c in cards]
    await bot.send_message(uid, T.READING.format(p0=positions[0], p1=positions[1], p2=positions[2],
                                                 n0=names[0], n1=names[1], n2=names[2],
                                                 t0=parts[0], t1=parts[1], t2=parts[2]))
    await db.remember(uid, question, names)
    await db.update(uid, step="done", last_question_at=now_iso(), followup_sent=0)

    if flow != "free":
        await bot.send_message(uid, "Можешь задать уточняющий вопрос — я помню контекст 🌙", reply_markup=MENU)
        return
    q, a = parts[3] or "Хочешь узнать, чем всё закончится на самом деле?", parts[4]
    await track(uid, "paywall")
    await db.update(uid, free_reading_used=1, paywall_at=now_iso(), nurture_stage=0,
                    pending_key=json.dumps({"card": key_card, "q": q, "a": a, "at": now_iso()}, ensure_ascii=False))
    await asyncio.sleep(3)
    await bot.send_message(uid, T.HOOK_INTRO.format(name=user["name"]) + f"\n\n<b>{q}</b>")
    await asyncio.sleep(1.5)
    await send_key_offer(bot, user, q, intro=False)


async def send_key_offer(bot, user, q, intro=True):
    if intro:
        await bot.send_message(user["id"], f"<b>{q}</b>")
    caption = T.KEY_CAPTION + "\n\n" + T.KEY_LEGAL.format(url=await oferta.url())
    await bot.send_photo(user["id"], FSInputFile(config.CARDS_DIR / "back.png"), caption=caption,
                         reply_markup=kb([(T.KEY_BTN, "pay:trial")], [(T.KEY_LATER, "later")]))


async def reveal_key(bot, uid):
    user = await db.user(uid)
    key = key_of(user)
    if not key:
        return False
    answer = key["a"]
    if not answer:
        try:
            answer = await llm.key_answer(user, key["q"], key["card"])
        except Exception:
            log.exception("key answer failed")
            answer = T.BAIT_FALLBACK.format(keywords=BY_ID[key["card"]]["keywords"])
    await card_photo(bot, uid, key["card"], T.KEY_REVEAL.format(name=BY_ID[key["card"]]["name"], q=key["q"], a=answer))
    await db.update(uid, pending_key=None)
    return True


@router.callback_query(F.data == "later")
async def later(cb: CallbackQuery):
    await cb.answer()
    await track(cb.from_user.id, "later")
    await cb.message.answer(T.KEY_SINGLE.format(days=config.KEY_TTL_DAYS),
                            reply_markup=kb([(T.KEY_SINGLE_BTN, "pay:single")], [(T.KEY_BTN, "pay:trial")]))


# ---------- Меню ----------

async def locked(msg, user):
    """Для бесплатных пользователей: напоминаем про карту-ключ или предлагаем доступ."""
    key = key_of(user)
    if key:
        await send_key_offer(msg.bot, user, key["q"])
    else:
        await msg.answer(T.LOCKED, reply_markup=LOCKED_KB)


@router.message(F.text == T.MENU_ASK)
async def menu_ask(msg: Message):
    user = await db.user(msg.from_user.id)
    if not (has_access(user) or user["single_credits"] > 0):
        await locked(msg, user)
        return
    await db.update(user["id"], step="ask")
    await msg.answer(T.ASK)


@router.message(F.text == T.MENU_PARTNER)
async def menu_partner(msg: Message):
    user = await db.user(msg.from_user.id)
    if not has_access(user):
        await locked(msg, user)
        return
    await db.update(user["id"], step="partner")
    await msg.answer(T.ASK_PARTNER)


@router.message(F.text == T.MENU_YESNO)
async def menu_yesno(msg: Message):
    user = await db.user(msg.from_user.id)
    if not has_access(user):
        await locked(msg, user)
        return
    await db.update(user["id"], step="yesno")
    await msg.answer(T.ASK_YESNO)


@router.message(F.text == T.MENU_DAILY)
async def menu_daily(msg: Message):
    await send_daily(msg.bot, await db.user(msg.from_user.id))


@router.message(F.text == T.MENU_PREMIUM)
async def menu_premium(msg: Message):
    await msg.answer(T.PREMIUM, reply_markup=kb([(T.PREMIUM_DEEP_BTN, "pay:deep")], [(T.PREMIUM_FORECAST_BTN, "pay:forecast")]))


@router.message(F.text == T.MENU_SUB)
async def menu_sub(msg: Message):
    user = await db.user(msg.from_user.id)
    link = T.OFERTA_LINK.format(url=await oferta.url())
    if has_access(user):
        until = parse_iso(user["paid_until"]).astimezone(TZ).strftime("%d.%m.%Y")
        auto = user["autopay"] and user["payment_method_id"]
        rows = [[("Отключить автопродление", "autopay:off")], [("Удалить способ оплаты", "card:del")]] if auto \
            else [[(f"Продлить за {config.PRICE_MONTH} ₽", "pay:month")]]
        await msg.answer(T.SUB_ACTIVE.format(until=until, auto="включено" if auto else "выключено") + "\n\n" + link,
                         reply_markup=kb(*rows), disable_web_page_preview=True)
    elif user["status"] == "expired":
        await msg.answer(T.SUB_EXPIRED + "\n\n" + link, reply_markup=kb([(f"30 дней за {config.PRICE_MONTH} ₽", "pay:month")]),
                         disable_web_page_preview=True)
    else:
        await msg.answer(T.SUB_FREE + "\n\n" + T.LOCKED + "\n\n" + link, reply_markup=LOCKED_KB, disable_web_page_preview=True)


@router.callback_query(F.data.in_({"autopay:off", "card:del"}))
async def autopay_off(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    fields = {"autopay": 0}
    if cb.data == "card:del":
        fields["payment_method_id"] = None
    await db.update(user["id"], **fields)
    until = parse_iso(user["paid_until"]).astimezone(TZ).strftime("%d.%m.%Y") if user["paid_until"] else "—"
    await cb.message.edit_text((T.CARD_DELETED if cb.data == "card:del" else T.AUTOPAY_OFF).format(until=until))


@router.message(Command("cancel"))
async def cancel(msg: Message):
    user = await db.user(msg.from_user.id)
    if not user:
        return
    await db.update(user["id"], autopay=0, payment_method_id=None)
    until = parse_iso(user["paid_until"]).astimezone(TZ).strftime("%d.%m.%Y") if user["paid_until"] else "—"
    await msg.answer(T.CARD_DELETED.format(until=until))


@router.message(Command("oferta"))
async def oferta_cmd(msg: Message):
    await send_oferta(msg.bot, msg.chat.id)


async def send_oferta(bot, chat_id):
    body, chunk = oferta.text(), ""
    for block in body.split("\n\n"):
        if len(chunk) + len(block) > 3800:
            await bot.send_message(chat_id, chunk, parse_mode=None)
            chunk = ""
        chunk += block + "\n\n"
    await bot.send_message(chat_id, chunk, parse_mode=None)


# ---------- Свободный текст по шагам ----------

@router.message(F.text)
async def text(msg: Message):
    user = await db.user(msg.from_user.id)
    if not user:
        await msg.answer(T.START_OVER)
        return
    if CRISIS_RE.search(msg.text):
        await msg.answer(T.CRISIS)
        return
    step, value = user["step"], msg.text.strip()
    if step == "name":
        name = value.split()[0][:30].capitalize()
        await db.update(user["id"], name=name)
        user["name"] = name
        if is_partner_topic(user):
            await db.update(user["id"], step="his_name")
            await msg.answer(T.ASK_HIS_NAME.format(name=name), reply_markup=kb([(T.SKIP_HIS_NAME, "skiphis")]))
        else:
            await ask_question(msg.bot, user)
    elif step == "his_name":
        await db.update(user["id"], partner_name=value[:40])
        await ask_question(msg.bot, await db.user(user["id"]))
    elif step == "question":
        await start_ritual(msg.bot, user, value[:300], "free")
    elif step == "ritual":
        await msg.answer("Выбери карты на картинке выше 👆")
    elif step in ("bait", "sphere_wait"):
        await send_bait(msg.bot, user["id"], "")
        await db.update(user["id"], step="bait")
    elif step == "sphere":
        await ask_sphere(msg)
    elif step == "partner":
        await db.update(user["id"], partner_name=value[:40])
        await start_ritual(msg.bot, user, f"Что {value[:40]} чувствует ко мне?", "partner", full=False)
    elif step == "yesno":
        await yes_no(msg, user, value[:300])
    elif step == "deep_q" and user["deep_credits"] > 0:
        await big_spread(msg.bot, user, value[:300], "deep")
    elif has_access(user) or user["single_credits"] > 0:
        await start_ritual(msg.bot, user, value[:300], "ask", full=False)
    else:
        await locked(msg, user)


async def yes_no(msg, user, question):
    spent = spend(user)
    if not spent:
        await msg.answer(T.LIMIT if has_access(user) else T.LOCKED)
        return
    await db.update(user["id"], step="done", **spent[0])
    card_id = rng.choice(DECK)["id"]
    try:
        answer = await llm.yes_no(user, question, card_id)
    except Exception:
        log.exception("yes/no failed")
        await db.update(user["id"], **spent[1])
        await msg.answer(T.ERROR)
        return
    await card_photo(msg.bot, user["id"], card_id, T.YESNO.format(name=BY_ID[card_id]["name"], text=answer), reply_markup=MENU)


async def big_spread(bot, user, question, kind):
    """Платные расклады: «Будущее отношений» (7 карт) и прогноз на месяц (4 карты)."""
    uid = user["id"]
    positions = T.DEEP_POSITIONS if kind == "deep" else T.FORECAST_POSITIONS
    title = "Будущее отношений" if kind == "deep" else "Прогноз на 30 дней"
    credit = "deep_credits" if kind == "deep" else "forecast_credits"
    await db.update(uid, step="done", **{credit: user[credit] - 1})
    cards = [c["id"] for c in rng.sample(DECK, len(positions))]
    status = await bot.send_message(uid, T.READING_WAIT[0])
    try:
        parts = await llm.big_reading(user, question, cards, positions)
        image = await asyncio.to_thread(images.spread, cards, positions if kind == "forecast" else [str(i + 1) for i in range(7)],
                                        (), 4)
    except Exception:
        log.exception("big reading failed")
        await db.update(uid, step="deep_q" if kind == "deep" else "done", **{credit: user[credit]})
        await status.edit_text(T.ERROR)
        return
    await status.delete()
    await bot.send_photo(uid, photo(image), caption=T.BIG_CAPTION.format(title=title))
    body = "\n\n".join(f"<b>{i + 1}. {p} — {BY_ID[c]['name']}</b>\n{t}" for i, (p, c, t) in enumerate(zip(positions, cards, parts)))
    body += f"\n\n<b>Итог</b>\n{parts[-1]}"
    for start in range(0, len(body), 4000):
        await bot.send_message(uid, body[start:start + 4000], reply_markup=MENU if start + 4000 >= len(body) else None)
    await db.remember(uid, f"{title}: {question}", [BY_ID[c]["name"] for c in cards])


# ---------- Оплата ----------

@router.callback_query(F.data.startswith("pay:"))
async def pay(cb: CallbackQuery):
    await cb.answer()
    kind = cb.data.split(":")[1]
    await track(cb.from_user.id, "pay_click", kind)
    if cb.from_user.id in TESTER_IDS and not config.YOOKASSA_SHOP_ID:
        await cb.message.answer(f"🧪 Тестовая оплата «{kind}» засчитана без денег.")
        await grant(cb.bot, cb.from_user.id, kind)
        return
    if kind == "trial":
        user = await db.user(cb.from_user.id)
        if has_access(user):
            await cb.message.answer("Подписка уже активна 💫", reply_markup=MENU)
            return
        if user["consent_at"] and user["status"] != "free":
            kind = "month"  # пробный период — один раз
        await db.update(cb.from_user.id, consent_at=now_iso(), offer_version=config.OFFER_VERSION)
    try:
        pid, url = await payments.create(cb.from_user.id, kind)
    except Exception:
        log.exception("payment create failed")
        await cb.message.answer(T.PAY_ERROR)
        return
    await db.add_payment(pid, cb.from_user.id, kind, payments.price(kind), "pending")
    text = T.PAY_LINK.format(price=payments.price(kind))
    if kind in ("trial", "month"):
        text += T.PAY_LINK_SUB + f" <a href=\"{await oferta.url()}\">Оферта</a>."
    await cb.message.answer(text, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=T.PAY_BTN.format(price=payments.price(kind)), url=url)],
        [InlineKeyboardButton(text=T.PAY_CHECK, callback_data=f"check:{pid}")]]))


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
    if method and method.get("saved") and kind in ("trial", "month"):
        fields["payment_method_id"] = method["id"]
    await track(uid, "paid", kind)
    if config.ADMIN_ID:
        await safe_send(bot, config.ADMIN_ID, f"💰 {kind}: {payments.price(kind)} ₽ от {uid} (источник: {user['source']})")

    if kind in ("trial", "month"):
        base = max(now, parse_iso(user["paid_until"]) or now)
        days = config.TRIAL_DAYS if kind == "trial" else 30
        fields.update(status=kind if kind == "trial" else "active", paid_until=(base + timedelta(days=days)).isoformat(),
                      renew_reminded=0, autopay=1, step="done")
        await db.update(uid, **fields)
        if kind == "trial" and key_of(user):
            await bot.send_message(uid, T.PAID_TRIAL)
            await reveal_key(bot, uid)
            await asyncio.sleep(2)
            await bot.send_message(uid, T.WELCOME_CLUB, reply_markup=MENU)
        else:
            await bot.send_message(uid, T.PAID_TRIAL_NOKEY if kind == "trial" else T.PAID_MONTH, reply_markup=MENU)
    elif kind == "single":
        if key_of(user):
            await bot.send_message(uid, T.PAID_SINGLE)
            await reveal_key(bot, uid)
            await bot.send_message(uid, T.LOCKED, reply_markup=LOCKED_KB)
        else:
            await db.update(uid, single_credits=user["single_credits"] + 1, step="ask")
            await bot.send_message(uid, T.PAID_SINGLE_CREDIT, reply_markup=MENU)
    elif kind == "deep":
        await db.update(uid, deep_credits=user["deep_credits"] + 1, step="deep_q")
        await bot.send_message(uid, T.PAID_DEEP)
    elif kind == "forecast":
        await db.update(uid, forecast_credits=user["forecast_credits"] + 1)
        await bot.send_message(uid, T.PAID_FORECAST)
        asyncio.create_task(big_spread(bot, await db.user(uid), "прогноз на ближайшие 30 дней", "forecast"))


# ---------- Карта дня ----------

async def send_daily(bot, user, tail=""):
    day = today()
    personal = has_access(user)
    key = str(user["id"]) if personal else "all"
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
        caption = T.DAILY_FREE.format(name=name, text=body) + tail
    markup = kb([(T.OPEN_KEY_BTN, "pay:trial")]) if tail else None
    await card_photo(bot, user["id"], card_id, caption, reply_markup=markup)
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


@router.callback_query(F.data == "resume")
async def resume(cb: CallbackQuery):
    await cb.answer()
    user = await db.user(cb.from_user.id)
    step = user["step"]
    if step in ("bait", "sphere_wait"):
        await db.update(user["id"], step="bait")
        await send_bait(cb.bot, user["id"], "")
    elif step == "sphere":
        await ask_sphere(cb.message)
    elif step == "name":
        await cb.message.answer(T.ASK_NAME)
    elif step == "his_name":
        await cb.message.answer(T.ASK_HIS_NAME.format(name=user["name"]), reply_markup=kb([(T.SKIP_HIS_NAME, "skiphis")]))
    elif step == "question":
        await ask_question(cb.bot, user)
    elif step == "ritual":
        await send_fan(cb.bot, user["id"])
        await db.update(user["id"], picks="[]")


async def nurture(bot, now, local):
    day_time = 10 <= local.hour < 21
    # Не дошла до расклада — одно напоминание через 3 часа
    if day_time:
        for user in await db.users_where("paywall_at IS NULL AND nurture_stage=0 AND status='free' AND step IN (%s)"
                                         % ",".join("?" * len(ONBOARDING)), ONBOARDING):
            if now - parse_iso(user["created_at"]) > timedelta(hours=3):
                await safe_send(bot, user["id"], T.UNFINISHED, reply_markup=kb([(T.UNFINISHED_BTN, "resume")]))
                await db.update(user["id"], nurture_stage=1)

    # Получила расклад, но не открыла карту-ключ
    for user in await db.users_where("status='free' AND pending_key IS NOT NULL AND nurture_stage<4"):
        key = json.loads(user["pending_key"])
        hours = (now - parse_iso(key["at"])).total_seconds() / 3600
        stage = user["nurture_stage"] + 1
        due = {1: 1, 2: 20, 3: 72, 4: (config.KEY_TTL_DAYS - 1) * 24}[stage]
        if hours < due or not (9 <= local.hour < 22 if stage == 1 else day_time):
            continue
        name = user["name"] or ""
        try:
            if stage == 1:
                await safe_send(bot, user["id"], T.NURTURE_1.format(name=name, q=key["q"]),
                                reply_markup=kb([(T.OPEN_KEY_BTN, "pay:trial")], [(T.KEY_SINGLE_BTN, "pay:single")]))
            elif stage == 2:
                days = max(1, config.KEY_TTL_DAYS - int(hours // 24))
                await send_daily(bot, user, T.NURTURE_2_TAIL.format(days=days))
            elif stage == 3:
                who = user["partner_name"] or "он"
                await safe_send(bot, user["id"], T.NURTURE_3.format(name=name, who=who), reply_markup=kb([(T.OPEN_KEY_BTN, "pay:trial")]))
            else:
                await safe_send(bot, user["id"], T.NURTURE_4.format(q=key["q"]),
                                reply_markup=kb([(T.OPEN_KEY_BTN, "pay:trial")], [(T.KEY_SINGLE_BTN, "pay:single")]))
        except TelegramForbiddenError:
            await db.update(user["id"], blocked=1)
        except Exception:
            log.exception("nurture failed for %s", user["id"])
        await db.update(user["id"], nurture_stage=stage)

    # Карта-ключ закрывается через KEY_TTL_DAYS
    for user in await db.users_where("pending_key IS NOT NULL"):
        if not key_of(user):
            await db.update(user["id"], pending_key=None)


async def tick(bot):
    now = datetime.now(timezone.utc)
    local = now.astimezone(TZ)

    if local.hour >= config.DAILY_CARD_HOUR:
        for user in await db.users_where("status IN ('trial','active') AND (last_card_date IS NULL OR last_card_date<>?)", (today(),)):
            if not has_access(user):
                continue
            try:
                await send_daily(bot, user)
                await asyncio.sleep(0.05)
            except TelegramForbiddenError:
                await db.update(user["id"], blocked=1)
            except Exception:
                log.exception("daily card failed for %s", user["id"])

    await nurture(bot, now, local)

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
    """Платежи сверяем каждые 10 секунд, остальное — раз в минуту."""
    n = 0
    while True:
        fresh = datetime.now(timezone.utc) - timedelta(minutes=30)
        for p in await db.pending_payments():
            if n % 6 and parse_iso(p["created_at"]) < fresh:
                continue
            try:
                await sync_payment(bot, p["id"])
            except Exception:
                log.exception("payment sync failed")
        if n % 6 == 0:
            try:
                await tick(bot)
            except Exception:
                log.exception("tick failed")
        n += 1
        await asyncio.sleep(10)


async def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    await db.connect()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    if not config.BOT_USERNAME:
        config.BOT_USERNAME = (await bot.get_me()).username
    log.info("Оферта: %s", await oferta.url())
    dp = Dispatcher()
    dp.message.outer_middleware(tester_middleware)
    dp.callback_query.outer_middleware(tester_middleware)
    dp["testers"] = TESTER_IDS
    admin = lambda u: bool(mark_tester(u) or (config.ADMIN_ID and u.id == config.ADMIN_ID))
    stats.setup(db, admin)
    broadcast.setup(db, admin)
    # админские роутеры раньше основного: там есть обработчик любого текста
    dp.include_router(stats.router)
    dp.include_router(broadcast.router)
    dp.include_router(router)
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
