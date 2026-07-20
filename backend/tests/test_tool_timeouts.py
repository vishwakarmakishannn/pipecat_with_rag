import asyncio
from types import SimpleNamespace

import pytest

import tools.tavily as tavily_module
from core.tool_config import tool_timeout_seconds


def test_tool_timeout_config_is_validated(monkeypatch):
    monkeypatch.setenv("VOICE_TOOL_TIMEOUT_SECONDS", "0.1")
    with pytest.raises(ValueError, match="VOICE_TOOL_TIMEOUT_SECONDS"):
        tool_timeout_seconds()


@pytest.mark.anyio
async def test_tavily_timeout_returns_fallback(monkeypatch):
    results = []

    async def callback(result):
        results.append(result)

    async def slow_to_thread(_function):
        await asyncio.sleep(0.05)

    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("VOICE_TOOL_TIMEOUT_SECONDS", "0.25")
    monkeypatch.setattr(tavily_module, "tool_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(tavily_module.asyncio, "to_thread", slow_to_thread)

    await tavily_module.tavily_search(
        SimpleNamespace(result_callback=callback),
        "current news",
    )

    assert results == [{
        "status": "timeout",
        "message": "Web search timed out. Give a brief fallback answer and disclose that live results were unavailable.",
    }]
