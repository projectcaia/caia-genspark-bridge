#!/usr/bin/env python3
"""
Railway 환경변수 설정 확인 스크립트
메일브릿지가 정상 작동하기 위한 모든 환경변수를 체크합니다
"""

import os
import json
import requests
from typing import Dict, List, Tuple
from datetime import datetime

# 색상 코드 (터미널 출력용)
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def check_env_var(name: str, required: bool = True, description: str = "") -> Tuple[bool, str]:
    """환경변수 체크"""
    value = os.getenv(name, "")
    exists = bool(value)
    
    if exists:
        # 민감한 정보는 일부만 표시
        if "KEY" in name or "TOKEN" in name:
            display_value = f"{value[:8]}..." if len(value) > 8 else "***"
        else:
            display_value = value
        return True, display_value
    else:
        return False, "NOT SET"

def check_railway_env():
    """Railway 환경변수 체크"""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}📋 Railway 환경변수 설정 확인{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    # 필수 환경변수
    required_vars = [
        ("AUTH_TOKEN", "API 인증 토큰 (GPT Tool 호출 시 필요)"),
        ("INBOUND_TOKEN", "인바운드 메일 수신 인증 토큰"),
        ("SENDGRID_API_KEY", "SendGrid API 키 (메일 발송)"),
        ("OPENAI_API_KEY", "OpenAI API 키 (Assistant 연동)"),
        ("ASSISTANT_ID", "OpenAI Assistant ID"),
        ("THREAD_ID", "OpenAI Thread ID"),
        ("SENDER_DEFAULT", "기본 발신자 이메일 주소"),
    ]
    
    # 선택 환경변수
    optional_vars = [
        ("AUTO_RUN", "자동 Assistant 실행 (true/false)"),
        ("TELEGRAM_BOT_TOKEN", "Telegram 봇 토큰 (알림용)"),
        ("TELEGRAM_CHAT_ID", "Telegram 채팅 ID (알림용)"),
        ("ALERT_CLASSES", "알림 클래스 (SENTINEL,REFLEX,ZENSPARK)"),
        ("ALERT_IMPORTANCE_MIN", "최소 중요도 임계값 (0.0-1.0)"),
        ("DB_PATH", "데이터베이스 경로"),
    ]
    
    print(f"{Colors.BOLD}1. 필수 환경변수:{Colors.END}")
    print("-" * 40)
    
    required_ok = True
    for var_name, description in required_vars:
        exists, value = check_env_var(var_name)
        if exists:
            print(f"  ✅ {Colors.GREEN}{var_name}{Colors.END}: {value}")
        else:
            print(f"  ❌ {Colors.RED}{var_name}{Colors.END}: NOT SET - {description}")
            required_ok = False
    
    print(f"\n{Colors.BOLD}2. 선택 환경변수:{Colors.END}")
    print("-" * 40)
    
    for var_name, description in optional_vars:
        exists, value = check_env_var(var_name)
        if exists:
            print(f"  ✅ {Colors.GREEN}{var_name}{Colors.END}: {value}")
        else:
            print(f"  ⚪ {Colors.YELLOW}{var_name}{Colors.END}: NOT SET (선택사항)")
    
    return required_ok

def test_api_connection():
    """API 연결 테스트"""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}🔌 API 연결 테스트{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    base_url = "https://worker-production-4369.up.railway.app"
    auth_token = os.getenv("AUTH_TOKEN", "")
    
    # 1. Health Check (인증 불필요)
    print("1. Health Check 테스트...")
    try:
        resp = requests.get(f"{base_url}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  ✅ {Colors.GREEN}서비스 정상{Colors.END}")
            print(f"     - 버전: {data.get('version', 'unknown')}")
            print(f"     - 발신자: {data.get('sender', 'unknown')}")
        else:
            print(f"  ❌ {Colors.RED}서비스 응답 오류{Colors.END}: {resp.status_code}")
    except Exception as e:
        print(f"  ❌ {Colors.RED}연결 실패{Colors.END}: {str(e)}")
    
    # 2. Status Check (인증 필요)
    if auth_token:
        print("\n2. Status Check 테스트 (인증 필요)...")
        try:
            headers = {"Authorization": f"Bearer {auth_token}"}
            resp = requests.get(f"{base_url}/status", headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  ✅ {Colors.GREEN}인증 성공{Colors.END}")
                print(f"     - 메시지 수: {data.get('messages', 0)}")
                print(f"     - AUTO_RUN: {data.get('auto_run', False)}")
                print(f"     - Alert Classes: {data.get('alert_classes', [])}")
            elif resp.status_code == 401:
                print(f"  ❌ {Colors.RED}인증 실패{Colors.END}: AUTH_TOKEN이 올바르지 않습니다")
            else:
                print(f"  ❌ {Colors.RED}오류{Colors.END}: {resp.status_code}")
        except Exception as e:
            print(f"  ❌ {Colors.RED}연결 실패{Colors.END}: {str(e)}")
    else:
        print(f"  ⚠️  {Colors.YELLOW}AUTH_TOKEN 미설정{Colors.END}: 테스트 건너뜀")

def test_openai_connection():
    """OpenAI API 연결 테스트"""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}🤖 OpenAI API 연결 테스트{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    assistant_id = os.getenv("ASSISTANT_ID", "")
    thread_id = os.getenv("THREAD_ID", "")
    
    if not api_key:
        print(f"  ⚠️  {Colors.YELLOW}OPENAI_API_KEY 미설정{Colors.END}: 테스트 건너뜀")
        return False
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        # Assistant 확인
        if assistant_id:
            print("1. Assistant 확인...")
            try:
                assistant = client.beta.assistants.retrieve(assistant_id)
                print(f"  ✅ {Colors.GREEN}Assistant 연결 성공{Colors.END}")
                print(f"     - 이름: {assistant.name}")
                print(f"     - 모델: {assistant.model}")
                tools_count = len(assistant.tools) if assistant.tools else 0
                print(f"     - 도구 수: {tools_count}")
            except Exception as e:
                print(f"  ❌ {Colors.RED}Assistant 확인 실패{Colors.END}: {str(e)}")
        
        # Thread 확인
        if thread_id:
            print("\n2. Thread 확인...")
            try:
                thread = client.beta.threads.retrieve(thread_id)
                print(f"  ✅ {Colors.GREEN}Thread 연결 성공{Colors.END}")
                print(f"     - Thread ID: {thread.id}")
                
                # 최근 메시지 확인
                messages = client.beta.threads.messages.list(thread_id, limit=1)
                if messages.data:
                    print(f"     - 최근 메시지: {len(messages.data)}개")
            except Exception as e:
                print(f"  ❌ {Colors.RED}Thread 확인 실패{Colors.END}: {str(e)}")
                
        return True
        
    except ImportError:
        print(f"  ⚠️  {Colors.YELLOW}OpenAI 라이브러리 미설치{Colors.END}")
        return False
    except Exception as e:
        print(f"  ❌ {Colors.RED}OpenAI 연결 실패{Colors.END}: {str(e)}")
        return False

def generate_setup_guide():
    """설정 가이드 생성"""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}📝 설정 가이드{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    missing_vars = []
    
    # 필수 환경변수 체크
    required = [
        "AUTH_TOKEN", "INBOUND_TOKEN", "SENDGRID_API_KEY",
        "OPENAI_API_KEY", "ASSISTANT_ID", "THREAD_ID"
    ]
    
    for var in required:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"{Colors.RED}⚠️  다음 환경변수를 Railway에 설정해주세요:{Colors.END}\n")
        
        for var in missing_vars:
            print(f"  {Colors.YELLOW}{var}{Colors.END}")
            if var == "AUTH_TOKEN":
                print("    → 임의의 보안 토큰 생성 (예: 32자 랜덤 문자열)")
            elif var == "INBOUND_TOKEN":
                print("    → SendGrid Webhook 인증용 토큰")
            elif var == "SENDGRID_API_KEY":
                print("    → SendGrid 콘솔에서 API Key 생성")
            elif var == "OPENAI_API_KEY":
                print("    → OpenAI 플랫폼에서 API Key 생성")
            elif var == "ASSISTANT_ID":
                print("    → OpenAI Assistant ID (asst_로 시작)")
            elif var == "THREAD_ID":
                print("    → OpenAI Thread ID (thread_로 시작)")
            print()
    else:
        print(f"{Colors.GREEN}✅ 모든 필수 환경변수가 설정되어 있습니다!{Colors.END}")
    
    # SendGrid Webhook 설정
    print(f"\n{Colors.BOLD}SendGrid Inbound Parse 설정:{Colors.END}")
    inbound_token = os.getenv("INBOUND_TOKEN", "YOUR_INBOUND_TOKEN")
    print(f"  Webhook URL: {Colors.BLUE}https://worker-production-4369.up.railway.app/inbound/sen?token={inbound_token}{Colors.END}")
    
    # GPT Assistant Tool 설정
    print(f"\n{Colors.BOLD}GPT Assistant Tools 설정:{Colors.END}")
    auth_token = os.getenv("AUTH_TOKEN", "YOUR_AUTH_TOKEN")
    print(f"  Base URL: {Colors.BLUE}https://worker-production-4369.up.railway.app{Colors.END}")
    print(f"  Auth Header: {Colors.BLUE}Authorization: Bearer {auth_token[:8] if auth_token else 'YOUR_AUTH_TOKEN'}...{Colors.END}")

def main():
    """메인 실행"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}🚀 메일브릿지 설정 종합 점검{Colors.END}")
    print(f"{Colors.BLUE}실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.END}")
    
    # 1. 환경변수 체크
    env_ok = check_railway_env()
    
    # 2. API 연결 테스트
    test_api_connection()
    
    # 3. OpenAI 연결 테스트
    test_openai_connection()
    
    # 4. 설정 가이드
    generate_setup_guide()
    
    # 최종 결과
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}📊 최종 점검 결과{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    if env_ok:
        print(f"  {Colors.GREEN}✅ 시스템이 정상적으로 설정되어 있습니다!{Colors.END}")
        print(f"  {Colors.GREEN}   카이아가 메일을 수신/발신할 준비가 되었습니다.{Colors.END}")
    else:
        print(f"  {Colors.YELLOW}⚠️  일부 설정이 필요합니다.{Colors.END}")
        print(f"  {Colors.YELLOW}   위의 가이드를 참고하여 설정을 완료해주세요.{Colors.END}")
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}\n")

if __name__ == "__main__":
    main()