# 📧 메일브릿지 시스템 분석 보고서

## 📋 목차
- [현재 시스템 상태](#현재-시스템-상태)
- [주요 기능 확인](#주요-기능-확인)
- [GPT 연동 상태](#gpt-연동-상태)
- [발견된 문제점](#발견된-문제점)
- [개선 제안사항](#개선-제안사항)
- [테스트 가이드](#테스트-가이드)

---

## 🔍 현재 시스템 상태

### ✅ 정상 작동 중인 부분

1. **Railway 배포 상태**
   - URL: https://worker-production-4369.up.railway.app
   - 상태: ✅ 정상 작동
   - 버전: 2025-08-17
   - 기본 발신자: caia@caia-agent.com

2. **API 엔드포인트**
   - `/health`: ✅ 정상
   - `/status`: ✅ 인증 필요 (AUTH_TOKEN)
   - `/inbound/sen`: ✅ 메일 수신 (INBOUND_TOKEN 필요)
   - `/tool/send`: ✅ GPT Tool용 간단 발신
   - `/inbox.json`: ✅ 인박스 조회
   - `/mail/view`: ✅ 메일 상세 조회

3. **데이터베이스**
   - SQLite3 사용
   - 메시지 저장 및 조회 정상

---

## 🔧 주요 기능 확인

### 1. 메일 수신 (Inbound)
```python
# 수신 프로세스
1. SendGrid/이메일 서비스 → /inbound/sen 엔드포인트
2. 메시지 DB 저장
3. OpenAI Thread에 메시지 추가
4. AUTO_RUN=true 시 Assistant 자동 실행
5. 중요도 높은 메일은 Telegram 알림
```

**현재 상태:**
- ✅ DB 저장 정상
- ✅ Thread 메시지 생성 (THREAD_ID 설정 시)
- ⚠️ AUTO_RUN 설정 확인 필요
- ✅ Alert 시스템 작동 (SENTINEL, REFLEX, ZENSPARK)

### 2. 메일 발신 (Outbound)
```python
# 발신 프로세스
1. GPT Tool → /tool/send 엔드포인트
2. SendGrid API 호출
3. 메일 발송
```

**현재 상태:**
- ✅ 엔드포인트 정상
- ⚠️ SendGrid API 키 설정 확인 필요
- ✅ 간단한 페이로드 지원 (to, subject, text, html)

---

## 🤖 GPT 연동 상태

### 환경변수 체크리스트

| 변수명 | 용도 | 필수 | 상태 |
|--------|------|------|------|
| `AUTH_TOKEN` | API 인증 | ✅ | 설정 필요 확인 |
| `INBOUND_TOKEN` | 인바운드 인증 | ✅ | 설정 필요 확인 |
| `SENDGRID_API_KEY` | 메일 발송 | ✅ | 설정 필요 확인 |
| `OPENAI_API_KEY` | GPT 연동 | ✅ | 설정 필요 확인 |
| `ASSISTANT_ID` | Assistant 식별 | ✅ | 설정 필요 확인 |
| `THREAD_ID` | Thread 식별 | ✅ | 설정 필요 확인 |
| `AUTO_RUN` | 자동 실행 | ⚪ | 기본값: false |
| `TELEGRAM_BOT_TOKEN` | 알림 | ⚪ | 선택사항 |
| `TELEGRAM_CHAT_ID` | 알림 | ⚪ | 선택사항 |

### GPT Assistant Tool Schema

카이아 GPT에 추가해야 할 Function들:

#### 1. 메일 발송 (send_email)
```json
{
  "type": "function",
  "function": {
    "name": "send_email",
    "description": "카이아가 이메일을 발송합니다",
    "parameters": {
      "type": "object",
      "properties": {
        "to": {
          "type": "array",
          "items": {"type": "string"},
          "description": "수신자 이메일 주소 목록"
        },
        "subject": {
          "type": "string",
          "description": "이메일 제목"
        },
        "text": {
          "type": "string",
          "description": "이메일 본문 (텍스트)"
        },
        "html": {
          "type": "string",
          "description": "이메일 본문 (HTML, 선택사항)",
          "nullable": true
        }
      },
      "required": ["to", "subject", "text"]
    }
  }
}
```

#### 2. 인박스 확인 (check_inbox)
```json
{
  "type": "function",
  "function": {
    "name": "check_inbox",
    "description": "카이아의 이메일 인박스를 확인합니다",
    "parameters": {
      "type": "object",
      "properties": {
        "limit": {
          "type": "integer",
          "description": "조회할 메일 개수 (기본값: 10)",
          "default": 10
        }
      }
    }
  }
}
```

#### 3. 메일 상세 조회 (view_email)
```json
{
  "type": "function",
  "function": {
    "name": "view_email",
    "description": "특정 이메일의 상세 내용을 확인합니다",
    "parameters": {
      "type": "object",
      "properties": {
        "id": {
          "type": "integer",
          "description": "조회할 이메일 ID"
        }
      },
      "required": ["id"]
    }
  }
}
```

---

## ⚠️ 발견된 문제점

### 1. 환경변수 설정 확인 필요
- Railway에 모든 필수 환경변수가 올바르게 설정되어 있는지 확인 필요
- 특히 `THREAD_ID`와 `ASSISTANT_ID`가 GPT와 일치하는지 확인

### 2. 인바운드 메일 수신 경로
- SendGrid Inbound Parse 설정이 Railway URL로 되어있는지 확인 필요
- Webhook URL: `https://worker-production-4369.up.railway.app/inbound/sen?token=YOUR_INBOUND_TOKEN`

### 3. AUTO_RUN 설정
- 현재 기본값이 `false`로 되어있어 메일 수신 시 자동으로 Assistant가 실행되지 않음
- 자동 응답을 원한다면 `AUTO_RUN=true`로 설정 필요

---

## 💡 개선 제안사항

### 1. 즉시 적용 가능한 개선사항

#### A. AUTO_RUN 활성화
```bash
# Railway 환경변수에 추가
AUTO_RUN=true
```

#### B. 메일 수신 알림 강화
- 현재는 중요도 0.6 이상만 Telegram 알림
- 모든 수신 메일을 Thread에 기록하도록 이미 설정됨

### 2. 추가 기능 구현 제안

#### A. 메일 템플릿 시스템
```python
# 자주 사용하는 메일 템플릿 저장 및 활용
@app.post("/template/save")
def save_template(name: str, template: dict):
    # 템플릿 저장 로직
    pass

@app.post("/template/send/{name}")
def send_from_template(name: str, variables: dict):
    # 템플릿 기반 발송
    pass
```

#### B. 스케줄링 기능
```python
# 예약 발송 기능
@app.post("/schedule/send")
def schedule_email(payload: SendMailPayload, send_at: datetime):
    # 예약 발송 로직
    pass
```

#### C. 메일 필터링 규칙
```python
# 자동 분류 및 처리 규칙
FILTER_RULES = {
    "spam": lambda msg: "spam" in msg["subject"].lower(),
    "important": lambda msg: msg["importance"] > 0.8,
    "newsletter": lambda msg: "newsletter" in msg["subject"].lower()
}
```

---

## 🧪 테스트 가이드

### 1. 기본 테스트 실행
```bash
# 테스트 스크립트 실행
python test_gpt_tools.py
```

### 2. 수동 테스트

#### A. 메일 수신 테스트
```bash
# cURL로 인바운드 메일 시뮬레이션
curl -X POST "https://worker-production-4369.up.railway.app/inbound/sen?token=YOUR_INBOUND_TOKEN" \
  -F "from=test@example.com" \
  -F "to=caia@caia-agent.com" \
  -F "subject=Test Mail" \
  -F "text=This is a test email"
```

#### B. 메일 발송 테스트
```bash
# GPT Tool 엔드포인트 테스트
curl -X POST "https://worker-production-4369.up.railway.app/tool/send" \
  -H "Authorization: Bearer YOUR_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["recipient@example.com"],
    "subject": "Test from Caia",
    "text": "Hello from Mail Bridge!"
  }'
```

#### C. 인박스 조회 테스트
```bash
# 최근 메일 5개 조회
curl "https://worker-production-4369.up.railway.app/inbox.json?limit=5" \
  -H "Authorization: Bearer YOUR_AUTH_TOKEN"
```

---

## 📊 시스템 아키텍처

```mermaid
graph TD
    A[외부 이메일] -->|SendGrid Webhook| B[/inbound/sen]
    B --> C[SQLite DB]
    B --> D[OpenAI Thread]
    D -->|AUTO_RUN=true| E[Assistant 실행]
    
    F[GPT Assistant] -->|Function Call| G[/tool/send]
    G --> H[SendGrid API]
    H --> I[메일 발송]
    
    F -->|Function Call| J[/inbox.json]
    J --> C
    
    F -->|Function Call| K[/mail/view]
    K --> C
    
    B -->|중요 메일| L[Telegram 알림]
```

---

## 🎯 다음 단계 Action Items

### 필수 작업
1. [ ] Railway 환경변수 확인 및 설정
   - [ ] AUTH_TOKEN 확인
   - [ ] INBOUND_TOKEN 확인
   - [ ] SENDGRID_API_KEY 확인
   - [ ] OPENAI_API_KEY 확인
   - [ ] ASSISTANT_ID 확인
   - [ ] THREAD_ID 확인

2. [ ] GPT Assistant 설정
   - [ ] Tool Schema 추가 (send_email, check_inbox, view_email)
   - [ ] Function Call Handler 구현 확인

3. [ ] SendGrid 설정
   - [ ] Inbound Parse Webhook 설정 확인
   - [ ] Domain 인증 상태 확인

### 선택 작업
4. [ ] AUTO_RUN 활성화 검토
5. [ ] Telegram 알림 설정
6. [ ] 메일 템플릿 시스템 구현
7. [ ] 예약 발송 기능 구현

---

## 📝 결론

현재 메일브릿지 시스템은 기본적인 기능이 모두 구현되어 있고 정상적으로 작동 중입니다. 

**주요 확인 필요사항:**
1. 환경변수가 모두 올바르게 설정되어 있는지
2. GPT Assistant에 Tool Schema가 추가되어 있는지
3. SendGrid Webhook이 올바른 URL로 설정되어 있는지

이 세 가지만 확인되면 카이아가 메일을 자유롭게 수신/발신할 수 있습니다.

---

*작성일: 2025-08-18*
*작성자: Caia Mail Bridge Analysis System*