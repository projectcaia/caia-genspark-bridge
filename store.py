# store.py
import sqlite3, os, time, json
from typing import List, Dict, Optional

DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")

def _conn():
    """
    - WAL 모드 + NORMAL 동기화로 동시성/성능 향상
    - check_same_thread=False: FastAPI 스레드 환경에서 안전하게 사용
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _get_msg_columns(c) -> List[str]:
    cur = c.execute("PRAGMA table_info(msg)")
    return [row[1] for row in cur.fetchall()]

def _table_exists(c, name: str) -> bool:
    cur = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def _ensure_new_schema(c):
    """
    신 스키마(id AUTOINCREMENT) 없으면 생성.
    필요한 보조 인덱스도 같이 생성.
    """
    if not _table_exists(c, "msg"):
        c.execute("""
            CREATE TABLE msg (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                frm  TEXT,
                rcpt TEXT,
                subj TEXT,
                dt   TEXT,
                text TEXT,
                html TEXT,
                atts TEXT,
                ts   INTEGER
            )
        """)
    # 인덱스(존재하면 무시)
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_id ON msg(id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON msg(ts)")

def init_db():
    """
    - 테이블 없으면 신 스키마로 생성
    - 과거 구스키마(uid 기반)도 허용(읽기/쓰기 로직에서 분기)
    - kv 테이블은 공용 상태저장용으로 항상 보장
    """
    with _conn() as c:
        if not _table_exists(c, "msg"):
            _ensure_new_schema(c)
            c.commit()
        else:
            # 기존 테이블이 있으면 인덱스만 보장
            try:
                _ensure_new_schema(c)
                c.commit()
            except Exception:
                # 구스키마(uid 기반)일 수도 있으므로 조용히 통과
                pass
        c.execute("""CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)""")
        c.commit()

def _is_old_uid_schema(c) -> bool:
    cols = _get_msg_columns(c)
    # 옛 스키마: uid, frm, subj, dt, text, html, ts
    return ("uid" in cols) and ("frm" in cols) and ("subj" in cols)

def save_messages(msgs: List[Dict]):
    """
    msgs = [{
      "from": str, "to": str, "subject": str, "date": str,
      "text": str, "html": Optional[str], "attachments": list[{"filename","content_b64"}]
    }]
    """
    now = int(time.time())
    with _conn() as c:
        if _is_old_uid_schema(c):
            # 구(UID 기반) 스키마 저장 경로
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
                frm  = m.get("from", "")
                rcpt = m.get("to", "")
                subj = m.get("subject", "")
                dt   = m.get("date", "")
                text = m.get("text", "")
                html = m.get("html")
                atts = json.dumps(m.get("attachments") or [])
                c.execute(
                    "INSERT INTO msg(frm, rcpt, subj, dt, text, html, atts, ts) VALUES(?,?,?,?,?,?,?,?)",
                    (frm, rcpt, subj, dt, text, html, atts, now)
                )
            c.commit()

def list_messages_since(since_id: Optional[int], limit: int = 20):
    """
    since_id가 주어지면 그 이후(id > since_id)만, 없으면 최신부터 limit개.
    반환 포맷:
      [{"id":int, "from":str, "to":str, "subject":str, "date":str, "text":str, "has_attachments":bool}, ...]
    (기존 필드 유지 + has_attachments 추가)
    """
    limit = max(1, min(int(limit or 20), 200))  # 과도한 요청 방지
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
            return [
                {
                    "id": r[0], "from": r[1], "to": "",
                    "subject": r[2], "date": r[3], "text": r[4],
                    "has_attachments": False
                }
                for r in rows
            ]
        else:
            # 신 스키마(id AUTOINCREMENT)
            if since_id:
                cur = c.execute(
                    "SELECT id, frm, rcpt, subj, dt, text, atts FROM msg WHERE id > ? ORDER BY id DESC LIMIT ?",
                    (since_id, limit)
                )
            else:
                cur = c.execute(
                    "SELECT id, frm, rcpt, subj, dt, text, atts FROM msg ORDER BY id DESC LIMIT ?",
                    (limit,)
                )
            rows = cur.fetchall()
            out = []
            for r in rows:
                try:
                    atts = json.loads(r[6] or "[]")
                    has_atts = bool(atts)
                except Exception:
                    has_atts = False
                out.append({
                    "id": r[0], "from": r[1], "to": r[2],
                    "subject": r[3], "date": r[4], "text": r[5],
                    "has_attachments": has_atts
                })
            return out

def get_message_by_id(msg_id: int) -> Optional[Dict]:
    """
    단건 조회: 본문·HTML·첨부까지 반환.
    반환 예:
      {
        "id": 123, "from": "...", "to": "...", "subject": "...",
        "date": "...", "text": "...", "html": "...",
        "attachments": [{"filename":"...","content_b64":"..."}]
      }
    """
    with _conn() as c:
        if _is_old_uid_schema(c):
            cur = c.execute(
                "SELECT uid, frm, subj, dt, text, html FROM msg WHERE uid = ? LIMIT 1",
                (msg_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "from": row[1], "to": "",
                "subject": row[2], "date": row[3],
                "text": row[4], "html": row[5],
                "attachments": []  # 구스키마에는 첨부 컬럼이 없음
            }
        else:
            cur = c.execute(
                "SELECT id, frm, rcpt, subj, dt, text, html, atts FROM msg WHERE id = ? LIMIT 1",
                (msg_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                atts = json.loads(row[7] or "[]")
            except Exception:
                atts = []
            return {
                "id": row[0], "from": row[1], "to": row[2],
                "subject": row[3], "date": row[4],
                "text": row[5], "html": row[6],
                "attachments": atts
            }

# ===== KV 유틸 (중복 방지, 임시 상태 저장 등) =====
def kv_get(k: str) -> Optional[str]:
    with _conn() as c:
        cur = c.execute("SELECT v FROM kv WHERE k=?", (k,))
        row = cur.fetchone()
        return row[0] if row else None

def kv_set(k: str, v: str):
    with _conn() as c:
        c.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v)
        )
        c.commit()

def kv_del(k: str):
    with _conn() as c:
        c.execute("DELETE FROM kv WHERE k=?", (k,))
        c.commit()
