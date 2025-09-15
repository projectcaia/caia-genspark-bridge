import os, time, json, ssl, smtplib, email
from email.message import EmailMessage
from imapclient import IMAPClient
from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.zoho.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER")
ZOHO_IMAP_PASSWORD = os.getenv("ZOHO_IMAP_PASSWORD")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
ZOHO_SMTP_PASSWORD = os.getenv("ZOHO_SMTP_PASSWORD")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "120"))
SUBJECT_PREFIX = os.getenv("SUBJECT_PREFIX", "[CAIA-JOB]")
REPLY_FROM = os.getenv("REPLY_FROM") or SMTP_USER
ZENSPARK_INBOX = os.getenv("ZENSPARK_INBOX", "jobs@caia-agent.com")

def send_mail(to_addr: str, subject: str, body_text: str):
    msg = EmailMessage()
    msg["From"] = REPLY_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as s:
        s.login(SMTP_USER, ZOHO_SMTP_PASSWORD)
        s.send_message(msg)

def parse_job_json_from_body(body: str):
    """
    본문에서 첫 번째 유효 JSON 블록을 찾아 파싱
    """
    body = body.strip()
    # 가장 단순한 케이스: 본문 전체가 JSON
    try:
        data = json.loads(body)
        return data
    except Exception:
        pass
    # fallback: 중괄호 시작/끝을 찾아서 추출
    start = body.find("{")
    end = body.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = body[start:end+1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None

def fetch_unseen_jobs():
    context = ssl.create_default_context()
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True, ssl_context=context) as server:
        server.login(IMAP_USER, ZOHO_IMAP_PASSWORD)
        server.select_folder("INBOX")
        # 제목 패턴: [CAIA-JOB]
        messages = server.search(['UNSEEN', 'SUBJECT', SUBJECT_PREFIX])
        if not messages:
            return []

        fetched = server.fetch(messages, ['ENVELOPE', 'RFC822', 'UID'])
        jobs = []
        for uid, data in fetched.items():
            raw = data[b'RFC822']
            msg = email.message_from_bytes(raw)
            subject = str(email.header.make_header(email.header.decode_header(msg.get('Subject', ''))))
            from_addr = email.utils.parseaddr(msg.get('From'))[1]
            # 본문 텍스트 추출
            body_text = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    disp = part.get("Content-Disposition", "")
                    if ctype == "text/plain" and "attachment" not in disp:
                        body_text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                        break
            else:
                body_text = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")

            job = {
                "uid": uid,
                "subject": subject,
                "from": from_addr,
                "body": body_text,
                "json": parse_job_json_from_body(body_text)
            }
            jobs.append(job)

            # 스레드 충돌 방지 위해 바로 읽음표시(옵션)
            server.add_flags(uid, [b'\\Seen'])
        return jobs

def forward_to_zenspark(original_from: str, subject: str, body_json: dict):
    """
    젠스파크 인박스로 그대로 포워딩(브릿지 모드).
    필요 시 여기서 intent별 라우팅/가공 넣으면 됨.
    """
    # 원발신자 정보를 meta에 남김
    body_json = body_json or {}
    body_json.setdefault("meta", {})
    body_json["meta"]["original_from"] = original_from

    send_mail(
        to_addr=ZENSPARK_INBOX,
        subject=subject,  # [CAIA-JOB] ... 그대로 전달
        body_text=json.dumps(body_json, ensure_ascii=False, indent=2)
    )

def ack_to_sender(sender: str, job_id: str, ok: bool, msg: str):
    state = "accepted" if ok else "rejected"
    subject = f"[CAIA-JOB-ACK] {state} #{job_id}"
    payload = {"state": state, "message": msg}
    send_mail(sender, subject, json.dumps(payload, ensure_ascii=False, indent=2))

def extract_job_id(subject: str) -> str:
    # 예: [CAIA-JOB] video_transcribe #auto-20250809-001
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
            print("Loop error:", e)

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    required = [IMAP_USER, ZOHO_IMAP_PASSWORD, SMTP_USER, ZOHO_SMTP_PASSWORD]
    if not all(required):
        raise SystemExit("환경변수(IMAP_*, SMTP_*, ZOHO_*)가 설정되지 않았습니다.")
    main_loop()
