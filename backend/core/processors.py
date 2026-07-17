import asyncio
import json
import time
from dataclasses import dataclass
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
    FunctionCallInProgressFrame,
    TTSSpeakFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from services.memory import build_turn_memory_context, save_conversation_message
from services.rag import build_rag_context_with_payload, is_rag_query
from services.rag import should_attempt_rag_retrieval
from core.rag_config import RAG_VOICE_QUERY_WINDOW_SECONDS, RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS


@dataclass
class TurnLatencyState:
    session_id: str | None = None
    turn_id: int = 0
    started_at: float | None = None
    first_llm_seen: bool = False
    first_audio_seen: bool = False
    tool_used: bool = False
    rag_used: bool = False
    first_llm_ms: float | None = None
    active: bool = False

    def start_turn(self):
        if self.active:
            self.emit("final_stt_fragment")
            return
        self.active = True
        self.turn_id += 1
        self.started_at = time.monotonic()
        self.first_llm_seen = False
        self.first_audio_seen = False
        self.tool_used = False
        self.rag_used = False
        self.first_llm_ms = None
        self.emit("final_stt")

    def finish_turn(self):
        self.active = False

    def emit(self, stage: str):
        elapsed = None if self.started_at is None else round((time.monotonic() - self.started_at) * 1000, 1)
        logger.info(
            "voice_latency session={} turn={} stage={} elapsed_ms={}",
            self.session_id, self.turn_id, stage, elapsed,
        )


class LatencyBoundaryProcessor(FrameProcessor):
    def __init__(self, state: TurnLatencyState, boundary: str, **kwargs):
        super().__init__(**kwargs)
        self._state = state
        self._boundary = boundary

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if self._boundary == "stt" and isinstance(frame, TranscriptionFrame):
                self._state.start_turn()
            elif self._boundary == "llm" and isinstance(frame, LLMTextFrame) and not self._state.first_llm_seen:
                self._state.first_llm_seen = True
                self._state.first_llm_ms = round((time.monotonic() - self._state.started_at) * 1000, 1) if self._state.started_at else None
                self._state.emit("first_llm_text")
            elif (
                self._boundary == "tts"
                and isinstance(frame, TTSAudioRawFrame)
                and self._state.first_llm_seen
                and not self._state.first_audio_seen
            ):
                self._state.first_audio_seen = True
                self._state.emit("first_tts_audio")
                total_ms = round((time.monotonic() - self._state.started_at) * 1000, 1) if self._state.started_at else None
                category = "tool" if self._state.tool_used else "rag" if self._state.rag_used else "direct"
                await self.push_frame(
                    OutputTransportMessageFrame({
                        "label": "rtvi-ai",
                        "type": "server-message",
                        "data": {
                            "type": "latency_stats",
                            "payload": {
                                "turn_id": self._state.turn_id,
                                "category": category,
                                "with_tools": self._state.tool_used,
                                "rag_used": self._state.rag_used,
                                "llm_ms": self._state.first_llm_ms,
                                "answer_audio_ms": total_ms,
                            },
                        },
                    }),
                    direction,
                )
        await self.push_frame(frame, direction)

class ConversationMemoryProcessor(FrameProcessor):
    def __init__(self, conversation_id: int | None, capture: str, **kwargs):
        super().__init__(**kwargs)
        self._conversation_id = conversation_id
        self._capture = capture
        self._assistant_chunks: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            direction == FrameDirection.DOWNSTREAM
            and self._capture == "user"
            and isinstance(frame, TranscriptionFrame)
        ):
            from core.task_queue import task_queue
            task_queue.enqueue(save_conversation_message, self._conversation_id, "You", frame.text, key=self._conversation_id)
        elif (
            direction == FrameDirection.DOWNSTREAM
            and self._capture == "assistant"
            and isinstance(frame, LLMTextFrame)
        ):
            self._assistant_chunks.append(frame.text)
        elif (
            direction == FrameDirection.DOWNSTREAM
            and self._capture == "assistant"
            and isinstance(frame, LLMFullResponseEndFrame)
        ):
            assistant_text = "".join(self._assistant_chunks).strip()
            self._assistant_chunks.clear()
            from core.task_queue import task_queue
            task_queue.enqueue(save_conversation_message, self._conversation_id, "Aura", assistant_text, key=self._conversation_id)

        await self.push_frame(frame, direction)


class ToolFillerProcessor(FrameProcessor):
    def __init__(self, latency_state: TurnLatencyState | None = None, **kwargs):
        super().__init__(**kwargs)
        self._latency_state = latency_state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, FunctionCallInProgressFrame):
            if self._latency_state:
                self._latency_state.tool_used = True
            await self.push_frame(TTSSpeakFrame("Let me check my tools.", append_to_context=False), direction)

        await self.push_frame(frame, direction)


