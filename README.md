# Caia Mail Bridge — Rebuilt (2025-09-15)

이 프로젝트는 **Zoho IMAP/SMTP + SendGrid** 전송, **IMAP 폴링 수신**, **첨부파일 저장/다운로드**,
**카이아 에이전트 알림 훅**(옵션), **텔레그램 알림**(옵션)을 포함한 경량 메일 브릿지입니다.

## 주요 기능
- `/mail/send` : JSON 기반 발송 (base64 첨부 지원). SendGrid 또는 SMTP 자동 선택
- `/mail/send-multipart` : multipart 업로드로 첨부파일 발송
- `/inbox.json` : 수신 메일 목록 (DB) — 첨부 유무, 중요도, 알림 클래스 포함
- `/mail/view` : 단건 조회 (본문/HTML/첨부 목록)
- `/mail/attach` : 첨부 다운로드 (저장 파일 서빙)
- `/mail/delete` : 메일 삭제(로컬 DB + IMAP UID 있으면 서버 삭제 시도)
- `/mail/poll-now` : 즉시 IMAP 폴링
- 백그라운드 폴링 : `AUTO_RUN=true` 이고 IMAP 설정이 존재하면 `POLL_INTERVAL_SEC` 간격으로 수신

## 배포
- Railway/Heroku 호환: `Procfile` 포함, `${PORT}` 자동 사용
- Docker 배포: `Dockerfile` 포함 (간단 헬스체크)

## ENV (일부)
- IMAP_HOST, IMAP_PORT, IMAP_USER, ZOHO_IMAP_PASSWORD
- SMTP_HOST, SMTP_PORT, SMTP_USER, ZOHO_SMTP_PASSWORD, SMTP_SSL (true|false)
- SENDGRID_API_KEY (존재하면 우선 SendGrid REST로 발송)
- SENDER_DEFAULT, INBOUND_TOKEN, AUTH_TOKEN
- DB_PATH (기본: mailbridge.sqlite3)
- MAIL_BASE (카이아 에이전트/허브 알림 용도 – 선택)
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (선택)
- POLL_INTERVAL_SEC (기본 120), AUTO_RUN (true|false)

> 보안·시크릿 관련 가이드는 여기서 다루지 않습니다. 운영 시 적절히 관리하세요.

## 시작
```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

## OpenAPI
- 서버 기저 URL 설정은 `MAIL_BASE` 가 있으면 해당 값으로 표시됩니다.
