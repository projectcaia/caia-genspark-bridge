
import json
from typing import List, Dict, Any
from datetime import datetime
from .qdrant_client import CaiaQdrantClient

class MemoryBackup:
    """메모리 백업 및 복원 유틸"""

    def export_memories(self, output_file: str | None = None, dim: int = 384) -> str:
        if not output_file:
            output_file = f"caia_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        client = CaiaQdrantClient(dim=dim)
        all_data = client.scroll_all()
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        print(f"✅ {len(all_data)}개 메모리 백업 완료: {output_file}")
        return output_file

    def import_memories(self, input_file: str, dim: int = 384) -> int:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"📥 {len(data)}개 메모리 복원 시작... (스키마 확인/업서트는 별도 구현 필요)")
        # TODO: 필요시 ERSP 재구성/임베딩 생성 후 upsert
        return len(data)
