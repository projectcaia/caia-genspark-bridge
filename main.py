+ import sqlite3, datetime
+ from pathlib import Path
+ from fastapi.responses import HTMLResponse
+ 
+ DB_PATH = Path("./mailbridge.db")
+ 
+ def _db():
+     conn = sqlite3.connect(DB_PATH)
+     conn.execute("""
+     CREATE TABLE IF NOT EXISTS mail_log(
+         id INTEGER PRIMARY KEY AUTOINCREMENT,
+         ts TEXT, direction TEXT, peer TEXT,
+         subject TEXT, snippet TEXT, meta TEXT, status TEXT
+     )
+     """)
+     return conn
+ 
+ def log_mail(direction, peer, subject, snippet, meta=None, status="queued"):
+     conn = _db()
+     conn.execute("INSERT INTO mail_log(ts,direction,peer,subject,snippet,meta,status) VALUES(?,?,?,?,?,?,?)",
+                  (datetime.datetime.utcnow().isoformat()+"Z", direction, peer, subject,
+                   (snippet or "")[:500], json.dumps(meta or {}), status))
+     conn.commit(); conn.close()

@@
 def send_mail(to_addr: str, subject: str, body_text: str, extra_headers: dict | None = None):
@@
-    r = requests.post(
+    r = requests.post(
         "https://api.sendgrid.com/v3/mail/send",
@@
-    if r.status_code >= 300:
+    if r.status_code >= 300:
         # SendGrid가 돌려준 본문 일부 포함 (원인 파악용)
         raise RuntimeError(f"SendGrid send failed: {r.status_code} {r.text[:240]}")
+    # 발신 성공 로그
+    try:
+        log_mail("outbound", to, subject, body_text, {"headers": extra_headers or {}}, status="sent")
+    except Exception:
+        pass

@@
 @app.post("/diag/send")
 def diag_send():
@@
-        send_mail(DIAG_TO or REPLY_FROM, "[CAIA-JOB] diag #ping", '{"task":"ping"}')
-        return {"ok": True, "sent_to": DIAG_TO or REPLY_FROM}
+        to = DIAG_TO or REPLY_FROM
+        subj = f"{SUBJECT_PREFIX} diag #ping"
+        send_mail(to, subj, '{"task":"ping"}')
+        return {"ok": True, "sent_to": to, "subject": subj}

@@
 @app.post("/inbound/sendgrid")
 async def inbound_sendgrid(
@@
 ):
     sender = mail_from or from_addr or ""
     body = text or html or ""
+    # 수신 로그(원문 요약)
+    try: log_mail("inbound", sender, subject, body, {"path":"sendgrid"}, status="received")
+    except Exception: pass
@@
     if not job_json:
@@
         try:
             ack_to_sender(sender, job_id, False, "본문에서 유효한 Job JSON을 찾지 못했습니다.")
         except Exception:
             pass
-        return {"ok": False, "reason": "no-json"}
+        return {"ok": False, "reason": "no-json"}
@@
     try:
-        forward_to_zenspark(sender, subject, job_json)
+        forward_to_zenspark(sender, subject, job_json)
+        try: log_mail("forward", ZENSPARK_INBOX, subject, json.dumps(job_json)[:500], {"from": sender}, status="forwarded")
+        except Exception: pass
         ack_to_sender(sender, job_id, True, "작업을 접수하여 젠스파크로 전달했습니다.")
         return {"ok": True, "job_id": job_id}
@@
-        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
+        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

+ # --- (선택) 파라미터형 발신: 젠스파크/수신자 임의 지정 ---
+ from pydantic import BaseModel
+ class OutReq(BaseModel):
+     to: str
+     subject: str
+     body: str
+ @app.post("/outbound")
+ def outbound(req: OutReq):
+     send_mail(req.to, req.subject, req.body)
+     return {"ok": True, "to": req.to, "subject": req.subject}

+ # --- WebUI: 컬 없이 브라우저에서 발신 ---
+ @app.get("/webui", response_class=HTMLResponse)
+ def webui():
+     return f"""
+     <html><head><meta charset='utf-8'><title>Caia Mail Bridge – WebUI</title></head>
+     <body style='font-family:system-ui;max-width:720px;margin:40px auto'>
+       <h1>발신 테스트</h1>
+       <form method='post' action='/webui/send'>
+         <div>To: <input name='to' style='width:420px' value='{ZENSPARK_INBOX}'/></div>
+         <div>Subject: <input name='subject' style='width:420px' value='{SUBJECT_PREFIX} probe #WEB-001'/></div>
+         <div>Body:<br/><textarea name='body' rows='8' style='width:100%'>{{"task":"ping"}}</textarea></div>
+         <button type='submit'>Send</button>
+       </form>
+       <p><a href='/logs/ui' target='_blank'>로그 보기</a></p>
+     </body></html>"""
+ 
+ @app.post("/webui/send")
+ async def webui_send(to: str = Form(...), subject: str = Form(...), body: str = Form(...)):
+     send_mail(to, subject, body)
+     return HTMLResponse("<p>✅ Sent.</p><p><a href='/logs/ui' target='_blank'>로그 열기</a></p>")

+ # --- SendGrid Event Webhook: 배달/반송/오픈 등 영수증 ---
+ @app.post("/events")
+ async def events(request: Request):
+     try:
+         events = await request.json()  # [{event,email,...}, ...]
+     except Exception:
+         events = []
+     for ev in events:
+         peer = ev.get("email","")
+         evt  = ev.get("event","")
+         subj = f"[SG]{evt}"
+         try: log_mail("event", peer, subj, json.dumps(ev)[:200], ev, status=evt)
+         except Exception: pass
+     return {"ok": True, "count": len(events)}

+ # --- Logs API/UI ---
+ @app.get("/logs")
+ def logs(limit: int = 150):
+     conn = _db()
+     cur = conn.execute("SELECT ts,direction,peer,subject,status FROM mail_log ORDER BY id DESC LIMIT ?", (limit,))
+     rows = [{"ts":r[0],"dir":r[1],"peer":r[2],"subject":r[3],"status":r[4]} for r in cur.fetchall()]
+     conn.close()
+     return {"ok": True, "rows": rows}
+ 
+ @app.get("/logs/ui", response_class=HTMLResponse)
+ def logs_ui():
+     return """
+     <html><head><meta charset='utf-8'><title>Logs</title>
+     <style>body{font-family:system-ui;max-width:960px;margin:30px auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px}</style>
+     <script>
+       async function load(){const r=await fetch('/logs?limit=200');const j=await r.json();
+         const el=document.getElementById('tbl'); el.innerHTML='';
+         (j.rows||[]).forEach(row=>{const tr=document.createElement('tr');
+           ['ts','dir','peer','subject','status'].forEach(k=>{const td=document.createElement('td');td.textContent=row[k];tr.appendChild(td);});
+           el.appendChild(tr);});}
+       setInterval(load,2000); window.onload=load;
+     </script></head>
+     <body><h2>Caia Mail Logs</h2>
+     <table><thead><tr><th>ts</th><th>dir</th><th>peer</th><th>subject</th><th>status</th></tr></thead>
+     <tbody id='tbl'></tbody></table></body></html>
+     """
