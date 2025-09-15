import os
from typing import Dict, Any

def validate_env() -> Dict[str, Any]:
    return {
        "QDRANT_URL": os.getenv("QDRANT_URL", "http://localhost:6333"),
        "QDRANT_API_KEY": os.getenv("QDRANT_API_KEY", ""),
        "COLLECTION_NAME": os.getenv("COLLECTION_NAME", "caia_memories"),
        "EMBED_BACKEND": os.getenv("EMBED_BACKEND", "auto"),  # auto | sbert | openai
        "EMBED_MODEL_NAME": os.getenv("EMBED_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"),
        "FALLBACK_COLLECTIONS": os.getenv("FALLBACK_COLLECTIONS", "caia-memory,caia_memories"),
        "LOCAL_EMBED_CACHE": os.getenv("LOCAL_EMBED_CACHE", "0"),
    }
