import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


RAG_UPLOAD_DIR = os.getenv("RAG_UPLOAD_DIR", "uploads/rag")
RAG_MAX_UPLOAD_MB = _int_env("RAG_MAX_UPLOAD_MB", 25)
RAG_RETRIEVAL_TOP_K = _int_env("RAG_RETRIEVAL_TOP_K", 5)
RAG_VECTOR_CANDIDATES = _int_env("RAG_VECTOR_CANDIDATES", 20)
RAG_TEXT_CANDIDATES = _int_env("RAG_TEXT_CANDIDATES", 20)
RAG_RRF_K = _int_env("RAG_RRF_K", 60)
RAG_RERANKER = os.getenv("RAG_RERANKER", "none").lower()
RAG_SMART_ROUTER = os.getenv("RAG_SMART_ROUTER", "hybrid").lower()
RAG_MIN_CONTENT_CHARS = _int_env("RAG_MIN_CONTENT_CHARS", 30)
RAG_MAX_CONTEXT_CHARS = _int_env("RAG_MAX_CONTEXT_CHARS", 6000)
RAG_CONTEXT_CHUNK_CHARS = _int_env("RAG_CONTEXT_CHUNK_CHARS", 1200)
RAG_TEXT_MATCH_MIN_RANK = _float_env("RAG_TEXT_MATCH_MIN_RANK", 0.0)
RAG_MIN_VECTOR_SIMILARITY = _float_env("RAG_MIN_VECTOR_SIMILARITY", 0.62)
RAG_MIN_TEXT_RANK = _float_env("RAG_MIN_TEXT_RANK", 0.15)
RAG_VOICE_QUERY_WINDOW_SECONDS = _float_env("RAG_VOICE_QUERY_WINDOW_SECONDS", 8.0)
RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS = _float_env("RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS", 2.5)
RAG_MIN_STRONG_MATCHES = _int_env("RAG_MIN_STRONG_MATCHES", 1)
RAG_LINK_EXTRACTOR = os.getenv("RAG_LINK_EXTRACTOR", "crawl4ai").lower()
RAG_LINK_FALLBACK_EXTRACTOR = os.getenv("RAG_LINK_FALLBACK_EXTRACTOR", "trafilatura").lower()
RAG_LINK_MAX_BYTES = _int_env("RAG_LINK_MAX_BYTES", 5_000_000)
RAG_LINK_TIMEOUT_SECONDS = _float_env("RAG_LINK_TIMEOUT_SECONDS", 20.0)
RAG_LINK_RESPECT_ROBOTS = os.getenv("RAG_LINK_RESPECT_ROBOTS", "true").lower() not in {"0", "false", "no"}
RAG_LINK_MIN_CHARS = _int_env("RAG_LINK_MIN_CHARS", 300)
RAG_LINK_CHUNK_CHARS = _int_env("RAG_LINK_CHUNK_CHARS", 1600)
RAG_LINK_CHUNK_OVERLAP = _int_env("RAG_LINK_CHUNK_OVERLAP", 200)
RAG_LINK_USER_AGENT = os.getenv("RAG_LINK_USER_AGENT", "AuraVoiceRAG/1.0")
RAG_INGEST_EMBED_CONCURRENCY = _int_env("RAG_INGEST_EMBED_CONCURRENCY", 4)
