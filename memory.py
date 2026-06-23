import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import jwt
from loguru import logger
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from auth import ALGORITHM, SECRET_KEY
from database import AsyncSessionLocal
from memory_config import (
    MEMORY_EMBEDDING_DIMENSION,
    MEMORY_EMBEDDING_PROVIDER,
    MEMORY_FACT_CONFIDENCE_MIN,
    MEMORY_LLM_TIMEOUT_SECONDS,
    MEMORY_RECALL_MIN_SCORE,
    MEMORY_RECALL_TOP_K,
    MEMORY_VECTOR_DB,
    PRIOR_CONVERSATION_MESSAGE_LIMIT,
    RECENT_MESSAGE_LIMIT,
    SUMMARY_CHAR_THRESHOLD,
    SUMMARY_MESSAGE_THRESHOLD,
)
from models import Conversation, MemoryChunk, Message, User, UserMemory


SINGLE_VALUE_KEYS = {"real_name", "preferred_name", "location", "role", "preferred_language"}
MULTI_VALUE_KEYS = {"likes", "dislikes", "interests", "goals"}
VALID_DURABILITY = {"stable", "temporary"}
VALID_STATUSES = {"active", "inactive"}
INVALID_NAME_VALUES = {
    "a",
    "an",
    "the",
    "not",
    "fine",
    "good",
    "great",
    "ok",
    "okay",
    "well",
    "from",
    "going",
    "working",
}


@dataclass
class MemoryBundle:
    user: User
    primary_conversation: Conversation
    facts: list[UserMemory]
    primary_summary: str
    primary_recent_messages: list[Message]
    prior_conversation: Conversation | None = None
    prior_recent_messages: list[Message] | None = None

    @property
    def conversation(self) -> Conversation:
        return self.primary_conversation

    @property
    def summary(self) -> str:
        return self.primary_summary

    @property
    def recent_messages(self) -> list[Message]:
        return self.primary_recent_messages


def normalize_runner_body(body: Any) -> dict[str, Any]:
    return body if isinstance(body, dict) else {}


async def authenticate_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except Exception as exc:
        logger.warning(f"Memory auth failed: {exc}")
        return None

    if not username:
        return None

    result = await db.execute(select(User).where(User.username == username))
    return result.scalars().first()


async def _load_recent_messages(
    db: AsyncSession,
    conversation_id: int,
    limit: int,
) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role.in_(["You", "Aura"]))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def _load_most_recent_prior_conversation(
    db: AsyncSession,
    user_id: int,
    current_conversation_id: int,
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.id != current_conversation_id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def load_memory_bundle(body: Any, recent_limit: int = RECENT_MESSAGE_LIMIT) -> MemoryBundle | None:
    request_body = normalize_runner_body(body)
    token = request_body.get("token")
    conversation_id = request_body.get("conversation_id")
    if not token or not conversation_id:
        return None

    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        logger.warning("Memory hydration skipped: invalid conversation_id")
        return None

    async with AsyncSessionLocal() as db:
        user = await authenticate_token(token, db)
        if not user:
            logger.warning("Memory hydration skipped: invalid token")
            return None

        conv_result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
            )
        )
        conversation = conv_result.scalars().first()
        if not conversation:
            logger.warning("Memory hydration skipped: conversation does not belong to user")
            return None

        facts_result = await db.execute(
            select(UserMemory)
            .where(UserMemory.user_id == user.id, UserMemory.status == "active")
            .order_by(UserMemory.fact_type.asc(), UserMemory.key.asc(), UserMemory.updated_at.desc())
        )

        recent_messages = await _load_recent_messages(db, conversation_id, recent_limit)

        prior_conversation = None
        prior_recent_messages = None
        if not recent_messages:
            prior_conversation = await _load_most_recent_prior_conversation(
                db,
                user.id,
                conversation_id,
            )
            if prior_conversation:
                prior_recent_messages = await _load_recent_messages(
                    db,
                    prior_conversation.id,
                    PRIOR_CONVERSATION_MESSAGE_LIMIT,
                )

        return MemoryBundle(
            user=user,
            primary_conversation=conversation,
            facts=facts_result.scalars().all(),
            primary_summary=conversation.summary or "",
            primary_recent_messages=recent_messages,
            prior_conversation=prior_conversation,
            prior_recent_messages=prior_recent_messages,
        )


