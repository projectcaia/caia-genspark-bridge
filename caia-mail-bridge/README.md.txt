# Caia Mail Bridge (Zoho IMAP/SMTP)

## 1) 환경변수(Railway Variables)
IMAP_HOST=imap.zoho.com
IMAP_PORT=993
IMAP_USER=jobs@caia-agent.com
IMAP_PASSWORD=***  # 앱 비밀번호
SMTP_HOST=smtp.zoho.com
SMTP_PORT=465
SMTP_USER=jobs@caia-agent.com
SMTP_PASSWORD=***  # 앱 비밀번호
POLL_INTERVAL_SEC=120
SUBJECT_PREFIX=[CAIA-JOB]
ZENSPARK_INBOX=jobs@caia-agent.com

## 2) 배포
- GitHub에 푸시 → Railway에서 New Project from GitHub
- Variables 입력 → Deploy
- Logs에서 "worker started" 확인

## 3) 테스트
- 메일 발송: TO=jobs@caia-agent.com
  SUBJECT: [CAIA-JOB] email_summarize #auto-YYYYMMDD-0001
  BODY(JSON):
  {
    "job_id":"auto-YYYYMMDD-0001",
    "intent":"email_summarize",
    "inputs":{"text":"테스트 본문입니다."}
  }

- 기대: ACK 메일 수신 + 젠스파크 인박스에 동일 제목/JSON 수신
