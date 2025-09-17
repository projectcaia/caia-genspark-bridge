# app.py (SendGrid Inbound Parse 전용 - 지능형 메일 처리 시스템 v3.0)
import os
import io
import ssl
import json
import base64
import sqlite3
import datetime as dt
import asyncio
import time
from typing import List, Optional, Dict, Any

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

APP_VER = "2025-01-17-v3"

app = FastAPI(
    title="Caia Mail Bridge - Intelligent",
    version="3.0.0",
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

# 지시어 키워드
INSTRUCTION_KEYWORDS = os.getenv("INSTRUCTION_KEYWORDS", "답장으로,텔레그램으로,보고,보내줘").split(",")

# ===== DB =====
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
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
        reply_sent INTEGER DEFAULT 0,
        assistant_response TEXT,
        actions_executed TEXT
    )
    """)
    
    # 새 컬럼 추가 (이미 있으면 무시)
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN assistant_response TEXT")
    except: pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN actions_executed TEXT")
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
        priority = "high" if sender_type == "agent" else "normal"
    
    # 지시/명령 패턴
    elif any(word in text.lower() for word in ["수행", "처리", "분석", "execute", "analyze", "해줘", "하세요", "보내줘"]):
        mail_type = "command"
        priority = "high"
    
    # 오류/경고 패턴 - 권한 문제 포함
    elif any(word in (subject + text).lower() for word in ["error", "warning", "실패", "오류", "fail", "critical", "권한", "permission", "denied", "못했"]):
        mail_type = "alert"
        priority = "critical"
    
    # 질문/요청 패턴
    elif "?" in text or any(word in text.lower() for word in ["요청", "부탁", "please", "could", "문의"]):
        mail_type = "request"
        priority = "normal"
    
    # 3. 액션 결정
    actions = []
    if sender_type == "agent" and mail_type == "report":
        actions.append("forward_to_owner")
        actions.append("summarize")
    elif sender_type == "agent" and mail_type == "alert":
        actions.append("immediate_notification")
        actions.append("analyze_problem")
    elif sender_type == "owner" and mail_type == "command":
        actions.append("execute_command")
        actions.append("track_execution")
    elif mail_type == "request":
        actions.append("process_request")
    
    return {
        "sender_type": sender_type,
        "mail_type": mail_type,
        "priority": priority,
        "actions": actions,
        "requires_reply": mail_type in ["request", "command"],
        "auto_reply_enabled": sender_type != "owner"
    }

def extract_instructions_from_text(text: str) -> dict:
    """메일 본문에서 구체적인 지시사항 추출"""
    
    instructions = {
        "method": "auto",
        "targets": [],
        "actions": [],
        "attachments_action": None
    }
    
    text_lower = text.lower()
    
    # 발송 대상 추출
    if "에게" in text or "한테" in text:
        # "00씨에게", "팀장님께" 등 패턴 추출
        import re
        targets = re.findall(r'([가-힣]+)(?:씨|님)?(?:에게|한테|께)', text)
        instructions["targets"] = targets
    
    # 메일 발송 지시
    if any(keyword in text for keyword in ["보내줘", "발송", "전달", "회신", "답장"]):
        instructions["actions"].append("send_email")
        if "답장" in text or "회신" in text:
            instructions["method"] = "reply"
        else:
            instructions["method"] = "forward"
    
    # 첨부파일 처리 지시
    if "첨부" in text:
        if "분석" in text:
            instructions["attachments_action"] = "analyze"
        if "요약" in text:
            instructions["attachments_action"] = "summarize"
    
    # 텔레그램 지시
    if "텔레그램" in text or "알림" in text_lower:
        instructions["method"] = "telegram"
    
    # 보고서 처리
    if "보고" in text or "정리" in text:
        instructions["actions"].append("create_report")
    
    return instructions

# ===== Assistant Integration with Execution =====
async def get_assistant_response_with_execution(sender: str, subject: str, text: str, analysis: dict, instructions: dict) -> dict:
    """Assistant에게 메일 전달하고 응답 받아서 실행"""
    
    if not (OPENAI_API_KEY and THREAD_ID and OpenAI):
        return {"success": False, "message": "OpenAI not configured"}
    
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 1. Thread에 메시지 추가 (컨텍스트 포함)
        enhanced_message = f"""[메일 수신]
