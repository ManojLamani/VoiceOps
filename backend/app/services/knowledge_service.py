"""Build the RAG index from the knowledge documents stored in the database."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeDocument
from app.db.seed import sync_knowledge_documents
from app.services.rag_service import get_rag_service, SourceDocument


async def rebuild_knowledge_index(session: AsyncSession, sync_files: bool = True) -> dict:
    """Sync new markdown files into the DB, then (re)index every active document."""
    added = await sync_knowledge_documents(session) if sync_files else 0
    docs = list((await session.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.is_active.is_(True)))).scalars().all())
    rag = get_rag_service()
    counts = rag.build_index([SourceDocument(doc_id=str(d.id), title=d.title, content=d.content, source=d.source)
                              for d in docs])
    now = datetime.utcnow()
    for d in docs:
        d.chunk_count = counts.get(str(d.id), 0)
        d.last_indexed_at = now
    await session.flush()
    return {"documents": len(docs), "chunks": rag.chunk_count, "new_documents": added,
            "backend": rag.index.backend}
