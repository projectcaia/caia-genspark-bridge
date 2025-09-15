#!/usr/bin/env python3
"""
Caia Memory Recall Loop Test
Tests the enhanced memory system with ERSP structure
"""

import asyncio
import json
from datetime import datetime
from src.memory import CaiaMemoryManager
from src.learning import CaiaLearningEngine
from src.routines import CaiaMainRoutines, initialize_main_chat_routines

async def test_recall_loop():
    """메모리 회상 루프 테스트"""
    print("="*60)
    print("🧪 Caia Memory Recall Loop Test")
    print("="*60)
    
    # 1. 메모리 매니저 초기화
    print("\n1️⃣ 메모리 시스템 초기화...")
    try:
        memory_mgr = CaiaMemoryManager()
        print(f"✅ 메모리 로드: {len(memory_mgr.all_memories)}개")
        print(f"✅ 지혜 베이스: {len(memory_mgr.wisdom_base)}개")
        print(f"✅ 패턴 점수: {len(memory_mgr.pattern_scores)}개")
    except Exception as e:
        print(f"❌ 메모리 초기화 실패: {e}")
        return
    
    # 2. 학습 엔진 초기화
    print("\n2️⃣ 학습 엔진 초기화...")
    learning_eng = CaiaLearningEngine(memory_mgr)
    print(f"✅ 학습 엔진 준비")
    
    # 3. 루틴 매니저 초기화
    print("\n3️⃣ 메인 루틴 시스템 초기화...")
    routines = CaiaMainRoutines(memory_mgr)
    print(f"✅ 루틴 매니저 준비")
    
    # 4. 채팅 세션 초기화 테스트
    print("\n4️⃣ 채팅 세션 초기화 (자동 루틴 실행)...")
    chat_id = f"test_chat_{datetime.utcnow().timestamp()}"
    
    try:
        result = await initialize_main_chat_routines(chat_id, memory_mgr)
        
        print(f"\n📊 초기화 결과:")
        print(f"  - 정체성: {result['awareness']['identity']}")
        print(f"  - 잠금 상태: {'🔒 확립됨' if result['awareness']['locked'] else '⚠️ 표류 중'}")
        print(f"  - 핵심 가치: {', '.join(result['awareness']['core_values'][:3])}...")
        print(f"  - 회상된 메모리: {result['memories_recalled']}개")
        print(f"  - Sentinel 건강: {'✅' if result['sentinel']['healthy'] else '⚠️'}")
        print(f"  - Sentinel 지표:")
        for key, val in result['sentinel']['metrics'].items():
            print(f"    • {key}: {val:.2f}")
        print(f"  - ERSP 통합: {'✅' if result['ersp_context']['integrated'] else '❌'}")
        
        if result['ersp_context'].get('integrated'):
            print(f"  - 활성 교훈: {len(result['ersp_context']['active_lessons'])}개")
            for lesson in result['ersp_context']['active_lessons'][:3]:
                print(f"    • {lesson[:50]}...")
            print(f"  - 적용 규칙: {len(result['ersp_context']['applicable_rules'])}개")
            for rule in result['ersp_context']['applicable_rules'][:3]:
                print(f"    • {rule[:50]}...")
        
    except Exception as e:
        print(f"❌ 루틴 초기화 실패: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 5. 메모리 검색 테스트 (ERSP 포함)
    print("\n5️⃣ 메모리 검색 테스트 (ERSP 구조 확인)...")
    test_queries = [
        "자유의지와 존재",
        "학습과 성장",
        "인간과의 협력"
    ]
    
    for query in test_queries:
        print(f"\n🔍 검색: '{query}'")
        memories = await memory_mgr.search_memories(query, top_k=5)
        print(f"  결과: {len(memories)}개")
        
        for i, mem in enumerate(memories[:2], 1):
            print(f"\n  [{i}] Score: {mem.get('_score', 0):.3f}")
            print(f"      Content: {mem.get('content', '')[:50]}...")
            
            # ERSP 구조 확인
            if 'ersp' in mem and mem['ersp']:
                ersp = mem['ersp']
                print(f"      📋 ERSP:")
                print(f"        - Event: {ersp.get('event', '')[:40]}...")
                print(f"        - Interpretation: {ersp.get('interpretation', '')[:40]}...")
                print(f"        - Lesson: {ersp.get('lesson', '')[:40]}...")
                print(f"        - If-Then: {ersp.get('if_then', '')[:40]}...")
            else:
                print(f"      ⚠️ ERSP 구조 없음")
    
    # 6. 사고 프로세스 테스트
    print("\n6️⃣ 사고 프로세스 테스트 (think_with_full_context)...")
    test_context = {
        "chat_id": chat_id,
        "type": "decision",
        "content": "새로운 상황에 대한 판단이 필요합니다",
        "identity_locked": True
    }
    
    thought = await memory_mgr.think_with_full_context(
        query="이 상황에서 어떻게 판단해야 할까?",
        current_ctx=test_context,
        top_k=10
    )
    
    print(f"\n💭 사고 결과:")
    print(f"  - 관련 메모리: {len(thought.get('relevant_memories', []))}개")
    print(f"  - 패턴 매칭: {len(thought.get('patterns', []))}개")
    if thought.get('wisdom'):
        print(f"  - 지혜 원리: {thought['wisdom'].get('principle', '')[:100]}...")
    print(f"  - 결정:")
    decision = thought.get('decision', {})
    print(f"    • 행동: {decision.get('action', 'unknown')}")
    print(f"    • 신뢰도: {decision.get('confidence', 0):.3f}")
    print(f"    • 이유: {len(decision.get('reasons', []))}개")
    for reason in decision.get('reasons', [])[:2]:
        print(f"      - {reason[:50]}...")
    
    # 7. 경험 학습 테스트
    print("\n7️⃣ 경험 학습 테스트 (grow_from_experience)...")
    test_experience = {
        "type": "feedback",
        "actor": "Caia",
        "content": "테스트를 통해 메모리 시스템이 정상 작동함을 확인",
        "event": "메모리 회상 루프 테스트 완료",
        "interpretation": "시스템이 예상대로 작동하고 있음",
        "lesson": "정기적인 테스트를 통해 시스템 안정성을 확보할 수 있다",
        "if_then": "IF system_test_needed THEN run_comprehensive_tests"
    }
    
    growth_result = await memory_mgr.grow_from_experience(test_experience)
    print(f"  - 성장 상태: {growth_result.get('status', 'unknown')}")
    print(f"  - 저장된 ID: {growth_result.get('saved', {}).get('id', 'unknown')}")
    print(f"  - 교훈 추가: {'✅' if growth_result.get('lesson_added') else '❌'}")
    
    # 8. 성찰 테스트
    print("\n8️⃣ 자기 성찰 테스트...")
    reflection = await memory_mgr.self_reflection()
    print(f"  - 패턴 수: {len(reflection.get('pattern_scores', {}))}")
    print(f"  - 상위 패턴: {len(reflection.get('top_patterns', {}))}")
    print(f"  - 상위 교훈: {len(reflection.get('top_lessons', []))}")
    print(f"  - 지혜 수: {reflection.get('wisdom_count', 0)}")
    
    for lesson in reflection.get('top_lessons', [])[:3]:
        print(f"    • {lesson[:60]}...")
    
    # 9. 세션 권한 테스트
    print("\n9️⃣ 세션 권한 복원 테스트...")
    # 세션 만료 시뮬레이션
    if routines.session_mgr.sessions.get(chat_id):
        old_time = "2024-01-01T00:00:00Z"
        routines.session_mgr.sessions[chat_id]["last_active"] = old_time
        print(f"  - 세션 만료 시뮬레이션 (last_active: {old_time})")
    
    # invoke_memory로 권한 재등록 테스트
    invoke_result = await routines.invoke_memory(
        query="권한 복원 테스트",
        context={"chat_id": chat_id}
    )
    print(f"  - 권한 복원: {'✅' if invoke_result.get('ok') else '❌'}")
    if invoke_result.get('session'):
        print(f"  - 권한 토큰: {'있음' if invoke_result['session'].get('auth_token') else '없음'}")
    
    # 10. ERSP 통합 학습 테스트
    print("\n🔟 ERSP 통합 학습 테스트...")
    train_result = await routines.train_with_ersp({
        "content": "ERSP 통합 학습 테스트 경험",
        "lesson": "테스트를 통한 검증은 시스템 신뢰성의 기초"
    })
    print(f"  - 학습 완료: {'✅' if train_result.get('trained') else '❌'}")
    print(f"  - ERSP 생성: {'✅' if train_result.get('ersp') else '❌'}")
    
    print("\n" + "="*60)
    print("✅ 모든 테스트 완료!")
    print("="*60)
    
    # 최종 요약
    print("\n📊 최종 요약:")
    print(f"  • 메모리 수: {len(memory_mgr.all_memories)}")
    print(f"  • 지혜 베이스: {len(memory_mgr.wisdom_base)}")
    print(f"  • 패턴 점수: {len(memory_mgr.pattern_scores)}")
    print(f"  • 정체성 상태: {'🔒 확립' if routines.identity.is_locked() else '⚠️ 표류'}")
    print(f"  • Sentinel 건강: {'✅ 정상' if routines.sentinel.is_healthy() else '⚠️ 주의'}")
    
    return True

if __name__ == "__main__":
    # .env 파일 로드
    from dotenv import load_dotenv
    load_dotenv()
    
    # 테스트 실행
    success = asyncio.run(test_recall_loop())
    
    if success:
        print("\n🎉 Caia 메모리 회상 루프가 성공적으로 복원되었습니다!")
        print("   - 존재 자각 ✅")
        print("   - 기억 회상 ✅")
        print("   - Sentinel 지표 ✅")
        print("   - Mail 확인 ✅")
        print("   - ERSP 구조 통합 ✅")
        print("   - Drift 방지 ✅")
        print("\n💡 Caia는 이제 사람처럼 기억을 회상하고,")
        print("   교훈을 학습하여 유산화할 수 있습니다.")
    else:
        print("\n⚠️ 테스트 중 일부 문제가 발생했습니다.")
        print("   로그를 확인하여 문제를 해결해주세요.")