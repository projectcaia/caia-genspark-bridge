#!/usr/bin/env python3
"""
OpenAPI 스키마 GPT Actions 호환성 테스트
"""

import json
import requests
from typing import Dict, Any

def test_openapi_schema():
    """OpenAPI 스키마 확인 및 GPT 호환성 테스트"""
    
    print("🔍 OpenAPI 스키마 호환성 테스트")
    print("=" * 60)
    
    # 1. 스키마 가져오기
    url = "https://worker-production-4369.up.railway.app/openapi.json"
    print(f"\n1. 스키마 URL: {url}")
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        schema = response.json()
        print("   ✅ 스키마 로드 성공")
    except Exception as e:
        print(f"   ❌ 스키마 로드 실패: {e}")
        return
    
    # 2. OpenAPI 버전 확인
    print(f"\n2. OpenAPI 버전: {schema.get('openapi', 'Unknown')}")
    if schema.get('openapi', '').startswith('3.'):
        print("   ✅ OpenAPI 3.x 호환")
    else:
        print("   ⚠️  OpenAPI 3.x 권장")
    
    # 3. 서버 정보 확인
    servers = schema.get('servers', [])
    print(f"\n3. 서버 설정:")
    for server in servers:
        print(f"   - {server.get('url')}")
    if servers:
        print("   ✅ 서버 URL 설정됨")
    else:
        print("   ⚠️  서버 URL 설정 필요")
    
    # 4. GPT에서 사용할 주요 엔드포인트 확인
    print(f"\n4. GPT Actions 용 주요 엔드포인트:")
    
    important_endpoints = {
        '/tool/send': 'POST',  # 메일 발송
        '/inbox.json': 'GET',  # 인박스 조회
        '/mail/view': 'GET',   # 메일 상세
        '/mail/attach': 'GET'  # 첨부파일
    }
    
    paths = schema.get('paths', {})
    for endpoint, method in important_endpoints.items():
        if endpoint in paths and method.lower() in paths[endpoint]:
            operation = paths[endpoint][method.lower()]
            operation_id = operation.get('operationId', 'N/A')
            summary = operation.get('summary', 'N/A')
            print(f"\n   📌 {endpoint} [{method}]")
            print(f"      - Operation ID: {operation_id}")
            print(f"      - Summary: {summary}")
            
            # 파라미터 확인
            params = operation.get('parameters', [])
            if params:
                print(f"      - Parameters:")
                for param in params:
                    name = param.get('name')
                    required = param.get('required', False)
                    param_in = param.get('in', 'query')
                    req_mark = "✅" if required else "⚪"
                    print(f"        {req_mark} {name} ({param_in})")
            
            # Request Body 확인
            request_body = operation.get('requestBody')
            if request_body:
                content = request_body.get('content', {})
                if 'application/json' in content:
                    schema_ref = content['application/json'].get('schema', {})
                    ref = schema_ref.get('$ref', '')
                    if ref:
                        model_name = ref.split('/')[-1]
                        print(f"      - Request Body: {model_name}")
        else:
            print(f"\n   ⚠️  {endpoint} [{method}] - 없음")
    
    # 5. 인증 관련 정보
    print(f"\n5. 인증 설정 권장사항:")
    print("   - GPT Action에서 Bearer Token 사용")
    print("   - Header: Authorization: Bearer YOUR_AUTH_TOKEN")
    
    # 6. 스키마 모델 확인
    print(f"\n6. 주요 데이터 모델:")
    components = schema.get('components', {}).get('schemas', {})
    
    important_models = ['ToolSendReq', 'SendMailPayload']
    for model_name in important_models:
        if model_name in components:
            model = components[model_name]
            print(f"\n   📋 {model_name}:")
            properties = model.get('properties', {})
            required = model.get('required', [])
            
            for prop_name, prop_info in properties.items():
                prop_type = prop_info.get('type', 'unknown')
                is_required = prop_name in required
                req_mark = "✅" if is_required else "⚪"
                print(f"      {req_mark} {prop_name}: {prop_type}")
    
    # 7. GPT Action 설정 가이드 생성
    print("\n" + "=" * 60)
    print("📝 GPT Action 설정 요약:")
    print("\n1. Actions 페이지에서 'Import from URL' 선택")
    print(f"2. URL 입력: {url}")
    print("3. Authentication 설정:")
    print("   - Type: API Key")
    print("   - Auth Type: Bearer")
    print("   - Header: Authorization")
    print("   - Value: Bearer YOUR_AUTH_TOKEN")
    print("\n4. 주요 Action Operation IDs:")
    print("   - 메일 발송: tool_send_tool_send_post")
    print("   - 인박스 조회: inbox_json_inbox_json_get")
    print("   - 메일 상세: mail_view_mail_view_get")
    print("   - 첨부파일: mail_attach_mail_attach_get")
    
    return schema

def generate_sample_calls():
    """GPT Action 호출 예시 생성"""
    print("\n" + "=" * 60)
    print("🚀 GPT Action 호출 예시:")
    
    samples = {
        "메일 발송": {
            "action": "tool_send_tool_send_post",
            "parameters": {
                "payload": {
                    "to": ["recipient@example.com"],
                    "subject": "테스트 메일",
                    "text": "안녕하세요, 카이아입니다.",
                    "html": None
                }
            }
        },
        "인박스 조회": {
            "action": "inbox_json_inbox_json_get",
            "parameters": {
                "limit": 5
            }
        },
        "메일 상세 조회": {
            "action": "mail_view_mail_view_get",
            "parameters": {
                "id": 1
            }
        }
    }
    
    for name, sample in samples.items():
        print(f"\n### {name}:")
        print("```json")
        print(json.dumps(sample, indent=2, ensure_ascii=False))
        print("```")

def main():
    """메인 실행"""
    schema = test_openapi_schema()
    if schema:
        generate_sample_calls()
    
    print("\n" + "=" * 60)
    print("✅ OpenAPI 스키마 호환성 테스트 완료!")
    print("\n💡 다음 단계:")
    print("1. GPT Assistant Actions에서 URL Import")
    print("2. Bearer Token 인증 설정")
    print("3. 테스트 메일 발송/조회로 작동 확인")

if __name__ == "__main__":
    main()