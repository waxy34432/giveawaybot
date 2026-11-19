import json
import os
import random
import sqlite3
import time

DB_PATH = os.environ.get("DB_PATH", "giveaways_v2.db")

_GIVEAWAY_COLS = (
    "owner_id", "title", "text", "photos", "price", "btn_text", "extra",
    "post_chat_id", "winners_count", "start_at", "end_mode", "end_at",
    "end_count", "draw_mode", "boost", "captcha", "status", "created_at", "pay_mode",
)
_UPDATABLE = {"post_message_id", "status", "start_at"}


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS channels(
            chat_id INTEGER PRIMARY KEY, title TEXT, username TEXT, link TEXT, added_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS giveaways(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER, title TEXT, text TEXT, photos TEXT DEFAULT '[]',
            price INTEGER DEFAULT 0, btn_text TEXT, extra TEXT DEFAULT '[]',
            post_chat_id INTEGER, post_message_id INTEGER,
            winners_count INTEGER DEFAULT 1,
            start_at INTEGER, end_mode TEXT, end_at INTEGER, end_count INTEGER,
            draw_mode TEXT DEFAULT 'random',
            boost INTEGER DEFAULT 0, captcha INTEGER DEFAULT 0,
            status TEXT DEFAULT 'scheduled', created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS participants(
            giveaway_id INTEGER, user_id INTEGER, username TEXT, full_name TEXT,
            PRIMARY KEY(giveaway_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS captcha(
            giveaway_id INTEGER, user_id INTEGER, PRIMARY KEY(giveaway_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            giveaway_id INTEGER, user_id INTEGER, username TEXT,
            qty INTEGER, receipt_id TEXT, receipt_type TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS tickets(
            giveaway_id INTEGER, user_id INTEGER, number INTEGER,
            PRIMARY KEY(giveaway_id, number)
        );
        CREATE TABLE IF NOT EXISTS winners(
            giveaway_id INTEGER, user_id INTEGER, ticket INTEGER,
            PRIMARY KEY(giveaway_id, user_id)
        );
        """)
    with _conn() as c:
        # миграции для баз, созданных прошлыми версиями
        def cols(table):
            return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if "pay_mode" not in cols("giveaways"):
            c.execute("ALTER TABLE giveaways ADD COLUMN pay_mode TEXT DEFAULT 'manual'")
        if "method" not in cols("orders"):
            c.execute("ALTER TABLE orders ADD COLUMN method TEXT DEFAULT 'manual'")
        if "charge_id" not in cols("orders"):
            c.execute("ALTER TABLE orders ADD COLUMN charge_id TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_charge "
                  "ON orders(charge_id) WHERE charge_id IS NOT NULL")


# ---------- настройки ----------

def get_setting(key, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with _conn() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ---------- каналы ----------

def upsert_channel(chat_id, title, username, link, added_by):
    with _conn() as c:
        c.execute(
            "INSERT INTO channels(chat_id, title, username, link, added_by) VALUES(?,?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username, "
            "link=COALESCE(excluded.link, channels.link), added_by=excluded.added_by",
            (chat_id, title, username, link, added_by),
        )


def remove_channel(chat_id):
    with _conn() as c:
        c.execute("DELETE FROM channels WHERE chat_id=?", (chat_id,))


def get_channel(chat_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM channels WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row) if row else None


def list_channels(added_by=None):
    with _conn() as c:
        if added_by is None:
            rows = c.execute("SELECT * FROM channels ORDER BY title")
        else:
            rows = c.execute("SELECT * FROM channels WHERE added_by=? ORDER BY title", (added_by,))
        return [dict(r) for r in rows]


# ---------- розыгрыши ----------

def _g(row):
    if not row:
        return None
    d = dict(row)
    d["photos"] = json.loads(d["photos"] or "[]")
    d["extra"] = json.loads(d["extra"] or "[]")
    return d


def add_giveaway(**kw):
    kw.setdefault("created_at", int(time.time()))
    kw.setdefault("pay_mode", "manual")
    kw["photos"] = json.dumps(kw.get("photos", []))
    kw["extra"] = json.dumps(kw.get("extra", []))
    values = [kw.get(col) for col in _GIVEAWAY_COLS]
    with _conn() as c:
        cur = c.execute(
            f"INSERT INTO giveaways({','.join(_GIVEAWAY_COLS)}) "
            f"VALUES({','.join('?' * len(_GIVEAWAY_COLS))})",
            values,
        )
        return cur.lastrowid


def get_giveaway(gid):
    with _conn() as c:
        return _g(c.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone())


def update_giveaway(gid, **fields):
    assert set(fields) <= _UPDATABLE, fields
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE giveaways SET {sets} WHERE id=?", (*fields.values(), gid))


def set_status_if(gid, from_statuses, to_status):
    """Атомарно меняет статус, только если он сейчас один из from_statuses."""
    marks = ",".join("?" * len(from_statuses))
    with _conn() as c:
        cur = c.execute(
            f"UPDATE giveaways SET status=? WHERE id=? AND status IN ({marks})",
            (to_status, gid, *from_statuses),
        )
        return cur.rowcount == 1


def list_giveaways(owner_id, limit=30):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM giveaways WHERE owner_id=? ORDER BY id DESC LIMIT ?", (owner_id, limit)
        )
        return [_g(r) for r in rows]


def due_scheduled(now):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM giveaways WHERE status='scheduled' AND start_at<=?", (now,)
        )
        return [_g(r) for r in rows]


def due_active(now):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM giveaways WHERE status='active' AND end_mode='time' AND end_at<=?", (now,)
        )
        return [_g(r) for r in rows]


# ---------- участники и билеты ----------

def upsert_participant(gid, user_id, username, full_name):
    with _conn() as c:
        c.execute(
            "INSERT INTO participants(giveaway_id, user_id, username, full_name) VALUES(?,?,?,?) "
            "ON CONFLICT(giveaway_id, user_id) DO UPDATE SET "
            "username=excluded.username, full_name=excluded.full_name",
            (gid, user_id, username, full_name),
        )


def get_participant(gid, user_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM participants WHERE giveaway_id=? AND user_id=?", (gid, user_id)
        ).fetchone()
        return dict(row) if row else None


def count_participants(gid):
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM tickets WHERE giveaway_id=?", (gid,)
        ).fetchone()["n"]


def count_tickets(gid):
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE giveaway_id=?", (gid,)
        ).fetchone()["n"]


def issue_tickets(gid, user_id, qty):
    """Выдаёт qty уникальных случайных номеров в рамках розыгрыша."""
    numbers = []
    with _conn() as c:
        taken = {r["number"] for r in c.execute(
            "SELECT number FROM tickets WHERE giveaway_id=?", (gid,))}
        while len(numbers) < qty:
            n = random.randint(100000, 999999)
            if n in taken:
                continue
            taken.add(n)
            numbers.append(n)
            c.execute("INSERT INTO tickets VALUES(?,?,?)", (gid, user_id, n))
    return numbers


def user_tickets(gid, user_id):
    with _conn() as c:
        return [r["number"] for r in c.execute(
            "SELECT number FROM tickets WHERE giveaway_id=? AND user_id=? ORDER BY number",
            (gid, user_id))]


def all_tickets(gid):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tickets WHERE giveaway_id=? ORDER BY number", (gid,))]


def find_ticket(gid, number):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM tickets WHERE giveaway_id=? AND number=?", (gid, number)
        ).fetchone()
        return dict(row) if row else None


def participants_report(gid):
    """Список участников с их билетами для выгрузки админу."""
    with _conn() as c:
        rows = c.execute(
            "SELECT t.user_id AS user_id, p.username AS username, p.full_name AS full_name, "
            "GROUP_CONCAT(t.number, ' ') AS tickets, COUNT(*) AS n "
            "FROM tickets t LEFT JOIN participants p "
            "ON p.giveaway_id=t.giveaway_id AND p.user_id=t.user_id "
            "WHERE t.giveaway_id=? GROUP BY t.user_id ORDER BY n DESC", (gid,)
        )
        return [dict(r) for r in rows]


# ---------- капча ----------

def captcha_passed(gid, user_id):
    with _conn() as c:
        return c.execute(
            "SELECT 1 FROM captcha WHERE giveaway_id=? AND user_id=?", (gid, user_id)
        ).fetchone() is not None


def mark_captcha(gid, user_id):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO captcha VALUES(?,?)", (gid, user_id))


# ---------- оплата ----------

def add_order(gid, user_id, username, qty, receipt_id, receipt_type):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO orders(giveaway_id, user_id, username, qty, receipt_id, receipt_type) "
            "VALUES(?,?,?,?,?,?)",
            (gid, user_id, username, qty, receipt_id, receipt_type),
        )
        return cur.lastrowid


def add_paid_order(gid, user_id, username, qty, charge_id, status="approved"):
    """Заявка по оплате через ЮKassa. Повторный charge_id даёт None (защита от дублей)."""
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO orders(giveaway_id, user_id, username, qty, status, method, charge_id) "
                "VALUES(?,?,?,?,?, 'yookassa', ?)",
                (gid, user_id, username, qty, status, charge_id),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_order(oid):
    with _conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        return dict(row) if row else None


def decide_order(oid, status):
    """Меняет статус только если заявка ещё pending. Возвращает True при успехе."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE orders SET status=? WHERE id=? AND status='pending'", (status, oid)
        )
        return cur.rowcount == 1


def count_pending_orders(gid):
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE giveaway_id=? AND status='pending'", (gid,)
        ).fetchone()["n"]


# ---------- победители ----------

def set_winners(gid, pairs):
    """pairs: список (user_id, ticket)."""
    with _conn() as c:
        c.execute("DELETE FROM winners WHERE giveaway_id=?", (gid,))
        c.executemany("INSERT INTO winners VALUES(?,?,?)", [(gid, u, t) for u, t in pairs])


def get_winners(gid):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT w.user_id AS user_id, w.ticket AS ticket, p.username AS username, "
            "p.full_name AS full_name FROM winners w "
            "LEFT JOIN participants p ON p.giveaway_id=w.giveaway_id AND p.user_id=w.user_id "
            "WHERE w.giveaway_id=?", (gid,))]
