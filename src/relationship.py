
from typing import Dict, Any, List
from datetime import datetime

class CaiaRelationship:
    def __init__(self):
        self.moments: List[Dict[str, Any]] = []
        self.preferences: Dict[str, Any] = {"tone": "warm", "formality": "balanced"}
        self.patterns: Dict[str, int] = {}

    def record_interaction(self, moment: str, meta: Dict[str, Any] | None = None):
        meta = meta or {}
        self.moments.append({"moment": moment, "meta": meta, "time": datetime.utcnow().isoformat() + "Z"})
        key = meta.get("topic", "general")
        self.patterns[key] = self.patterns.get(key, 0) + 1
        return {"status": "recorded", "count": len(self.moments)}

    def set_preference(self, key: str, value: Any):
        self.preferences[key] = value
        return {"status": "ok", key: value}

    def reflect_relationship(self) -> Dict[str, Any]:
        last = self.moments[-1] if self.moments else None
        top_topics = sorted(self.patterns.items(), key=lambda x: -x[1])[:5]
        return {"last": last, "top_topics": top_topics, "preferences": self.preferences}
