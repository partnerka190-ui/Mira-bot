import json
from datetime import datetime, timezone

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    birth TEXT,
    sign TEXT,
    sphere TEXT,
    topic TEXT,                           -- love | ex | future | money
    bait_card TEXT,
    partner_name TEXT,
    adult INTEGER DEFAULT 0,
    source TEXT,
    step TEXT DEFAULT 'bait',
    status TEXT DEFAULT 'free',          -- free | trial | active | expired
    paid_until TEXT,
    payment_method_id TEXT,
    autopay INTEGER DEFAULT 1,
    renew_reminded INTEGER DEFAULT 0,
    free_reading_used INTEGER DEFAULT 0,
    single_credits INTEGER DEFAULT 0,
    pending_final TEXT,                   -- устарело, оставлено для старых баз
    pending_key TEXT,                     -- карта-ключ, ждущая оплаты: {"card","q","a","at"}
    pending_q TEXT,                       -- вопрос, под который сейчас выбирают карты
    picks TEXT DEFAULT '[]',              -- номера карт, выбранных из веера
    flow TEXT,                            -- free | ask | partner | deep — для чего выбираются карты
    paywall_at TEXT,
    consent_at TEXT,                      -- когда принята оферта и согласие на автопродление
    offer_version TEXT,
    deep_credits INTEGER DEFAULT 0,
    forecast_credits INTEGER DEFAULT 0,
    memory TEXT DEFAULT '[]',             -- последние вопросы: [{"d","q","cards"}]
    last_card_date TEXT,
    questions_date TEXT,
    questions_today INTEGER DEFAULT 0,
    last_question_at TEXT,
    followup_sent INTEGER DEFAULT 1,
    nurture_stage INTEGER DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    last_seen TEXT,
    username TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    user_id INTEGER,
    event TEXT,                           -- start | bait | go | sphere | question | paywall | pay_click | later | paid
    source TEXT,                          -- метка входа пользователя (rNN, rNN_2, direct…)
    detail TEXT,
    at TEXT
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE TABLE IF NOT EXISTS admins (
    key TEXT PRIMARY KEY,                 -- числовой Telegram ID или ник без @ в нижнем регистре
    added_by INTEGER,
    at TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    kind TEXT,                            -- trial | month | single | deep | forecast
    amount INTEGER,
    status TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS daily_cache (
    day TEXT,
    key TEXT,                             -- знак зодиака или id пользователя
    card_id TEXT,
    text TEXT,
    PRIMARY KEY (day, key)
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s):
    return datetime.fromisoformat(s) if s else None


class DB:
    def __init__(self, path=config.DB_PATH):
        self.path = path
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self._backfill_events()
        await self.conn.commit()

    async def _migrate(self):
        """Добавляет в старую базу колонки, появившиеся в новых версиях."""
        cur = await self.conn.execute("PRAGMA table_info(users)")
        have = {r["name"] for r in await cur.fetchall()}
        body = SCHEMA.split("CREATE TABLE IF NOT EXISTS users (")[1].split(");")[0]
        for line in body.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            if not line or line.split()[0] in have or "PRIMARY KEY" in line:
                continue
            await self.conn.execute(f"ALTER TABLE users ADD COLUMN {line}")

    async def _backfill_events(self):
        """Один раз восстанавливает журнал событий по уже накопленным данным, чтобы статистика не начиналась с нуля."""
        cur = await self.conn.execute("SELECT COUNT(*) FROM events")
        if (await cur.fetchone())[0]:
            return
        await self.conn.execute(
            "INSERT INTO events SELECT id, 'start', source, NULL, created_at FROM users WHERE created_at IS NOT NULL")
        await self.conn.execute(
            "INSERT INTO events SELECT id, 'paywall', source, NULL, paywall_at FROM users WHERE paywall_at IS NOT NULL")
        await self.conn.execute(
            "INSERT INTO events SELECT p.user_id, 'paid', u.source, p.kind, p.created_at FROM payments p "
            "LEFT JOIN users u ON u.id = p.user_id WHERE p.status = 'succeeded'")

    async def log(self, uid, event, detail=None):
        await self.conn.execute(
            "INSERT INTO events (user_id, event, source, detail, at) "
            "VALUES (?, ?, (SELECT source FROM users WHERE id=?), ?, ?)",
            (uid, event, uid, detail, now_iso()))
        await self.conn.commit()

    async def seen(self, uid, username=None):
        await self.conn.execute("UPDATE users SET last_seen=?, username=? WHERE id=?",
                                (now_iso(), (username or "").lower() or None, uid))
        await self.conn.commit()

    async def admins(self):
        return await self.rows("SELECT * FROM admins ORDER BY at")

    async def add_admin(self, key, added_by):
        await self.conn.execute("INSERT OR REPLACE INTO admins VALUES (?, ?, ?)", (key, added_by, now_iso()))
        await self.conn.commit()

    async def remove_admin(self, key):
        await self.conn.execute("DELETE FROM admins WHERE key=?", (key,))
        await self.conn.commit()

    async def rows(self, sql, params=()):
        cur = await self.conn.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]

    async def user(self, uid):
        cur = await self.conn.execute("SELECT * FROM users WHERE id=?", (uid,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_user(self, uid, source):
        await self.conn.execute(
            "INSERT OR IGNORE INTO users (id, source, created_at) VALUES (?, ?, ?)",
            (uid, source, now_iso()),
        )
        await self.conn.commit()

    async def delete_user(self, uid):
        await self.conn.execute("DELETE FROM users WHERE id=?", (uid,))
        await self.conn.commit()

    async def update(self, uid, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.conn.execute(f"UPDATE users SET {cols} WHERE id=?", (*fields.values(), uid))
        await self.conn.commit()

    async def users_where(self, where, params=()):
        cur = await self.conn.execute(f"SELECT * FROM users WHERE blocked=0 AND {where}", params)
        return [dict(r) for r in await cur.fetchall()]

    async def remember(self, uid, question, cards):
        user = await self.user(uid)
        memory = json.loads(user["memory"] or "[]")
        memory.append({"d": now_iso()[:10], "q": question[:300], "cards": cards})
        await self.update(uid, memory=json.dumps(memory[-5:], ensure_ascii=False))

    async def add_payment(self, pid, uid, kind, amount, status):
        await self.conn.execute(
            "INSERT OR REPLACE INTO payments VALUES (?, ?, ?, ?, ?, ?)",
            (pid, uid, kind, amount, status, now_iso()),
        )
        await self.conn.commit()

    async def set_payment_status(self, pid, status):
        await self.conn.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))
        await self.conn.commit()

    async def pending_payments(self):
        cur = await self.conn.execute("SELECT * FROM payments WHERE status='pending'")
        rows = [dict(r) for r in await cur.fetchall()]
        now = datetime.now(timezone.utc)
        return [r for r in rows if (now - parse_iso(r["created_at"])).total_seconds() < 86400]

    async def payment(self, pid):
        cur = await self.conn.execute("SELECT * FROM payments WHERE id=?", (pid,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def cached(self, day, key):
        cur = await self.conn.execute("SELECT card_id, text FROM daily_cache WHERE day=? AND key=?", (day, key))
        row = await cur.fetchone()
        return (row["card_id"], row["text"]) if row else None

    async def cache(self, day, key, card_id, text):
        await self.conn.execute("INSERT OR REPLACE INTO daily_cache VALUES (?, ?, ?, ?)", (day, key, card_id, text))
        await self.conn.commit()
