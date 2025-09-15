import os
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class EmbedderInfo:
    backend: str   # "openai" | "sbert"
    model: str
    dim: Optional[int] = None

def _detect_dim_for_openai(model: str) -> int:
    name = (model or "").lower()
    if "large" in name:
        return 3072
    # default to small if unknown
    return 1536

class EmbeddingBackend:
    """
    백엔드별 임베딩 통합 클래스
    - backend = "openai": OPENAI_EMBED_MODEL만 사용
    - backend = "sbert":  SentenceTransformers 모델만 사용
    """
    def __init__(self, backend: str, model: str, dim_hint: int | None = None):
        self.backend = backend.lower()
        self.model = model
        self.dim = None

        if self.backend == "openai":
            # OpenAI는 항상 OPENAI_EMBED_MODEL을 사용
            self.model = os.getenv("OPENAI_EMBED_MODEL", self.model or "text-embedding-3-small")
            from openai import OpenAI
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.dim = dim_hint or _detect_dim_for_openai(self.model)

        elif self.backend == "sbert":
            # SBERT는 EMBED_MODEL_NAME을 사용
            from sentence_transformers import SentenceTransformer
            self.model_obj = SentenceTransformer(self.model or "sentence-transformers/all-MiniLM-L6-v2")
            # 더 정확한 차원: 모델 메타정보 사용 시도
            try:
                self.dim = int(self.model_obj.get_sentence_embedding_dimension())
            except Exception:
                test = self.model_obj.encode(["init"])[0]
                self.dim = len(test)

        else:
            raise ValueError(f"Unsupported backend: {backend}")

    def embed(self, text: str) -> List[float]:
        if self.backend == "sbert":
            vec = self.model_obj.encode([text])[0]
            try:
                # numpy array -> list(float32)
                return vec.astype("float32").tolist()
            except Exception:
                return [float(x) for x in vec]
        elif self.backend == "openai":
            resp = self.client.embeddings.create(model=self.model, input=text)
            return resp.data[0].embedding
        else:
            raise ValueError(f"Unsupported backend: {self.backend}")

def select_backend_auto(qdrant_vec_size: int | None, env_backend: str, env_sbert_model: str) -> EmbedderInfo:
    """
    백엔드 선택 규칙:
    1) env_backend 명시:
       - "openai" → OPENAI_EMBED_MODEL 사용
       - "sbert"  → env_sbert_model 사용
    2) auto:
       - Qdrant vec size가 1536/3072면 OpenAI + 해당 차원에 맞는 기본 모델
       - 아니면 SBERT + env_sbert_model
    """
    env_backend = (env_backend or "auto").lower()

    if env_backend == "openai":
        model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        return EmbedderInfo("openai", model, _detect_dim_for_openai(model))

    if env_backend == "sbert":
        model = env_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
        return EmbedderInfo("sbert", model, None)

    # auto 모드
    if qdrant_vec_size in (1536, 3072):
        # Qdrant 컬렉션 차원에 맞춰 OpenAI 모델 자동 선택
        default_model = "text-embedding-3-small" if qdrant_vec_size == 1536 else "text-embedding-3-large"
        model = os.getenv("OPENAI_EMBED_MODEL", default_model)
        return EmbedderInfo("openai", model, qdrant_vec_size)

    # 그 외에는 SBERT 기본
    model = env_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
    return EmbedderInfo("sbert", model, None)
