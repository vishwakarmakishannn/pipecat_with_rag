import asyncio
from types import SimpleNamespace

import pytest
from loguru import logger

from providers.llm.google_llm import FirstTokenTimeoutError, LatencyBoundGoogleLLMService


def _chunk(text=None):
    part = SimpleNamespace(text=text, function_call=None, inline_data=None)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


@pytest.mark.anyio
async def test_google_stream_times_out_before_first_meaningful_output():
    async def stalled_stream():
        yield _chunk()
        await asyncio.Event().wait()

    with pytest.raises(FirstTokenTimeoutError):
        chunks = LatencyBoundGoogleLLMService._first_output_stream(stalled_stream(), 0.01)
        [chunk async for chunk in chunks]


@pytest.mark.anyio
async def test_google_stream_replays_metadata_then_first_output():
    metadata = _chunk()
    output = _chunk("hello")

    async def stream():
        yield metadata
        yield output

    chunks = [
        chunk
        async for chunk in LatencyBoundGoogleLLMService._first_output_stream(stream(), 0.1)
    ]
    assert chunks == [metadata, output]


@pytest.mark.anyio
async def test_google_timeout_becomes_spoken_recovery_chunk():
    async def stalled_stream():
        await asyncio.Event().wait()
        yield

    chunks = [
        chunk
        async for chunk in LatencyBoundGoogleLLMService._recovering_stream(
            stalled_stream(), 0.01, "Please try again."
        )
    ]
    assert len(chunks) == 1
    assert chunks[0].candidates[0].content.parts[0].text == "Please try again."


@pytest.mark.anyio
async def test_google_first_output_log_keeps_request_correlation_id():
    logs = []
    sink = logger.add(logs.append, format="{message}")
    try:
        async def stream():
            yield _chunk("answer")

        chunks = [
            chunk
            async for chunk in LatencyBoundGoogleLLMService._recovering_stream(
                stream(),
                0.2,
                "Please try again.",
                request_id="trace-123",
                provider_model="google-model",
            )
        ]
        assert chunks
    finally:
        logger.remove(sink)

    rendered = "".join(str(item) for item in logs)
    assert "request_id=trace-123" in rendered
    assert "provider=google" in rendered
    assert "status=first_output" in rendered
    assert "latency_ms=" in rendered
