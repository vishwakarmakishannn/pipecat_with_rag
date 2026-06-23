import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import delete, func, literal_column
from sqlalchemy.future import select

from database import AsyncSessionLocal
from memory import embed_text
from models import RagChunk, RagFile
from rag_config import (
    RAG_CONTEXT_CHUNK_CHARS,
    RAG_MAX_CONTEXT_CHARS,
    RAG_MIN_CONTENT_CHARS,
    RAG_MIN_STRONG_MATCHES,
    RAG_MIN_TEXT_RANK,
    RAG_MIN_VECTOR_SIMILARITY,
    RAG_RETRIEVAL_TOP_K,
    RAG_RRF_K,
    RAG_SMART_ROUTER,
    RAG_TEXT_CANDIDATES,
    RAG_TEXT_MATCH_MIN_RANK,
    RAG_UPLOAD_DIR,
    RAG_VECTOR_CANDIDATES,
)


RAG_QUERY_PATTERNS = [
    r"\b(pdf|document|doc|file|uploaded|upload|paper|report)\b",
    r"\b(in|from|inside|according to)\s+(my\s+)?(pdf|document|file|upload|paper|report)\b",
    r"\bwhat does (it|the file|the document|the pdf) say\b",
    r"\bsummarize\s+(my\s+)?(pdf|document|file|paper|report)\b",
]


@dataclass
class ParsedChunk:
    content: str
    embedding_text: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: str | None = None


@dataclass
class RetrievedRagChunk:
    id: int
    file_id: int
    filename: str
    content: str
    page_start: int | None
    page_end: int | None
    heading_path: str | None
    score: float
    vector_similarity: float | None = None
    text_rank: float | None = None
    source_types: tuple[str, ...] = ()


def is_rag_query(query: str) -> bool:
    normalized = (query or "").lower()
    return any(re.search(pattern, normalized) for pattern in RAG_QUERY_PATTERNS)


def rag_storage_path(user_id: int, file_id: int) -> Path:
    return Path(RAG_UPLOAD_DIR) / str(user_id) / f"{file_id}.pdf"


def _safe_filename(filename: str) -> str:
    cleaned = os.path.basename(filename or "document.pdf").strip()
    return cleaned or "document.pdf"


def normalize_pdf_filename(filename: str) -> str:
    cleaned = _safe_filename(filename)
    return cleaned if cleaned.lower().endswith(".pdf") else f"{cleaned}.pdf"


def _collect_page_numbers(chunk: Any) -> list[int]:
    pages: list[int] = []
    meta = getattr(chunk, "meta", None)
    doc_items = getattr(meta, "doc_items", None) or []
    for item in doc_items:
        prov_items = getattr(item, "prov", None) or getattr(item, "provenance", None) or []
        for prov in prov_items:
            page_no = getattr(prov, "page_no", None)
            if isinstance(page_no, int):
                pages.append(page_no)
    return sorted(set(pages))


def _extract_heading_path(chunk: Any) -> str | None:
    meta = getattr(chunk, "meta", None)
    headings = getattr(meta, "headings", None) or []
    headings = [str(heading).strip() for heading in headings if str(heading).strip()]
    return " > ".join(headings) if headings else None


def _parse_pdf_to_chunks(path: str) -> list[ParsedChunk]:
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter

    converted = DocumentConverter().convert(source=path)
    document = converted.document
    chunker = HybridChunker()
    parsed_chunks: list[ParsedChunk] = []

    for chunk in chunker.chunk(dl_doc=document):
        content = (getattr(chunk, "text", "") or "").strip()
        if len(content) < RAG_MIN_CONTENT_CHARS:
            continue

        try:
            embedding_text = (chunker.contextualize(chunk=chunk) or content).strip()
        except TypeError:
            embedding_text = (chunker.contextualize(chunk) or content).strip()
        except Exception:
            embedding_text = content

        pages = _collect_page_numbers(chunk)
        parsed_chunks.append(
            ParsedChunk(
                content=content,
                embedding_text=embedding_text,
                page_start=pages[0] if pages else None,
                page_end=pages[-1] if pages else None,
                heading_path=_extract_heading_path(chunk),
            )
        )

    return parsed_chunks


