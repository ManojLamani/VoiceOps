"""Health check and runtime provider status."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import get_current_user
from app.db.database import engine
from app.services.llm_service import get_llm_provider
from app.services.rag_service import get_rag_service
from app.services.voice_service import get_stt_provider, get_tts_provider

router = APIRouter(tags=["System"])


async def _database_ok() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health")
async def health():
    db_ok = await _database_ok()
    body = {"status": "healthy" if db_ok else "degraded", "database": "up" if db_ok else "down",
            "rag_chunks": get_rag_service().chunk_count}
    return JSONResponse(status_code=200 if db_ok else 503, content=body)


@router.get("/system/status")
async def system_status(current_user: dict = Depends(get_current_user)):
    settings = get_settings()
    llm, stt, tts, rag = get_llm_provider(), get_stt_provider(), get_tts_provider(), get_rag_service()
    return {
        "llm": {"provider": llm.name if llm else "offline",
                "mode": "llm" if llm else "offline",
                "description": "LLM tool-calling agent" if llm else
                "Offline policy agent (semantic intent + validated tools + extractive RAG)"},
        "stt": {"provider": stt.name if stt else "browser", "server": stt is not None},
        "tts": {"provider": tts.name if tts else "browser", "server": tts is not None},
        "rag": {"chunks": rag.chunk_count, "documents": len(rag.documents), "index": rag.index.backend,
                "embedding_model": settings.embedding_model, "min_score": settings.rag_min_score},
        "database": {"dialect": engine.dialect.name, "up": await _database_ok()},
    }
