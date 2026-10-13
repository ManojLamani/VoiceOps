"""RAG pipeline: clean -> chunk -> embed -> vector index -> semantic retrieval -> grounded context."""
import os
import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("rag_service")


@dataclass
class SourceDocument:
    doc_id: str
    title: str
    content: str
    source: Optional[str] = None


# ── Cleaning & chunking ──

def clean_markdown(text: str) -> str:
    """Normalize markdown into plain, embedding-friendly text."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)        # bold
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)  # italics
    text = re.sub(r"`([^`]+)`", r"\1", text)             # inline code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class TextChunker:
    """Heading-aware chunker.

    Splits a markdown document into its sections, then splits long sections into
    overlapping windows. Every chunk is prefixed with "<doc title> > <section>" so the
    chunk is self-describing both for embedding and for the LLM.
    """

    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 80):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_sections(self, text: str) -> List[Tuple[str, str]]:
        sections, heading, buf = [], "", []
        for line in text.split("\n"):
            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m:
                if buf and "\n".join(buf).strip():
                    sections.append((heading, "\n".join(buf).strip()))
                level, title = len(m.group(1)), m.group(2).strip()
                buf = []
                if level == 1:
                    heading = ""  # document title; the doc title is added separately
                else:
                    heading = title
            else:
                buf.append(line)
        if buf and "\n".join(buf).strip():
            sections.append((heading, "\n".join(buf).strip()))
        return sections

    def _window(self, body: str) -> List[str]:
        if len(body) <= self.chunk_size:
            return [body]
        units = [u.strip() for u in re.split(r"\n|(?<=[.!?])\s+", body) if u.strip()]
        windows, current = [], ""
        for unit in units:
            if current and len(current) + len(unit) + 1 > self.chunk_size:
                windows.append(current)
                tail = current[-self.chunk_overlap:]
                tail = tail[tail.find(" ") + 1:] if " " in tail else tail
                current = (tail + "\n" + unit).strip()
            else:
                current = (current + "\n" + unit).strip()
        if current:
            windows.append(current)
        return windows

    def chunk(self, title: str, text: str) -> List[Dict[str, str]]:
        chunks = []
        for heading, body in self.split_sections(clean_markdown(text)):
            prefix = f"{title} > {heading}" if heading else title
            for piece in self._window(body):
                chunks.append({"section": heading or title, "content": f"{prefix}\n{piece}"})
        return chunks


# ── Embeddings ──

class EmbeddingService:
    """Sentence-transformers embeddings (normalized, so dot product == cosine similarity)."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self.available = True

    def _load(self):
        if self._model is not None or not self.available:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device="cpu")
            logger.info("embedding_model_loaded", model=self.model_name)
        except Exception as e:  # pragma: no cover - depends on environment
            logger.error("embedding_model_unavailable", error=str(e))
            self.available = False

    def embed(self, texts: List[str]) -> np.ndarray:
        self._load()
        if not self.available:
            raise RuntimeError("Embedding model unavailable")
        vectors = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True,
                                     batch_size=32)
        return np.asarray(vectors, dtype=np.float32)


# ── Vector index ──

class VectorIndex:
    """In-memory vector index. Uses FAISS (IndexFlatIP) when importable, else exact numpy search.

    Both compute exact cosine similarity over normalized vectors; the corpus is small, so
    the index is rebuilt from the database documents at startup instead of persisted.
    """

    def __init__(self):
        self._matrix: Optional[np.ndarray] = None
        self._faiss_index = None
        self.metadata: List[Dict] = []
        try:
            import faiss  # noqa: F401
            self._faiss = faiss
        except Exception:
            self._faiss = None

    @property
    def backend(self) -> str:
        return "faiss" if self._faiss else "numpy"

    def build(self, vectors: np.ndarray, metadata: List[Dict]):
        self.metadata = metadata
        self._matrix = vectors
        if self._faiss is not None and len(vectors):
            self._faiss_index = self._faiss.IndexFlatIP(vectors.shape[1])
            self._faiss_index.add(vectors)

    def search(self, query_vector: np.ndarray, top_k: int) -> List[Tuple[int, float]]:
        if self._matrix is None or not len(self.metadata):
            return []
        k = min(top_k, len(self.metadata))
        if self._faiss_index is not None:
            scores, idx = self._faiss_index.search(query_vector.reshape(1, -1), k)
            return [(int(i), float(s)) for i, s in zip(idx[0], scores[0]) if i >= 0]
        scores = self._matrix @ query_vector
        top = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in top]


