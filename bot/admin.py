"""Админ-панель: /admin — статистика, рассылка и управление админами (последнее — только владельцу)."""
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (BotCommand, BotCommandScopeChat, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from . import broadcast, config, stats

router = Router()
db = None
ADMINS = set()   # ключи из таблицы admins: числовой ID или ник в нижнем регистре
adding = set()   # владельцы, от которых ждём ник или ID нового админа

COMMANDS = [BotCommand(command="admin", description="⚙️ Админ-панель"),
            BotCommand(command="stats", description="📊 Статистика"),
            BotCommand(command="broadcast", description="📣 Рассылка"),
            BotCommand(command="start", description="🔮 Начать заново")]


async def setup(database, bot: Bot):
    global db
    db = database
    ADMINS.clear()
    ADMINS.update(a["key"] for a in await db.admins())
    for key in ADMINS | config.OWNERS:
        await set_commands(bot, key)


def keys_of(u):
    return {str(u.id), (u.username or "").lower()} - {""}


def is_owner(u):
    return bool(u and keys_of(u) & config.OWNERS)


def is_admin(u):
    return bool(u and (is_owner(u) or keys_of(u) & ADMINS))


async def set_commands(bot, key):
    """Команды админки в меню чата — только если знаем числовой ID."""
    uid = int(key) if key.isdigit() else None
    if not uid:
        rows = await db.rows("SELECT id FROM users WHERE username=?", (key,))
        uid = rows[0]["id"] if rows else None
    if uid:
        try:
            await bot.set_my_commands(COMMANDS, scope=BotCommandScopeChat(chat_id=uid))
        except Exception:
            pass


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


def panel(u):
    rows = [[("📊 Статистика", "adm:stats"), ("📣 Рассылка", "adm:bc")],
            [("🎬 По роликам", "adm:src"), ("👥 Подписчики", "adm:now")]]
    if is_owner(u):
        rows.append([("👮 Админы", "adm:list")])
    return kb(*rows)


PANEL_TEXT = "⚙️ <b>Админ-панель «Мира»</b>\n\nРоль: {role}"


@router.message(Command("admin"))
async def admin_cmd(msg: Message):
    if not is_admin(msg.from_user):
        return
    await set_commands(msg.bot, str(msg.from_user.id))
    await msg.answer(PANEL_TEXT.format(role="владелец" if is_owner(msg.from_user) else "админ"), reply_markup=panel(msg.from_user))


async def admins_view(cb):
    rows = await db.admins()
    lines = ["👮 <b>Админы</b>", "", "Владелец: " + ", ".join("@" + o if not o.isdigit() else o for o in sorted(config.OWNERS))]
    lines += [f"• {'@' + a['key'] if not a['key'].isdigit() else a['key']}" for a in rows] or ["Других админов пока нет."]
    lines += ["", "Админы видят статистику и делают рассылки. Назначать админов может только владелец."]
    buttons = [[(f"❌ {'@' + a['key'] if not a['key'].isdigit() else a['key']}", f"adm:del:{a['key']}")] for a in rows]
    await cb.message.edit_text("\n".join(lines), reply_markup=kb(*buttons, [("➕ Добавить админа", "adm:add")], [("↩️ Назад", "adm:home")]))


@router.callback_query(F.data.startswith("adm:"))
async def on_button(cb: CallbackQuery, testers: set):
    u = cb.from_user
    if not is_admin(u):
        await cb.answer()
        return
    await cb.answer()
    action = cb.data.split(":")
    if action[1] == "home":
        adding.discard(u.id)
        await cb.message.edit_text(PANEL_TEXT.format(role="владелец" if is_owner(u) else "админ"), reply_markup=panel(u))
    elif action[1] in ("stats", "src", "now"):
        view = {"stats": "main"}.get(action[1], action[1])
        await cb.message.answer(await stats.render(view, "today", testers), reply_markup=stats.keyboard("today", view))
    elif action[1] == "bc":
        await broadcast.begin(cb.bot, u.id)
    elif not is_owner(u):
        return
    elif action[1] == "list":
        await admins_view(cb)
    elif action[1] == "add":
        adding.add(u.id)
        await cb.message.answer("Пришли ник нового админа (например, @nickname) или его числовой Telegram ID. "
                                "Можно просто переслать сюда любое его сообщение.\n\nОтмена — /admin")
    elif action[1] == "del":
        key = action[2]
        await db.remove_admin(key)
        ADMINS.discard(key)
        await admins_view(cb)


def adding_admin(msg: Message):
    return msg.from_user and msg.from_user.id in adding


@router.message(adding_admin)
async def got_admin(msg: Message):
    adding.discard(msg.from_user.id)
    origin = getattr(msg.forward_origin, "sender_user", None) if msg.forward_origin else None
    if origin:
        key = str(origin.id)
    else:
        key = (msg.text or "").strip().lstrip("@").lower()
        if not key or " " in key:
            await msg.answer("Не понял. Нажми «➕ Добавить админа» ещё раз и пришли ник вида @nickname или числовой ID.")
            return
    await db.add_admin(key, msg.from_user.id)
    ADMINS.add(key)
    await set_commands(msg.bot, key)
    shown = key if key.isdigit() else "@" + key
    await msg.answer(f"✅ {shown} теперь админ. Пусть напишет боту /admin.", reply_markup=kb([("👮 К списку админов", "adm:list")]))
