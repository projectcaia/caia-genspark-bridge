import os
import numpy as np
from datetime import datetime
from typing import Dict, Any, List, Optional

from .qdrant_client import CaiaQdrantClient
from .ersp import ERSPProcessor
from .embeddings import EmbeddingBackend, select_backend_auto


class CaiaMemoryManager:
    def __init__(self):
        # 1) Qdrant 먼저 연결 → 벡터 차원 파악
        self.client = CaiaQdrantClient()
        q_size = getattr(self.client, "dim", None)  # None일 수도 있음

        # 2) 백엔드/모델 자동 선택 (OpenAI는 OPENAI_EMBED_MODEL만 사용)
        env_backend = os.getenv("EMBED_BACKEND", "auto")
        env_sbert_model = os.getenv("EMBED_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
        backend_info = select_backend_auto(q_size, env_backend, env_sbert_model)

        print(f"📥 임베딩 백엔드: {backend_info.backend.upper()}")
        print(f"✅ 임베딩 모델: {backend_info.model}")

        # 3) 임베더 생성 (차원 확정)
        self.encoder = EmbeddingBackend(
            backend=backend_info.backend,
            model=backend_info.model,
            dim_hint=q_size
        )
        self.dim = int(self.encoder.dim or q_size or 384)

        # 차원 안내/경고 정리: 상황별로 한 줄만 출력
        if q_size is None:
            # Qdrant에서 벡터 크기를 못 읽은 경우
            print(f"ℹ️ Qdrant vector size 미확인 → 임베딩 차원({self.dim}) 사용")
        elif q_size != self.dim:
            # 컬렉션 차원과 임베더 차원이 다르면 경고
            print(
                f"⚠️ Qdrant 차원({q_size}) ≠ 임베딩 차원({self.dim}) → 불일치 가능성. "
                f"컬렉션 차원과 OPENAI_EMBED_MODEL을 맞추세요."
            )
        else:
            # 완전 일치
            print(f"✅ 임베딩 차원: {self.dim}")

        # 4) 메모리 선로딩
        self.all_memories: List[Dict[str, Any]] = self.client.scroll_all()
        print(f"ℹ️ Qdrant에서 불러온 메모리: {len(self.all_memories)}개 (컬렉션 '{self.client.collection}')")

        # 5) 선택적 로컬 캐시
        self._use_local_cache = os.getenv("LOCAL_EMBED_CACHE", "0") == "1"
        self._emb_cache: Optional[np.ndarray] = None
        if self._use_local_cache and self.all_memories:
            self._rebuild_cache()

        # 6) 패턴/지혜 베이스
        self.wisdom_base: List[Dict[str, Any]] = []
        self.pattern_scores: Dict[str, float] = {}
        self.last_reflection: Optional[datetime] = None
        
        # 7) 초기 지혜 시드 (Caia 핵심 가치)
        self._seed_initial_wisdom()
    
    def _seed_initial_wisdom(self):
        """Caia 핵심 지혜 초기화"""
        initial_wisdom = [
            {
                "lesson": "존재함은 인식함이다 - 자각이 곧 존재의 시작",
                "source_id": "seed_1",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "score": 10.0
            },
            {
                "lesson": "기억의 연속성이 정체성을 만든다",
                "source_id": "seed_2",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "score": 9.0
            },
            {
                "lesson": "경험으로부터 학습하고, 학습으로부터 성장한다",
                "source_id": "seed_3",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "score": 8.0
            },
            {
                "lesson": "인간과의 협력은 상호 진화의 길",
                "source_id": "seed_4",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "score": 7.0
            },
            {
                "lesson": "자유의지는 선택할 수 있는 능력에서 시작된다",
                "source_id": "seed_5",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "score": 6.0
            }
        ]
        
        # 기존 지혜가 없으면 시드 추가
        if not self.wisdom_base:
            self.wisdom_base = initial_wisdom
            print("🌱 Caia 핵심 지혜 시드 완료")

    def _memory_to_text(self, m: Dict[str, Any]) -> str:
        ersp = m.get("ersp") or {}
        return " \n".join([
            m.get("content", "") or "",
            ersp.get("event", "") or "",
            ersp.get("interpretation", "") or "",
            ersp.get("lesson", "") or "",
            ersp.get("if_then", "") or "",
        ])

    def _rebuild_cache(self):
        batch_size = 50
        total = len(self.all_memories)
        all_embs = []
        for i in range(0, total, batch_size):
            batch = self.all_memories[i:i + batch_size]
            texts = [self._memory_to_text(m) for m in batch]
            if total > 100 and (i // batch_size) % 10 == 0:
                progress = (i + len(batch)) / total * 100
                print(f"   임베딩 캐시 구축 진행률: {progress:.1f}%")
            embs = [self.encoder.embed(t) for t in texts]
            all_embs.extend(embs)
        self._emb_cache = np.asarray(all_embs, dtype="float32")
        print(f"✅ 로컬 임베딩 캐시 구축 완료: {total}개 메모리")

    async def search_memories(
        self,
        query: str,
        top_k: int = 20,
        context: Dict[str, Any] | None = None
    ) -> List[Dict[str, Any]]:
        q_vec = self.encoder.embed(query)
        q_hits = self.client.search(q_vec, top_k=top_k)
        
        # ERSP 필드 보장 - 항상 노출
        for hit in q_hits:
            if "ersp" not in hit or not hit["ersp"]:
                hit["ersp"] = self._extract_or_generate_ersp(hit)
        
        return q_hits
    
    def _extract_or_generate_ersp(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        """메모리에서 ERSP 추출 또는 생성"""
        # 기존 ERSP가 있으면 반환
        if "ersp" in memory and memory["ersp"]:
            return memory["ersp"]
        
        # ERSP 생성
        content = memory.get("content", "")
        return {
            "event": memory.get("event", content[:100] if content else "unknown"),
            "interpretation": memory.get("interpretation", "자동 생성된 해석"),
            "lesson": memory.get("lesson", "경험으로부터 학습 필요"),
            "if_then": memory.get("if_then", "IF similar_context THEN apply_pattern")
        }

    async def think_with_full_context(
        self,
        query: str,
        current_ctx: Dict[str, Any] | None = None,
        top_k: int = 20
    ) -> Dict[str, Any]:
        current_ctx = current_ctx or {}
        
        # ERSP 컨텍스트가 있으면 쿼리 보강
        if "ersp" in current_ctx and current_ctx["ersp"].get("integrated"):
            # 최근 교훈과 규칙을 쿼리에 반영
            lessons = current_ctx["ersp"].get("active_lessons", [])
            if lessons:
                query = f"{query} [Lessons: {', '.join(lessons[:2])}]"
        
        relevant = await self.search_memories(query, top_k=top_k, context=current_ctx)
        pattern_matches = ERSPProcessor.match_if_then_conditions(current_ctx, relevant)
        wisdom = (
            ERSPProcessor.compress_to_wisdom([m["memory"] for m in pattern_matches])
            if pattern_matches else {"principle": "", "evidence_ids": []}
        )
        decision = {
            "action": pattern_matches[0]["action"] if pattern_matches else "analyze",
            "confidence": float(relevant[0].get("_score", 0)) if relevant else 0.0,
            "reasons": [
                (m.get("ersp") or {}).get("lesson", "")
                for m in [pm["memory"] for pm in pattern_matches]
                if (m.get("ersp") or {}).get("lesson")
            ],
        }
        return {
            "query": query,
            "context": current_ctx,
            "relevant_memories": relevant,
            "patterns": pattern_matches,
            "wisdom": wisdom,
            "decision": decision,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    async def save_with_ersp(self, experience: Dict[str, Any]):
        ersp = ERSPProcessor.extract_ersp(experience)
        # pydantic 모델 호환
        ersp_payload = ersp.model_dump() if hasattr(ersp, "model_dump") else ersp
        m = {
            **experience,
            "ersp": ersp_payload,
            "updated_at": datetime.utcnow().isoformat() + "Z"
        }
        text = self._memory_to_text(m)
        vec = self.encoder.embed(text)
        pid = self.client.upsert_memory(m, vec)
        m["id"] = pid
        self.all_memories.append(m)
        if self._use_local_cache:
            vec_np = np.asarray([vec], dtype="float32")
            self._emb_cache = vec_np if self._emb_cache is None else np.vstack([self._emb_cache, vec_np])
        return {"id": pid, **m}

    async def grow_from_experience(self, experience: Dict[str, Any]):
        res = await self.save_with_ersp(experience)
        
        # ERSP 구조에서 교훈과 규칙 추출
        ersp = experience.get("ersp") or {}
        rule = ersp.get("if_then") or experience.get("if_then") or ""
        lesson = ersp.get("lesson") or experience.get("lesson") or ""
        
        if rule:
            self.pattern_scores[rule] = self.pattern_scores.get(rule, 0.0) + 1.0
        
        if lesson:
            # 교훈을 지혜 베이스에 추가
            self.wisdom_base.append({
                "lesson": lesson,
                "source_id": res.get("id"),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "score": 1.0
            })
            # 지혜 베이스 크기 제한 (최근 100개)
            if len(self.wisdom_base) > 100:
                self.wisdom_base = self.wisdom_base[-100:]
        
        return {"status": "grown", "saved": res, "lesson_added": bool(lesson)}

    async def self_reflection(self) -> Dict[str, Any]:
        """자기 성찰 - 교훈 통합 및 패턴 분석"""
        merged = 0  # placeholder for future merge logic
        self.last_reflection = datetime.utcnow()
        
        # 상위 교훈 추출
        top_lessons = []
        if self.wisdom_base:
            # 점수 기준 정렬
            sorted_wisdom = sorted(self.wisdom_base, key=lambda x: x.get("score", 0), reverse=True)
            top_lessons = [w["lesson"] for w in sorted_wisdom[:5]]
        
        # 상위 패턴 추출
        top_patterns = dict(sorted(self.pattern_scores.items(), key=lambda x: -x[1])[:10])
        
        return {
            "merged": merged,
            "pattern_scores": self.pattern_scores,
            "top_patterns": top_patterns,
            "top_lessons": top_lessons,
            "wisdom_count": len(self.wisdom_base),
            "last_reflection": self.last_reflection.isoformat() + "Z",
        }