async def process_rag_file(file_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(RagFile).where(RagFile.id == file_id))
        rag_file = result.scalars().first()
        if not rag_file:
            return

        rag_file.status = "processing"
        rag_file.error = None
        rag_file.updated_at = datetime.utcnow()
        await db.commit()

        try:
            parsed_chunks = await asyncio.to_thread(_parse_pdf_to_chunks, rag_file.storage_path)
            if not parsed_chunks:
                raise ValueError("No usable text chunks found in PDF")

            await db.execute(delete(RagChunk).where(RagChunk.file_id == rag_file.id))
            for index, parsed in enumerate(parsed_chunks):
                embedding = await embed_text(parsed.embedding_text)
                db.add(
                    RagChunk(
                        user_id=rag_file.user_id,
                        file_id=rag_file.id,
                        chunk_index=index,
                        page_start=parsed.page_start,
                        page_end=parsed.page_end,
                        heading_path=parsed.heading_path,
                        content=parsed.content,
                        embedding=embedding,
                        search_vector=func.to_tsvector(literal_column("'english'"), parsed.content),
                    )
                )

            rag_file.status = "ready"
            rag_file.error = None
            rag_file.updated_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Processed RAG file {rag_file.id} with {len(parsed_chunks)} chunks")
        except Exception as exc:
            logger.warning(f"RAG file processing failed for file={file_id}: {exc}")
            rag_file.status = "failed"
            rag_file.error = str(exc)[:2000]
            rag_file.updated_at = datetime.utcnow()
            await db.commit()


async def delete_rag_file_record(rag_file: RagFile, db) -> None:
    storage_path = rag_file.storage_path
    await db.delete(rag_file)
    await db.commit()
    if storage_path:
        try:
            Path(storage_path).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"Could not delete RAG file from disk: {exc}")


def _rrf(rank: int) -> float:
    return 1.0 / (RAG_RRF_K + rank)


def _vector_similarity(distance: Any) -> float | None:
    if distance is None:
        return None
    try:
        return 1.0 - float(distance)
    except (TypeError, ValueError):
        return None


def _is_strong_rag_match(chunk: RetrievedRagChunk) -> bool:
    if chunk.vector_similarity is not None and chunk.vector_similarity >= RAG_MIN_VECTOR_SIMILARITY:
        return True
    if chunk.text_rank is not None and chunk.text_rank >= RAG_MIN_TEXT_RANK:
        return True
    return False


def should_inject_rag_context(
    chunks: list[RetrievedRagChunk],
    query: str = "",
    force: bool = False,
) -> bool:
    if force:
        return bool(chunks)
    if not chunks:
        return False
    if RAG_SMART_ROUTER == "off":
        return is_rag_query(query)
    if is_rag_query(query):
        return True

    strong_matches = sum(1 for chunk in chunks if _is_strong_rag_match(chunk))
    return strong_matches >= RAG_MIN_STRONG_MATCHES


def _rag_stats(chunks: list[RetrievedRagChunk]) -> tuple[int, float | None, float | None]:
    similarities = [chunk.vector_similarity for chunk in chunks if chunk.vector_similarity is not None]
    text_ranks = [chunk.text_rank for chunk in chunks if chunk.text_rank is not None]
    return (
        len(chunks),
        max(similarities) if similarities else None,
        max(text_ranks) if text_ranks else None,
    )


