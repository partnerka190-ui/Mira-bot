"""Статистика для админа: /stats — воронка, деньги, подписчики и какие ролики приводят людей."""
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import config

router = Router()
TZ = ZoneInfo(config.TZ)
db = None
is_admin = None  # функция (tg_user) -> bool, задаётся в setup()

PERIODS = {"today": "Сегодня", "yday": "Вчера", "7d": "7 дней", "30d": "30 дней", "all": "Всё время"}
FUNNEL = [
    ("start", "Нажали «Старт»"),
    ("bait", "Открыли первую карту"),
    ("go", "Захотели расклад"),
    ("sphere", "Выбрали тему (если ролик её не задал)"),
    ("question", "Задали вопрос"),
    ("paywall", "Получили расклад + карту-ключ"),
    ("pay_click", "Нажали «Оплатить»"),
    ("paid", "Оплатили"),
]
KIND_NAMES = {"trial": "Пробная за 1 ₽", "month": "Подписка 299 ₽", "single": "Разовый ответ",
              "deep": "Глубокий расклад", "forecast": "Прогноз на месяц"}


def setup(database, admin_check):
    global db, is_admin
    db, is_admin = database, admin_check


def bounds(period):
    """Границы периода по Москве → строки ISO в UTC (так хранятся даты в базе)."""
    today = datetime.now(TZ).date()
    start_day, end_day = {
        "today": (today, today + timedelta(days=1)),
        "yday": (today - timedelta(days=1), today),
        "7d": (today - timedelta(days=6), today + timedelta(days=1)),
        "30d": (today - timedelta(days=29), today + timedelta(days=1)),
        "all": (None, None),
    }[period]
    if not start_day:
        return "0", "9"
    conv = lambda d: datetime.combine(d, time(), TZ).astimezone(timezone.utc).isoformat()
    return conv(start_day), conv(end_day)


def pct(a, b):
    return f"{a * 100 / b:.0f}%" if b else "—"


def keyboard(active, view):
    periods = [InlineKeyboardButton(text=("• " if k == active else "") + v, callback_data=f"st:{view}:{k}")
               for k, v in PERIODS.items()]
    views = [InlineKeyboardButton(text=("• " if v == view else "") + t, callback_data=f"st:{v}:{active}")
             for v, t in (("main", "📊 Воронка"), ("src", "🎬 По роликам"), ("now", "👥 Подписчики"))]
    return InlineKeyboardMarkup(inline_keyboard=[periods[:3], periods[3:], views,
                                                 [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"st:{view}:{active}")]])


def testers_clause(testers, col="user_id"):
    ids = ",".join(str(int(t)) for t in testers) or "0"
    return f"{col} NOT IN ({ids})"


async def main_report(period, testers):
    a, b = bounds(period)
    nt = testers_clause(testers)
    rows = await db.rows(f"SELECT event, COUNT(DISTINCT user_id) n FROM events WHERE at>=? AND at<? AND {nt} GROUP BY event", (a, b))
    counts = {r["event"]: r["n"] for r in rows}
    new = (await db.rows(f"SELECT COUNT(*) n FROM users WHERE created_at>=? AND created_at<? AND {testers_clause(testers, 'id')}", (a, b)))[0]["n"]
    pays = await db.rows(f"SELECT kind, COUNT(*) n, SUM(amount) s FROM payments WHERE status='succeeded' "
                         f"AND created_at>=? AND created_at<? AND {nt} GROUP BY kind", (a, b))

    lines = [f"📊 <b>Статистика · {PERIODS[period]}</b>", "", f"🆕 Новых людей в боте: <b>{new}</b>", "", "<b>Воронка</b> (уникальные люди)"]
    top = counts.get("start", 0)
    for key, name in FUNNEL:
        n = counts.get(key, 0)
        lines.append(f"{name}: <b>{n}</b>" + (f"  ·  {pct(n, top)}" if key != "start" else ""))
    if counts.get("later"):
        lines.append(f"Нажали «Позже»: {counts['later']}")

    total = sum(p["s"] or 0 for p in pays)
    lines += ["", f"💰 <b>Выручка: {total:,} ₽</b>".replace(",", " ")]
    for p in sorted(pays, key=lambda p: -(p["s"] or 0)):
        lines.append(f"{KIND_NAMES.get(p['kind'], p['kind'])}: {p['n']} шт · {p['s'] or 0} ₽")
    if not pays:
        lines.append("Оплат за период нет")
    return "\n".join(lines)


