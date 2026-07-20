import pytest
import asyncio

from core.processors import ContextRetrievalProcessor, RollingVoiceQueryBuffer, ToolRoutingProcessor, TurnLatencyState
from pipecat.frames.frames import OutputTransportMessageFrame, TranscriptionFrame, TTSAudioRawFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection
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
    bump_rag_corpus_version,
    clear_rag_result_cache,
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


def test_authoritative_user_turn_start_clears_rolling_query_and_dynamic_context():
    context = LLMContext(messages=[])
    processor = ContextRetrievalProcessor(1, 1, context)
    message = {"role": "developer", "content": "old turn context"}
    context.add_message(message)
    processor._dynamic_messages.append(message)
    processor._query_buffer.add("old interrupted question", now=1)

    processor.start_user_turn()

    assert processor._query_buffer.add("new question", now=2) == "new question"
    assert message not in context.messages


@pytest.mark.anyio
async def test_new_retrieval_cancels_stale_fragment_without_delivering_it(monkeypatch):
    first_started = asyncio.Event()
    release_second = asyncio.Event()
    delivered = []

    async def fake_rag(_user_id, query, query_embedding=None):
        assert query_embedding is None
        if query == "first document question":
            first_started.set()
            await asyncio.Event().wait()
        await release_second.wait()
        return (f"context for {query}", None)

    processor = ContextRetrievalProcessor(1, 1, LLMContext(messages=[]))

    async def capture(frame, _direction):
        delivered.append(frame)

    monkeypatch.setattr("core.processors.build_rag_context_with_payload", fake_rag)
    monkeypatch.setattr("core.processors.should_attempt_rag_retrieval", lambda _query: True)
    monkeypatch.setattr(processor, "push_frame", capture)

    first = TranscriptionFrame("first document question", "user", "1", finalized=True)
    second = TranscriptionFrame("second document question", "user", "2", finalized=True)
    await processor.process_frame(first, FrameDirection.DOWNSTREAM)
    await asyncio.wait_for(first_started.wait(), timeout=0.2)
    await processor.process_frame(second, FrameDirection.DOWNSTREAM)
    release_second.set()
    await asyncio.wait_for(processor._active_task, timeout=0.2)

    transcriptions = [frame for frame in delivered if isinstance(frame, TranscriptionFrame)]
    assert transcriptions == [second]


