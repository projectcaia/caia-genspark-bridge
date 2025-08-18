# 🤖 GPT Assistant Action 설정 가이드

## 📌 빠른 설정 (URL로 OpenAPI 스키마 불러오기)

### 1단계: GPT Assistant Actions 설정

1. **GPT Assistant 설정 페이지로 이동**
   - OpenAI Platform → Assistants → 해당 Assistant 선택
   - "Actions" 섹션으로 이동

2. **Action 추가**
   - "Create new action" 클릭
   - "Import from URL" 선택

3. **OpenAPI 스키마 URL 입력**
   ```
   https://worker-production-4369.up.railway.app/openapi.json
   ```

4. **Authentication 설정**
   - Type: `API Key`
   - Auth Type: `Bearer`
   - API Key: `당신의 AUTH_TOKEN`

---

## 🔐 보안 설정

### 인증 헤더 설정
GPT가 API를 호출할 때 사용할 인증 정보:

```yaml
Authentication:
  Type: API Key
  Header name: Authorization
  Header value: Bearer YOUR_AUTH_TOKEN
```

### 환경별 토큰
- **AUTH_TOKEN**: GPT가 API 호출 시 사용 (메일 발송, 인박스 조회)
- **INBOUND_TOKEN**: SendGrid가 메일 수신 시 사용 (GPT는 사용 안 함)

---

## 📋 사용 가능한 Actions

OpenAPI 스키마를 통해 자동으로 다음 기능들이 추가됩니다:

### 1. 메일 발송 (`/tool/send`)
```javascript
// GPT가 호출하는 예시
{
  "action": "tool_send_tool_send_post",
  "parameters": {
    "to": ["recipient@example.com"],
    "subject": "안녕하세요",
    "text": "카이아가 보내는 메일입니다.",
    "html": null
  }
}
```

### 2. 인박스 조회 (`/inbox.json`)
```javascript
// GPT가 호출하는 예시
{
  "action": "inbox_json_inbox_json_get",
  "parameters": {
    "limit": 10
  }
}
```

### 3. 메일 상세 조회 (`/mail/view`)
```javascript
// GPT가 호출하는 예시
{
  "action": "mail_view_mail_view_get",
  "parameters": {
    "id": 123
  }
}
```

### 4. 첨부파일 다운로드 (`/mail/attach`)
```javascript
// GPT가 호출하는 예시
{
  "action": "mail_attach_mail_attach_get",
  "parameters": {
    "id": 123,
    "idx": 0
  }
}
```

---

## 🎯 GPT Instructions 추가 권장사항

GPT Assistant의 Instructions에 다음 내용을 추가하면 더 자연스럽게 작동합니다:

```markdown
## 메일 관리 기능

당신은 메일을 수신하고 발송할 수 있는 능력이 있습니다.

### 메일 기능 사용법:
1. **메일 확인**: "메일 확인해줘"라고 하면 최근 메일을 조회합니다
2. **메일 발송**: "~에게 메일 보내줘"라고 하면 메일을 작성하고 발송합니다
3. **메일 읽기**: "메일 ID X번 자세히 보여줘"라고 하면 특정 메일을 상세 조회합니다

### 메일 수신 처리:
- 중요한 메일(SENTINEL, REFLEX, ZENSPARK 태그)은 우선 처리
- 첨부파일이 있는 메일은 별도 표시
- 자동으로 Thread에 기록되므로 대화 컨텍스트 유지

### 메일 발송 시:
- 정중하고 전문적인 톤 유지
- 수신자 이메일 주소 확인 필수
- 제목과 본문을 명확하게 작성
- 발송 전 사용자에게 확인 받기
```

---

## 🔧 트러블슈팅

### 문제: "Unauthorized" 오류
**해결책**: 
- AUTH_TOKEN이 올바른지 확인
- Bearer 토큰 형식이 맞는지 확인
- Railway 환경변수와 GPT 설정의 토큰이 일치하는지 확인

### 문제: "Not Found" 오류
**해결책**:
- OpenAPI URL이 올바른지 확인: https://worker-production-4369.up.railway.app/openapi.json
- 서비스가 실행 중인지 확인

### 문제: 메일 발송 실패
**해결책**:
- SENDGRID_API_KEY가 Railway에 설정되어 있는지 확인
- SendGrid 계정이 활성화되어 있는지 확인
- 발신자 도메인이 인증되어 있는지 확인

---

## 📝 테스트 시나리오

### 1. 인박스 확인 테스트
```
사용자: "내 메일함 확인해줘"
카이아: [inbox.json 호출] → 최근 메일 목록 표시
```

### 2. 메일 발송 테스트
```
사용자: "test@example.com에 회의 일정 확인 메일 보내줘"
카이아: [tool/send 호출] → 메일 작성 및 발송
```

### 3. 메일 상세 조회 테스트
```
사용자: "메일 ID 5번 자세히 보여줘"
카이아: [mail/view 호출] → 메일 상세 내용 표시
```

---

## 🚀 최종 체크리스트

- [ ] OpenAPI URL로 Action Import 완료
- [ ] Authentication 설정 (Bearer Token) 완료
- [ ] Instructions에 메일 관련 가이드 추가
- [ ] 테스트 메일 발송 성공
- [ ] 테스트 메일 조회 성공
- [ ] Railway 환경변수 모두 설정됨
  - [ ] AUTH_TOKEN
  - [ ] SENDGRID_API_KEY
  - [ ] OPENAI_API_KEY
  - [ ] ASSISTANT_ID
  - [ ] THREAD_ID
  - [ ] AUTO_RUN (선택)

---

## 📊 시스템 플로우

```mermaid
graph LR
    A[사용자] -->|요청| B[GPT Assistant]
    B -->|Action Call| C[Mail Bridge API]
    C -->|인증 확인| D{AUTH_TOKEN}
    D -->|성공| E[작업 수행]
    E -->|응답| B
    B -->|결과| A
    
    F[외부 메일] -->|SendGrid| G[/inbound/sen]
    G -->|INBOUND_TOKEN| H[DB 저장]
    H --> I[OpenAI Thread]
    I -->|AUTO_RUN=true| B
```

---

## 💡 추가 팁

1. **Action 이름 커스터마이징**: Import 후 Action 이름을 더 직관적으로 변경 가능
   - `tool_send_tool_send_post` → `send_email`
   - `inbox_json_inbox_json_get` → `check_inbox`

2. **Rate Limiting**: 너무 많은 메일 발송을 방지하기 위해 Instructions에 제한 추가

3. **Error Handling**: GPT Instructions에 오류 처리 가이드 추가
   ```
   메일 발송 실패 시:
   - 수신자 이메일 주소 확인
   - 네트워크 연결 확인
   - 잠시 후 재시도
   ```

---

*작성일: 2025-08-18*
*메일브릿지 GPT Action 설정 가이드*