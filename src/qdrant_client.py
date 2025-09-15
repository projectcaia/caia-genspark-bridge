# src/qdrant_client.py
import os
import uuid
from typing import List, Dict, Any, Optional, Tuple, Union

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


class CaiaQdrantClient:
    def __init__(self, dim: Optional[int] = None, collection: Optional[str] = None):
        self.url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = os.getenv("QDRANT_API_KEY") or None
        self.client = QdrantClient(url=self.url, api_key=self.api_key)

        # 배포 환경과 맞춤: 기본 'caia-memory'
        self.collection = collection or os.getenv("COLLECTION_NAME", "caia-memory")

        # dim은 미지정일 수 있음 → ensure 과정에서 확정
        self.dim: Optional[int] = dim

        # 누락되어 에러났던 보증 루틴
        self._ensure_collection_or_switch()

    # -----------------------
    # 내부 유틸
    # -----------------------
    def _env_dim(self) -> Optional[int]:
        """여러 환경변수 후보에서 벡터 차원 힌트를 읽어온다."""
        for key in (
            "QDRANT_VECTOR_SIZE",
            "QDRANT_VECTOR_DIM",
            "EMBED_DIM",
            "QDRANT_벡터_크기",
            "임베드_차원",
        ):
            val = os.getenv(key)
            if val:
                try:
                    return int(val)
                except Exception:
                    pass
        return None

    def list_collections(self) -> List[str]:
        try:
            resp = self.client.get_collections()
            return [c.name for c in getattr(resp, "collections", [])]
        except Exception:
            return []

    def count_points(self, name: Optional[str] = None) -> int:
        name = name or self.collection
        try:
            res = self.client.count(collection_name=name, exact=True)
            return int(getattr(res, "count", 0))
        except Exception:
            return 0

    # -----------------------
    # 차원 감지
    # -----------------------
    def get_vector_size(self, name: Optional[str] = None) -> Optional[int]:
        """
        컬렉션의 벡터 차원(size)을 최대한 호환성 있게 추출한다.
        1) config 경로들 시도 (버전차 호환)
        2) 실패 시 실제 포인트 1개를 vectors 포함 스크롤 → 길이로 추론
        """
        name = name or self.collection

        # 1) config에서 감지
        try:
            info = self.client.get_collection(name)

            vectors = None
            # 최신 경로: info.config.params.vectors
            cfg = getattr(info, "config", None)
            if cfg is not None:
                params = getattr(cfg, "params", None)
                if params is not None:
                    vectors = getattr(params, "vectors", None)
            # 구버전/대체: info.vectors_config
            if vectors is None:
                vectors = getattr(info, "vectors_config", None)

            # 단일 벡터
            size = getattr(vectors, "size", None)
            if isinstance(size, int):
                return size

            # 네임드 벡터(dict-like 또는 모델)
            if isinstance(vectors, dict):
                for v in vectors.values():
                    vsz = getattr(v, "size", None)
                    if isinstance(vsz, int):
                        return vsz
                    if isinstance(v, dict) and isinstance(v.get("size"), int):
                        return int(v["size"])

            to_dict = getattr(vectors, "to_dict", None)
            if callable(to_dict):
                vd = to_dict()
                if isinstance(vd, dict):
                    if isinstance(vd.get("size"), int):
                        return int(vd["size"])
                    for v in vd.values():
                        if isinstance(v, dict) and isinstance(v.get("size"), int):
                            return int(v["size"])
        except Exception:
            # 조용히 폴백
            pass

        # 2) 폴백: 실제 포인트 1개에서 벡터 길이로 추론
        try:
            res = self.client.scroll(
                collection_name=name,
                limit=1,
                with_payload=False,
                with_vectors=True,
            )
            # 버전에 따라 tuple일 수 있음: (points, next_offset)
            points = getattr(res, "points", None)
            if points is None and isinstance(res, tuple):
                points, _ = res

            if points:
                v = getattr(points[0], "vector", None)
                # 네임드 벡터
                if isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, list):
                            return len(vv)
                # 단일 벡터
                if isinstance(v, list):
                    return len(v)
        except Exception:
            pass

        return None

    # -----------------------
    # 보증(ensure)
    # -----------------------
    def _ensure_collection_or_switch(self):
        cols = set(self.list_collections())
        if self.collection not in cols:
            # 새 컬렉션 생성
            size = self.dim or self._env_dim() or 1536  # OpenAI text-embedding-3-small 기본
            try:
                print(f"📦 Creating new collection: {self.collection} (dim={size})")
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=size, distance=Distance.COSINE),
                )
                print(f"✅ Collection created: {self.collection}")
            except Exception as e:
                print(f"⚠️ create_collection 오류: {e}")
            self.dim = size
        else:
            # 기존 컬렉션 사용 → 차원 감지
            detected = self.get_vector_size(self.collection)
            if detected:
                self.dim = self.dim or detected
                print(
                    f"✅ Using existing collection: {self.collection} "
                    f"(vectors: {self.count_points(self.collection)}, dim={detected})"
                )
            else:
                print(
                    f"✅ Using existing collection: {self.collection} "
                    f"(vectors: {self.count_points(self.collection)})"
                )

    # -----------------------
    # 데이터 I/O
    # -----------------------
    def scroll_all(self, batch: int = 100) -> List[Dict[str, Any]]:
        """
        전체 포인트 페이징 스크롤. payload만 모아 반환.
        """
        out: List[Dict[str, Any]] = []
        next_offset = None
        while True:
            try:
                res = self.client.scroll(
                    collection_name=self.collection,
                    limit=batch,
                    with_payload=True,
                    with_vectors=False,
                    offset=next_offset,
                )
                # 버전에 따라 tuple일 수 있음
                points = getattr(res, "points", None)
                next_offset = getattr(res, "next_page_offset", None)
                if points is None and isinstance(res, tuple):
                    points, next_offset = res

                if not points:
                    break

                for p in points:
                    payload = dict(getattr(p, "payload", {}) or {})
                    pid = getattr(p, "id", None)
                    if pid is not None:
                        payload.setdefault("id", str(pid))
                        payload.setdefault("_point_id", str(pid))
                    out.append(payload)

                if not next_offset:
                    break
            except Exception as e:
                print(f"⚠️ scroll_all 오류: {e}")
                break
        return out

    def search(self, query_vector: List[float] | Any, top_k: int = 20) -> List[Dict[str, Any]]:
        """
        벡터 검색: payload + _score를 dict로 반환
        """
        try:
            hits = self.client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
            out: List[Dict[str, Any]] = []
            for h in hits or []:
                item = dict(getattr(h, "payload", {}) or {})
                score = getattr(h, "score", None)
                pid = getattr(h, "id", None)
                if pid is not None:
                    item.setdefault("id", str(pid))
                    item.setdefault("_point_id", str(pid))
                if score is not None:
                    item["_score"] = float(score)
                out.append(item)
            return out
        except Exception as e:
            print(f"⚠️ search 오류: {e}")
            return []

    def upsert_memory(self, memory: Dict[str, Any], embedding: List[float] | Any) -> str:
        """
        단일 포인트 upsert. id는 UUID 문자열.
        """
        pid = memory.get("id") or str(uuid.uuid4())
        payload = dict(memory)
        try:
            self.client.upsert(
                collection_name=self.collection,
                points=[
                    PointStruct(
                        id=pid,
                        vector=embedding,
                        payload=payload,
                    )
                ],
            )
            return pid
        except Exception as e:
            print(f"⚠️ upsert_memory 오류: {e}")
            # 실패 시에도 id 리턴 (호출부에서 처리)
            return pid
