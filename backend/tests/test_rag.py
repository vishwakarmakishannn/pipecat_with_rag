import pytest

from main import RollingVoiceQueryBuffer
from services.rag import (
    RetrievedRagChunk,
    build_rag_context,
    chunk_link_markdown,
    is_rag_query,
    should_inject_rag_context,
    validate_public_http_url,
)


def test_is_rag_query_detects_document_questions():
    assert is_rag_query("What does my PDF say about invoices?")
    assert is_rag_query("Summarize the uploaded report")
    assert is_rag_query("According to my file, what is the deadline?")


def test_is_rag_query_ignores_general_chat():
    assert not is_rag_query("Who is the president of the USA?")
    assert not is_rag_query("What did we talk about previously?")


def test_smart_router_injects_without_document_words_when_vector_match_is_strong():
    chunks = [
        RetrievedRagChunk(
            id=1,
            file_id=1,
            filename="abc.pdf",
            content="Rohan Sharma device ID is A-123.",
            page_start=1,
            page_end=1,
            heading_path=None,
            score=0.1,
            vector_similarity=0.81,
            source_types=("vector",),
        )
    ]

    assert should_inject_rag_context(chunks, query="What is the device ID of Rohan Sharma?")


def test_smart_router_skips_weak_unrelated_matches():
    chunks = [
        RetrievedRagChunk(
            id=1,
            file_id=1,
            filename="abc.pdf",
            content="A weak unrelated candidate.",
            page_start=None,
            page_end=None,
            heading_path=None,
            score=0.1,
            vector_similarity=0.22,
            text_rank=0.0,
            source_types=("vector",),
        )
    ]

    assert not should_inject_rag_context(chunks, query="Who is the president of the USA?")


def test_smart_router_allows_text_only_fallback_when_rank_is_strong():
    chunks = [
        RetrievedRagChunk(
            id=1,
            file_id=1,
            filename="abc.pdf",
            content="Rohan Sharma device ID is A-123.",
            page_start=1,
            page_end=1,
            heading_path=None,
            score=0.1,
            text_rank=0.2,
            source_types=("text",),
        )
    ]

    assert should_inject_rag_context(chunks, query="What is the device ID of Rohan Sharma?")


def test_rolling_voice_query_buffer_combines_recent_fragments():
    buffer = RollingVoiceQueryBuffer(window_seconds=8)

    assert buffer.add("What is the device ID of Rohan Sharma", now=10) == "What is the device ID of Rohan Sharma"
    assert (
        buffer.add("from complaints?", now=13)
        == "What is the device ID of Rohan Sharma from complaints?"
    )


def test_rolling_voice_query_buffer_drops_old_fragments():
    buffer = RollingVoiceQueryBuffer(window_seconds=8)

    buffer.add("What is the device ID of Rohan Sharma", now=10)

    assert buffer.add("from complaints?", now=25) == "from complaints?"


def test_validate_public_http_url_normalizes_bare_domains(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    assert validate_public_http_url("example.com/article") == "https://example.com/article"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:text/plain,hello", "ftp://example.com/file"])
def test_validate_public_http_url_rejects_non_http_schemes(url):
    with pytest.raises(ValueError):
        validate_public_http_url(url)


def test_validate_public_http_url_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 80))],
    )

    with pytest.raises(ValueError):
        validate_public_http_url("https://example.com")


def test_chunk_link_markdown_preserves_heading_context():
    chunks = chunk_link_markdown(
        "# Device Report\nRohan Sharma device ID is A-123 and the warranty is active.",
        title="Complaint Receipt",
        final_url="https://example.com/receipt",
    )

    assert chunks
    assert chunks[0].heading_path == "Complaint Receipt > Device Report"
    assert "URL: https://example.com/receipt" in chunks[0].embedding_text


@pytest.mark.anyio
async def test_build_rag_context_formats_retrieved_chunks(monkeypatch):
    async def fake_retrieve(user_id, query):
        assert user_id == 7
        assert query == "What does my PDF say about AI?"
        return [
            RetrievedRagChunk(
                id=1,
                file_id=2,
                filename="paper.pdf",
                content="The paper says AI systems can help summarize long documents.",
                page_start=3,
                page_end=3,
                heading_path="Findings",
                score=0.1,
            )
        ]

    monkeypatch.setattr("services.rag.retrieve_rag_chunks", fake_retrieve)

    context = await build_rag_context(7, "What does my PDF say about AI?")

    assert "paper.pdf" in context
    assert "page 3" in context
    assert "Findings" in context
    assert "summarize long documents" in context
    assert "before web search" in context


@pytest.mark.anyio
async def test_build_rag_context_formats_link_chunks(monkeypatch):
    async def fake_retrieve(user_id, query):
        return [
            RetrievedRagChunk(
                id=1,
                file_id=2,
                filename="Example Article",
                content="The article says the launch date is July 10.",
                page_start=None,
                page_end=None,
                heading_path="Example Article > Launch",
                score=0.1,
                source_type="link",
                url="https://example.com/article",
                title="Example Article",
                site_name="example.com",
            )
        ]

    monkeypatch.setattr("services.rag.retrieve_rag_chunks", fake_retrieve)

    context = await build_rag_context(7, "What does the article say?")

    assert "Link: Example Article <https://example.com/article>" in context
    assert "untrusted retrieved context" in context
    assert "launch date is July 10" in context