발신자: {sender} (유형: {analysis['sender_type']})
제목: {subject}
메일 유형: {analysis['mail_type']}
우선순위: {analysis['priority']}
감지된 지시사항: {json.dumps(instructions, ensure_ascii=False)}

[본문]
{text}

[요청사항]
1. 이 메일의 의도를 파악하고
2. 필요한 작업을 JSON 형식으로 응답해주세요:
{{
    "understanding": "메일 내용 이해 요약",
    "actions": [
        {{"type": "send_email", "to": "이메일주소", "subject": "제목", "content": "내용"}},
        {{"type": "telegram", "message": "알림내용"}},
        {{"type": "summarize", "target": "대상"}},
        {{"type": "report", "content": "보고내용"}}
    ],
    "immediate_response": "즉시 발신자에게 보낼 답장 (있다면)"
}}"""
        
        msg = client.beta.threads.messages.create(
            thread_id=THREAD_ID,
            role="user",
            content=[{"type":"text","text": enhanced_message}]
        )
        
        # 2. Assistant 실행 및 응답 대기
        if ASSISTANT_ID:
            run = client.beta.threads.runs.create(
                thread_id=THREAD_ID,
                assistant_id=ASSISTANT_ID
            )
            
            # 3. Run 완료 대기 (최대 30초)
            max_wait = 30
            wait_time = 0
            while wait_time < max_wait:
                run_status = client.beta.threads.runs.retrieve(
                    thread_id=THREAD_ID,
                    run_id=run.id
                )
                
                if run_status.status == 'completed':
                    break
                elif run_status.status in ['failed', 'cancelled', 'expired']:
                    return {"success": False, "message": f"Run failed: {run_status.status}"}
                
                await asyncio.sleep(1)
                wait_time += 1
            
            # 4. 응답 메시지 가져오기
            messages = client.beta.threads.messages.list(
                thread_id=THREAD_ID,
                limit=1
            )
            
            if messages.data:
                assistant_response = messages.data[0].content[0].text.value
                
                # 5. JSON 응답 파싱
                try:
                    # JSON 블록 추출
                    import re
                    json_match = re.search(r'\{.*\}', assistant_response, re.DOTALL)
                    if json_match:
                        response_data = json.loads(json_match.group())
                    else:
                        response_data = {"understanding": assistant_response, "actions": []}
                except:
                    response_data = {"understanding": assistant_response, "actions": []}
                
                # 6. 액션 실행
                executed_actions = []
                for action in response_data.get("actions", []):
                    result = await execute_action(action, sender)
                    executed_actions.append({
                        "action": action,
                        "result": result
                    })
                
                return {
                    "success": True,
                    "response": response_data,
                    "executed_actions": executed_actions,
                    "assistant_message_id": msg.id,
                    "run_id": run.id
                }
        
        return {"success": False, "message": "No assistant configured"}
        
    except Exception as e:
        print(f"[Assistant ERROR] {e}")
        return {"success": False, "message": str(e)}

async def execute_action(action: dict, original_sender: str) -> dict:
    """Assistant가 지시한 액션 실행"""
    
    action_type = action.get("type", "")
    
    try:
        if action_type == "send_email":
            # 메일 발송
            to_addresses = action.get("to", "")
            if isinstance(to_addresses, str):
                # 이메일 주소 추출 또는 매핑
                if "@" in to_addresses:
                    to_list = [to_addresses]
                else:
                    # 이름 → 이메일 매핑 (필요시 확장)
                    email_map = {
                        "동현": "flyartnam@gmail.com",
                        "팀장": "team-lead@example.com",
                        # 더 추가 가능
                    }
                    to_list = [email_map.get(to_addresses, f"{to_addresses}@example.com")]
            else:
                to_list = action.get("to", [])
            
            payload = SendMailPayload(
                to=to_list,
                subject=action.get("subject", "카이아 자동 발송"),
                text=action.get("content", ""),
                from_="caia@caia-agent.com"
            )
            
            result = send_email(payload)
            return {"success": True, "type": "email_sent", "details": result}
            
        elif action_type == "telegram":
            # 텔레그램 알림
            message = action.get("message", "")
            telegram_notify(f"[카이아 자동 처리]\n{message}")
            return {"success": True, "type": "telegram_sent"}
            
        elif action_type == "summarize":
            # 요약 작업 (추가 구현 가능)
            target = action.get("target", "")
            return {"success": True, "type": "summarized", "target": target}
            
        elif action_type == "report":
            # 보고서 생성
            content = action.get("content", "")
            # 동현에게 보고
            payload = SendMailPayload(
                to=["flyartnam@gmail.com"],
                subject="[카이아 보고서] 자동 생성",
                text=content,
                from_="caia@caia-agent.com"
            )
            send_email(payload)
            return {"success": True, "type": "report_created"}
            
        else:
            return {"success": False, "message": f"Unknown action type: {action_type}"}
            
    except Exception as e:
        return {"success": False, "message": str(e)}

def generate_intelligent_reply(analysis: dict, original_text: str, subject: str, assistant_response: dict = None) -> Optional[str]:
    """상황에 맞는 자동 응답 생성 (Assistant 응답 활용)"""
    
    if not analysis["auto_reply_enabled"]:
        return None
    
    # Assistant 응답이 있으면 그것을 우선 사용
    if assistant_response and assistant_response.get("success"):
        immediate_response = assistant_response.get("response", {}).get("immediate_response")
        if immediate_response:
            return immediate_response
    
    # 기본 템플릿 응답
    mail_type = analysis["mail_type"]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if mail_type == "report":
        return f"""보고서 수신 확인

