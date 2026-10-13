"""Knowledge base management routes."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, require_admin
from app.db.database import get_db
from app.db.repositories import KnowledgeRepository
from app.schemas.schemas import KnowledgeDocumentCreate, KnowledgeDocumentResponse
from app.services.knowledge_service import rebuild_knowledge_index
from app.services.rag_service import get_rag_service

router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])


@router.get("", response_model=list[KnowledgeDocumentResponse])
async def list_documents(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
                         current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    docs = await KnowledgeRepository(db).get_all_documents(skip=skip, limit=limit)
    return [KnowledgeDocumentResponse.model_validate(d) for d in docs]


@router.get("/search")
async def search_knowledge(query: str = Query(..., min_length=1, max_length=500),
                           top_k: int = Query(5, ge=1, le=20), current_user: dict = Depends(get_current_user)):
    """Semantic search over the indexed knowledge base (the same retrieval the agent uses)."""
    rag = get_rag_service()
    results = rag.retrieve(query, top_k=top_k, min_score=0.0)
    threshold = rag.settings.rag_min_score
    return {"query": query, "threshold": threshold, "count": len(results),
            "results": [{**r, "relevant": r["score"] >= threshold} for r in results]}


@router.post("/ingest")
async def ingest_knowledge_base(current_user: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Import new markdown files from data/knowledge_base and rebuild the vector index."""
    return {"status": "success", **await rebuild_knowledge_index(db)}


@router.post("", response_model=KnowledgeDocumentResponse, status_code=201)
async def create_document(data: KnowledgeDocumentCreate, current_user: dict = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    repo = KnowledgeRepository(db)
    doc = await repo.create_document(title=data.title, category=data.category, content=data.content,
                                     source=data.source)
    await rebuild_knowledge_index(db, sync_files=False)
    return KnowledgeDocumentResponse.model_validate(doc)


@router.get("/{doc_id}", response_model=KnowledgeDocumentResponse)
async def get_document(doc_id: UUID, current_user: dict = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    doc = await KnowledgeRepository(db).get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return KnowledgeDocumentResponse.model_validate(doc)
