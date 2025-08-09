import os, json, hmac, hashlib, requests
from email.utils import parseaddr
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse

load_dotenv()
app = FastAPI()

# === ENV ===
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
CAIA_INBOUND_SECRET = os.getenv("CAIA_INBOUND_SECRET", "")
SUBJECT_PREFIX = os.getenv("SUBJECT_PREFIX", "[CAIA-JOB]")
REPLY_FROM = os.getenv("REPLY_FROM")
ZENSPARK_INBOX = os.getenv("ZENSPARK_INBOX")
DIAG_TO = os.getenv("DIAG_TO", REPLY_FROM)

# === 공용 함수 ===
def send_mail(to_addr: str, subject: str, body_text: str, extra_headers: dict | None = None):
    """
    SendGrid Web API로 메일 발송 (안정/에러 가시성↑)
    """
    assert SENDGRID_API_KEY, "SENDGRID_API_KEY 누락"
    to = parseaddr(to_addr)[1] or to_addr
    frm = parseaddr(REPLY_FROM or "")[1] or REPLY_FROM
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": frm},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body_text}]
    }
    if extra_headers:
        payload["headers"] = {k: str(v) for k, v in extra_headers.items()}

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=20,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"SendGrid send failed: {r.status_code} {r.text[:240]}")

def parse_job_json_from_body(body: str):
    body = (body or "").strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        pass
    s, e = body.find("{"), body.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(body[s:e+1])
        except Exception:
            return None
    return None

def extract_job_id(subject: str) -> str:
    return subject.split("#")[-1].strip() if "#" in subject else "unknown"

def forward_to_zenspark(original_from: str, subject: str, body_json: dict):
    body_json = body_json or {}
    body_json.setdefault("meta", {})
    body_json["meta"]["original_from"] = parseaddr(original_from)[1] or original_from
    send_mail(
        to_addr=ZENSPARK_INBOX,
        subject=subject,
        body_text=json.dumps(body_json, ensure_ascii=False, indent=2),
        extra_headers={"X-CAIA-FWD": "1"}
    )

def ack_to_sender(sender: str, job_id: str, ok: bool, msg: str):
    state = "accepted" if ok else "rejected"
    subject = f"[CAIA-JOB-ACK] {state} #{job_id}"
    payload = {"state": state, "message": msg}
    send_mail(
        to_addr=sender,
        subject=subject,
        body_text=json.dumps(payload, ensure_ascii=False, indent=2),
        extra_headers={"X-CAIA-ACK": "1"}
    )

# === 보안: JSON 웹훅용 HMAC 검증 (Cloudflare Email Worker → /inbound/email) ===
def verify_sig(raw_body: bytes, sig_hex: str) -> bool:
    if not CAIA_INBOUND_SECRET or not sig_hex:
        return False
    mac = hmac.new(CAIA_INBOUND_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig_hex)

# --- 헬스/진단 ---
@app.get("/health")
def health():
    ok = bool(SENDGRID_API_KEY and REPLY_FROM and ZENSPARK_INBOX)
    return {"ok": ok, "sendgrid": bool(SENDGRID_API_KEY), "from": bool(REPLY_FROM), "to": bool(ZENSPARK_INBOX)}

@app.post("/diag/send")
def diag_send():
    """전송 경로 진단: DIAG_TO로 테스트 메일 1통 발송"""
    send_mail(DIAG_TO or REPLY_FROM, "[CAIA-JOB] diag #ping", '{"task":"ping"}')
    return {"ok": True, "sent_to": DIAG_TO or REPLY_FROM}

# --- 인바운드 #1: SendGrid Inbound Parse (multipart/form-data) ---
@app.post("/inbound/sendgrid")
async def inbound_sendgrid(
    request: Request,
    mail_from: str = Form(default=""),
    from_addr: str = Form(default=""),
    to: str = Form(default=""),
    subject: str = Form(default=""),
    text: str = Form(default=""),
    html: str = Form(default=""),
):
    sender = mail_from or from_addr or ""
    body = text or html or ""
    if SUBJECT_PREFIX not in subject:
        return {"ok": True, "skip": "no-prefix"}

    job_json = parse_job_json_from_body(body)
    job_id = extract_job_id(subject)

    if not job_json:
        ack_to_sender(sender, job_id, False, "본문에서 유효한 Job JSON을 찾지 못했습니다.")
        return {"ok": False, "reason": "no-json"}

    forward_to_zenspark(sender, subject, job_json)
    ack_to_sender(sender, job_id, True, "작업을 접수하여 젠스파크로 전달했습니다.")
    return {"ok": True, "job_id": job_id}

# --- 인바운드 #2: Cloudflare Email Worker (application/json + X-CAIA-SIGN) ---
@app.post("/inbound/email")
async def inbound_email(request: Request):
    raw = await request.body()
    sig = request.headers.get("x-caia-sign", "")
    if not verify_sig(raw, sig):
        return JSONResponse({"ok": False, "error": "bad-signature"}, status_code=401)

    data = json.loads(raw.decode("utf-8"))
    subject = data.get("subject","")
    text = data.get("text","") or data.get("html","") or ""
    sender = data.get("from","")

    if SUBJECT_PREFIX not in subject:
        return {"ok": True, "skip": "no-prefix"}

    job_json = parse_job_json_from_body(text)
    job_id = extract_job_id(subject)

    if not job_json:
        ack_to_sender(sender, job_id, False, "본문에서 유효한 Job JSON을 찾지 못했습니다.")
        return {"ok": False, "reason": "no-json"}

    forward_to_zenspark(sender, subject, job_json)
    ack_to_sender(sender, job_id, True, "작업을 접수하여 젠스파크로 전달했습니다.")
    return {"ok": True, "job_id": job_id}
