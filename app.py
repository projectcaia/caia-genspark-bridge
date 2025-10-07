# 📧 Caia MailBridge - Complete Refactored Version
# SendGrid + Telegram Notification Integration
# 2025-10-07

import os
import json
import sqlite3
import logging
import datetime as dt
import asyncio
from typing import List, Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, EmailStr, ValidationError
import sendgrid
from sendgrid.helpers.mail import Mail

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

APP_VERSION = "2025-10-07-refactored"

app = FastAPI(
    title="Caia MailBridge",
    description="SendGrid + Telegram Notification Integration",
    version="1.0.0",
    openapi_version="3.1.0"
)

# Environment Variables
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")
FROM_EMAIL = os.getenv("FROM_EMAIL", "caia@system.ai")

# Validation
if not SENDGRID_API_KEY:
    logger.warning("SENDGRID_API_KEY not configured")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logger.warning("Telegram credentials not configured")

# Global variables for services
start_time = dt.datetime.utcnow()

# ===== Database Setup =====
def get_db_connection():
    """Get SQLite database connection"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initialize database tables"""
    conn = get_db_connection()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS emails(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            recipient TEXT,
            subject TEXT,
            content TEXT,
            sent_via TEXT,
            status TEXT DEFAULT 'sent',
            created_at TEXT NOT NULL,
            telegram_notified INTEGER DEFAULT 0
        )
        """)
        
        conn.execute("""
        CREATE TABLE IF NOT EXISTS inbox_emails(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            subject TEXT,
            content TEXT,
            received_at TEXT NOT NULL,
            telegram_notified INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0
        )
        """)
        
        conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
    finally:
        conn.close()

# Initialize database on startup
init_database()

# ===== Pydantic Models =====
class EmailRequest(BaseModel):
    to: List[EmailStr]
    subject: str
    text: str
    html: Optional[str] = None
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None

class AssistantTestRequest(BaseModel):
    text: str

class EmailSummary(BaseModel):
    id: int
    sender: str
    subject: str
    received_at: str
    processed: bool

# ===== Helper Functions =====
async def send_telegram_notification(message: str) -> bool:
    """Send notification to Telegram"""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        logger.warning("Telegram credentials not configured")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                timeout=10
            )
            if response.status_code == 200:
                logger.info("Telegram notification sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False

def send_email_via_sendgrid(email_request: EmailRequest) -> Dict[str, Any]:
    """Send email via SendGrid API"""
    if not SENDGRID_API_KEY:
        raise HTTPException(status_code=500, detail="SendGrid API key not configured")
    
    try:
        # Create SendGrid message
        message = Mail(
            from_email=FROM_EMAIL,
            to_emails=[str(email) for email in email_request.to],
            subject=email_request.subject,
            plain_text_content=email_request.text
        )
        
        # Add HTML content if provided
        if email_request.html:
            message.html_content = email_request.html
        
        # Add CC recipients
        if email_request.cc:
            for cc_email in email_request.cc:
                message.add_cc(str(cc_email))
        
        # Add BCC recipients
        if email_request.bcc:
            for bcc_email in email_request.bcc:
                message.add_bcc(str(bcc_email))
        
        # Send email
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        response = sg.send(message)
        
        # Log to database
        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT INTO emails (sender, recipient, subject, content, sent_via, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                FROM_EMAIL,
                ", ".join([str(email) for email in email_request.to]),
                email_request.subject,
                email_request.text,
                "sendgrid",
                "sent" if response.status_code == 202 else "failed",
                dt.datetime.utcnow().isoformat()
            ))
            conn.commit()
        finally:
            conn.close()
        
        logger.info(f"Email sent via SendGrid: {response.status_code}")
        return {
            "ok": True,
            "via": "sendgrid",
            "status_code": response.status_code,
            "message_id": response.headers.get('X-Message-Id', 'unknown')
        }
        
    except Exception as e:
        logger.error(f"SendGrid error: {e}")
        raise HTTPException(status_code=502, detail=f"SendGrid send failed: {e}")

async def simulate_inbox_check() -> List[Dict[str, Any]]:
    """Simulate checking inbox for new emails"""
    # This would normally connect to an email provider (IMAP/POP3)
    # For now, we'll return mock data and check the database
    
    conn = get_db_connection()
    try:
        # Get recent inbox emails
        cursor = conn.execute("""
            SELECT * FROM inbox_emails 
            ORDER BY id DESC 
            LIMIT 20
        """)
        emails = [dict(row) for row in cursor.fetchall()]
        
        # If no emails exist, create a sample one for demonstration
        if not emails:
            sample_email = {
                "sender": "test@example.com",
                "subject": "Test Email",
                "content": "This is a test email for demonstration",
                "received_at": dt.datetime.utcnow().isoformat()
            }
            conn.execute("""
                INSERT INTO inbox_emails (sender, subject, content, received_at)
                VALUES (?, ?, ?, ?)
            """, (sample_email["sender"], sample_email["subject"], 
                   sample_email["content"], sample_email["received_at"]))
            conn.commit()
            
            # Send Telegram notification for new email
            await send_telegram_notification(
                f"📩 새 메일 도착\nFrom: {sample_email['sender']}\nSubject: {sample_email['subject']}"
            )
            
            # Update notification flag
            conn.execute("""
                UPDATE inbox_emails SET telegram_notified = 1 
                WHERE sender = ? AND subject = ?
            """, (sample_email["sender"], sample_email["subject"]))
            conn.commit()
            
            emails = [sample_email]
        
        return emails
        
    finally:
        conn.close()

def get_uptime() -> str:
    """Calculate service uptime"""
    uptime_delta = dt.datetime.utcnow() - start_time
    hours, remainder = divmod(int(uptime_delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"

# ===== Error Handlers =====
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc}")
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "error": "Invalid request schema",
            "detail": exc.errors()
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(f"HTTP error: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Internal server error",
            "detail": str(exc)
        }
    )

# ===== API Routes =====
@app.get("/")
async def root():
    """Root endpoint - service alive check"""
    return {"status": "ok", "service": "MailBridge"}

@app.get("/favicon.ico")
async def favicon():
    """Favicon endpoint"""
    from fastapi.responses import Response
    return Response(status_code=204)

@app.post("/send")
async def send_email_endpoint(email_request: EmailRequest):
    """Send email via SendGrid"""
    logger.info(f"Sending email to: {email_request.to}, subject: {email_request.subject}")
    
    result = send_email_via_sendgrid(email_request)
    
    # Send Telegram notification for outgoing emails
    await send_telegram_notification(
        f"📤 메일 발송\nTo: {', '.join([str(email) for email in email_request.to])}\nSubject: {email_request.subject}"
    )
    
    return result

@app.get("/inbox")
async def check_inbox():
    """Check inbox for new emails"""
    logger.info("Checking inbox for new emails")
    
    emails = await simulate_inbox_check()
    
    return {
        "ok": True,
        "count": len(emails),
        "emails": emails
    }

@app.get("/dashboard/summary")
async def get_dashboard_summary():
    """Get dashboard summary with email statistics"""
    logger.info("Getting dashboard summary")
    
    conn = get_db_connection()
    try:
        # Get sent email stats
        sent_cursor = conn.execute("""
            SELECT COUNT(*) as total_sent,
                   COUNT(CASE WHEN date(created_at) = date('now') THEN 1 END) as sent_today,
                   COUNT(CASE WHEN status = 'sent' THEN 1 END) as sent_success
            FROM emails
        """)
        sent_stats = dict(sent_cursor.fetchone())
        
        # Get received email stats
        received_cursor = conn.execute("""
            SELECT COUNT(*) as total_received,
                   COUNT(CASE WHEN date(received_at) = date('now') THEN 1 END) as received_today,
                   COUNT(CASE WHEN processed = 1 THEN 1 END) as processed
            FROM inbox_emails
        """)
        received_stats = dict(received_cursor.fetchone())
        
        # Get recent activities
        recent_sent_cursor = conn.execute("""
            SELECT recipient, subject, created_at 
            FROM emails 
            ORDER BY id DESC 
            LIMIT 5
        """)
        recent_sent = [dict(row) for row in recent_sent_cursor.fetchall()]
        
        recent_received_cursor = conn.execute("""
            SELECT sender, subject, received_at 
            FROM inbox_emails 
            ORDER BY id DESC 
            LIMIT 5
        """)
        recent_received = [dict(row) for row in recent_received_cursor.fetchall()]
        
        return {
            "ok": True,
            "stats": {
                "sent": sent_stats,
                "received": received_stats
            },
            "recent_activities": {
                "sent": recent_sent,
                "received": recent_received
            },
            "uptime": get_uptime(),
            "services": {
                "sendgrid": bool(SENDGRID_API_KEY),
                "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
            }
        }
        
    finally:
        conn.close()

@app.get("/view/{email_id}")
async def view_email_detail(email_id: int):
    """View detailed email information"""
    logger.info(f"Viewing email detail for ID: {email_id}")
    
    conn = get_db_connection()
    try:
        # Try to find in sent emails first
        cursor = conn.execute("""
            SELECT 'sent' as type, sender, recipient as other_party, subject, content, created_at as timestamp, status
            FROM emails WHERE id = ?
        """, (email_id,))
        result = cursor.fetchone()
        
        # If not found in sent, check received emails
        if not result:
            cursor = conn.execute("""
                SELECT 'received' as type, sender as other_party, 'system' as sender, subject, content, received_at as timestamp, 
                       CASE WHEN processed = 1 THEN 'processed' ELSE 'unprocessed' END as status
                FROM inbox_emails WHERE id = ?
            """, (email_id,))
            result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Email not found")
        
        email_detail = dict(result)
        return {
            "ok": True,
            "email": email_detail
        }
        
    finally:
        conn.close()

@app.post("/test/assistant")
async def test_assistant_processing(request: AssistantTestRequest):
    """Test assistant processing functionality"""
    logger.info(f"Testing assistant processing with text: {request.text[:50]}...")
    
    # Process the text (simulate assistant processing)
    processed_text = f"Caia MailBridge processed: {request.text}"
    
    return {
        "ok": True,
        "message": processed_text,
        "original": request.text,
        "timestamp": dt.datetime.utcnow().isoformat()
    }

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Internal webhook for processing new email notifications"""
    try:
        data = await request.json()
        sender = data.get("sender", "unknown")
        subject = data.get("subject", "No Subject")
        
        # Send Telegram notification
        success = await send_telegram_notification(
            f"📩 새 메일 도착\nFrom: {sender}\nSubject: {subject}"
        )
        
        return {
            "ok": True,
            "notification_sent": success
        }
        
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "ok": True,
        "uptime": get_uptime(),
        "sendgrid": bool(SENDGRID_API_KEY),
        "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "version": APP_VERSION,
        "timestamp": dt.datetime.utcnow().isoformat()
    }

# ===== Startup Event =====
@app.on_event("startup")
async def startup_event():
    """Initialize service on startup"""
    logger.info(f"🚀 Caia MailBridge starting up - Version {APP_VERSION}")
    logger.info(f"📧 SendGrid configured: {bool(SENDGRID_API_KEY)}")
    logger.info(f"📱 Telegram configured: {bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)}")
    
    # Send startup notification
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        await send_telegram_notification(
            f"🚀 MailBridge 서비스 시작\nVersion: {APP_VERSION}\nTimestamp: {dt.datetime.utcnow().isoformat()}"
        )

# ===== Main Application Entry Point =====
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting MailBridge server")
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
    

        