async def retrieve_rag_chunks(
    user_id: int,
    query: str,
    top_k: int = RAG_RETRIEVAL_TOP_K,
    force: bool = False,
) -> list[RetrievedRagChunk]:
    merged: dict[int, dict[str, Any]] = {}

    async with AsyncSessionLocal() as db:
        embedding = await embed_text(query)
        if embedding:
            distance = RagChunk.embedding.cosine_distance(embedding).label("distance")
            vector_result = await db.execute(
                select(RagChunk, RagFile.filename, distance)
                .join(RagFile, RagFile.id == RagChunk.file_id)
                .where(
                    RagChunk.user_id == user_id,
                    RagFile.user_id == user_id,
                    RagFile.status == "ready",
                    RagChunk.embedding.is_not(None),
                )
                .order_by(distance.asc())
                .limit(RAG_VECTOR_CANDIDATES)
            )
            for rank, (chunk, filename, _distance) in enumerate(vector_result.all(), start=1):
                similarity = _vector_similarity(_distance)
                item = merged.setdefault(
                    chunk.id,
                    {
                        "chunk": chunk,
                        "filename": filename,
                        "score": 0.0,
                        "vector_similarity": None,
                        "text_rank": None,
                        "source_types": set(),
                    },
                )
                item["score"] += _rrf(rank)
                item["vector_similarity"] = max(
                    value for value in [item["vector_similarity"], similarity] if value is not None
                ) if item["vector_similarity"] is not None or similarity is not None else None
                item["source_types"].add("vector")

        ts_query = func.websearch_to_tsquery(literal_column("'english'"), query)
        text_rank = func.ts_rank_cd(RagChunk.search_vector, ts_query).label("text_rank")
        text_result = await db.execute(
            select(RagChunk, RagFile.filename, text_rank)
            .join(RagFile, RagFile.id == RagChunk.file_id)
            .where(
                RagChunk.user_id == user_id,
                RagFile.user_id == user_id,
                RagFile.status == "ready",
                RagChunk.search_vector.op("@@")(ts_query),
                text_rank > RAG_TEXT_MATCH_MIN_RANK,
            )
            .order_by(text_rank.desc())
            .limit(RAG_TEXT_CANDIDATES)
        )
        for rank, (chunk, filename, _rank_value) in enumerate(text_result.all(), start=1):
            try:
                rank_value = float(_rank_value)
            except (TypeError, ValueError):
                rank_value = None
            item = merged.setdefault(
                chunk.id,
                {
                    "chunk": chunk,
                    "filename": filename,
                    "score": 0.0,
                    "vector_similarity": None,
                    "text_rank": None,
                    "source_types": set(),
                },
            )
            item["score"] += _rrf(rank)
            item["text_rank"] = max(
                value for value in [item["text_rank"], rank_value] if value is not None
            ) if item["text_rank"] is not None or rank_value is not None else None
            item["source_types"].add("text")

    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]
    chunks = [
        RetrievedRagChunk(
            id=item["chunk"].id,
            file_id=item["chunk"].file_id,
            filename=item["filename"],
            content=item["chunk"].content,
            page_start=item["chunk"].page_start,
            page_end=item["chunk"].page_end,
            heading_path=item["chunk"].heading_path,
            score=item["score"],
            vector_similarity=item["vector_similarity"],
            text_rank=item["text_rank"],
            source_types=tuple(sorted(item["source_types"])),
        )
        for item in ranked
    ]
    candidate_count, best_similarity, best_text_rank = _rag_stats(chunks)
    should_inject = should_inject_rag_context(chunks, query=query, force=force)

    if not should_inject:
        logger.info(
            "RAG skipped: "
            f"query_len={len(query or '')} candidates={candidate_count} "
            f"best_vector_similarity={best_similarity} best_text_rank={best_text_rank}"
        )
        return []

    selected = chunks if force or is_rag_query(query) else [chunk for chunk in chunks if _is_strong_rag_match(chunk)]
    selected_count, selected_similarity, selected_text_rank = _rag_stats(selected)
    selected_sources = ", ".join(
        f"{chunk.filename}:{_format_pages(chunk)}" for chunk in selected[:top_k]
    )
    logger.info(
        "RAG injected: "
        f"query_len={len(query or '')} candidates={candidate_count} selected={selected_count} "
        f"best_vector_similarity={selected_similarity} best_text_rank={selected_text_rank} "
        f"sources={selected_sources}"
    )
    return selected[:top_k]


def _format_pages(chunk: RetrievedRagChunk) -> str:
    if chunk.page_start and chunk.page_end and chunk.page_start != chunk.page_end:
        return f"pages {chunk.page_start}-{chunk.page_end}"
    if chunk.page_start:
        return f"page {chunk.page_start}"
    return "page unknown"


def _truncate(value: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


async def build_rag_context(user_id: int | None, query: str) -> str | None:
    if not user_id:
        return None
    try:
        chunks = await retrieve_rag_chunks(user_id, query)
    except Exception as exc:
        logger.warning(f"RAG retrieval failed: {exc}")
        return None

    if not chunks:
        return None

    lines = [
        "Relevant uploaded PDF context for this authenticated user. This is private, authorized context from the user's uploads. "
        "Use it before web search for the current question when it is relevant. If you rely on it, briefly cite the filename and page when available."
    ]
    total_chars = 0
    for index, chunk in enumerate(chunks, start=1):
        content = _truncate(chunk.content, RAG_CONTEXT_CHUNK_CHARS)
        total_chars += len(content)
        if total_chars > RAG_MAX_CONTEXT_CHARS:
            break
        heading = f" | {chunk.heading_path}" if chunk.heading_path else ""
        lines.append(f"[{index}] {chunk.filename} ({_format_pages(chunk)}){heading}\n{content}")

    return "\n\n".join(lines)