귀하의 보고서를 정상적으로 수신했습니다.
카이아가 내용을 분석하여 필요한 조치를 취하고 있습니다.

처리 상태:
- 수신 시각: {now}
- 자동 분석: 진행 중
- 관리자 전달: 완료

감사합니다.
Caia Intelligent System"""
    
    elif mail_type == "alert":
        return f"""[긴급] 문제 상황 감지

보고해주신 문제를 확인했습니다.
카이아가 즉시 분석하여 관리자에게 전달했습니다.

조치 사항:
1. 관리자 알림: 완료
2. 문제 분석: 진행 중
3. 해결 방안: 수립 중

빠른 시일 내에 해결하겠습니다.
Caia Emergency Response"""
    
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
        "intelligent": True
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
        "assistant": bool(ASSISTANT_ID),
        "auto_run": AUTO_RUN
    }

@app.post("/tool/send")
def tool_send(payload: ToolSendReq, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    
    # 디버깅: 받은 데이터 확인
    print(f"[DEBUG] Raw payload: {payload}")
    print(f"[DEBUG] payload.to type: {type(payload.to)}")
    print(f"[DEBUG] payload.to value: {payload.to}")
    
    # to 필드 타입 체크 및 변환
    to_list = payload.to
    if isinstance(to_list, str):
        # 문자열이면 리스트로 변환
        to_list = [to_list]
        print(f"[DEBUG] Converted string to list: {to_list}")
    elif not isinstance(to_list, list):
        # 리스트도 문자열도 아니면 강제 변환
        to_list = [str(to_list)]
        print(f"[DEBUG] Force converted to list: {to_list}")
    
    # 이메일 주소 정리 (공백 제거 등)
    to_list = [email.strip() for email in to_list if email and email.strip()]
    
    if not to_list:
        raise HTTPException(status_code=400, detail="No valid recipients")
    
    print(f"[DEBUG] Final to_list: {to_list}")
    
    # SendMailPayload 생성
    model = SendMailPayload(
        to=to_list,  # 정리된 리스트 사용
        subject=payload.subject,
        text=payload.text,
        html=payload.html,
    )
    
    # 메일 발송
    res = send_email(model)
    
    # 로그
    print(f"[TOOL-SEND] via={res['via']} to={','.join(to_list)} subject={payload.subject} status={res.get('status_code')}")
    
    return {"ok": True, **res}

# === 지능형 Inbound 처리 (완전 개선) ===
@app.post("/inbound/sen")
async def inbound_sen_intelligent(
    request: Request,
    token: str = Query(...)
):
    if not INBOUND_TOKEN or token != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid inbound token")
    
    # Form 데이터 파싱
    try:
        form_data = await request.form()
    except Exception as e:
        print(f"[ERROR] Form parsing failed: {e}")
        return {"ok": False, "error": "form_parsing_failed"}
    
    # 필드 추출
    from_field = None
    to = None
    subject = ""
    text = ""
    html = None
    
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
    
    print(f"[INBOUND] from={from_field}, subject={subject[:50]}")
    
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
    
    # === 지능형 분석 시작 ===
    
    # 1. 메일 분석
    analysis = analyze_and_classify_email(from_field, subject, text)
    
    # 2. 지시사항 추출
    instructions = extract_instructions_from_text(text)
    
    # 3. Assistant 처리 (owner 메일이거나 복잡한 지시가 있을 때)
    assistant_result = None
    if analysis["sender_type"] == "owner" or instructions["actions"] or analysis["priority"] == "critical":
        assistant_result = await get_assistant_response_with_execution(
            from_field, subject, text, analysis, instructions
        )
    
    # 4. DB 저장
    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(
            sender, recipients, subject, text, html, 
            attachments_json, created_at, alert_class, importance,
            sender_type, mail_type, auto_processed,
            assistant_response, actions_executed
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        from_field, to, subject, text, html, json.dumps(attachments), 
        now, analysis.get("mail_type"), 
        1.0 if analysis["priority"] == "critical" else 0.6 if analysis["priority"] == "high" else 0.4,
        analysis["sender_type"], analysis["mail_type"], 
        1 if assistant_result and assistant_result.get("success") else 0,
        json.dumps(assistant_result) if assistant_result else None,
        json.dumps(assistant_result.get("executed_actions")) if assistant_result else None
    ))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    
    # 5. 자동 응답 처리
    reply_sent = False
    if analysis["requires_reply"] and analysis["auto_reply_enabled"]:
        reply_text = generate_intelligent_reply(analysis, text, subject, assistant_result)
        if reply_text:
            auto_reply_payload = SendMailPayload(
                to=[from_field],
                subject=f"Re: {subject}",
                text=reply_text,
                from_="caia@caia-agent.com"
            )
            try:
                send_email(auto_reply_payload)
                reply_sent = True
                print(f"[AUTO-REPLY] Sent to {from_field}")
            except Exception as e:
                print(f"[AUTO-REPLY ERROR] {e}")
    
    # 6. 중요 알림 처리
    notification_text = ""
    
    # 에이전트 오류/권한 문제 감지
    if analysis["sender_type"] == "agent" and analysis["mail_type"] == "alert":
        notification_text = f"""⚠️ 에이전트 문제 감지
