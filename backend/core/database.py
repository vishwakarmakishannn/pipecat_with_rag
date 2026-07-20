import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

_voice_budget_ms = int(float(os.getenv("RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS", "2.5")) * 1000)
_voice_statement_timeout_ms = int(os.getenv("DB_VOICE_STATEMENT_TIMEOUT_MS", "2000"))
_voice_hnsw_iterative_scan = os.getenv("DB_VOICE_HNSW_ITERATIVE_SCAN", "relaxed_order").strip()
if _voice_hnsw_iterative_scan not in {"off", "strict_order", "relaxed_order"}:
    raise ValueError(
        "DB_VOICE_HNSW_ITERATIVE_SCAN must be off, strict_order, or relaxed_order"
    )
if not 100 <= _voice_statement_timeout_ms < _voice_budget_ms:
    raise ValueError(
        "DB_VOICE_STATEMENT_TIMEOUT_MS must be at least 100 ms and below "
        f"the {_voice_budget_ms} ms voice retrieval budget"
    )

# Reserve database capacity for live turns so REST and ingestion cannot consume
# every connection needed by latency-sensitive retrieval and tool calls.
voice_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=int(os.getenv("DB_VOICE_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_VOICE_MAX_OVERFLOW", "0")),
    pool_timeout=float(os.getenv("DB_VOICE_POOL_TIMEOUT", "1.0")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
    connect_args={
        "server_settings": {
            "statement_timeout": str(_voice_statement_timeout_ms),
            # pgvector 0.8+ continues an approximate scan until enough rows
            # survive tenant/status filters instead of returning a sparse top-k.
            "hnsw.iterative_scan": _voice_hnsw_iterative_scan,
            "hnsw.max_scan_tuples": os.getenv("DB_VOICE_HNSW_MAX_SCAN_TUPLES", "10000"),
        }
    },
)
VoiceSessionLocal = async_sessionmaker(voice_engine, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
