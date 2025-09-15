# src/ersp.py
import re
from typing import Dict, Any, List

class ERSPProcessor:
    @staticmethod
    def extract_ersp(experience: Dict[str, Any]) -> Dict[str, Any]:
        """경험에서 ERSP 구조 추출 - 없으면 자동 생성"""
        # 기존 ERSP가 있으면 사용
        if "ersp" in experience and experience["ersp"]:
            return experience["ersp"]
        
        # ERSP 구조 생성
        content = experience.get("content", "")
        ersp = {
            "event": experience.get("event") or content[:100] if content else "unknown event",
            "interpretation": experience.get("interpretation") or ERSPProcessor._auto_interpret(experience),
            "lesson": experience.get("lesson") or ERSPProcessor._extract_lesson(experience),
            "if_then": experience.get("if_then") or ERSPProcessor._generate_rule(experience),
        }
        
        # priority/confidence 등 추가 필드 있으면 그대로 보존
        for k in ("priority", "confidence", "type", "actor", "timestamp"):
            if k in experience:
                ersp[k] = experience[k]
        return ersp
    
    @staticmethod
    def _auto_interpret(experience: Dict[str, Any]) -> str:
        """자동 해석 생성"""
        content = experience.get("content", "")
        event_type = experience.get("type", "")
        
        if "error" in content.lower():
            return "오류 상황 발생 - 원인 분석 필요"
        elif "success" in content.lower():
            return "성공적 수행 - 패턴 강화 필요"
        elif event_type == "feedback":
            return "피드백 수신 - 학습 기회"
        elif event_type == "decision":
            return "의사결정 시점 - 판단 근거 분석"
        else:
            return "경험 기록 - 추후 분석 필요"
    
    @staticmethod
    def _extract_lesson(experience: Dict[str, Any]) -> str:
        """교훈 추출"""
        content = experience.get("content", "").lower()
        event_type = experience.get("type", "")
        
        if "fail" in content or "error" in content:
            return "실패는 성장의 기회 - 원인을 분석하고 개선점을 찾자"
        elif "success" in content or "complete" in content:
            return "성공 패턴을 기억하고 강화하자"
        elif event_type == "interaction":
            return "상호작용을 통해 배우고 적응한다"
        elif event_type == "reflection":
            return "성찰을 통해 지혜를 얻는다"
        else:
            return "모든 경험은 학습의 재료가 된다"
    
    @staticmethod
    def _generate_rule(experience: Dict[str, Any]) -> str:
        """IF-THEN 규칙 생성"""
        event_type = experience.get("type", "")
        content = experience.get("content", "").lower()
        
        if event_type == "decision":
            return "IF similar_decision_context THEN apply_learned_pattern"
        elif "error" in content:
            return "IF error_detected THEN analyze_and_recover"
        elif "pattern" in content:
            return "IF pattern_matched THEN execute_associated_action"
        elif event_type == "feedback":
            return "IF feedback_received THEN update_pattern_scores"
        else:
            return "IF similar_context THEN recall_related_memories"

    @staticmethod
    def _parse_rule(rule: str):
        """
        'ΔVIX >= 7%' / 'dVIX > 7' 같은 형태를 파싱
        returns: (signal_key, op, threshold, percent_flag)
        """
        if not rule:
            return None
        m = re.search(r'([Δd]?VIX)\s*(>=|<=|>|<|==)\s*([0-9]+(?:\.[0-9]+)?)\s*%?', rule, re.I)
        if not m:
            return None
        sig = m.group(1)
        op = m.group(2)
        thr = float(m.group(3))
        # 컨텍스트 키 정규화: ΔVIX/dVIX → dVIX
        key = "dVIX"
        return (key, op, thr)

    @staticmethod
    def _eval(op: str, lhs: float, rhs: float) -> bool:
        return {
            ">":  lhs >  rhs,
            "<":  lhs <  rhs,
            ">=": lhs >= rhs,
            "<=": lhs <= rhs,
            "==": lhs == rhs,
        }[op]

    @staticmethod
    def match_if_then_conditions(current_context: Dict[str, Any], ersp_memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        현재 context와 메모리들의 ERSP.if_then 규칙을 대조해 일치하는 것만 반환
        반환 형식: [{ "rule": str, "action": str, "memory": dict }, ...]
        """
        # 컨텍스트 기반 매칭 확장
        context_type = current_context.get("type", "")
        context_keywords = set(str(current_context.get("content", "")).lower().split())
        out: List[Dict[str, Any]] = []
        ctx_vix = None

        # 컨텍스트에서 dVIX(%) 찾기: {"dVIX": 8.0} 형태 가정
        if isinstance(current_context, dict):
            v = current_context.get("dVIX")
            try:
                ctx_vix = float(v) if v is not None else None
            except Exception:
                ctx_vix = None

        for m in ersp_memories or []:
            # ERSP 구조 확인 - 없으면 생성
            if "ersp" not in m or not m["ersp"]:
                m["ersp"] = ERSPProcessor.extract_ersp(m)
            
            ersp = m["ersp"]
            rule = ersp.get("if_then") or m.get("if_then") or ""
            
            # 규칙 매칭 - 다양한 조건 평가
            matched = False
            action = "analyze"
            
            # 1. VIX 기반 규칙
            parsed = ERSPProcessor._parse_rule(rule)
            if parsed:
                key, op, thr = parsed
                if key == "dVIX" and ctx_vix is not None:
                    if ERSPProcessor._eval(op, ctx_vix, thr):
                        matched = True
                        text = f"{ersp.get('lesson','')} {rule}".lower()
                        if "hedge" in text:
                            action = "enter_hedge"
                        else:
                            action = "act_on_rule"
            
            # 2. 컨텍스트 타입 매칭
            if not matched and context_type:
                if context_type in rule.lower():
                    matched = True
                    action = "apply_type_specific_pattern"
            
            # 3. 키워드 매칭
            if not matched:
                rule_keywords = set(rule.lower().split())
                if context_keywords & rule_keywords:  # 교집합이 있으면
                    matched = True
                    action = "apply_keyword_pattern"
            
            # 4. 일반 패턴 매칭
            if not matched:
                if "similar_context" in rule.lower():
                    # 유사도 기반 매칭 (간단한 버전)
                    if m.get("_score", 0) > 0.7:  # 높은 유사도
                        matched = True
                        action = "apply_similar_pattern"
            
            if matched:
                out.append({
                    "rule": rule,
                    "action": action,
                    "memory": m,
                    "confidence": m.get("_score", 0.5)
                })
        
        # 신뢰도 기준 정렬
        out.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return out

    @staticmethod
    def compress_to_wisdom(ersp_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        여러 ERSP를 하나의 '원리'로 압축하여 지혜 생성
        """
        lessons = []
        interpretations = []
        rules = []
        ids = []
        total_confidence = 0.0
        
        for m in ersp_list or []:
            # ERSP 확인
            if "ersp" not in m or not m["ersp"]:
                m["ersp"] = ERSPProcessor.extract_ersp(m)
            
            ersp = m["ersp"]
            
            if ersp.get("lesson"):
                lessons.append(ersp["lesson"])
            if ersp.get("interpretation"):
                interpretations.append(ersp["interpretation"])
            if ersp.get("if_then"):
                rules.append(ersp["if_then"])
            if m.get("id"):
                ids.append(m["id"])
            
            # 신뢰도 누적
            total_confidence += m.get("_score", 0.5)
        
        # 중복 제거하며 원리 생성
        unique_lessons = list(dict.fromkeys(lessons))
        unique_interpretations = list(dict.fromkeys(interpretations))
        unique_rules = list(dict.fromkeys(rules))
        
        # 통합 원리 생성
        principle_parts = []
        if unique_lessons:
            principle_parts.append(f"교훈: {'; '.join(unique_lessons[:3])}")
        if unique_interpretations:
            principle_parts.append(f"해석: {'; '.join(unique_interpretations[:2])}")
        
        principle = " | ".join(principle_parts)[:500] if principle_parts else "경험을 통한 학습 필요"
        
        # 평균 신뢰도 계산
        avg_confidence = total_confidence / len(ersp_list) if ersp_list else 0.0
        
        return {
            "principle": principle,
            "evidence_ids": ids,
            "lesson_count": len(unique_lessons),
            "rule_count": len(unique_rules),
            "confidence": round(avg_confidence, 3),
            "applicable_rules": unique_rules[:5]  # 상위 5개 규칙
        }