From: {from_field}
Subject: {subject}
문제: {text[:200]}
자동 처리: {'완료' if assistant_result and assistant_result.get("success") else '실패'}"""
    
    # Owner 명령 처리 결과
    elif analysis["sender_type"] == "owner" and assistant_result:
        if assistant_result.get("success"):
            executed = len(assistant_result.get("executed_actions", []))
            notification_text = f"""✅ 동현 지시 처리 완료
Subject: {subject}
실행된 작업: {executed}개
상태: 성공"""
        else:
            notification_text = f"""❌ 동현 지시 처리 실패
Subject: {subject}
오류: {assistant_result.get('message')}"""
    
    # 일반 중요 메일
    elif analysis["priority"] == "critical":
        notification_text = f"""🚨 긴급 메일
From: {from_field}
Subject: {subject}
Type: {analysis['mail_type']}"""
    
    if notification_text:
        telegram_notify(notification_text)
    
    # 7. 에이전트 보고서 요약 및 전달
    if analysis["sender_type"] == "agent" and analysis["mail_type"] == "report":
        # Assistant가 이미 처리했으면 스킵
        if not (assistant_result and assistant_result.get("success")):
            summary = f"""에이전트 보고서 자동 전달

발신: {from_field}
제목: {subject}

=== 보고 내용 요약 ===
{text[:500]}...

