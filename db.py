import sqlite3
import json
import secrets
from datetime import datetime, timezone

class Database:
    def __init__(self, path):
        self.path = path
        self._init()

    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init(self):
        with self.conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS giveaways(
              id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL,
              price INTEGER NOT NULL, payment TEXT NOT NULL, channel1 TEXT NOT NULL, channel2 TEXT NOT NULL,
              photo_id TEXT, status TEXT NOT NULL DEFAULT 'draft', created_by INTEGER NOT NULL,
              published_message_id INTEGER, winner_ticket INTEGER, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payment_requests(
              id INTEGER PRIMARY KEY AUTOINCREMENT, giveaway_id INTEGER NOT NULL REFERENCES giveaways(id),
              user_id INTEGER NOT NULL, quantity INTEGER NOT NULL, total INTEGER NOT NULL,
              receipt_file_id TEXT NOT NULL, receipt_kind TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tickets(
              id INTEGER PRIMARY KEY AUTOINCREMENT, giveaway_id INTEGER NOT NULL REFERENCES giveaways(id),
              request_id INTEGER NOT NULL UNIQUE REFERENCES payment_requests(id),
              user_id INTEGER NOT NULL, ticket_no TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS winners(
              id INTEGER PRIMARY KEY AUTOINCREMENT, giveaway_id INTEGER NOT NULL UNIQUE REFERENCES giveaways(id),
              user_id INTEGER NOT NULL, ticket_no TEXT NOT NULL, drawn_at TEXT NOT NULL
            );
            """)

    def create_giveaway(self, d, owner):
        with self.conn() as c:
            cur = c.execute("""INSERT INTO giveaways(title,description,price,payment,channel1,channel2,photo_id,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (d["title"],d["description"],d["price"],d["payment"],d["channel"],d["channel2"],d.get("photo_id"),owner,datetime.now(timezone.utc).isoformat()))
            return cur.lastrowid

    def get_giveaway(self, gid):
        with self.conn() as c:
            row = c.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()
            return dict(row) if row else None

    def mark_published(self, gid, channel, msgid):
        with self.conn() as c:
            c.execute("UPDATE giveaways SET status='published', published_message_id=? WHERE id=?", (msgid,gid))

    def list_giveaways(self):
        with self.conn() as c:
            rows = c.execute("""SELECT g.*, (SELECT COUNT(*) FROM tickets t WHERE t.giveaway_id=g.id) ticket_count
            FROM giveaways g ORDER BY id DESC""").fetchall()
            return [dict(x) for x in rows]

    def create_payment_request(self, gid, uid, q, total, fid, kind):
        with self.conn() as c:
            cur = c.execute("""INSERT INTO payment_requests(giveaway_id,user_id,quantity,total,receipt_file_id,receipt_kind,created_at)
            VALUES(?,?,?,?,?,?,?)""", (gid,uid,q,total,fid,kind,datetime.now(timezone.utc).isoformat()))
            return cur.lastrowid

    def get_payment_request(self, rid):
        with self.conn() as c:
            row = c.execute("SELECT * FROM payment_requests WHERE id=?", (rid,)).fetchone()
            return dict(row) if row else None

    def approve_payment(self, rid):
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            req = c.execute("SELECT * FROM payment_requests WHERE id=?", (rid,)).fetchone()
            if not req or req["status"] != "pending":
                c.rollback()
                return []
            g = c.execute("SELECT status FROM giveaways WHERE id=?", (req["giveaway_id"],)).fetchone()
            if not g or g["status"] != "published":
                c.execute("UPDATE payment_requests SET status='rejected',processed_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(),rid))
                c.commit()
                return []
            c.execute("UPDATE payment_requests SET status='approved',processed_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(),rid))
            tickets = []
            for _ in range(req["quantity"]):
                while True:
                    num = f"{secrets.randbelow(900000)+100000}"
                    exists = c.execute("SELECT 1 FROM tickets WHERE ticket_no=?", (num,)).fetchone()
                    if not exists:
                        break
                c.execute("INSERT INTO tickets(giveaway_id,request_id,user_id,ticket_no,created_at) VALUES(?,?,?,?,?)",
                          (req["giveaway_id"],rid,req["user_id"],num,datetime.now(timezone.utc).isoformat()))
                tickets.append(num)
            c.commit()
            return tickets

    def reject_payment(self, rid):
        with self.conn() as c:
            c.execute("UPDATE payment_requests SET status='rejected',processed_at=? WHERE id=? AND status='pending'",
                      (datetime.now(timezone.utc).isoformat(),rid))

    def draw_winner(self, gid):
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            g = c.execute("SELECT status FROM giveaways WHERE id=?", (gid,)).fetchone()
            if not g or g["status"] != "published":
                c.rollback()
                return None
            rows = c.execute("SELECT user_id,ticket_no FROM tickets WHERE giveaway_id=?", (gid,)).fetchall()
            if not rows:
                c.rollback()
                return None
            winner = secrets.choice(rows)
            c.execute("INSERT INTO winners(giveaway_id,user_id,ticket_no,drawn_at) VALUES(?,?,?,?)",
                      (gid,winner["user_id"],winner["ticket_no"],datetime.now(timezone.utc).isoformat()))
            c.execute("UPDATE giveaways SET status='finished',winner_ticket=? WHERE id=?", (winner["ticket_no"],gid))
            c.commit()
            return dict(winner)

    def stats(self):
        with self.conn() as c:
            return {
                "giveaways": c.execute("SELECT COUNT(*) FROM giveaways").fetchone()[0],
                "requests": c.execute("SELECT COUNT(*) FROM payment_requests").fetchone()[0],
                "tickets": c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            }
