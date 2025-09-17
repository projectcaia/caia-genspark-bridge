# app.py (SendGrid Inbound Parse 전용 - 지능형 메일 처리 시스템)
import os
import io
import ssl
import json
import base64
import sqlite3
import datetime as dt
from typing import List, Optional, Dict

import requests

from fastapi import FastAPI, Request, UploadFile, Form, File, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr

# --- Optional SDKs ---
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Cc, Bcc, Content, Attachment
except Exception:
    SendGridAPIClient = None

APP_VER = "2025-09-17"

app = FastAPI(
    title="Caia Mail Bridge",
    version="2.5.0",
    openapi_version="3.1.0",
    servers=[{"url": "https://mail-bridge.up.railway.app"}],
)

# ===== ENV =====
def env_get(names, default=""):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
INBOUND_TOKEN = os.getenv("INBOUND_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")

SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")

# Alerts / Noti
ALERT_CLASSES = set([s.strip().upper() for s in os.getenv("ALERT_CLASSES","SENTINEL,REFLEX,ZENSPARK").split(",") if s.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# OpenAI Assistants
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ASSISTANT_ID = os.getenv("ASSISTANT_ID", "")
THREAD_ID = os.getenv("THREAD_ID", "")
AUTO_RUN = os.getenv("AUTO_RUN", "false").lower() in ("1","true","yes")

# SendGrid
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
sg = SendGridAPIClient(api_key=SENDGRID_API_KEY) if (SENDGRID_API_KEY and SendGridAPIClient) else None

# ===== DB =====
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    # 기본 테이블 생성
    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        recipients TEXT,
        subject TEXT,
        text TEXT,
        html TEXT,
        attachments_json TEXT,
        created_at TEXT,
        alert_class TEXT,
        importance REAL,
        sender_type TEXT DEFAULT 'unknown',
        mail_type TEXT DEFAULT 'general',
        auto_processed INTEGER DEFAULT 0,
        reply_sent INTEGER DEFAULT 0
    )
    """)
    
    # 기존 테이블에 새 컬럼 추가 (이미 있으면 무시)
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN sender_type TEXT DEFAULT 'unknown'")
    except: pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN mail_type TEXT DEFAULT 'general'")
    except: pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN auto_processed INTEGER DEFAULT 0")
    except: pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN reply_sent INTEGER DEFAULT 0")
    except: pass
    
    conn.commit()
    conn.close()

init_db()

# ===== Mail Analysis System =====
def analyze_and_classify_email(sender: str, subject: str, text: str) -> dict:
    """메일 내용 분석 및 분류"""
    
    # 1. 발신자 분류
    sender_type = "unknown"
    if any(agent in sender.lower() for agent in ["agent", "zenspark", "reflex", "sentinel"]):
        sender_type = "agent"
    elif "flyartnam" in sender.lower():  # 동현
        sender_type = "owner"
    else:
        sender_type = "external"
    
    # 2. 메일 유형 분류
    mail_type = "general"
    priority = "normal"
    
    # 보고서 패턴
    if any(word in (subject + text).lower() for word in ["report", "보고", "완료", "결과", "처리완료"]):
        mail_type = "report"
        priority = "high"
    
    # 지시/명령 패턴
    elif any(word in text.lower() for word in ["수행", "처리", "분석", "execute", "analyze", "해줘", "하세요"]):
        mail_type = "command"
        priority = "high"
    
    # 질문/요청 패턴
    elif "?" in text or any(word in text.lower() for word in ["요청", "부탁", "please", "could", "문의"]):
        mail_type = "request"
        priority = "normal"
    
    # 오류/경고 패턴
    elif any(word in (subject + text).lower() for word in ["error", "warning", "실패", "오류", "fail", "critical"]):
        mail_type = "alert"
        priority = "critical"
    
    # 3. 액션 결정
    actions = []
    if sender_type == "agent" and mail_type == "report":
        actions.append("forward_to_owner")
        actions.append("summarize")
    elif sender_type == "owner" and mail_type == "command":
        actions.append("distribute_to_agents")
        actions.append("track_execution")
    elif mail_type == "alert":
        actions.append("immediate_notification")
        actions.append("auto_troubleshoot")
    elif mail_type == "request":
        actions.append("process_request")
    
    return {
        "sender_type": sender_type,
        "mail_type": mail_type,
        "priority": priority,
        "actions": actions,
        "requires_reply": mail_type in ["request", "command"],
        "auto_reply_enabled": sender_type != "owner"  # 동현에게는 자동 응답 안 함
    }

def generate_intelligent_reply(analysis: dict, original_text: str, subject: str) -> Optional[str]:
    """상황에 맞는 자동 응답 생성"""
    
    if not analysis["auto_reply_enabled"]:
        return None
    
    mail_type = analysis["mail_type"]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if mail_type == "report":
        return f"""보고서 수신 확인

귀하의 보고서를 정상적으로 수신했습니다.
내용을 검토 후 필요시 추가 지시사항을 전달하겠습니다.

주요 내용:
- 수신 시각: {now}
- 분류: {mail_type}
- 우선순위: {analysis['priority']}

감사합니다.
Caia System"""
    
    elif mail_type == "request":
        return f"""요청 접수 완료

귀하의 요청을 접수했습니다.
처리 후 결과를 회신드리겠습니다.

예상 처리 시간: 30분 이내
요청 유형: {mail_type}

Caia System"""
    
    elif mail_type == "alert":
        return f"""[긴급] 오류 알림 수신

오류 보고를 수신했습니다.
즉시 담당자에게 전달하고 조치를 시작합니다.

조치 사항:
1. 관리자 알림 전송 완료
2. 자동 진단 시작
3. 30분 이내 상세 분석 결과 전달 예정

Caia Emergency Response System"""
    
    return None

# ===== Helpers =====
def _bearer_from_header(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def require_token(token_qs: Optional[str], request: Optional[Request] = None):
    incoming = token_qs or (_bearer_from_header(request) if request else None)
    if not AUTH_TOKEN or incoming != AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

def telegram_notify(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass

def html_to_text(html: str) -> str:
    """Very light HTML->text fallback: strip tags & unescape basic entities."""
    try:
        import html as _html
        import re as _re
        s = _re.sub(r'<br\s*/?>', '\n', html, flags=_re.I)
        s = _re.sub(r'</p\s*>', '\n', s, flags=_re.I)
        s = _re.sub(r'<[^>]+>', '', s)
        s = _html.unescape(s)
        return s.strip()
    except Exception:
        return (html or '').strip()

def assistants_log_and_maybe_run(sender: str, recipients: str, subject: str, text: str, html: Optional[str]):
    if not (OPENAI_API_KEY and THREAD_ID and OpenAI):
        return {"thread_message_id": None, "run_id": None}
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        msg = client.beta.threads.messages.create(
            thread_id=THREAD_ID,
            role="user",
            content=[{"type":"text","text": text}]
        )
        run = None
        if AUTO_RUN and ASSISTANT_ID:
            run = client.beta.threads.runs.create(thread_id=THREAD_ID, assistant_id=ASSISTANT_ID)
        return {"thread_message_id": msg.id, "run_id": getattr(run, "id", None) if run else None}
    except Exception as e:
        print(f"[Assistants ERROR] {e}")
        return {"thread_message_id": None, "run_id": None}

def b64_of_upload(f: UploadFile) -> str:
    data = f.file.read()
    f.file.seek(0)
    return base64.b64encode(data).decode("utf-8")

def parse_to_list(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = []
        for x in raw:
            items += [s.strip() for s in str(x).split(",") if s.strip()]
        return items
    return [s.strip() for s in str(raw).split(",") if s.strip()]

# ===== Pydantic Models =====
class AttachmentInModel(BaseModel):
    filename: str
    content_b64: str
    content_type: Optional[str] = "application/octet-stream"

class SendMailPayload(BaseModel):
    to: List[EmailStr]
    subject: str
    text: Optional[str] = ""
    html: Optional[str] = None
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    from_: Optional[EmailStr] = None
    reply_to: Optional[EmailStr] = None
    attachments_b64: Optional[List[AttachmentInModel]] = None

class ToolSendReq(BaseModel):
    to: List[EmailStr]
    subject: str
    text: str
    html: Optional[str] = None

class DeleteRequest(BaseModel):
    id: int

# ===== Send Functions =====
def send_via_sendgrid(payload: SendMailPayload) -> int:
    if not sg:
        raise RuntimeError("SendGrid client not configured")

    from_email = Email(str(payload.from_ or SENDER_DEFAULT))
    msg = Mail(from_email=from_email, subject=payload.subject)

    for addr in payload.to:
        msg.add_to(To(str(addr)))

    if payload.cc:
        for addr in payload.cc:
            msg.add_cc(Cc(str(addr)))

    if payload.bcc:
        for addr in payload.bcc:
            msg.add_bcc(Bcc(str(addr)))

    if payload.reply_to:
        msg.reply_to = Email(str(payload.reply_to))

    if payload.text is not None:
        msg.add_content(Content("text/plain", payload.text))

    if payload.html is not None:
        msg.add_content(Content("text/html", payload.html))

    if payload.attachments_b64:
        for att in payload.attachments_b64:
            attachment = Attachment()
            attachment.file_content = att.content_b64
            attachment.file_name = att.filename
            attachment.file_type = att.content_type or "application/octet-stream"
            attachment.disposition = "attachment"
            msg.add_attachment(attachment)

    resp = sg.send(msg)
    return int(resp.status_code)

def send_email(payload: SendMailPayload):
    if not sg:
        raise HTTPException(status_code=500, detail="SendGrid not configured")
    try:
        sc = send_via_sendgrid(payload)
        return {"via": "sendgrid", "status_code": sc}
    except Exception as e:
        print("[SendGrid ERROR]", e)
        raise HTTPException(status_code=502, detail=f"SendGrid send failed: {e}")

# ===== Routes =====

@app.get("/ping")
def ping():
    return {"pong": True}

@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VER,
        "sender": SENDER_DEFAULT,
        "sendgrid": bool(sg),
        "inbound": "sendgrid"
    }

@app.get("/status")
def status(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    cnt = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    conn.close()
    return {
        "ok": True,
        "version": APP_VER,
        "messages": cnt,
        "sendgrid": bool(sg),
        "inbound": "sendgrid"
    }

@app.post("/tool/send")
def tool_send(payload: ToolSendReq, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    model = SendMailPayload(
        to=payload.to,
        subject=payload.subject,
        text=payload.text,
        html=payload.html,
    )
    res = send_email(model)
    print(f"[TOOL-SEND] via={res['via']} to={','.join(payload.to)} subject={payload.subject}")
    return {"ok": True, **res}

# === 강화된 Inbound 처리 (422 에러 해결 + 지능형 처리) ===
@app.post("/inbound/sen")
async def inbound_sen_enhanced(
    request: Request,
    token: str = Query(...)
):
    if not INBOUND_TOKEN or token != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid inbound token")
    
    # Form 데이터를 직접 파싱 (필드명 문제 해결)
    try:
        form_data = await request.form()
    except Exception as e:
        print(f"[ERROR] Form parsing failed: {e}")
        return {"ok": False, "error": "form_parsing_failed"}
    
    # 필드 추출 (대소문자 무관, 다양한 필드명 지원)
    from_field = None
    to = None
    subject = ""
    text = ""
    html = None
    
    # 가능한 모든 필드명 체크
    for key in form_data:
        key_lower = key.lower()
        value = form_data[key]
        
        if key_lower in ['from', 'sender', 'email']:
            from_field = str(value)
        elif key_lower in ['to', 'recipient']:
            to = str(value)
        elif key_lower in ['subject']:
            subject = str(value)
        elif key_lower in ['text', 'plain', 'body']:
            text = str(value)
        elif key_lower in ['html', 'html_body']:
            html = str(value)
    
    # 디버그 로그
    print(f"[INBOUND] Fields received: {list(form_data.keys())}")
    print(f"[INBOUND] from={from_field}, to={to}, subject={subject[:50]}")
    
    # 기본값 설정
    from_field = from_field or "unknown@sendgrid.com"
    to = to or SENDER_DEFAULT
    
    # HTML to text 변환
    if not text and html:
        text = html_to_text(html)
    
    # 첨부파일 처리
    attachments = []
    for key in form_data:
        if 'attachment' in key.lower():
            file = form_data[key]
            if hasattr(file, 'filename'):
                attachments.append({
                    "filename": file.filename,
                    "content_b64": b64_of_upload(file),
                    "content_type": getattr(file, 'content_type', 'application/octet-stream')
                })
    
    # 메일 내용 분석
    analysis = analyze_and_classify_email(from_field, subject, text)
    
    # DB 저장 (분석 결과 포함)
    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(
            sender, recipients, subject, text, html, 
            attachments_json, created_at, alert_class, importance,
            sender_type, mail_type, auto_processed
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        from_field, to, subject, text, html, json.dumps(attachments), 
        now, analysis.get("mail_type"), 
        1.0 if analysis["priority"] == "critical" else 0.6 if analysis["priority"] == "high" else 0.4,
        analysis["sender_type"], analysis["mail_type"], 
        1 if analysis["auto_reply_enabled"] else 0
    ))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    
    # OpenAI Thread에 컨텍스트 포함해서 전달
    enhanced_text = f"""[메일 분석 결과]
발신자 유형: {analysis['sender_type']}
메일 유형: {analysis['mail_type']}
우선순위: {analysis['priority']}
필요 조치: {', '.join(analysis['actions'])}

[원본 메일]
From: {from_field}
To: {to}
Subject: {subject}

{text}"""
    
    # Assistant에게 전달 및 자동 실행
    assist_res = assistants_log_and_maybe_run(
        from_field, to, subject, enhanced_text, html
    )
    
    # 자동 응답 처리
    reply_sent = False
    if analysis["requires_reply"] and analysis["auto_reply_enabled"]:
        reply_text = generate_intelligent_reply(analysis, text, subject)
        if reply_text:
            # 자동 응답 발송
            auto_reply_payload = SendMailPayload(
                to=[EmailStr(from_field)],
                subject=f"Re: {subject}",
                text=reply_text,
                from_=EmailStr("caia@caia-agent.com")
            )
            try:
                send_email(auto_reply_payload)
                reply_sent = True
                print(f"[AUTO-REPLY] Sent to {from_field}")
            except Exception as e:
                print(f"[AUTO-REPLY ERROR] {e}")
    
    # 중요 메일 알림
    if analysis["priority"] in ["critical", "high"]:
        notification_text = f"""[{analysis['priority'].upper()}] {analysis['mail_type']}
From: {from_field}
Subject: {subject}
Actions: {', '.join(analysis['actions'])}
Auto-Reply: {'Sent' if reply_sent else 'N/A'}"""
        telegram_notify(notification_text)
    
    # 동현에게 에이전트 보고서 전달
    if analysis["sender_type"] == "agent" and "forward_to_owner" in analysis["actions"]:
        forward_payload = SendMailPayload(
            to=[EmailStr("flyartnam@gmail.com")],
            subject=f"[Agent Report] {subject}",
            text=f"""에이전트 보고서 전달

발신 에이전트: {from_field}
원본 제목: {subject}

=== 보고 내용 ===
{text}

=== 분석 결과 ===
메일 유형: {analysis['mail_type']}
우선순위: {analysis['priority']}
자동 처리: {'완료' if reply_sent else '대기'}""",
            from_=EmailStr("caia@caia-agent.com")
        )
        try:
            send_email(forward_payload)
            print(f"[FORWARD] Agent report forwarded to owner")
        except Exception as e:
            print(f"[FORWARD ERROR] {e}")
    
    print(f"[INBOUND-SEN] stored id={msg_id} from={from_field} type={analysis['mail_type']} priority={analysis['priority']}")
    
    return {
        "ok": True, 
        "id": msg_id, 
        "assistant": assist_res,
        "analysis": analysis,
        "auto_reply_sent": reply_sent
    }

# === 나머지 기존 엔드포인트들 (변경 없음) ===
@app.get("/inbox.json")
def inbox_json(limit: int = 20, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    conn = db()
    rows = conn.execute("""
        SELECT id, sender, recipients, subject, substr(text,1,500) AS text, created_at,
               CASE WHEN (attachments_json IS NOT NULL AND length(attachments_json) > 2 AND attachments_json != '[]') THEN 1 ELSE 0 END AS has_attachments,
               sender_type, mail_type, importance
        FROM messages ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {
        "ok": True,
        "messages": [
            {
                "id": r["id"],
                "from": r["sender"],
                "to": r["recipients"],
                "subject": r["subject"],
                "date": r["created_at"],
                "text": r["text"],
                "has_attachments": bool(r["has_attachments"]),
                "sender_type": r["sender_type"],
                "mail_type": r["mail_type"],
                "importance": r["importance"]
            } for r in rows
        ]
    }

@app.get("/mail/view")
def mail_view(id: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    files = json.loads(row["attachments_json"] or "[]")
    return {
        "ok": True,
        "message": {
            "id": row["id"],
            "from": row["sender"],
            "to": row["recipients"],
            "subject": row["subject"],
            "date": row["created_at"],
            "text": row["text"],
            "html": row["html"],
            "attachments": files,
            "sender_type": row["sender_type"],
            "mail_type": row["mail_type"],
            "importance": row["importance"]
        }
    }

# === 새로운 대시보드 엔드포인트 ===
@app.get("/dashboard/summary")
def dashboard_summary(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    
    conn = db()
    
    # 통계 조회
    stats = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN sender_type = 'agent' THEN 1 ELSE 0 END) as from_agents,
            SUM(CASE WHEN sender_type = 'owner' THEN 1 ELSE 0 END) as from_owner,
            SUM(CASE WHEN mail_type = 'report' THEN 1 ELSE 0 END) as reports,
            SUM(CASE WHEN mail_type = 'command' THEN 1 ELSE 0 END) as commands,
            SUM(CASE WHEN mail_type = 'alert' THEN 1 ELSE 0 END) as alerts,
            SUM(CASE WHEN auto_processed = 1 THEN 1 ELSE 0 END) as auto_processed
        FROM messages
        WHERE datetime(created_at) > datetime('now', '-7 days')
    """).fetchone()
    
    # 최근 중요 메일
    important = conn.execute("""
        SELECT id, sender, subject, mail_type, created_at
        FROM messages
        WHERE importance >= 0.6
        ORDER BY id DESC
        LIMIT 10
    """).fetchall()
    
    conn.close()
    
    return {
        "ok": True,
        "stats": {
            "total": stats["total"] or 0,
            "from_agents": stats["from_agents"] or 0,
            "from_owner": stats["from_owner"] or 0,
            "reports": stats["reports"] or 0,
            "commands": stats["commands"] or 0,
            "alerts": stats["alerts"] or 0,
            "auto_processed": stats["auto_processed"] or 0
        },
        "recent_important": [
            {
                "id": row["id"],
                "sender": row["sender"],
                "subject": row["subject"],
                "mail_type": row["mail_type"],
                "created_at": row["created_at"]
            } for row in important
        ],
        "system_status": "operational"
    }

# 기존 엔드포인트들은 그대로 유지...