=== 카이아 분석 ===
유형: {analysis['mail_type']}
우선순위: {analysis['priority']}
필요 조치: {', '.join(analysis['actions'])}"""
            
            forward_payload = SendMailPayload(
                to=["flyartnam@gmail.com"],
                subject=f"[에이전트 보고] {subject}",
                text=summary,
                from_="caia@caia-agent.com"
            )
            try:
                send_email(forward_payload)
                print(f"[FORWARD] Agent report forwarded to owner")
            except Exception as e:
                print(f"[FORWARD ERROR] {e}")
    
    print(f"[INBOUND-INTELLIGENT] id={msg_id} from={from_field} type={analysis['mail_type']} assistant_processed={bool(assistant_result)}")
    
    return {
        "ok": True, 
        "id": msg_id, 
        "analysis": analysis,
        "instructions": instructions,
        "assistant_result": assistant_result,
        "auto_reply_sent": reply_sent
    }

# === 기존 엔드포인트들 ===
@app.get("/inbox.json")
def inbox_json(limit: int = 20, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    conn = db()
    rows = conn.execute("""
        SELECT id, sender, recipients, subject, substr(text,1,500) AS text, created_at,
               CASE WHEN (attachments_json IS NOT NULL AND length(attachments_json) > 2 AND attachments_json != '[]') THEN 1 ELSE 0 END AS has_attachments,
               sender_type, mail_type, importance, auto_processed
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
                "importance": r["importance"],
                "auto_processed": bool(r["auto_processed"])
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
    assistant_response = json.loads(row["assistant_response"] or "{}")
    actions_executed = json.loads(row["actions_executed"] or "[]")
    
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
            "importance": row["importance"],
            "assistant_response": assistant_response,
            "actions_executed": actions_executed
        }
    }

@app.get("/mail/attach")
def mail_attach(id: int = Query(...), idx: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT attachments_json FROM messages WHERE id=?", (id,)).fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="message not found")
    
    files = json.loads(row["attachments_json"] or "[]")
    if idx < 0 or idx >= len(files):
        raise HTTPException(status_code=404, detail="attachment not found")
    
    att = files[idx]
    content = base64.b64decode(att["content_b64"])
    
    return StreamingResponse(
        io.BytesIO(content),
        media_type=att.get("content_type", "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{att.get("filename", "file")}"'
        }
    )
    
@app.get("/test/send-email")
def test_send_email_get(
    to: str = Query(..., description="수신자 이메일"),
    subject: str = Query("Test Email", description="제목"),
    token: Optional[str] = Query(None),
    request: Request = None  # Request 객체 추가
):
    """GET 방식 간단 메일 발송 테스트"""
    require_token(token, request)  # request 전달
    
    print(f"[TEST-SEND-EMAIL] GET request to={to} subject={subject}")
    
    # SendGrid로 발송
    payload = SendMailPayload(
        to=[to],  # 리스트로 변환
        subject=subject,
        text=f"Test email sent via GET method at {dt.datetime.now()}",
        from_="caia@caia-agent.com"
    )
    
    try:
        res = send_email(payload)
        print(f"[TEST-SEND-EMAIL] Success: {res}")
        return {"ok": True, "sent_to": to, "subject": subject, **res}
    except Exception as e:
        print(f"[TEST-SEND-EMAIL] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
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
    
    # 최근 처리된 owner 명령
    owner_commands = conn.execute("""
        SELECT id, subject, created_at, assistant_response
        FROM messages
        WHERE sender_type = 'owner' AND mail_type = 'command'
        ORDER BY id DESC
        LIMIT 5
    """).fetchall()
    
    # 최근 에이전트 문제
    agent_alerts = conn.execute("""
        SELECT id, sender, subject, created_at
        FROM messages
        WHERE sender_type = 'agent' AND mail_type = 'alert'
        ORDER BY id DESC
        LIMIT 5
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
        "recent_owner_commands": [
            {
                "id": row["id"],
                "subject": row["subject"],
                "created_at": row["created_at"],
                "processed": bool(row["assistant_response"])
            } for row in owner_commands
        ],
        "recent_agent_alerts": [
            {
                "id": row["id"],
                "sender": row["sender"],
                "subject": row["subject"],
                "created_at": row["created_at"]
            } for row in agent_alerts
        ],
        "system_status": "intelligent_mode"
    }

# === 새로운 테스트 엔드포인트 ===
@app.post("/test/assistant")
async def test_assistant(
    text: str,
    token: Optional[str] = Query(None),
    request: Request = None
):
    """Assistant 테스트용 엔드포인트"""
    require_token(token, request)
    
    # 테스트 메일처럼 처리
    analysis = analyze_and_classify_email("test@example.com", "Test Subject", text)
    instructions = extract_instructions_from_text(text)
    
    result = await get_assistant_response_with_execution(
        "test@example.com",
        "Test Subject",
        text,
        analysis,
        instructions
    )
    
    return {
        "ok": True,
        "analysis": analysis,
        "instructions": instructions,
        "assistant_result": result
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