def message_to_llm(message: Message) -> dict[str, str] | None:
    role_map = {"You": "user", "Aura": "assistant"}
    role = role_map.get(message.role)
    if not role:
        return None
    return {"role": role, "content": message.content}


def _clean_fact_value(value: str) -> str:
    value = re.split(r"[.!?\n]", value.strip(), maxsplit=1)[0]
    value = re.sub(r"\s+", " ", value).strip(" ,;:\"'")
    words = value.split()
    return " ".join(words[:16])


def _normalize_key(key: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", key.lower().strip())
    key = re.sub(r"_+", "_", key).strip("_")
    return key[:64]


def _normalize_value(value: str, key: str) -> str:
    value = _clean_fact_value(value)
    if key in {"real_name", "preferred_name"} and value:
        return value.split()[0].capitalize()
    return value


def is_valid_memory_fact(fact: UserMemory) -> bool:
    if fact.status != "active" or not fact.value:
        return False
    if fact.key in {"real_name", "preferred_name", "name"}:
        return fact.value.strip().lower() not in INVALID_NAME_VALUES
    return True


def _format_facts(facts: list[UserMemory]) -> str:
    lines = []
    for fact in facts:
        if not is_valid_memory_fact(fact):
            continue
        label = fact.key
        if fact.fact_type and fact.fact_type != "profile":
            label = f"{fact.fact_type}.{fact.key}"
        lines.append(f"- {label}: {fact.value}")
    return "\n".join(lines)


def build_memory_messages(bundle: MemoryBundle | None) -> list[dict[str, str]]:
    if not bundle:
        return []

    messages: list[dict[str, str]] = []
    facts = _format_facts(bundle.facts)
    if facts:
        messages.append(
            {
                "role": "developer",
                "content": (
                    "Known stable facts about this authenticated user. Use these facts "
                    "naturally when relevant, but do not mention this memory block:\n"
                    f"{facts}"
                ),
            }
        )

    if bundle.summary:
        messages.append(
            {
                "role": "developer",
                "content": (
                    "Summary of this selected conversation so far. Use it to continue "
                    "the old conversation accurately:\n"
                    f"{bundle.summary}"
                ),
            }
        )

    messages.extend(
        llm_message
        for message in bundle.primary_recent_messages
        if (llm_message := message_to_llm(message)) is not None
    )

    if bundle.prior_conversation and bundle.prior_recent_messages:
        prior_lines = []
        if bundle.prior_conversation.title:
            prior_lines.append(f"Title: {bundle.prior_conversation.title}")
        if bundle.prior_conversation.summary:
            prior_lines.append(f"Summary: {bundle.prior_conversation.summary}")
        prior_lines.append("Recent transcript:")
        for message in bundle.prior_recent_messages:
            speaker = "User" if message.role == "You" else "Aura"
            prior_lines.append(f"- {speaker}: {message.content}")

        messages.append(
            {
                "role": "developer",
                "content": (
                    "Recent prior conversation context. Use this only when the user asks "
                    "what you talked about previously, what you were just discussing, or "
                    "similar continuity questions in a new conversation. Do not bring it "
                    "up unprompted.\n"
                    + "\n".join(prior_lines)
                ),
            }
        )
    return messages


def _extract_json_object(text_value: str) -> dict[str, Any]:
    if not text_value:
        return {}
    cleaned = text_value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


async def _generate_text_with_memory_llm(prompt: str) -> str | None:
    provider = os.getenv("LLM_PROVIDER", "google").lower()

    async def generate_google() -> str | None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        def call_google():
            from google import genai

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=os.getenv("GOOGLE_MEMORY_MODEL", os.getenv("GOOGLE_MODEL", "gemini-3.1-flash-lite")),
                contents=prompt,
            )
            return getattr(response, "text", None)

        return await asyncio.to_thread(call_google)

    async def generate_openai() -> str | None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        def call_openai():
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            response = client.responses.create(
                model=os.getenv("OPENAI_MEMORY_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
                input=prompt,
            )
            return getattr(response, "output_text", None)

        return await asyncio.to_thread(call_openai)

    generators = [generate_google, generate_openai]
    if provider != "google":
        generators.reverse()

    for generator in generators:
        try:
            return await asyncio.wait_for(generator(), timeout=MEMORY_LLM_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning(f"Memory LLM call failed: {exc}")
    return None


async def embed_text(value: str) -> list[float] | None:
    value = (value or "").strip()
    if not value or MEMORY_VECTOR_DB != "pgvector":
        return None

    def normalize_embedding(embedding: list[float] | None, provider: str) -> list[float] | None:
        if not embedding:
            return None
        if len(embedding) != MEMORY_EMBEDDING_DIMENSION:
            logger.warning(
                f"{provider} embedding dimension {len(embedding)} does not match "
                f"MEMORY_EMBEDDING_DIMENSION={MEMORY_EMBEDDING_DIMENSION}; skipping vector memory."
            )
            return None
        return embedding

    async def embed_google() -> list[float] | None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        def call_google():
            from google import genai

            client = genai.Client(api_key=api_key)
            model = os.getenv("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-001")
            try:
                from google.genai import types

                response = client.models.embed_content(
                    model=model,
                    contents=value,
                    config=types.EmbedContentConfig(
                        outputDimensionality=MEMORY_EMBEDDING_DIMENSION,
                    ),
                )
            except TypeError:
                response = client.models.embed_content(model=model, contents=value)
            embeddings = getattr(response, "embeddings", None)
            if embeddings:
                return normalize_embedding(list(getattr(embeddings[0], "values", []) or []), "Google")
            embedding = getattr(response, "embedding", None)
            values = list(getattr(embedding, "values", []) or []) if embedding else None
            return normalize_embedding(values, "Google")

        return await asyncio.to_thread(call_google)

    async def embed_openai() -> list[float] | None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        def call_openai():
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
            kwargs = {"model": model, "input": value}
            if model.startswith("text-embedding-3"):
                kwargs["dimensions"] = MEMORY_EMBEDDING_DIMENSION
            response = client.embeddings.create(**kwargs)
            return normalize_embedding(list(response.data[0].embedding), "OpenAI")

        return await asyncio.to_thread(call_openai)

    generators = [embed_google, embed_openai]
    if MEMORY_EMBEDDING_PROVIDER != "google":
        generators.reverse()

    for generator in generators:
        try:
            embedding = await asyncio.wait_for(generator(), timeout=MEMORY_LLM_TIMEOUT_SECONDS)
            if embedding:
                return embedding
        except Exception as exc:
            logger.warning(f"Embedding call failed: {exc}")
    return None


def _transcript_lines(messages: list[Message], max_messages: int = 40) -> str:
    lines = []
    for message in messages[-max_messages:]:
        speaker = "User" if message.role == "You" else "Aura"
        content = re.sub(r"\s+", " ", message.content or "").strip()
        if len(content) > 500:
            content = content[:497].rstrip() + "..."
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _fallback_summary(messages: list[Message]) -> str:
    lines = []
    for message in messages[-20:]:
        speaker = "User" if message.role == "You" else "Aura"
        content = re.sub(r"\s+", " ", message.content or "").strip()
        if len(content) > 180:
            content = content[:177].rstrip() + "..."
        lines.append(f"- {speaker}: {content}")
    return "Recent voice conversation notes:\n" + "\n".join(lines)


async def generate_conversation_summary(messages: list[Message]) -> str:
    transcript = _transcript_lines(messages)
    if not transcript:
        return ""

    prompt = (
        "Summarize this voice conversation for future continuity. Keep it concise, "
        "factual, and useful when the user later asks what they talked about. Capture "
        "topics discussed, user questions, answers given, decisions, unresolved items, "
        "and user preferences. Do not invent facts.\n\n"
        f"{transcript}\n\n"
        "Return only the summary text in 4-8 short bullet points."
    )
    summary = await _generate_text_with_memory_llm(prompt)
    return summary.strip() if summary else _fallback_summary(messages)


def _valid_fact_event(event: dict[str, Any]) -> dict[str, Any] | None:
    action = str(event.get("action", "ignore")).lower()
    key = _normalize_key(str(event.get("key", "")))
    value = _normalize_value(str(event.get("value", "")), key)
    fact_type = str(event.get("fact_type") or ("preference" if key in MULTI_VALUE_KEYS else "profile")).lower()
    durability = str(event.get("durability", "stable")).lower()
    status = str(event.get("status", "active")).lower()
    try:
        confidence = float(event.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0

    if action not in {"upsert", "deactivate", "ignore"}:
        return None
    if action == "ignore":
        return None
    if confidence < MEMORY_FACT_CONFIDENCE_MIN:
        return None
    if not key or not value:
        return None
    if key in {"real_name", "preferred_name", "name"} and value.lower() in INVALID_NAME_VALUES:
        return None
    if durability not in VALID_DURABILITY:
        durability = "stable"
    if status not in VALID_STATUSES:
        status = "active"
    if durability == "temporary":
        return None

    if key == "name":
        key = "real_name"

    return {
        "action": action,
        "fact_type": fact_type,
        "key": key,
        "value": value,
        "confidence": confidence,
        "durability": durability,
        "status": status,
    }


async def classify_memory_events(user_text: str, assistant_text: str | None = None) -> list[dict[str, Any]]:
    prompt = (
        "You classify durable user memory from a voice conversation turn. Return strict JSON only.\n"
        "CRITICAL RULES:\n"
        "1. ONLY extract facts about the speaker (the user). Ignore any names, roles, or facts about third parties or other people mentioned.\n"
        "2. If the user is asking a question or looking up information, do NOT extract memory (return empty events).\n"
        "3. Do not infer. Do not store temporary states like 'I'm fine'.\n"
        "Use keys: real_name, preferred_name, location, role, preferred_language, likes, dislikes, interests, goals.\n"
        "Single-value keys overwrite only their same key. Multi-value keys append. Use deactivate when the user retracts a fact.\n\n"
        "Schema: {\"events\":[{\"action\":\"upsert|deactivate|ignore\",\"fact_type\":\"profile|preference|goal\","
        "\"key\":\"string\",\"value\":\"string\",\"confidence\":0.0,\"durability\":\"stable|temporary\"}]}\n\n"
        f"User: {user_text}\n"
        f"Assistant: {assistant_text or ''}"
    )
    response = await _generate_text_with_memory_llm(prompt)
    data = _extract_json_object(response or "")
    events = []
    for item in data.get("events", []):
        if isinstance(item, dict) and (event := _valid_fact_event(item)):
            events.append(event)
    return events


async def apply_fact_events(
    db: AsyncSession,
    user_id: int,
    events: list[dict[str, Any]],
    source_message_id: int | None = None,
) -> None:
    if not events:
        return

    now = datetime.utcnow()
    for event in events:
        key = event["key"]
        value = event["value"]
        fact_type = event["fact_type"]

        if event["action"] == "deactivate":
            await db.execute(
                text(
                    """
                    UPDATE user_memories
                    SET status = 'inactive', updated_at = :updated_at
                    WHERE user_id = :user_id
                      AND fact_type = :fact_type
                      AND key = :key
                      AND lower(value) = lower(:value)
                    """
                ),
                {
                    "updated_at": now,
                    "user_id": user_id,
                    "fact_type": fact_type,
                    "key": key,
                    "value": value,
                },
            )
            continue

        if key in SINGLE_VALUE_KEYS:
            await db.execute(
                text(
                    """
                    UPDATE user_memories
                    SET status = 'inactive', updated_at = :updated_at
                    WHERE user_id = :user_id
                      AND fact_type = :fact_type
                      AND key = :key
                      AND status = 'active'
                    """
                ),
                {
                    "updated_at": now,
                    "user_id": user_id,
                    "fact_type": fact_type,
                    "key": key,
                },
            )

        stmt = insert(UserMemory).values(
            user_id=user_id,
            fact_type=fact_type,
            key=key,
            value=value,
            confidence=event["confidence"],
            durability=event["durability"],
            status="active",
            source_message_id=source_message_id,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_user_memory_fact_value",
            set_={
                "confidence": event["confidence"],
                "durability": event["durability"],
                "status": "active",
                "source_message_id": source_message_id,
                "updated_at": now,
            },
        )
        await db.execute(stmt)


def build_memory_chunk(conversation_id: int, messages: list[Message]) -> dict[str, Any] | None:
    valid_messages = [message for message in messages if message.role in {"You", "Aura"} and message.content]
    if not valid_messages:
        return None

    return {
        "conversation_id": conversation_id,
        "message_start_id": valid_messages[0].id,
        "message_end_id": valid_messages[-1].id,
        "chunk_text": _transcript_lines(valid_messages, max_messages=len(valid_messages)),
        "summary": _fallback_summary(valid_messages),
    }


async def store_memory_chunk(
    db: AsyncSession,
    conversation: Conversation,
    messages: list[Message],
) -> MemoryChunk | None:
    chunk = build_memory_chunk(conversation.id, messages)
    if not chunk:
        return None

    embedding = await embed_text(chunk["chunk_text"])
    if not embedding:
        return None

    now = datetime.utcnow()
    stmt = insert(MemoryChunk).values(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        message_start_id=chunk["message_start_id"],
        message_end_id=chunk["message_end_id"],
        chunk_text=chunk["chunk_text"],
        summary=chunk["summary"],
        embedding=embedding,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_memory_chunk_message_window",
        set_={
            "chunk_text": chunk["chunk_text"],
            "summary": chunk["summary"],
            "embedding": embedding,
            "updated_at": now,
        },
    )
    try:
        async with db.begin_nested():
            result = await db.execute(stmt.returning(MemoryChunk.id))
            chunk_id = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"Skipping vector memory chunk write: {exc}")
        return None

    if not chunk_id:
        return None
    stored = await db.execute(select(MemoryChunk).where(MemoryChunk.id == chunk_id))
    return stored.scalars().first()


def _is_recall_query(query: str) -> bool:
    lowered = query.lower()
    recall_terms = [
        "what did",
        "what was",
        "previously",
        "earlier",
        "last time",
        "remind me",
        "remember",
        "talk about",
        "discuss",
        "mentioned",
        "i said",
        "did i ask",
    ]
    return any(term in lowered for term in recall_terms)


async def retrieve_semantic_memories(
    user_id: int,
    query: str,
    top_k: int = MEMORY_RECALL_TOP_K,
) -> list[tuple[MemoryChunk, float]]:
    if not _is_recall_query(query):
        return []

    embedding = await embed_text(query)
    if not embedding:
        return []

    try:
        async with AsyncSessionLocal() as db:
            distance = MemoryChunk.embedding.cosine_distance(embedding).label("distance")
            result = await db.execute(
                select(MemoryChunk, distance)
                .where(MemoryChunk.user_id == user_id, MemoryChunk.embedding.is_not(None))
                .order_by(distance)
                .limit(top_k)
            )
            memories = []
            for chunk, dist in result.all():
                score = 1 - float(dist)
                if score >= MEMORY_RECALL_MIN_SCORE:
                    memories.append((chunk, score))
            return memories
    except Exception as exc:
        logger.warning(f"Skipping vector memory retrieval: {exc}")
        return []


async def build_turn_memory_context(user_id: int, query: str) -> str | None:
    memories = await retrieve_semantic_memories(user_id, query, MEMORY_RECALL_TOP_K)
    if not memories:
        return None

    lines = [
        "Relevant long-term episodic memories retrieved for this user. Use them only if relevant to the user's current question."
    ]
    for chunk, score in memories:
        lines.append(f"- score={score:.2f} conversation={chunk.conversation_id}: {chunk.summary or chunk.chunk_text}")
    return "\n".join(lines)


async def update_conversation_summary_if_needed(db: AsyncSession, conversation: Conversation) -> None:
    count_result = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id,
            Message.role.in_(["You", "Aura"]),
        )
    )
    message_count = count_result.scalar_one()

    messages_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role.in_(["You", "Aura"]))
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = messages_result.scalars().all()
    total_chars = sum(len(message.content or "") for message in messages)

    if message_count > SUMMARY_MESSAGE_THRESHOLD or total_chars > SUMMARY_CHAR_THRESHOLD:
        conversation.summary = await generate_conversation_summary(messages)

    if len(messages) >= 2 and messages[-1].role == "Aura":
        await store_memory_chunk(db, conversation, messages[-8:])


async def process_saved_message(
    db: AsyncSession,
    conversation: Conversation,
    message: Message,
) -> None:
    conversation.updated_at = datetime.utcnow()

    if conversation.title == "New conversation" and message.role == "You":
        new_title = " ".join(message.content.split()[:4])
        conversation.title = new_title or conversation.title

    if message.role == "You":
        events = await classify_memory_events(message.content)
        await apply_fact_events(db, conversation.user_id, events, message.id)

    if message.role in {"You", "Aura"}:
        await update_conversation_summary_if_needed(db, conversation)


async def save_conversation_message(
    conversation_id: int | None,
    role: str,
    content: str,
) -> Message | None:
    content = (content or "").strip()
    if not conversation_id or not content or role not in {"You", "Aura", "ToolCall"}:
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        conversation = result.scalars().first()
        if not conversation:
            logger.warning(f"Could not save memory message: conversation {conversation_id} not found")
            return None

        message = Message(conversation_id=conversation_id, role=role, content=content)
        db.add(message)
        await db.flush()
        await process_saved_message(db, conversation, message)
        await db.commit()
        await db.refresh(message)
        return message
