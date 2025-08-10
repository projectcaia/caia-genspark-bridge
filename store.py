# store.py
import sqlite3, os, json, time
from typing import List, Dict, Optional

DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")

def _conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS msg (
            uid INTEGER PRIMARY KEY,
            frm TEXT, subj TEXT, dt TEXT, text TEXT, html TEXT, ts INTEGER
        )""")
        c.commit()

def get_last_uid() -> Optional[int]:
    with _conn() as c:
        cur = c.execute("SELECT v FROM kv WHERE k='last_uid'")
        row = cur.fetchone()
        return int(row[0]) if row else None

def set_last_uid(uid: int):
    with _conn() as c:
        c.execute("INSERT INTO kv(k,v) VALUES('last_uid',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(uid),))
        c.commit()

def get_setting(key: str) -> Optional[str]:
    with _conn() as c:
        cur = c.execute("SELECT v FROM kv WHERE k=?", (key,))
        r = cur.fetchone()
        return r[0] if r else None

def set_setting(key: str, val: str):
    with _conn() as c:
        c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, val))
        c.commit()

def save_messages(msgs: List[Dict]):
    with _conn() as c:
        for m in msgs:
            c.execute("INSERT OR IGNORE INTO msg(uid, frm, subj, dt, text, html, ts) VALUES(?,?,?,?,?,?,?)",
                      (m["uid"], m["from"], m["subject"], m["date"], m["text"], m["html"], int(time.time())))
        c.commit()

def list_messages_since(since_uid: Optional[int], limit: int = 20):
    q = "SELECT uid, frm, subj, dt, text FROM msg "
    args = []
    if since_uid:
        q += "WHERE uid > ? "
        args.append(since_uid)
    q += "ORDER BY uid DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        cur = c.execute(q, args)
        rows = [{"uid": r[0], "from": r[1], "subject": r[2], "date": r[3], "text": r[4]} for r in cur.fetchall()]
    return rows
