import sys
from types import SimpleNamespace

import pytest

from providers.llm.factory import get_llm


@pytest.mark.parametrize(
    ("provider", "selected_module", "selected_factory"),
    [
        ("google", "providers.llm.google_llm", "get_google_llm"),
        ("groq", "providers.llm.groq_llm", "get_groq_llm"),
        ("openai", "providers.llm.openai_llm", "get_openai_llm"),
    ],
)
def test_factory_constructs_exactly_one_selected_provider(
    monkeypatch,
    provider,
    selected_module,
    selected_factory,
):
    calls = []
    marker = object()

    def factory(name, result=None):
        def build():
            calls.append(name)
            return result

        return build

    modules = {
        "providers.llm.google_llm": SimpleNamespace(
            get_google_llm=factory("google", marker if provider == "google" else None)
        ),
        "providers.llm.groq_llm": SimpleNamespace(
            get_groq_llm=factory("groq", marker if provider == "groq" else None)
        ),
        "providers.llm.openai_llm": SimpleNamespace(
            get_openai_llm=factory("openai", marker if provider == "openai" else None)
        ),
    }
    for module_name, module in modules.items():
        monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setenv("LLM_PROVIDER", f" {provider.upper()} ")
    monkeypatch.setenv("GROQ_API_KEY", "must-not-create-an-unselected-provider")

    assert get_llm() is marker
    assert calls == [provider]
    assert selected_module in sys.modules
    assert hasattr(sys.modules[selected_module], selected_factory)


def test_factory_rejects_unsupported_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="Expected google, groq, or openai"):
        get_llm()


def test_google_builder_has_no_cross_provider_fallback(monkeypatch):
    from providers.llm import google_llm

    captured = {}

    class FakeGoogleService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(google_llm, "LatencyBoundGoogleLLMService", FakeGoogleService)
    monkeypatch.setattr(google_llm, "load_system_prompt", lambda: "system prompt")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("GROQ_API_KEY", "must-be-ignored")

    google_llm.get_google_llm()

    assert captured["api_key"] == "google-key"
    assert "fallback_llm" not in captured
    assert "hedge_delay_secs" not in captured


def test_openai_builder_uses_openai_env_and_tool_configuration(monkeypatch):
    from providers.llm import openai_llm

    captured = {}

    class FakeSettings:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeOpenAIService:
        Settings = FakeSettings

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai_llm, "OpenAILLMService", FakeOpenAIService)
    monkeypatch.setattr(openai_llm, "load_system_prompt", lambda: "system prompt")
    monkeypatch.setattr(openai_llm, "tool_timeout_seconds", lambda: 3.5)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "configured-openai-model")

    openai_llm.get_openai_llm()

    assert captured["api_key"] == "openai-key"
    assert captured["settings"].model == "configured-openai-model"
    assert captured["settings"].system_instruction == "system prompt"
    assert captured["function_call_timeout_secs"] == 3.5
    assert captured["enable_async_tool_cancellation"] is True
    assert captured["retry_on_timeout"] is False
