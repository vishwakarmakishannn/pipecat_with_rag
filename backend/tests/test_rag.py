import pytest
import asyncio

from core.processors import ContextRetrievalProcessor, RollingVoiceQueryBuffer, TurnLatencyState
from pipecat.processors.aggregators.llm_context import LLMContext
from services.rag import (
    RetrievedRagChunk,
    build_rag_context,
    chunk_link_markdown,
    is_rag_query,
    should_inject_rag_context,
    should_attempt_rag_retrieval,
    validate_public_http_url,
    retrieve_rag_chunks,
)
import services.rag as rag_service


def test_is_rag_query_detects_document_questions():
    assert is_rag_query("What does my PDF say about invoices?")
    assert is_rag_query("Summarize the uploaded report")
    assert is_rag_query("According to my file, what is the deadline?")
    assert is_rag_query("What are the top five documentaries according to the documents?")


def test_is_rag_query_detects_saved_link_questions():
    assert is_rag_query("What does the link say about the 2022 awards?")
    assert is_rag_query("Summarize my saved webpage")
    assert is_rag_query("According to the article, what are the top five documentaries?")


def test_is_rag_query_ignores_general_chat():
    assert not is_rag_query("Who is the president of the USA?")
    assert not is_rag_query("What did we talk about previously?")


def test_pre_router_bypasses_conversation_but_keeps_information_queries():
    assert not should_attempt_rag_retrieval("Okay, thank you")
    assert not should_attempt_rag_retrieval("What is your name?")
    assert not should_attempt_rag_retrieval("Explain what AI is")
    assert should_attempt_rag_retrieval("What is the device ID of Rohan Sharma in my PDF?")
    assert should_attempt_rag_retrieval("I mean, according to my documents.")
    assert should_attempt_rag_retrieval("Use my saved link for the answer")


def test_interrupted_response_does_not_remove_fresh_rag_context():
    context = LLMContext(messages=[])
    processor = ContextRetrievalProcessor(1, 1, context)
    message = {"role": "developer", "content": "RAG_GROUNDED_TURN: PDF answer"}
    context.add_message(message)
    processor._dynamic_messages.append(message)

    processor.finish_response()
    assert message in context.messages

    processor.clear_dynamic_context()
    assert message not in context.messages


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


def test_latency_state_counts_transcript_fragments_as_one_active_turn():
    state = TurnLatencyState(session_id="test")

    state.start_turn()
    state.start_turn()
    assert state.turn_id == 1

    state.finish_turn()
    state.start_turn()
    assert state.turn_id == 2


@pytest.mark.anyio
async def test_hybrid_retrieval_runs_vector_and_text_queries_concurrently(monkeypatch):
    vector_started = asyncio.Event()
    text_started = asyncio.Event()

    async def fake_embed(_query):
        return [0.1]

    async def fake_vector(_user_id, _embedding):
        vector_started.set()
        await asyncio.wait_for(text_started.wait(), timeout=0.2)
        return []

    async def fake_text(_user_id, _query):
        text_started.set()
        await asyncio.wait_for(vector_started.wait(), timeout=0.2)
        return []

    monkeypatch.setattr(rag_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service, "_retrieve_vector_candidates", fake_vector)
    monkeypatch.setattr(rag_service, "_retrieve_text_candidates", fake_text)

    assert await retrieve_rag_chunks(1, "What does my document say?") == []


@pytest.mark.anyio
async def test_validate_public_http_url_normalizes_bare_domains(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    assert await validate_public_http_url("example.com/article") == "https://example.com/article"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:text/plain,hello", "ftp://example.com/file"])
@pytest.mark.anyio
async def test_validate_public_http_url_rejects_non_http_schemes(url):
    with pytest.raises(ValueError):
        await validate_public_http_url(url)


@pytest.mark.anyio
async def test_validate_public_http_url_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 80))],
    )

    with pytest.raises(ValueError):
        await validate_public_http_url("https://example.com")


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
    assert "RAG_GROUNDED_TURN" in context
    assert "Do not call the web-search tool" in context


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
