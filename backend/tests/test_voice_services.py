import asyncio

import pytest

import core.voice_services as voice_services


@pytest.mark.anyio
async def test_voice_service_constructors_are_scheduled_together(monkeypatch):
    scheduled = []
    release = asyncio.Event()
    all_scheduled = asyncio.Event()

    async def fake_to_thread(factory):
        scheduled.append(factory.__name__)
        if len(scheduled) == 3:
            all_scheduled.set()
        await release.wait()
        return factory()

    monkeypatch.setattr(voice_services.asyncio, "to_thread", fake_to_thread)
    task = asyncio.create_task(
        voice_services.initialize_voice_services(
            lambda: "stt",
            lambda: "tts",
            lambda: "llm",
        )
    )
    await asyncio.wait_for(all_scheduled.wait(), timeout=0.2)
    assert len(scheduled) == 3
    release.set()
    assert await task == ("stt", "tts", "llm")
