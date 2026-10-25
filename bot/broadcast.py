"""Рассылка по базе для админа: /broadcast → прислать сообщение → выбрать аудиторию → предпросмотр → подтвердить."""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

router = Router()
log = logging.getLogger("mira.broadcast")
db = None
is_admin = None
drafts = {}  # admin_id -> {"stage": "wait" | "ready", "chat": id, "msg": id, "aud": ...}
running = set()

AUDIENCES = {
    "all": ("Вся база", "1"),
    "subs": ("Подписчики (пробный и платный)", "status IN ('trial','active') AND paid_until > :now"),
    "free": ("Без подписки", "NOT (status IN ('trial','active') AND paid_until > :now)"),
    "warm": ("Дошли до расклада, но не оплатили", "free_reading_used=1 AND NOT (status IN ('trial','active') AND paid_until > :now)"),
    "expired": ("Подписка закончилась", "status='expired'"),
}
RATE = 20  # сообщений в секунду, лимит Telegram — 30


def setup(database, admin_check):
    global db, is_admin
    db, is_admin = database, admin_check


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


async def audience(key, exclude):
    where = AUDIENCES[key][1]
    rows = await db.rows(f"SELECT id FROM users WHERE blocked=0 AND {where}", {"now": datetime.now(timezone.utc).isoformat()})
    return [r["id"] for r in rows if r["id"] != exclude]


def waiting(msg: Message):
    return msg.from_user and drafts.get(msg.from_user.id, {}).get("stage") == "wait"


@router.message(Command("broadcast"))
async def start(msg: Message):
    if not is_admin(msg.from_user):
        return
    if msg.from_user.id in running:
        await msg.answer("Рассылка уже идёт, дождись отчёта.")
        return
    drafts[msg.from_user.id] = {"stage": "wait"}
    await msg.answer("📣 <b>Рассылка</b>\n\nПришли сообщение, которое нужно разослать: текст, фото или видео с подписью, "
                     "можно с эмодзи и форматированием. Я покажу, как оно будет выглядеть, и спрошу, кому отправить.\n\n"
                     "Передумал — /cancel_broadcast")


@router.message(Command("cancel_broadcast"))
async def cancel(msg: Message):
    if drafts.pop(msg.from_user.id, None):
        await msg.answer("Рассылка отменена.")


@router.message(waiting)
async def got_message(msg: Message):
    drafts[msg.from_user.id] = {"stage": "ready", "chat": msg.chat.id, "msg": msg.message_id}
    rows = [[(f"{name}", f"bc:aud:{key}")] for key, (name, _) in AUDIENCES.items()]
    await msg.answer("Кому отправить?", reply_markup=kb(*rows, [("✖️ Отмена", "bc:cancel")]))


@router.callback_query(F.data.startswith("bc:"))
async def on_button(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id
    draft = drafts.get(uid)
    if not is_admin(cb.from_user) or not draft or draft.get("stage") != "ready":
        return
    parts = cb.data.split(":")
    if parts[1] == "cancel":
        drafts.pop(uid, None)
        await cb.message.edit_text("Рассылка отменена.")
        return
    if parts[1] == "aud":
        key = parts[2]
        ids = await audience(key, uid)
        draft["aud"] = key
        await cb.message.edit_text("Вот так сообщение увидят люди 👇")
        await cb.bot.copy_message(uid, draft["chat"], draft["msg"])
        await cb.message.answer(
            f"Аудитория: <b>{AUDIENCES[key][0]}</b> — <b>{len(ids)}</b> чел.\n"
            f"Отправка займёт около {max(1, len(ids) // RATE)} сек. Отменить после старта нельзя.",
            reply_markup=kb([(f"✅ Отправить {len(ids)} людям", "bc:go")], [("↩️ Другая аудитория", "bc:back"), ("✖️ Отмена", "bc:cancel")]))
        return
    if parts[1] == "back":
        rows = [[(name, f"bc:aud:{key}")] for key, (name, _) in AUDIENCES.items()]
        await cb.message.edit_text("Кому отправить?", reply_markup=kb(*rows, [("✖️ Отмена", "bc:cancel")]))
        return
    if parts[1] == "go" and draft.get("aud"):
        drafts.pop(uid, None)
        ids = await audience(draft["aud"], uid)
        await cb.message.edit_text(f"🚀 Рассылка запущена: {len(ids)} чел. Пришлю отчёт, когда закончу.")
        running.add(uid)
        asyncio.create_task(send_all(cb.bot, uid, draft["chat"], draft["msg"], ids))


async def send_all(bot, admin, chat, msg_id, ids):
    ok = blocked = failed = 0
    try:
        for n, uid in enumerate(ids, 1):
            for attempt in range(3):
                try:
                    await bot.copy_message(uid, chat, msg_id)
                    ok += 1
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramForbiddenError:
                    blocked += 1
                    await db.update(uid, blocked=1)
                    break
                except TelegramBadRequest:
                    failed += 1
                    break
                except Exception:
                    log.exception("broadcast to %s failed", uid)
                    failed += 1
                    break
            await asyncio.sleep(1 / RATE)
            if n % 500 == 0:
                await bot.send_message(admin, f"…отправлено {n} из {len(ids)}")
    finally:
        running.discard(admin)
        await bot.send_message(admin, f"✅ <b>Рассылка завершена</b>\n\nДоставлено: <b>{ok}</b>\n"
                                      f"Заблокировали бота: {blocked}\nОшибки: {failed}")
