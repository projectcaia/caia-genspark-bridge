import os, time, json, ssl, smtplib, email
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from imapclient import IMAPClient
from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.zoho.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "false").lower() == "true"

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "120"))
SUBJECT_PREFIX = os.getenv("SUBJECT_PREFIX", "[CAIA-JOB]")
REPLY_FROM = os.getenv("REPLY_FROM") or SMTP_USER
ZENSPARK_INBOX = os.getenv("ZENSPARK_INBOX", "jobs@caia-agent.com")

def _env_ready():
    missing = []
    for k in ["IMAP_HOST","IMAP_PORT","IMAP_USER","IMAP_PASSWORD",
              "SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_PASSWORD"]:
        if not os.getenv(k):
            missing.append(k)
    if missing:
        print(f"[ENV] 누락 변수: {missing}")
        return False
    if not ZENSPARK_INBOX:
        print("[ENV] ZENSPARK_INBOX가 비어 있음 (권장: 별도 수신함 주소 설정)")
    if IMAP_USER and ZENSPARK_INBOX and IMAP_USER.lower() == ZENSPARK_INBOX.lower():
        print("[WARN] ZENSPARK_INBOX가 IMAP_USER와 동일 → 자체 루프 위험. 헤더/가드로 차단 시도.")
    safe = {
        "IMAP_HOST": IMAP_HOST, "IMAP_PORT": IMAP_PORT, "IMAP_USER": IMAP_USER,
        "SMTP_HOST": SMTP_HOST, "SMTP_PORT": SMTP_PORT, "SMTP_USER": SMTP_USER,
        "REPLY_FROM": REPLY_FROM, "ZENSPARK_INBOX": ZENSPARK_INBOX
    }
    print("[ENV] ", safe)
    return True

def send_mail(to_addr: str, subject: str, body_text: str, extra_headers: dict | None = None):
    msg = EmailMessage()
    msg["From"] = REPLY_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(REPLY_FROM or SMTP_USER).split("@")[-1])
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = str(v)
    msg.set_content(body_text)

    context = ssl.create_default_context()
    # 587 또는 플래그면 STARTTLS, 아니면 SSL(465)
    if SMTP_STARTTLS or SMTP_PORT == 587:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls(context=context)
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as s:
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)

def parse_job_json_from_body(body: str):
    body = body.strip()
    try:
        return json.loads(body)
    except Exception:
        pass
    start = body.find("{")
    end = body.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = body[start:end+1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None

def _extract_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")

def fetch_unseen_jobs():
    context = ssl.create_default_context()
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True, ssl_context=context) as server:
        server.login(IMAP_USER, IMAP_PASSWORD)
        server.select_folder("INBOX")
        # 서버측 필터: 제목만. (헤더/보낸사람 필터는 클라이언트에서 가드)
        messages = server.search(['UNSEEN', 'SUBJECT', SUBJECT_PREFIX])
        if not messages:
            return []

        fetched = server.fetch(messages, ['ENVELOPE', 'RFC822', 'UID'])
        jobs = []
        for uid, data in fetched.items():
            raw = data[b'RFC822']
            msg = email.message_from_bytes(raw)

            subject = str(email.header.make_header(email.header.decode_header(msg.get('Subject', ''))))
            from_addr = parseaddr(msg.get('From'))[1] or ""

            # 루프 방지: 우리가 보낸 메일(X-CAIA-FWD/ACK) 또는 REPLY_FROM에서 온 메일은 즉시 읽음 처리 후 스킵
            if (msg.get('X-CAIA-FWD') == '1') or (msg.get('X-CAIA-ACK') == '1') or \
               (REPLY_FROM and from_addr.lower() == REPLY_FROM.lower()):
                server.add_flags(uid, [b'\\Seen'])
                continue

            body_text = _extract_text(msg)
            job = {
                "uid": uid,
                "subject": subject,
                "from": from_addr,
                "body": body_text,
                "json": parse_job_json_from_body(body_text)
            }
            jobs.append(job)

            # 스레드 충돌 방지: 큐에 올린 즉시 읽음 표시
            server.add_flags(uid, [b'\\Seen'])
        return jobs

def forward_to_zenspark(original_from: str, subject: str, body_json: dict):
    body_json = body_json or {}
    body_json.setdefault("meta", {})
    body_json["meta"]["original_from"] = original_from

    send_mail(
        to_addr=ZENSPARK_INBOX,
        subject=subject,  # [CAIA-JOB] ... 그대로 전달
        body_text=json.dumps(body_json, ensure_ascii=False, indent=2),
        extra_headers={"X-CAIA-FWD": "1"}  # ★ 루프 방지 표식
    )

def ack_to_sender(sender: str, job_id: str, ok: bool, msg: str):
    state = "accepted" if ok else "rejected"
    subject = f"[CAIA-JOB-ACK] {state} #{job_id}"
    payload = {"state": state, "message": msg}
    send_mail(sender, subject, json.dumps(payload, ensure_ascii=False, indent=2),
              extra_headers={"X-CAIA-ACK": "1"})

def extract_job_id(subject: str) -> str:
    if "#" in subject:
        return subject.split("#")[-1].strip()
    return "unknown"

def main_loop():
    print("Caia Mail Bridge worker started.")
    while True:
        try:
            jobs = fetch_unseen_jobs()
            for job in jobs:
                subj = job["subject"]
                sender = job["from"]
                job_id = extract_job_id(subj)

                if SUBJECT_PREFIX not in subj:
                    continue

                if not job["json"]:
                    ack_to_sender(sender, job_id, False, "본문에서 유효한 Job JSON을 찾지 못했습니다.")
                    continue

                # 젠스파크로 전달
                forward_to_zenspark(sender, subj, job["json"])
                # 접수 확인 회신
                ack_to_sender(sender, job_id, True, "작업을 접수하여 젠스파크로 전달했습니다.")
                print(f"Forwarded job {job_id} from {sender}")

        except Exception as e:
            print("Loop error:", repr(e))

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    if not _env_ready():
        raise SystemExit("환경변수(IMAP_*, SMTP_*) 누락으로 종료")
    main_loop()
