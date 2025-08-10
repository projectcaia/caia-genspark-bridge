# store.py
import sqlite3, os, time, json
from typing import List, Dict, Optional

DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")

def _conn():
    return sqlite3.connect(DB_PATH)

def _get_msg_columns(c) -> List[str]:
    cur = c.execute("PRAGMA table_info(msg)")
    return [row[1] for row in cur.fetchall()]

def _table_exists(c, name: str) -> bool:
    cur = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def init_db():
    with _conn() as c:
        if not _table_exists(c, "msg"):
            # 새 스키마(id AUTOINCREMENT)
            c.execute("""
                CREATE TABLE msg (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    frm TEXT,
                    rcpt TEXT,
                    subj TEXT,
                    dt TEXT,
                    text TEXT,
                    html TEXT,
                    atts TEXT,
                    ts INTEGER
                )
            """)
            c.commit()
        # kv 테이블은 필요 없지만, 과거 코드 호환을 위해 생성해두어도 무해
        c.execute("""CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)""")
        c.commit()

def _is_old_uid_schema(c) -> bool:
    cols = _get_msg_columns(c)
    # 옛 스키마: uid, frm, subj, dt, text, html, ts
    return ("uid" in cols) and ("frm" in cols) and ("subj" in cols)

def save_messages(msgs: List[Dict]):
    now = int(time.time())
    with _conn() as c:
        if _is_old_uid_schema(c):
            # 구(UID 기반) 스키마에 맞춰 저장
            for i, m in enumerate(msgs):
                uid = m.get("uid")
                if uid is None:
                    # Inbound Parse에는 uid가 없음 → 시간 기반 고유값 생성
                    uid = now * 1000 + i
                frm = m.get("from", "")
                subj = m.get("subject", "")
                dt = m.get("date", "")
                text = m.get("text", "")
                html = m.get("html")
                c.execute(
                    "INSERT OR IGNORE INTO msg(uid, frm, subj, dt, text, html, ts) VALUES(?,?,?,?,?,?,?)",
                    (int(uid), frm, subj, dt, text, html, now)
                )
            c.commit()
        else:
            # 신 스키마(id AUTOINCREMENT)
            for m in msgs:
                frm = m.get("from", "")
                rcpt = m.get("to", "")
                subj = m.get("subject", "")
                dt = m.get("date", "")
                text = m.get("text", "")
                html = m.get("html")
                atts = json.dumps(m.get("attachments") or [])
                c.execute(
                    "INSERT INTO msg(frm, rcpt, subj, dt, text, html, atts, ts) VALUES(?,?,?,?,?,?,?,?)",
                    (frm, rcpt, subj, dt, text, html, atts, now)
                )
            c.commit()

def list_messages_since(since_id: Optional[int], limit: int = 20):
    with _conn() as c:
        if _is_old_uid_schema(c):
            # 구 스키마는 uid를 정렬 기준으로 사용
            if since_id:
                cur = c.execute(
                    "SELECT uid, frm, subj, dt, text FROM msg WHERE uid > ? ORDER BY uid DESC LIMIT ?",
                    (since_id, limit)
                )
            else:
                cur = c.execute(
                    "SELECT uid, frm, subj, dt, text FROM msg ORDER BY uid DESC LIMIT ?",
                    (limit,)
                )
            rows = cur.fetchall()
            # API 응답 포맷 통일
            return [
                {"id": r[0], "from": r[1], "to": "", "subject": r[2], "date": r[3], "text": r[4]}
                for r in rows
            ]
        else:
            # 신 스키마(id AUTOINCREMENT)
            if since_id:
                cur = c.execute(
                    "SELECT id, frm, rcpt, subj, dt, text FROM msg WHERE id > ? ORDER BY id DESC LIMIT ?",
                    (since_id, limit)
                )
            else:
                cur = c.execute(
                    "SELECT id, frm, rcpt, subj, dt, text FROM msg ORDER BY id DESC LIMIT ?",
                    (limit,)
                )
            rows = cur.fetchall()
            return [
                {"id": r[0], "from": r[1], "to": r[2], "subject": r[3], "date": r[4], "text": r[5]}
                for r in rows
            ]
