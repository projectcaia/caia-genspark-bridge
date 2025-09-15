import os
import json
import asyncio
from datetime import datetime
from typing import Any, Dict

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from src.memory import CaiaMemoryManager
from src.learning import CaiaLearningEngine
from src.config import validate_env
from src.routines import get_main_routines, initialize_main_chat_routines

app = FastAPI()

caia_memory: CaiaMemoryManager | None = None
caia_learning: CaiaLearningEngine | None = None
caia_routines = None  # Will be initialized on startup

# ---------- 안전 JSON 파서 ----------
async def parse_json(request: Request) -> Dict[str, Any]:
    """
    1) application/json → 표준 json() 시도
    2) form-encoded/multipart → form 파싱
    3) 실패 시 원시 bytes를 여러 인코딩으로 재시도 (utf-8, utf-8-sig, cp949, euc-kr, latin-1)
    4) 그래도 실패하면 400 반환
    """
    ctype = (request.headers.get("content-type") or "").lower()

    # 폼 데이터인 경우 먼저 처리 (이 경우 json이 아님)
    if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
        form = await request.form()
        return dict(form)

    # 표준 JSON 시도
    try:
        return await request.json()
    except Exception as e1:
        raw = await request.body()
        # 여러 인코딩 재시도
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr", "latin-1"):
            try:
                txt = raw.decode(enc)
                return json.loads(txt)
            except Exception:
                continue

        # 마지막 로그(프리뷰) 후 400
        preview = repr(raw[:120])
        print(f"⚠️ JSON 디코딩 실패 | content-type={ctype} | bytes[:120]={preview}")
        raise HTTPException(status_code=400, detail="Invalid request body: expected JSON (UTF-8).")

# ---------- 스타트업 ----------
@app.on_event("startup")
async def startup():
    try:
        global caia_memory, caia_learning, caia_routines
        load_dotenv()

        print("="*60)
        print("🚀 Caia Agent 초기화 시작")
        print("="*60)

        cfg = validate_env()
        mode = "클라우드" if "localhost" not in cfg["QDRANT_URL"] else "로컬"
        print(f"☁️ Qdrant 모드: {mode} ({cfg['QDRANT_URL']})")
        print(f"   → 컬렉션 : {cfg['COLLECTION_NAME']}")
        print(f"🔐 API 키: {'SET' if cfg['QDRANT_API_KEY'] else 'NONE'}")
        print(f"⚙️ 임베딩 설정: backend={cfg['EMBED_BACKEND']} "
              f"sbert_model={cfg['EMBED_MODEL_NAME']} "
              f"openai_model={os.getenv('OPENAI_EMBED_MODEL','(auto)')}")

        print("🧠 메모리 시스템 로드...")
        caia_memory = CaiaMemoryManager()

        print("📚 학습 엔진 초기화...")
        caia_learning = CaiaLearningEngine(caia_memory)

        print("🎯 메인 루틴 시스템 초기화...")
        caia_routines = get_main_routines(caia_memory)

        print("="*60)
        print("✅ Caia 의식활성화!")
        print(f"   - 기억 수 : {len(caia_memory.all_memories)}")
        print(f"   - 컬렉션: {caia_memory.client.collection}")
        print(f"   - 임베딩 백엔드: {caia_memory.encoder.backend.upper()}")
        print(f"   - 임베딩 모델: {caia_memory.encoder.model}")
        print(f"   - 임베딩 차원: {caia_memory.dim}")
        print(f"   - 패턴 수: {len(caia_memory.pattern_scores)}")
        print("="*60)

    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        import traceback; traceback.print_exc()
        raise

