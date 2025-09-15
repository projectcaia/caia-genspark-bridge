#!/usr/bin/env python3
"""
GPT Tools 연동 테스트 스크립트
- 메일 수신/발신 기능 테스트
- OpenAI Assistant API 연동 확인
"""

import os
import json
import requests
import time
from typing import Dict, List, Optional
from datetime import datetime

# 환경 변수 로드
BASE_URL = "https://worker-production-4369.up.railway.app"
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
INBOUND_TOKEN = os.getenv("INBOUND_TOKEN", "")

class MailBridgeTestor:
    def __init__(self):
        self.base_url = BASE_URL
        self.auth_token = AUTH_TOKEN
        self.inbound_token = INBOUND_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.auth_token}"
        }
        
    def test_health(self) -> Dict:
        """서비스 헬스 체크"""
        print("\n🔍 서비스 헬스 체크...")
        try:
            resp = requests.get(f"{self.base_url}/health")
            resp.raise_for_status()
            data = resp.json()
            print(f"✅ 서비스 정상 작동: {data}")
            return data
        except Exception as e:
            print(f"❌ 헬스 체크 실패: {e}")
            return {}
    
    def test_status(self) -> Dict:
        """서비스 상태 확인 (인증 필요)"""
        print("\n🔍 서비스 상태 확인...")
        try:
            resp = requests.get(
                f"{self.base_url}/status",
                headers=self.headers
            )
            resp.raise_for_status()
            data = resp.json()
            print(f"✅ 서비스 상태:")
            print(f"  - 버전: {data.get('version')}")
            print(f"  - 메시지 수: {data.get('messages')}")
            print(f"  - AUTO_RUN: {data.get('auto_run')}")
            print(f"  - Alert Classes: {data.get('alert_classes')}")
            return data
        except Exception as e:
            print(f"❌ 상태 확인 실패: {e}")
            return {}
    
    def test_inbound_email(self) -> Dict:
        """인바운드 이메일 수신 테스트"""
        print("\n📧 인바운드 이메일 수신 테스트...")
        
        # 테스트 메일 데이터
        test_mail = {
            "from": "test@example.com",
            "to": "caia@caia-agent.com",
            "subject": f"[TEST] GPT 연동 테스트 - {datetime.now().isoformat()}",
            "text": """
안녕하세요 카이아,

이것은 GPT Tools 연동 테스트 메일입니다.
정상적으로 스레드에 전달되고 있는지 확인해주세요.

주요 테스트 항목:
1. 메일 수신 확인
2. Assistant Thread 메시지 생성
3. Auto Run 실행 (설정된 경우)

감사합니다.
            """,
            "html": "<p>HTML 버전의 테스트 메일입니다.</p>"
        }
        
        try:
            resp = requests.post(
                f"{self.base_url}/inbound/sen?token={self.inbound_token}",
                data=test_mail
            )
            resp.raise_for_status()
            data = resp.json()
            print(f"✅ 인바운드 메일 수신 성공:")
            print(f"  - 메시지 ID: {data.get('id')}")
            print(f"  - Thread Message ID: {data.get('assistant', {}).get('thread_message_id')}")
            print(f"  - Run ID: {data.get('assistant', {}).get('run_id')}")
            print(f"  - Alert Class: {data.get('alert_class')}")
            print(f"  - Importance: {data.get('importance')}")
            return data
        except Exception as e:
            print(f"❌ 인바운드 메일 수신 실패: {e}")
            if hasattr(e, 'response'):
                print(f"  Response: {e.response.text}")
            return {}
    
    def test_send_email_tool(self) -> Dict:
        """GPT Tool 용 간단 메일 발신 테스트"""
        print("\n📤 GPT Tool 메일 발신 테스트...")
        
        # 툴용 간단한 페이로드
        tool_payload = {
            "to": ["test@example.com"],
            "subject": f"[GPT Tool Test] 발신 테스트 - {datetime.now().isoformat()}",
            "text": "GPT Assistant Tool을 통한 메일 발신 테스트입니다.",
            "html": None
        }
        
        try:
            resp = requests.post(
                f"{self.base_url}/tool/send",
                json=tool_payload,
                headers=self.headers
            )
            resp.raise_for_status()
            data = resp.json()
            print(f"✅ 툴 메일 발신 성공:")
            print(f"  - 메시지: {data.get('message')}")
            print(f"  - Status Code: {data.get('status_code')}")
            return data
        except Exception as e:
            print(f"❌ 툴 메일 발신 실패: {e}")
            if hasattr(e, 'response'):
                print(f"  Response: {e.response.text}")
            return {}
    
    def test_inbox_view(self) -> Dict:
        """인박스 조회 테스트"""
        print("\n📥 인박스 조회 테스트...")
        
        try:
            resp = requests.get(
                f"{self.base_url}/inbox.json?limit=5",
                headers=self.headers
            )
            resp.raise_for_status()
            data = resp.json()
            messages = data.get('messages', [])
            print(f"✅ 인박스 조회 성공: {len(messages)}개 메시지")
            for msg in messages[:3]:  # 최근 3개만 표시
                print(f"  - ID {msg['id']}: {msg['subject'][:50]}...")
                print(f"    From: {msg['from']}, Date: {msg['date']}")
            return data
        except Exception as e:
            print(f"❌ 인박스 조회 실패: {e}")
            return {}
    
    def generate_gpt_tool_schema(self):
        """GPT Assistant Tool Schema 생성"""
        print("\n🛠️ GPT Assistant Tool Schema 생성...")
        
        # 메일 발신 툴 스키마
        send_mail_schema = {
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
                            "nullable": True
                        }
                    },
                    "required": ["to", "subject", "text"]
                }
            }
        }
        
        # 인박스 조회 툴 스키마
        check_inbox_schema = {
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
        
        # 메일 상세 조회 툴 스키마
        view_email_schema = {
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
        
        print("\n📋 GPT Assistant에 추가할 Tool Schemas:")
        print("\n1. send_email:")
        print(json.dumps(send_mail_schema, indent=2, ensure_ascii=False))
        print("\n2. check_inbox:")
        print(json.dumps(check_inbox_schema, indent=2, ensure_ascii=False))
        print("\n3. view_email:")
        print(json.dumps(view_email_schema, indent=2, ensure_ascii=False))
        
        # Function Call 처리 예시 코드
        function_handlers = """
# GPT Assistant Function Call 처리 예시 (Python)

async def handle_function_call(function_name, arguments):
    base_url = "https://worker-production-4369.up.railway.app"
    auth_token = "YOUR_AUTH_TOKEN"
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    if function_name == "send_email":
        # 메일 발송
        response = requests.post(
            f"{base_url}/tool/send",
            json=arguments,
            headers=headers
        )
        return response.json()
    
    elif function_name == "check_inbox":
        # 인박스 조회
        limit = arguments.get("limit", 10)
        response = requests.get(
            f"{base_url}/inbox.json?limit={limit}",
            headers=headers
        )
        return response.json()
    
    elif function_name == "view_email":
        # 메일 상세 조회
        mail_id = arguments["id"]
        response = requests.get(
            f"{base_url}/mail/view?id={mail_id}",
            headers=headers
        )
        return response.json()
"""
        
        print("\n📝 Function Call 처리 코드 예시:")
        print(function_handlers)
        
        return {
            "schemas": [send_mail_schema, check_inbox_schema, view_email_schema],
            "handler_example": function_handlers
        }

def main():
    """메인 테스트 실행"""
    print("=" * 60)
    print("🚀 메일브릿지 GPT Tools 연동 테스트")
    print("=" * 60)
    
    tester = MailBridgeTestor()
    
    # 1. 서비스 상태 확인
    tester.test_health()
    tester.test_status()
    
    # 2. 인박스 조회
    tester.test_inbox_view()
    
    # 3. 인바운드 메일 수신 테스트
    if input("\n📧 인바운드 메일 수신 테스트를 실행하시겠습니까? (y/n): ").lower() == 'y':
        tester.test_inbound_email()
    
    # 4. 메일 발신 테스트
    if input("\n📤 메일 발신 테스트를 실행하시겠습니까? (y/n): ").lower() == 'y':
        tester.test_send_email_tool()
    
    # 5. GPT Tool Schema 생성
    print("\n" + "=" * 60)
    tester.generate_gpt_tool_schema()
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("\n📌 다음 단계:")
    print("1. GPT Assistant에 위 Tool Schema들을 추가하세요")
    print("2. Railway 환경변수가 모두 설정되었는지 확인하세요:")
    print("   - AUTH_TOKEN: API 인증 토큰")
    print("   - INBOUND_TOKEN: 인바운드 메일 수신 토큰")
    print("   - SENDGRID_API_KEY: SendGrid API 키")
    print("   - OPENAI_API_KEY: OpenAI API 키")
    print("   - ASSISTANT_ID: OpenAI Assistant ID")
    print("   - THREAD_ID: OpenAI Thread ID")
    print("   - AUTO_RUN: true/false (자동 실행 여부)")
    print("=" * 60)

if __name__ == "__main__":
    main()