class RollingVoiceQueryBuffer:
    def __init__(self, window_seconds: float = RAG_VOICE_QUERY_WINDOW_SECONDS):
        self._window_seconds = window_seconds
        self._items: list[tuple[float, str]] = []

    def add(self, text: str, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        text = (text or "").strip()
        if text:
            self._items.append((now, text))
        self._items = [
            (timestamp, value)
            for timestamp, value in self._items
            if now - timestamp <= self._window_seconds
        ]
        return " ".join(value for _, value in self._items)

    def clear(self) -> None:
        self._items.clear()


class ContextRetrievalProcessor(FrameProcessor):
    def __init__(
        self,
        user_id: int | None,
        conversation_id: int | None,
        context: LLMContext,
        latency_state: TurnLatencyState | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._context = context
        self._latency_state = latency_state
        self._query_buffer = RollingVoiceQueryBuffer()
        self._delivery_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._dynamic_messages: list[dict] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            direction == FrameDirection.DOWNSTREAM
            and self._user_id
            and isinstance(frame, TranscriptionFrame)
        ):
            # Query-specific context from the previous user turn must be gone
            # before this transcription reaches the aggregator. Do not remove
            # new context on LLM end: an interrupted response from an earlier
            # transcription can end after a later fragment has injected RAG.
            self.clear_dynamic_context()
            combined_query = self._query_buffer.add(frame.text)

            needs_memory = any(term in frame.text.lower() for term in ("remember", "previously", "earlier", "last time", "what did"))
            needs_rag = should_attempt_rag_retrieval(combined_query)
            if not needs_memory and not needs_rag:
                logger.info("voice_route route=direct query={!r}", frame.text[:120])
                await self.push_frame(frame, direction)
                return

            if is_rag_query(frame.text):
                await self.push_frame(TTSSpeakFrame("Let me look that up for you.", append_to_context=False), direction)

            task = asyncio.create_task(
                self._retrieve_and_push(frame, combined_query, direction, needs_memory, needs_rag)
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return

        await self.push_frame(frame, direction)

    async def _retrieve_and_push(self, frame, combined_query, direction, needs_memory, needs_rag):
        started = time.monotonic()
        delivered = False
        async with self._delivery_lock:
            try:
                async def retrieve():
                    memory_task = build_turn_memory_context(self._user_id, frame.text) if needs_memory else asyncio.sleep(0, result=None)
                    rag_task = build_rag_context_with_payload(self._user_id, combined_query) if needs_rag else asyncio.sleep(0, result=(None, None))
                    return await asyncio.gather(memory_task, rag_task)

                memory_context, (rag_context, rag_payload) = await asyncio.wait_for(
                    retrieve(), timeout=RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS
                )
            
                for content in (memory_context, rag_context):
                    if content:
                        message = {"role": "developer", "content": content}
                        self._context.add_message(message)
                        self._dynamic_messages.append(message)
                if rag_payload:
                    if self._latency_state:
                        self._latency_state.rag_used = True
                # Dynamic context is installed. Release the inference-critical
                # transcription before shipping/persisting diagnostic payloads.
                await self.push_frame(frame, direction)
                delivered = True
                if rag_payload:
                    from core.task_queue import task_queue
                    task_queue.enqueue(save_conversation_message, self._conversation_id, "RagCall", json.dumps(rag_payload), key=self._conversation_id)
                    await self.push_frame(OutputTransportMessageFrame({"label": "rtvi-ai", "type": "server-message", "data": {"type": "rag_call", "payload": rag_payload}}), direction)
            except TimeoutError:
                logger.warning(
                    "voice_retrieval status=timeout budget_ms={} query={!r}",
                    round(RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS * 1000), combined_query[:120],
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Context retrieval error: {e}")
            finally:
                logger.info(
                    "voice_retrieval status=complete duration_ms={} rag={} memory={} query={!r}",
                    round((time.monotonic() - started) * 1000, 1), needs_rag, needs_memory, combined_query[:120],
                )
                if not delivered:
                    await self.push_frame(frame, direction)

    def clear_dynamic_context(self):
        if self._dynamic_messages:
            ids = {id(message) for message in self._dynamic_messages}
            self._context.messages[:] = [message for message in self._context.messages if id(message) not in ids]
            self._dynamic_messages.clear()

    def finish_response(self):
        self._query_buffer.clear()

    async def cleanup(self):
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await super().cleanup()


class TurnContextCleanupProcessor(FrameProcessor):
    def __init__(self, retrieval: ContextRetrievalProcessor, latency_state: TurnLatencyState | None = None, **kwargs):
        super().__init__(**kwargs)
        self._retrieval = retrieval
        self._latency_state = latency_state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseEndFrame):
            self._retrieval.finish_response()
            if self._latency_state:
                self._latency_state.finish_turn()
        await self.push_frame(frame, direction)