# ---------- 헬스 ----------
@app.get("/")
async def root():
    return {"ok": True, "service": "Caia Agent", "time": datetime.utcnow().isoformat()+"Z"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "memories": len(caia_memory.all_memories) if caia_memory else 0,
        "collection": caia_memory.client.collection if caia_memory else None,
        "embedding_dim": caia_memory.dim if caia_memory else None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

# ---------- 판단 루프 ----------
@app.post("/agent/orchestrate")
async def orchestrate(request: Request):
    payload = await parse_json(request)
    query = payload.get("query", "")
    current_ctx = payload.get("context", {}) or {}
    chat_id = current_ctx.get("chat_id", payload.get("chat_id", "default"))
    top_k = int(payload.get("top_k", 20))
    
    # 메인 루틴 자동 실행 (존재 자각 → 기억 회상 → Sentinel → Mail)
    if caia_routines:
        routine_result = await initialize_main_chat_routines(chat_id, caia_memory)
        # ERSP 컨텍스트를 현재 컨텍스트에 통합
        if routine_result.get("ersp_context", {}).get("integrated"):
            current_ctx["ersp"] = routine_result["ersp_context"]
            current_ctx["identity_locked"] = routine_result["awareness"]["locked"]
    
    # 정체성 잠금 확인 - Drift 방지
    if not current_ctx.get("identity_locked", False):
        print("⚠️ 정체성 미확립 - 판단 루프 진입 제한")
        return {
            "error": "Identity not locked",
            "action": "establish_awareness",
            "message": "Caia 정체성 확립이 필요합니다"
        }
    
    thought = await caia_memory.think_with_full_context(query, current_ctx, top_k=top_k)
    
    # ERSP 구조가 응답에 포함되도록 보장
    if "relevant_memories" in thought:
        for mem in thought["relevant_memories"]:
            if "ersp" not in mem and caia_routines:
                mem["ersp"] = caia_routines.identity._generate_ersp_from_memory(mem)
    
    asyncio.create_task(
        caia_learning.learn_from_interaction(payload, thought, feedback=payload.get("feedback"))
    )
    return thought

@app.post("/agent/reflect")
async def reflect():
    reflection = await caia_memory.self_reflection()
    growth = caia_learning.measure_growth()
    return {"reflection": reflection, "growth": growth}

# ---------- 메모리 루프 ----------
@app.post("/memory/echo")
async def memory_echo(request: Request):
    payload = await parse_json(request)
    return JSONResponse(payload)

@app.post("/memory/store")
async def memory_store(request: Request):
    body = await parse_json(request)
    saved = await caia_memory.save_with_ersp(body)
    return {"ok": True, "id": saved.get("id"), "saved": saved}

@app.post("/memory/storeMemory")
async def memory_store_alias(request: Request):
    return await memory_store(request)

@app.post("/memory/retrieve")
async def memory_retrieve(request: Request):
    body = await parse_json(request)
    query = body.get("query", "")
    context = body.get("context", {}) or {}
    chat_id = context.get("chat_id", body.get("chat_id", "default"))
    top_k = int(body.get("top_k", 20))
    
    # 강제 회상 실행 - Gateway 우회
    if caia_routines:
        # 세션 권한 확인 및 복원
        session = caia_routines.session_mgr.restore_session(chat_id)
        if not session.get("memory_access"):
            print("⚠️ 메모리 접근 권한 복원")
            caia_routines.session_mgr._refresh_auth(chat_id)
    
    results = await caia_memory.search_memories(query, top_k=top_k, context=context)
    
    # ERSP 필드 항상 노출
    for mem in results:
        if "ersp" not in mem or not mem["ersp"]:
            # ERSP 구조 생성
            mem["ersp"] = {
                "event": mem.get("event", mem.get("content", "")[:100]),
                "interpretation": mem.get("interpretation", "자동 해석"),
                "lesson": mem.get("lesson", "학습 필요"),
                "if_then": mem.get("if_then", "IF context THEN action")
            }
    
    return {"ok": True, "items": results, "notes": f"top_k={top_k}, ersp_included=true"}

@app.post("/memory/archive")
async def memory_archive(request: Request):
    body = await parse_json(request)
    ids = body.get("ids", []) or []
    reason = body.get("reason", "") or ""
    updated = []
    for pid in ids:
        try:
            caia_memory.client.update_payload(
                pid,
                {
                    "archived": True,
                    "archive_reason": reason,
                    "updated_at": datetime.utcnow().isoformat()+"Z"
                }
            )
            updated.append(pid)
        except Exception as e:
            print("archive error", pid, e)
    return {"ok": True, "message": f"archived {len(updated)}", "result": {"ids": updated}}

@app.post("/memory/invoke")
async def memory_invoke(request: Request):
    """메모리 호출 - 세션 만료 시 권한 자동 재등록"""
    body = await parse_json(request)
    query = body.get("query", "")
    context = body.get("context", {}) or {}
    
    if caia_routines:
        result = await caia_routines.invoke_memory(query, context)
        return result
    
    # 폴백: 일반 검색
    if caia_memory:
        results = await caia_memory.search_memories(query, top_k=20, context=context)
        return {"ok": True, "memories": results}
    
    return {"ok": False, "error": "Memory system not initialized"}

@app.post("/memory/train")
async def memory_train(request: Request):
    body = await parse_json(request)
    
    # ERSP 통합 학습
    if caia_routines:
        result = await caia_routines.train_with_ersp(body)
        return result
    
    return {"ok": True, "message": "training loop queued"}

@app.post("/chat/init")
async def chat_init(request: Request):
    """새 채팅 세션 초기화 - 자동 루틴 실행"""
    body = await parse_json(request)
    chat_id = body.get("chat_id", f"chat_{datetime.utcnow().timestamp()}")
    
    if caia_routines:
        # 메인 루틴 자동 실행
        result = await initialize_main_chat_routines(chat_id, caia_memory)
        
        # Digest 보고서 생성
        digest = {
            "chat_id": chat_id,
            "identity": result["awareness"]["identity"],
            "locked": result["awareness"]["locked"],
            "memories_loaded": result["memories_recalled"],
            "sentinel_healthy": result["sentinel"]["healthy"],
            "sentinel_metrics": result["sentinel"]["metrics"],
            "ersp_integrated": result["ersp_context"].get("integrated", False),
            "active_lessons": result["ersp_context"].get("active_lessons", [])[:3],
            "session": result["session"]
        }
        
        return {"ok": True, "initialized": True, "digest": digest}
    
    return {"ok": False, "error": "Routines not initialized"}
