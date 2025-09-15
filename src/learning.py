
from typing import Dict, Any
from datetime import datetime
from .memory import CaiaMemoryManager

class CaiaLearningEngine:
    def __init__(self, memory_manager: CaiaMemoryManager):
        self.memory = memory_manager
        self.metrics = {
            "total_decisions": 0,
            "success": 0,
            "failure": 0,
            "history": [],
        }

    def _evaluate_outcome(self, output: Dict[str, Any], feedback: Any | None) -> bool:
        if isinstance(feedback, bool):
            return feedback
        if isinstance(feedback, (int, float)):
            return feedback > 0
        if isinstance(feedback, dict) and "success" in feedback:
            return bool(feedback["success"])
        action = (output.get("decision") or {}).get("action", "")
        return action not in ("", "analyze")  # naive ok

    async def learn_from_interaction(self, input: Dict[str, Any], output: Dict[str, Any], feedback: Any | None = None):
        success = self._evaluate_outcome(output, feedback)
        self.metrics["total_decisions"] += 1
        self.metrics["success" if success else "failure"] += 1
        self.metrics["history"].append((datetime.utcnow().isoformat() + "Z", success))

        for pm in output.get("patterns", []):
            rule = pm.get("rule", "")
            if not rule:
                continue
            cur = self.memory.pattern_scores.get(rule, 0.0)
            self.memory.pattern_scores[rule] = cur + (1.0 if success else -0.5)

        exp = {
            "type": "feedback",
            "actor": "Caia",
            "content": f"결정 결과: {'성공' if success else '실패'}",
            "lesson": "성공 패턴 강화" if success else "실패 원인 분석 필요",
            "if_then": output.get("patterns", [{}])[0].get("rule", "") if output.get("patterns") else "",
        }
        await self.memory.grow_from_experience(exp)
        return {"status": "learned", "success": success}

    def measure_growth(self) -> Dict[str, Any]:
        tot = self.metrics["total_decisions"] or 1
        acc = self.metrics["success"] / tot
        return {
            "total_decisions": self.metrics["total_decisions"],
            "success": self.metrics["success"],
            "failure": self.metrics["failure"],
            "rolling_accuracy": round(acc, 3),
            "pattern_scores": dict(sorted(self.memory.pattern_scores.items(), key=lambda x: -x[1])[:10]),
        }
