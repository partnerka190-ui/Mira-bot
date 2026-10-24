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
    partner_name TEXT,
    adult INTEGER DEFAULT 0,
    source TEXT,
    step TEXT DEFAULT 'adult',
    status TEXT DEFAULT 'free',          -- free | trial | active | expired
    paid_until TEXT,
    payment_method_id TEXT,
    autopay INTEGER DEFAULT 1,
    renew_reminded INTEGER DEFAULT 0,
    free_reading_used INTEGER DEFAULT 0,
    single_credits INTEGER DEFAULT 0,
    pending_final TEXT,                   -- скрытая третья карта бесплатного расклада
    memory TEXT DEFAULT '[]',             -- последние вопросы: [{"d","q","cards"}]
    last_card_date TEXT,
    questions_date TEXT,
    questions_today INTEGER DEFAULT 0,
    last_question_at TEXT,
    followup_sent INTEGER DEFAULT 1,
    nurture_stage INTEGER DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    kind TEXT,                            -- trial | month | single
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
        await self.conn.commit()

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