@pytest.mark.anyio
async def test_retrieval_deadline_includes_delivery_lock_wait(monkeypatch):
    delivered = []
    processor = ContextRetrievalProcessor(1, 1, LLMContext(messages=[]))
    processor._retrieval_generation = 1

    async def capture(frame, _direction):
        delivered.append(frame)

    monkeypatch.setattr("core.processors.RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(processor, "push_frame", capture)
    frame = TranscriptionFrame("document question", "user", "1", finalized=True)

    await processor._delivery_lock.acquire()
    started = asyncio.get_running_loop().time()
    try:
        await processor._retrieve_and_push(
            frame,
            frame.text,
            FrameDirection.DOWNSTREAM,
            False,
            True,
            False,
            1,
        )
    finally:
        processor._delivery_lock.release()

    assert asyncio.get_running_loop().time() - started < 0.1
    assert delivered == [frame]


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


def test_latency_turn_identity_starts_with_speech_not_final_transcript():
    state = TurnLatencyState(session_id="test")

    state.mark_user_started()
    assert state.turn_id == 1
    assert state.started_at is None
    state.mark_user_stopped()
    state.start_turn()
    assert state.turn_id == 1
    assert state.started_at == state.final_stt_at

    state.finish_turn()
    state.mark_user_started()
    assert state.turn_id == 2
    assert state.started_at is None


def test_latency_state_reports_endpoint_relative_stage_telemetry():
    state = TurnLatencyState(session_id="test")
    state.mark_user_started()
    state.mark_user_stopped()
    state.start_turn()
    state.mark_stage("retrieval_queued")

    payload = state.telemetry_payload()

    assert payload["basis"] == "user_stopped"
    assert payload["speech_ms"] is not None
    assert payload["stages_ms"]["user_stopped"] == 0.0
    assert payload["stages_ms"]["final_stt"] >= 0.0
    assert payload["stages_ms"]["retrieval_queued"] >= 0.0
    assert payload["server_emitted_unix_ms"] > 0


@pytest.mark.anyio
async def test_first_audio_is_pushed_before_latency_diagnostics(monkeypatch):
    state = TurnLatencyState(session_id="test")
    state.start_turn()
    state.first_llm_seen = True
    from core.processors import LatencyBoundaryProcessor
    boundary = LatencyBoundaryProcessor(state, "tts")
    delivered = []

    async def capture(frame, _direction):
        delivered.append(frame)

    monkeypatch.setattr(boundary, "push_frame", capture)
    audio = TTSAudioRawFrame(b"\x00\x00", 24000, 1)
    await boundary.process_frame(audio, FrameDirection.DOWNSTREAM)

    assert delivered[0] is audio
    assert isinstance(delivered[1], OutputTransportMessageFrame)


@pytest.mark.anyio
async def test_hybrid_retrieval_runs_vector_and_text_queries_concurrently(monkeypatch):
    clear_rag_result_cache()
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
async def test_text_retrieval_starts_before_embedding_finishes(monkeypatch):
    clear_rag_result_cache()
    text_started = asyncio.Event()

    async def fake_embed(_query):
        await asyncio.wait_for(text_started.wait(), timeout=0.2)
        return [0.1]

    async def fake_vector(_user_id, _embedding):
        return []

    async def fake_text(_user_id, _query):
        text_started.set()
        return []

    monkeypatch.setattr(rag_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service, "_retrieve_vector_candidates", fake_vector)
    monkeypatch.setattr(rag_service, "_retrieve_text_candidates", fake_text)

    assert await retrieve_rag_chunks(1, "What does my document say?") == []


@pytest.mark.anyio
async def test_rag_result_cache_uses_corpus_version(monkeypatch):
    calls = 0

    async def fake_retrieve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return []

    clear_rag_result_cache()
    monkeypatch.setattr(rag_service, "_retrieve_rag_chunks_uncached", fake_retrieve)

    await retrieve_rag_chunks(9, "  My   Document ")
    await retrieve_rag_chunks(9, "my document")
    assert calls == 1

    bump_rag_corpus_version(9)
    await retrieve_rag_chunks(9, "my document")
    assert calls == 2


@pytest.mark.anyio
async def test_combined_memory_and_rag_share_one_embedding(monkeypatch):
    embedding_calls = 0
    seen_embeddings = []
    processor = ContextRetrievalProcessor(1, 1, LLMContext(messages=[]))
    processor._retrieval_generation = 1

    async def fake_embed(_query):
        nonlocal embedding_calls
        embedding_calls += 1
        await asyncio.sleep(0)
        return [0.5]

    async def fake_memory(
        _user_id,
        _query,
        query_embedding=None,
        current_conversation_id=None,
    ):
        assert current_conversation_id == 1
        seen_embeddings.append(await query_embedding)
        return None

    async def fake_rag(_user_id, _query, query_embedding=None):
        seen_embeddings.append(await query_embedding)
        return None, None

    async def capture(_frame, _direction):
        return None

    monkeypatch.setattr("core.processors.embed_text", fake_embed)
    monkeypatch.setattr("core.processors.build_turn_memory_context", fake_memory)
    monkeypatch.setattr("core.processors.build_rag_context_with_payload", fake_rag)
    monkeypatch.setattr(processor, "push_frame", capture)
    frame = TranscriptionFrame("remember my document", "user", "1", finalized=True)

    await processor._retrieve_and_push(
        frame,
        frame.text,
        FrameDirection.DOWNSTREAM,
        True,
        True,
        False,
        1,
    )

    assert embedding_calls == 1
    assert seen_embeddings == [[0.5], [0.5]]


@pytest.mark.anyio
async def test_deterministic_web_search_runs_before_llm_and_suppresses_tool_pass(monkeypatch):
    context = LLMContext(messages=[])
    state = TurnLatencyState(session_id="test")
    state.start_turn()
    delivered = []

    async def fake_web_search(query):
        await asyncio.sleep(0)
        return {"query": query, "answer": "It is sunny."}

    async def capture(frame, _direction):
        delivered.append(frame)

    processor = ContextRetrievalProcessor(
        1,
        1,
        context,
        state,
        web_search=fake_web_search,
    )
    monkeypatch.setattr("core.processors.should_attempt_rag_retrieval", lambda _query: False)
    monkeypatch.setattr(processor, "push_frame", capture)
    frame = TranscriptionFrame("look up the latest weather", "user", "1", finalized=True)

    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    task = processor._active_task
    assert task is not None
    await asyncio.wait_for(task, timeout=0.2)

    assert isinstance(delivered[0], TTSSpeakFrame)
    assert delivered[-1] is frame
    assert processor.web_search_resolved is True
    assert processor.tool_filler_emitted is True
    assert state.tool_used is True
    assert any("It is sunny" in message.get("content", "") for message in context.messages)

    context.add_message({"role": "user", "content": frame.text})
    router = ToolRoutingProcessor(
        context,
        search_tool=lambda: None,
        issue_tool=lambda: None,
        retrieval=processor,
    )
    assert router.route() == []


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
    async def fake_retrieve(user_id, query, query_embedding=None):
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
    async def fake_retrieve(user_id, query, query_embedding=None):
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
