from core import database


def test_voice_database_pool_is_reserved_and_bounded():
    assert database.voice_engine is not database.engine
    assert database._voice_statement_timeout_ms < database._voice_budget_ms
    assert database.voice_engine.pool._timeout <= 1.0


def test_voice_vector_queries_use_filtered_hnsw_iterative_scans():
    settings = database.voice_engine.url  # force engine construction before checking config
    assert settings is not None
    assert database._voice_hnsw_iterative_scan in {"strict_order", "relaxed_order"}
