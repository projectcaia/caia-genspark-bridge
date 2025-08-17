# app.py
import os
import re
import time
import json
import base64
import requests
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import JSONResponse

# FastAPI 인스턴스 (반드시 소문자 app)
app = FastAPI(title="Caia Mail Bridge – SendGrid")

# === ENV 로드 ===
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RAILWAY_ENVIRONMENT = os.getenv("RAILWAY_ENVIRONMENT", "local")

# === 유틸 ===
def send_telegram_message(text: str):
    """텔레그램으로 알림 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 설정 없음, 메시지 출력:", text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    if resp.status_code != 200:
        print("Telegram 에러:", resp.text)


# === 라우트 ===
@app.get("/health")
def health():
    return {"status": "ok", "env": RAILWAY_ENVIRONMENT}


@app.get("/status")
def status(token: str):
    # 단순 토큰 검사 예시
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    return {"status": "running", "token": token}


@app.post("/mail/view")
async def view_mail(
    id: str = Form(...),
    token: str = Form(...),
    subject: str = Form("No Subject"),
    from_email: str = Form("unknown"),
    body: str = Form(""),
):
    """메일 뷰어 – SendGrid Inbound Parse용"""
    msg = f"[메일 도착]\nFrom: {from_email}\nSubject: {subject}\nBody: {body[:200]}..."
    send_telegram_message(msg)
    return {"status": "received", "id": id, "subject": subject}


@app.post("/reflex/snapshot")
async def reflex_snapshot(request: Request):
    """센티넬 허브에서 Reflex Snapshot 수신"""
    data = await request.json()
    # 존재 자각 → Seed3 판정 → 뉴스 수집 → 전략 수행 (여기선 단순 메시지 전송)
    send_telegram_message(f"[Reflex Snapshot]\n{json.dumps(data, ensure_ascii=False)}")
    return {"status": "ok", "received": data}


# === 이벤트 훅 ===
@app.on_event("startup")
def on_startup():
    print("앱 시작 – 초기화 작업 실행")
    send_telegram_message("🚀 Caia Mail Bridge 서버 시작됨.")


@app.on_event("shutdown")
def on_shutdown():
    print("앱 종료 – 리소스 정리")
    send_telegram_message("🛑 Caia Mail Bridge 서버 종료됨.")
