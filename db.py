import sqlite3
import random
import json

DB_PATH = "giveaway.db"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS giveaways(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, description TEXT, price INTEGER,
            photos TEXT DEFAULT '[]', ch1 TEXT, ch2 TEXT,
            status TEXT DEFAULT 'active',
            winner_id INTEGER, winner_ticket INTEGER
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
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT
        );
        """)
    with _conn() as c:
        # миграция: колонка для канала розыгрыша (для уже существующих баз)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(giveaways)")}
        if "post_ch" not in cols:
            c.execute("ALTER TABLE giveaways ADD COLUMN post_ch TEXT")


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


def add_giveaway(title, description, price, photos, ch1, ch2, post_ch):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO giveaways(title, description, price, photos, ch1, ch2, post_ch) "
            "VALUES(?,?,?,?,?,?,?)",
            (title, description, price, json.dumps(photos), ch1, ch2, str(post_ch)),
        )
        return cur.lastrowid


def get_giveaway(gid):
    with _conn() as c:
        row = c.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()
        return dict(row) if row else None


def active_giveaways():
    with _conn() as c:
        rows = c.execute("SELECT * FROM giveaways WHERE status='active' ORDER BY id DESC")
        return [dict(r) for r in rows]


def add_order(gid, user_id, username, qty, receipt_id, receipt_type):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO orders(giveaway_id, user_id, username, qty, receipt_id, receipt_type) "
            "VALUES(?,?,?,?,?,?)",
            (gid, user_id, username, qty, receipt_id, receipt_type),
        )
        return cur.lastrowid


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


def all_tickets(gid):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tickets WHERE giveaway_id=?", (gid,))]


def find_ticket(gid, number):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM tickets WHERE giveaway_id=? AND number=?", (gid, number)
        ).fetchone()
        return dict(row) if row else None


def finish_giveaway(gid, user_id, number):
    with _conn() as c:
        c.execute(
            "UPDATE giveaways SET status='finished', winner_id=?, winner_ticket=? WHERE id=?",
            (user_id, number, gid),
        )
