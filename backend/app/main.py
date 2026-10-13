"""VoiceOps - AI-Powered Customer Support Voice Agent Platform."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError, OperationalError, InterfaceError, IntegrityError

from app.core.config import get_settings
from app.core.logging import setup_logging, get_logger
from app.db.database import init_db, close_db, async_session_factory
from app.services.agent_service import ConversationNotFound, ConversationClosed
from app.api.routes import (
    auth, conversations, voice, customers, orders, tickets, knowledge, analytics, escalations, system,
)

logger = get_logger("main")


async def _warmup_models():
    """Load heavy models in the background so the first voice turn is fast."""
    from app.services.nlu import get_intent_classifier
    from app.services.llm_service import warmup
    from app.services.voice_service import get_stt_provider, get_tts_provider
    try:
        await asyncio.to_thread(get_intent_classifier().classify, "warm up")
        stt = get_stt_provider()
        get_tts_provider()
        if hasattr(stt, "warmup"):
            await stt.warmup()
        await warmup()
        logger.info("models_warm")
    except Exception as e:
        logger.warning("warmup_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    if settings.models_offline:
        # Must be set before transformers/sentence-transformers load: startup never touches the network.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    await init_db()
    if settings.auto_seed:
        from app.db.seed import seed_database
        if await seed_database():
            logger.info("database_auto_seeded")

    from app.services.knowledge_service import rebuild_knowledge_index
    try:
        async with async_session_factory() as session:
            info = await rebuild_knowledge_index(session)
            await session.commit()
        logger.info("rag_ready", **info)
    except Exception as e:
        logger.error("rag_init_failed", error=str(e))

    from app.services.maintenance import end_stale_conversations, sweep_forever
    try:
        await end_stale_conversations()  # conversations left open by a crash or a vanished client
    except Exception as e:
        logger.warning("stale_sweep_failed", error=str(e))

    background = [asyncio.create_task(_warmup_models()), asyncio.create_task(sweep_forever())]
    yield
    for task in background:
        task.cancel()
    await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VoiceOps API", description="AI-Powered Customer Support Voice Agent Platform",
                  version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    @app.exception_handler(ConversationNotFound)
    async def _not_found(request: Request, exc: ConversationNotFound):
        return JSONResponse(status_code=404, content={"detail": "Conversation not found"})

    @app.exception_handler(ConversationClosed)
    async def _closed(request: Request, exc: ConversationClosed):
        return JSONResponse(status_code=409, content={"detail": "Conversation has already ended"})

    @app.exception_handler(IntegrityError)
    async def _conflict(request: Request, exc: IntegrityError):
        logger.warning("integrity_error", path=request.url.path, error=str(exc.orig)[:200])
        return JSONResponse(status_code=409, content={"detail": "The request conflicts with existing data"})

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(request: Request, exc: SQLAlchemyError):
        logger.error("database_error", path=request.url.path, error=str(exc))
        unavailable = isinstance(exc, (OperationalError, InterfaceError, ConnectionError, OSError))
        return JSONResponse(status_code=503 if unavailable else 500,
                            content={"detail": "Database unavailable" if unavailable else "Database error"})

    for module in (auth, conversations, voice, customers, orders, tickets, knowledge, analytics, escalations,
                   system):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()