def reel_of(source):
    """r01_2 → r01, direct → «без метки»."""
    if not source or source == "direct":
        return "без метки"
    m = re.match(r"(r\d+)", source)
    return m.group(1) if m else source


async def source_report(period, testers):
    a, b = bounds(period)
    nt = testers_clause(testers)
    rows = await db.rows(f"SELECT source, event, COUNT(DISTINCT user_id) n FROM events "
                         f"WHERE at>=? AND at<? AND {nt} AND event IN ('start','paywall','paid') GROUP BY source, event", (a, b))
    money = await db.rows(f"SELECT u.source, SUM(p.amount) s FROM payments p LEFT JOIN users u ON u.id=p.user_id "
                          f"WHERE p.status='succeeded' AND p.created_at>=? AND p.created_at<? AND {testers_clause(testers, 'p.user_id')} "
                          f"GROUP BY u.source", (a, b))
    table = {}
    for r in rows:
        t = table.setdefault(reel_of(r["source"]), {"start": 0, "paywall": 0, "paid": 0, "rub": 0})
        t[r["event"]] += r["n"]
    for r in money:
        table.setdefault(reel_of(r["source"]), {"start": 0, "paywall": 0, "paid": 0, "rub": 0})["rub"] += r["s"] or 0

    lines = [f"🎬 <b>Откуда приходят · {PERIODS[period]}</b>", "", "метка: старт → расклад → оплатили · ₽", ""]
    if not table:
        lines.append("За период никто не заходил")
    for src, t in sorted(table.items(), key=lambda kv: (-kv[1]["rub"], -kv[1]["start"]))[:25]:
        lines.append(f"<b>{src}</b>: {t['start']} → {t['paywall']} → {t['paid']} · {t['rub']} ₽  ({pct(t['paid'], t['start'])})")
    lines += ["", "Метка rNN = ссылка t.me/бот?start=rNN из ролика номер NN."]
    return "\n".join(lines)


async def now_report(testers):
    nt = testers_clause(testers, "id")
    now = datetime.now(timezone.utc)
    day, week = (now - timedelta(days=1)).isoformat(), (now - timedelta(days=7)).isoformat()
    q = lambda where, *p: db.rows(f"SELECT COUNT(*) n FROM users WHERE {nt} AND {where}", p)
    total = (await q("1"))[0]["n"]
    trial = (await q("status='trial' AND paid_until>?", now.isoformat()))[0]["n"]
    active = (await q("status='active' AND paid_until>?", now.isoformat()))[0]["n"]
    autopay_off = (await q("status IN ('trial','active') AND paid_until>? AND autopay=0", now.isoformat()))[0]["n"]
    expired = (await q("status='expired'"))[0]["n"]
    blocked = (await q("blocked=1"))[0]["n"]
    dau = (await q("last_seen>?", day))[0]["n"]
    wau = (await q("last_seen>?", week))[0]["n"]
    keys = (await q("pending_key IS NOT NULL"))[0]["n"]
    lines = [
        "👥 <b>Подписчики сейчас</b>", "",
        f"Всего людей в базе: <b>{total}</b>",
        f"Активны за 24 часа: <b>{dau}</b>",
        f"Активны за 7 дней: <b>{wau}</b>", "",
        f"На пробном периоде: <b>{trial}</b>",
        f"Платная подписка: <b>{active}</b>",
        f"Из них отключили автопродление: {autopay_off}",
        f"Подписка закончилась: {expired}",
        f"Ждут с неоплаченной картой-ключом: {keys}",
        f"Заблокировали бота: {blocked}", "",
        f"MRR (подписки × 299 ₽): <b>{active * 299:,} ₽</b>".replace(",", " "),
    ]
    return "\n".join(lines)


async def render(view, period, testers):
    if view == "src":
        return await source_report(period, testers)
    if view == "now":
        return await now_report(testers)
    return await main_report(period, testers)


@router.message(Command("stats"))
async def stats_cmd(msg: Message, testers: set):
    if not is_admin(msg.from_user):
        return
    await msg.answer(await render("main", "today", testers), reply_markup=keyboard("today", "main"))


@router.callback_query(F.data.startswith("st:"))
async def stats_cb(cb: CallbackQuery, testers: set):
    if not is_admin(cb.from_user):
        await cb.answer()
        return
    _, view, period = cb.data.split(":")
    try:
        await cb.message.edit_text(await render(view, period, testers), reply_markup=keyboard(period, view))
    except Exception:
        pass  # «message is not modified» — ничего не изменилось
    await cb.answer("Обновлено")
