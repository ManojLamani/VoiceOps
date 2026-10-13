"""Analytics routes - computed from real database data."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.repositories import AnalyticsRepository
from app.core.security import get_current_user
from app.schemas.schemas import AnalyticsOverview

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview", response_model=AnalyticsOverview)
async def get_overview(current_user: dict = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Get analytics overview computed from real conversation data."""
    repo = AnalyticsRepository(db)
    overview = await repo.get_overview()
    return AnalyticsOverview(**overview)


@router.get("/conversations-over-time")
async def conversations_over_time(days: int = Query(30, ge=1, le=365),
                                    current_user: dict = Depends(get_current_user),
                                    db: AsyncSession = Depends(get_db)):
    """Get conversation counts over time."""
    repo = AnalyticsRepository(db)
    return await repo.get_conversations_over_time(days=days)


@router.get("/intents")
async def intent_distribution(current_user: dict = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    """Get intent distribution across conversations."""
    repo = AnalyticsRepository(db)
    return await repo.get_intent_distribution()


@router.get("/tool-usage")
async def tool_usage(current_user: dict = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """Get tool usage statistics."""
    repo = AnalyticsRepository(db)
    return await repo.get_tool_usage_stats()


@router.get("/latency")
async def response_latency(limit: int = Query(50, ge=1, le=500), current_user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """Recent end-to-end agent turn latencies."""
    return await AnalyticsRepository(db).get_response_latency(limit=limit)


@router.get("/escalation-reasons")
async def escalation_reasons(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await AnalyticsRepository(db).get_escalation_reasons()


@router.get("/sentiment")
async def sentiment(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await AnalyticsRepository(db).get_sentiment_distribution()
