# receiver.py
import asyncio, imaplib, email
from typing import Tuple, List, Optional, Dict

async def ensure_imap_ok(host: str, port: int, user: str, pwd: str, folder: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _ensure_blocking, host, port, user, pwd, folder)

def _ensure_blocking(host, port, user, pwd, folder):
    with imaplib.IMAP4_SSL(host, port) as M:
        M.login(user, pwd)
        M.select(folder)

async def poll_once(host: str, port: int, user: str, pwd: str, folder: str, since_uid: Optional[int]) -> Tuple[Optional[int], List[Dict]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _poll_blocking, host, port, user, pwd, folder, since_uid)

def _poll_blocking(host, port, user, pwd, folder, since_uid):
    msgs = []
    last_uid = since_uid or 0
    with imaplib.IMAP4_SSL(host, port) as M:
        M.login(user, pwd)
        M.select(folder)
        criteria = f"(UID {since_uid+1}:*)" if since_uid else "ALL"
        typ, data = M.uid("search", None, criteria)
        if typ != "OK":
            return last_uid, msgs
        uids = [int(x) for x in data[0].split()] if data and data[0] else []
        for uid in uids:
            typ, msgdata = M.uid("fetch", str(uid), "(RFC822)")
            if typ != "OK": 
                continue
            raw = msgdata[0][1]
            em = email.message_from_bytes(raw)
            # 본문 추출(텍스트 우선)
            body_text = ""
            body_html = None
            if em.is_multipart():
                for part in em.walk():
                    ctype = part.get_content_type()
                    disp = part.get("Content-Disposition", "")
                    if ctype == "text/plain" and "attachment" not in (disp or "").lower():
                        body_text += part.get_payload(decode=True).decode(errors="ignore")
                    elif ctype == "text/html" and "attachment" not in (disp or "").lower():
                        body_html = (part.get_payload(decode=True) or b"").decode(errors="ignore")
            else:
                payload = em.get_payload(decode=True)
                if payload:
                    body_text = payload.decode(errors="ignore")

            frm = em.get("From", "")
            subj = em.get("Subject", "")
            date = em.get("Date", "")
            msgs.append({
                "uid": uid,
                "from": frm,
                "subject": subj,
                "date": date,
                "text": body_text.strip(),
                "html": body_html
            })
            if uid > last_uid:
                last_uid = uid
    return last_uid, msgs
