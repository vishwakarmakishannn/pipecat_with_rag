import pytest

from memory import (
    MemoryBundle,
    build_memory_chunk,
    build_memory_messages,
    build_turn_memory_context,
    classify_memory_events,
    message_to_llm,
)
from models import Conversation, MemoryChunk, Message, User, UserMemory


def test_build_memory_messages_ignores_invalid_name_memory():
    bundle = MemoryBundle(
        user=User(id=1, username="kishan", password_hash="x"),
        primary_conversation=Conversation(id=10, user_id=1, title="Old chat", summary=""),
        facts=[UserMemory(key="real_name", value="fine", status="active", fact_type="profile")],
        primary_summary="",
        primary_recent_messages=[],
    )

    assert build_memory_messages(bundle) == []


def test_message_to_llm_maps_supported_roles_only():
    assert message_to_llm(Message(role="You", content="hello")) == {
        "role": "user",
        "content": "hello",
    }
    assert message_to_llm(Message(role="Aura", content="hi")) == {
        "role": "assistant",
        "content": "hi",
    }
    assert message_to_llm(Message(role="ToolCall", content="{}")) is None


def test_build_memory_messages_includes_typed_facts_summary_and_recent_transcript():
    bundle = MemoryBundle(
        user=User(id=1, username="kishan", password_hash="x"),
        primary_conversation=Conversation(id=10, user_id=1, title="Old chat", summary="Discussed memory."),
        facts=[
            UserMemory(key="real_name", value="Kishan", status="active", fact_type="profile"),
            UserMemory(key="likes", value="football", status="active", fact_type="preference"),
            UserMemory(key="likes", value="apple", status="active", fact_type="preference"),
        ],
        primary_summary="Discussed memory.",
        primary_recent_messages=[
            Message(role="You", content="What did we discuss?"),
            Message(role="Aura", content="Memory implementation."),
        ],
    )

    messages = build_memory_messages(bundle)

    assert messages[0]["role"] == "developer"
    assert "real_name: Kishan" in messages[0]["content"]
    assert "preference.likes: football" in messages[0]["content"]
    assert "preference.likes: apple" in messages[0]["content"]
    assert messages[1]["role"] == "developer"
    assert "Discussed memory." in messages[1]["content"]
    assert messages[-2:] == [
        {"role": "user", "content": "What did we discuss?"},
        {"role": "assistant", "content": "Memory implementation."},
    ]


def test_build_memory_messages_includes_prior_context_as_secondary():
    bundle = MemoryBundle(
        user=User(id=1, username="kishan", password_hash="x"),
        primary_conversation=Conversation(id=11, user_id=1, title="New conversation", summary=""),
        facts=[],
        primary_summary="",
        primary_recent_messages=[],
        prior_conversation=Conversation(
            id=10,
            user_id=1,
            title="AI chat",
            summary="The user asked what AI is.",
        ),
        prior_recent_messages=[
            Message(role="You", content="Explain AI in one line."),
            Message(role="Aura", content="AI lets computers simulate human intelligence."),
        ],
    )

    messages = build_memory_messages(bundle)

    assert len(messages) == 1
    assert messages[0]["role"] == "developer"
    assert "Recent prior conversation context" in messages[0]["content"]
    assert "Explain AI in one line." in messages[0]["content"]


@pytest.mark.anyio
async def test_classifier_rejects_invalid_name_memory(monkeypatch):
    async def fake_generate(_prompt):
        return '{"events":[{"action":"upsert","fact_type":"profile","key":"real_name","value":"fine","confidence":0.99,"durability":"stable"}]}'

    monkeypatch.setattr("memory._generate_text_with_memory_llm", fake_generate)

    events = await classify_memory_events("I'm fine.")

    assert events == []


@pytest.mark.anyio
async def test_classifier_treats_temporary_call_me_as_ignored(monkeypatch):
    async def fake_generate(_prompt):
        return '{"events":[{"action":"upsert","fact_type":"profile","key":"preferred_name","value":"Raj","confidence":0.95,"durability":"temporary"}]}'

    monkeypatch.setattr("memory._generate_text_with_memory_llm", fake_generate)

    events = await classify_memory_events("Call me Raj for now.")

    assert events == []


def test_build_memory_chunk_from_turn_window():
    chunk = build_memory_chunk(
        7,
        [
            Message(id=1, role="You", content="I like football."),
            Message(id=2, role="Aura", content="Nice, football is fun."),
        ],
    )

    assert chunk["conversation_id"] == 7
    assert chunk["message_start_id"] == 1
    assert chunk["message_end_id"] == 2
    assert "I like football." in chunk["chunk_text"]


@pytest.mark.anyio
async def test_turn_memory_context_formats_retrieved_chunks(monkeypatch):
    async def fake_retrieve(_user_id, _query, _top_k):
        return [
            (
                MemoryChunk(
                    user_id=1,
                    conversation_id=3,
                    message_start_id=1,
                    message_end_id=2,
                    chunk_text="User: What is AI?\nAura: AI simulates intelligence.",
                    summary="Discussed AI basics.",
                ),
                0.91,
            )
        ]

    monkeypatch.setattr("memory.retrieve_semantic_memories", fake_retrieve)

    context = await build_turn_memory_context(1, "What did we discuss about AI?")

    assert "Relevant long-term episodic memories" in context
    assert "Discussed AI basics." in context
