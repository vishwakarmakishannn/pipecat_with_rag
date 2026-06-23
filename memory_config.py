import os


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


RECENT_MESSAGE_LIMIT = env_int("RECENT_MESSAGE_LIMIT", 20)
PRIOR_CONVERSATION_MESSAGE_LIMIT = env_int("PRIOR_CONVERSATION_MESSAGE_LIMIT", 10)
SUMMARY_MESSAGE_THRESHOLD = env_int("SUMMARY_MESSAGE_THRESHOLD", 20)
SUMMARY_CHAR_THRESHOLD = env_int("SUMMARY_CHAR_THRESHOLD", 4000)
MEMORY_LLM_TIMEOUT_SECONDS = env_float("MEMORY_LLM_TIMEOUT_SECONDS", 4.0)
MEMORY_FACT_CONFIDENCE_MIN = env_float("MEMORY_FACT_CONFIDENCE_MIN", 0.85)
MEMORY_RECALL_TOP_K = env_int("MEMORY_RECALL_TOP_K", 5)
MEMORY_RECALL_MIN_SCORE = env_float("MEMORY_RECALL_MIN_SCORE", 0.72)
MEMORY_VECTOR_DB = os.getenv("MEMORY_VECTOR_DB", "pgvector").lower()
MEMORY_EMBEDDING_PROVIDER = os.getenv("MEMORY_EMBEDDING_PROVIDER", "google").lower()

# Keep the DB schema provider-neutral by storing one fixed vector size.
# Both Google gemini-embedding-001 and OpenAI text-embedding-3-small can be used
# with this dimension; change it once before storing production memory if needed.
MEMORY_EMBEDDING_DIMENSION = env_int("MEMORY_EMBEDDING_DIMENSION", 768)
