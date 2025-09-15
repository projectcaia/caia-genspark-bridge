# store.py
import sqlite3, os, time, json, hashlib
from typing import List, Dict, Optional

from app import simple_alert_parse, APPROVAL_IMPORTANCE_MIN, APPROVAL_SENDERS
from server.utils.telegram_notify import send_approval_request

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

def _get_messages_columns(c) -> List[str]:
    cur = c.execute("PRAGMA table_info(messages)")
    return [row[1] for row in cur.fetchall()]

def _table_exists(c, name: str) -> bool:
    cur = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def _ensure_new_schema(c):
    """
    신 스키마(id AUTOINCREMENT) 없으면 생성.
    필요한 보조 인덱스도 같이 생성.
    """
    if not _table_exists(c, "messages"):
        c.execute("""
            CREATE TABLE messages (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                frm  TEXT,
                rcpt TEXT,
                subj TEXT,
                dt   TEXT,
                text TEXT,
                html TEXT,
                atts TEXT,
                ts   INTEGER,
                hash TEXT,
                needs_approval INTEGER DEFAULT 0,
                approved INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                ersp_event TEXT,
                ersp_interpretation TEXT,
                ersp_lesson TEXT,
                ersp_if_then TEXT
            )
        """)
    # 인덱스(존재하면 무시)
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_id  ON messages(id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_ts  ON messages(ts)")
    # 디듀프용 유니크 인덱스 (이미 있으면 무시)
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_hash ON messages(hash)")
    except Exception:
        pass

def _ensure_approval_columns(c):
    cols = _get_messages_columns(c)
    if "needs_approval" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN needs_approval INTEGER DEFAULT 0")
        except Exception:
            pass
    if "approved" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN approved INTEGER DEFAULT 0")
        except Exception:
            pass
    if "processed" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN processed INTEGER DEFAULT 0")
        except Exception:
            pass

def _ensure_hash_column(c):
    """기존 신 스키마에 hash 컬럼이 없다면 추가 + 유니크 인덱스 생성."""
    cols = _get_messages_columns(c)
    if "hash" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN hash TEXT")
        except Exception:
            pass
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_hash ON messages(hash)")
    except Exception:
        pass

def _ensure_ersp_columns(c):
    cols = _get_messages_columns(c)
    if "ersp_event" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN ersp_event TEXT")
        except Exception:
            pass
    if "ersp_interpretation" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN ersp_interpretation TEXT")
        except Exception:
            pass
    if "ersp_lesson" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN ersp_lesson TEXT")
        except Exception:
            pass
    if "ersp_if_then" not in cols:
        try:
            c.execute("ALTER TABLE messages ADD COLUMN ersp_if_then TEXT")
        except Exception:
            pass

def init_db():
    """
    - 테이블 없으면 신 스키마로 생성
    - 과거 구스키마(uid 기반)도 허용(읽기/쓰기 로직에서 분기)
    - kv 테이블은 공용 상태저장용으로 항상 보장
    """
    with _conn() as c:
        if not _table_exists(c, "messages"):
            # 이전 이름이 msg인 경우 자동 마이그레이션
            if _table_exists(c, "msg"):
                c.execute("ALTER TABLE msg RENAME TO messages")
                c.execute("DROP INDEX IF EXISTS idx_msg_id")
                c.execute("DROP INDEX IF EXISTS idx_msg_ts")
                c.execute("DROP INDEX IF EXISTS idx_msg_hash")
            _ensure_new_schema(c)
            c.commit()
        else:
            # 기존 테이블이 있으면 인덱스/해시/승인 컬럼 보장
            try:
                _ensure_new_schema(c)
                _ensure_hash_column(c)
                _ensure_approval_columns(c)
                _ensure_ersp_columns(c)
                c.commit()
            except Exception:
                # 구스키마(uid 기반)일 수도 있으므로 조용히 통과
                pass
        c.execute("""CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)""")
        c.commit()

def _is_old_uid_schema(c) -> bool:
    cols = _get_messages_columns(c)
    # 옛 스키마: uid, frm, subj, dt, text, html, ts
    return ("uid" in cols) and ("frm" in cols) and ("subj" in cols)

def _make_hash(frm: str, rcpt: str, subj: str, text: str) -> str:
    base = f"{frm}|{rcpt}|{subj}|{text}"
    return hashlib.sha256(base.encode("utf-8", "ignore")).hexdigest()

def save_messages(msgs: List[Dict]):
    """
    msgs = [{
      "from": str, "to": str, "subject": str, "date": str,
      "text": str, "html": Optional[str], "attachments": list[{"filename","content_b64"}]
    }]
    - 신 스키마: hash 기반 디듀프(UNIQUE)로 중복 저장 방지
    - 구 스키마: 기존 로직 유지
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
                    "INSERT OR IGNORE INTO messages(uid, frm, subj, dt, text, html, ts) VALUES(?,?,?,?,?,?,?)",
                    (int(uid), frm, subj, dt, text, html, now)
                )
            c.commit()
        else:
            # 신 스키마(id AUTOINCREMENT) + 디듀프
            for m in msgs:
                frm  = (m.get("from") or "").strip()
                rcpt = (m.get("to") or "").strip()
                subj = (m.get("subject") or "").strip()
                dt   = (m.get("date") or "").strip()
                text = (m.get("text") or "").strip()
                html = m.get("html")
                atts_list = m.get("attachments") or []
                atts = json.dumps(atts_list)
                _, importance = simple_alert_parse(subj, text)
                needs_appr = bool(atts_list) or (importance >= APPROVAL_IMPORTANCE_MIN) or (frm.lower() in APPROVAL_SENDERS)
                hval = _make_hash(frm, rcpt, subj, text)
                ersp = m.get("ersp") or {}
                ev = m.get("ersp_event") or ersp.get("event")
                interp = m.get("ersp_interpretation") or ersp.get("interpretation")
                lesson = m.get("ersp_lesson") or ersp.get("lesson")
                if_then = m.get("ersp_if_then") or ersp.get("if_then")
                cur = c.execute(
                    "INSERT OR IGNORE INTO messages(frm, rcpt, subj, dt, text, html, atts, ts, hash, needs_approval, approved, processed, ersp_event, ersp_interpretation, ersp_lesson, ersp_if_then) VALUES(?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?)",
                    (frm, rcpt, subj, dt, text, html, atts, now, hval, needs_appr, ev, interp, lesson, if_then)
                )
                if cur.rowcount and needs_appr:
                    try:
                        send_approval_request(cur.lastrowid, frm, subj)
                    except Exception:
                        pass
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
                    "SELECT uid, frm, subj, dt, text FROM messages WHERE uid > ? ORDER BY uid DESC LIMIT ?",
                    (since_id, limit)
                )
            else:
                cur = c.execute(
                    "SELECT uid, frm, subj, dt, text FROM messages ORDER BY uid DESC LIMIT ?",
                    (limit,)
                )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0], "from": r[1], "to": "",
                    "subject": r[2], "date": r[3], "text": r[4],
                    "has_attachments": False,
                    "ersp_event": None,
                    "ersp_interpretation": None,
                    "ersp_lesson": None,
                    "ersp_if_then": None,
                }
                for r in rows
            ]
        else:
            # 신 스키마(id AUTOINCREMENT)
            if since_id:
                cur = c.execute(
                    "SELECT id, frm, rcpt, subj, dt, text, atts, ersp_event, ersp_interpretation, ersp_lesson, ersp_if_then FROM messages WHERE id > ? ORDER BY id DESC LIMIT ?",
                    (since_id, limit)
                )
            else:
                cur = c.execute(
                    "SELECT id, frm, rcpt, subj, dt, text, atts, ersp_event, ersp_interpretation, ersp_lesson, ersp_if_then FROM messages ORDER BY id DESC LIMIT ?",
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
                    "has_attachments": has_atts,
                    "ersp_event": r[7],
                    "ersp_interpretation": r[8],
                    "ersp_lesson": r[9],
                    "ersp_if_then": r[10],
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
                "SELECT uid, frm, subj, dt, text, html FROM messages WHERE uid = ? LIMIT 1",
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
                "SELECT id, frm, rcpt, subj, dt, text, html, atts, ersp_event, ersp_interpretation, ersp_lesson, ersp_if_then FROM messages WHERE id = ? LIMIT 1",
                (msg_id,),
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
                "attachments": atts,
                "ersp_event": row[8],
                "ersp_interpretation": row[9],
                "ersp_lesson": row[10],
                "ersp_if_then": row[11],
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