# ── RAG service ──

class RAGService:
    def __init__(self):
        settings = get_settings()
        self.settings = settings
        self.chunker = TextChunker(settings.chunk_size, settings.chunk_overlap)
        self.embedder = EmbeddingService(settings.embedding_model)
        self.index = VectorIndex()
        self.documents: Dict[str, SourceDocument] = {}

    @property
    def ready(self) -> bool:
        return bool(self.index.metadata)

    @property
    def chunk_count(self) -> int:
        return len(self.index.metadata)

    def build_index(self, documents: List[SourceDocument]) -> Dict[str, int]:
        """(Re)build the index from documents. Returns chunk count per document id."""
        metadata, counts = [], {}
        for doc in documents:
            chunks = self.chunker.chunk(doc.title, doc.content)
            counts[doc.doc_id] = len(chunks)
            for i, c in enumerate(chunks):
                metadata.append({
                    "chunk_id": f"{doc.source or doc.doc_id}#{i}",
                    "document_id": doc.doc_id,
                    "document_title": doc.title,
                    "section": c["section"],
                    "content": c["content"],
                })
        vectors = self.embedder.embed([m["content"] for m in metadata]) if metadata else np.zeros((0, 384))
        self.index.build(vectors, metadata)
        self.documents = {d.doc_id: d for d in documents}
        logger.info("rag_index_built", documents=len(documents), chunks=len(metadata), backend=self.index.backend)
        return counts

    def retrieve(self, query: str, top_k: int = None, min_score: float = None) -> List[Dict]:
        """Semantic retrieval. Only chunks above the relevance threshold are returned."""
        if not self.ready or not query.strip():
            return []
        top_k = top_k or self.settings.rag_top_k
        min_score = self.settings.rag_min_score if min_score is None else min_score
        qv = self.embedder.embed([query])[0]
        results = []
        for idx, score in self.index.search(qv, top_k):
            if score >= min_score:
                results.append({**self.index.metadata[idx], "score": round(score, 4)})
        logger.info("rag_retrieval", query=query[:80], results=len(results))
        return results

    @staticmethod
    def format_context(results: List[Dict]) -> str:
        return "\n\n".join(
            f"[Source {i + 1}: {r['document_title']} - {r['section']}]\n{r['content'].split(chr(10), 1)[-1]}"
            for i, r in enumerate(results)
        )

    def best_sentences(self, query: str, results: List[Dict], max_sentences: int = 3) -> List[str]:
        """Extractive grounding: the sentences/bullets from retrieved chunks most similar to the query."""
        candidates = []
        for r in results[:3]:
            body = r["content"].split("\n", 1)[-1]
            for line in re.split(r"\n|(?<=[.!?])\s+", body):
                line = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip()
                if len(line) > 15 and not line.endswith(":"):
                    candidates.append(line)
        if not candidates:
            return []
        vectors = self.embedder.embed([query] + candidates)
        scores = vectors[1:] @ vectors[0]
        order = [i for i in np.argsort(-scores)[:max_sentences] if scores[i] > 0.25]
        return [candidates[i] for i in sorted(order)]


def load_knowledge_files(kb_dir: str) -> List[SourceDocument]:
    """Read the markdown knowledge-base documents from disk."""
    docs = []
    if not os.path.isdir(kb_dir):
        logger.warning("kb_dir_not_found", path=kb_dir)
        return docs
    for filename in sorted(os.listdir(kb_dir)):
        if filename.endswith(".md"):
            with open(os.path.join(kb_dir, filename), encoding="utf-8") as f:
                content = f.read()
            first = content.strip().split("\n", 1)[0]
            title = re.sub(r"^#\s*", "", first).split(" - ")[0].strip() if first.startswith("#") \
                else filename[:-3].replace("_", " ").title()
            docs.append(SourceDocument(doc_id=filename[:-3], title=title, content=content, source=filename))
    return docs


_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
