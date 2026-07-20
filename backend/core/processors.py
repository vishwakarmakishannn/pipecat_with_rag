import asyncio
import json
import time
from dataclasses import dataclass
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMContextFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallCancelFrame,
    InterruptionFrame,
    TTSSpeakFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from services.memory import build_turn_memory_context, embed_text, save_conversation_message
from services.rag import build_rag_context_with_payload, is_rag_query
from services.rag import should_attempt_rag_retrieval
from core.rag_config import RAG_VOICE_QUERY_WINDOW_SECONDS, RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS
from core.tool_config import tool_filler_delay_seconds
from core.audio_config import (
    trim_tts_leading_silence,
    tts_silence_preroll_ms,
    tts_silence_threshold,
)


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
    speech_turn_open: bool = False
    turn_identity_open: bool = False
    speech_started_at: float | None = None
    speech_stopped_at: float | None = None
    final_stt_at: float | None = None
    stage_times: dict[str, float] | None = None

    def __post_init__(self):
        self.stage_times = {}

    def mark_user_started(self):
        if self.speech_turn_open:
            return
        self.turn_id += 1
        # Response-relative timing is assigned at final STT. Clearing it here
        # prevents this speech event from inheriting the previous turn origin.
        self.started_at = None
        self.active = False
        self.speech_turn_open = True
        self.turn_identity_open = True
        self.speech_started_at = time.monotonic()
        self.speech_stopped_at = None
        self.final_stt_at = None
        self.stage_times = {"user_started": self.speech_started_at}
        self.emit("user_started")

    def mark_user_stopped(self):
        if not self.speech_turn_open and not self.active:
            self.mark_user_started()
        self.speech_stopped_at = time.monotonic()
        self.mark_stage("user_stopped", self.speech_stopped_at)
        self.emit("user_stopped")
        self.speech_turn_open = False

    def mark_stage(self, stage: str, at: float | None = None):
        if self.stage_times is None:
            self.stage_times = {}
        self.stage_times.setdefault(stage, time.monotonic() if at is None else at)

    def start_turn(self):
        if self.active:
            self.mark_stage("final_stt_fragment")
            self.emit("final_stt_fragment")
            return
        if not self.turn_identity_open:
            # Text-only/no-VAD transports still need a stable turn identity.
            self.turn_id += 1
            self.turn_identity_open = True
            self.stage_times = {}
        self.active = True
        self.final_stt_at = time.monotonic()
        self.started_at = self.final_stt_at
        self.mark_stage("final_stt", self.final_stt_at)
        self.first_llm_seen = False
        self.first_audio_seen = False
        self.tool_used = False
        self.rag_used = False
        self.first_llm_ms = None
        self.emit("final_stt")

    def finish_turn(self):
        self.active = False
        self.speech_turn_open = False
        self.turn_identity_open = False

    def telemetry_payload(self) -> dict:
        origin = self.speech_stopped_at or self.final_stt_at or self.started_at
        stages_ms = {}
        if origin is not None:
            stages_ms = {
                name: round((timestamp - origin) * 1000, 1)
                for name, timestamp in (self.stage_times or {}).items()
            }
        speech_ms = None
        if self.speech_started_at is not None and self.speech_stopped_at is not None:
            speech_ms = round((self.speech_stopped_at - self.speech_started_at) * 1000, 1)
        return {
            "basis": "user_stopped" if self.speech_stopped_at is not None else "final_stt",
            "speech_ms": speech_ms,
            "stages_ms": stages_ms,
            "server_emitted_unix_ms": round(time.time() * 1000),
        }

    def emit(self, stage: str):
        if stage in {"user_started", "user_stopped"}:
            origin = self.speech_started_at
        else:
            origin = self.started_at
        elapsed = None if origin is None else round((time.monotonic() - origin) * 1000, 1)
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
        telemetry_frame = None
        if direction == FrameDirection.DOWNSTREAM:
            if self._boundary == "turn" and isinstance(frame, UserStartedSpeakingFrame):
                self._state.mark_user_started()
            elif self._boundary == "turn" and isinstance(frame, UserStoppedSpeakingFrame):
                self._state.mark_user_stopped()
            elif self._boundary == "stt" and isinstance(frame, TranscriptionFrame):
                self._state.start_turn()
            elif self._boundary == "llm" and isinstance(frame, LLMTextFrame) and not self._state.first_llm_seen:
                self._state.first_llm_seen = True
                self._state.first_llm_ms = round((time.monotonic() - self._state.started_at) * 1000, 1) if self._state.started_at else None
                self._state.mark_stage("first_llm_text")
                self._state.emit("first_llm_text")
            elif (
                self._boundary == "tts"
                and isinstance(frame, TTSAudioRawFrame)
                and self._state.first_llm_seen
                and not self._state.first_audio_seen
            ):
                self._state.first_audio_seen = True
                self._state.mark_stage("first_tts_audio")
                self._state.emit("first_tts_audio")
                total_ms = round((time.monotonic() - self._state.started_at) * 1000, 1) if self._state.started_at else None
                category = "tool" if self._state.tool_used else "rag" if self._state.rag_used else "direct"
                telemetry_frame = OutputTransportMessageFrame({
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
                                **self._state.telemetry_payload(),
                            },
                        },
                    })
        await self.push_frame(frame, direction)
        if telemetry_frame:
            await self.push_frame(telemetry_frame, direction)


class LeadingSilenceTrimmerProcessor(FrameProcessor):
    """Remove initial 16-bit PCM silence while preserving a small speech preroll."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        threshold: int | None = None,
        preroll_ms: int | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._enabled = trim_tts_leading_silence() if enabled is None else enabled
        self._threshold = tts_silence_threshold() if threshold is None else threshold
        self._preroll_ms = tts_silence_preroll_ms() if preroll_ms is None else preroll_ms
        self._buffers: dict[str | None, bytearray] = {}
        self._audible_contexts: set[str | None] = set()

    def _reset_context(self, context_id: str | None) -> None:
        self._buffers.pop(context_id, None)
        self._audible_contexts.discard(context_id)

    def _trim_frame(self, frame: TTSAudioRawFrame) -> bool:
        context_id = frame.context_id
        if context_id in self._audible_contexts:
            return True

        buffer = self._buffers.setdefault(context_id, bytearray())
        audio = frame.audio
        first_audible_byte = None
        for offset in range(0, len(audio) - 1, 2):
            sample = int.from_bytes(audio[offset:offset + 2], "little", signed=True)
            if abs(sample) > self._threshold:
                first_audible_byte = offset
                break

        preroll_bytes = int(
            frame.sample_rate * frame.num_channels * 2 * self._preroll_ms / 1000
        )
        if first_audible_byte is None:
            buffer.extend(audio)
            if preroll_bytes == 0:
                buffer.clear()
            elif len(buffer) > preroll_bytes:
                del buffer[:-preroll_bytes]
            return False

        combined = bytes(buffer) + audio
        speech_offset = len(buffer) + first_audible_byte
        start = max(0, speech_offset - preroll_bytes)
        frame.audio = combined[start:]
        frame.num_frames = len(frame.audio) // (frame.num_channels * 2)
        self._buffers.pop(context_id, None)
        self._audible_contexts.add(context_id)
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM or not self._enabled:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStartedFrame):
            self._reset_context(frame.context_id)
            self._buffers[frame.context_id] = bytearray()
        elif isinstance(frame, TTSAudioRawFrame) and not self._trim_frame(frame):
            return
        elif isinstance(frame, TTSStoppedFrame):
            self._reset_context(frame.context_id)
        elif isinstance(frame, InterruptionFrame):
            self._buffers.clear()
            self._audible_contexts.clear()

        await self.push_frame(frame, direction)


class BoundedContextProcessor(FrameProcessor):
    def __init__(
        self,
        context: LLMContext,
        protected_messages: list[dict] | None = None,
        max_messages: int = 24,
        max_chars: int = 18000,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._context = context
        self._protected_ids = {id(message) for message in (protected_messages or [])}
        self._max_messages = max(2, max_messages)
        self._max_chars = max(1000, max_chars)

    @staticmethod
    def _message_chars(message) -> int:
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        if isinstance(content, str):
            return len(content)
        try:
            return len(json.dumps(content, default=str))
        except (TypeError, ValueError):
            return len(str(content))

    def trim(self) -> int:
        messages = self._context.messages
        protected = [message for message in messages if id(message) in self._protected_ids]
        candidates = [message for message in messages if id(message) not in self._protected_ids]

        groups: list[list] = []
        current: list = []
        for message in candidates:
            role = message.get("role") if isinstance(message, dict) else None
            if role == "user" and current:
                groups.append(current)
                current = []
            current.append(message)
        if current:
            groups.append(current)

        selected_groups: list[list] = []
        selected_count = len(protected)
        selected_chars = sum(self._message_chars(message) for message in protected)
        for group in reversed(groups):
            group_count = len(group)
            group_chars = sum(self._message_chars(message) for message in group)
            if selected_groups and (
                selected_count + group_count > self._max_messages
                or selected_chars + group_chars > self._max_chars
            ):
                break
            selected_groups.append(group)
            selected_count += group_count
            selected_chars += group_chars

        selected = protected + [
            message
            for group in reversed(selected_groups)
            for message in group
        ]
        removed = len(messages) - len(selected)
        if removed:
            messages[:] = selected
            logger.info(
                "voice_context status=trimmed removed={} retained={} chars={}",
                removed,
                len(selected),
                selected_chars,
            )
        return removed

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
            self.trim()
        await self.push_frame(frame, direction)


class ToolRoutingProcessor(FrameProcessor):
    SEARCH_TERMS = (
        "search",
        "look up",
        "online",
        "internet",
        "latest",
        "current",
        "today",
        "news",
        "weather",
        "president",
        "prime minister",
        "stock price",
    )
    ISSUE_TERMS = (
        "raise an issue",
        "create an issue",
        "open an issue",
        "file an issue",
        "report a problem",
        "create a ticket",
        "open a ticket",
        "file a complaint",
    )

    def __init__(
        self,
        context: LLMContext,
        search_tool,
        issue_tool,
        retrieval=None,
        latency_state: TurnLatencyState | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._context = context
        self._search_tool = search_tool
        self._issue_tool = issue_tool
        self._retrieval = retrieval
        self._latency_state = latency_state

    @staticmethod
    def _latest_user_text(context: LLMContext) -> str:
        for message in reversed(context.messages):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content", "")
                return content if isinstance(content, str) else json.dumps(content, default=str)
        return ""

    def route(self) -> list:
        text = self._latest_user_text(self._context).lower()
        tools = []
        web_search_resolved = bool(
            self._retrieval and self._retrieval.web_search_resolved
        )
        if any(term in text for term in self.SEARCH_TERMS) and not web_search_resolved:
            tools.append(self._search_tool)
        if any(term in text for term in self.ISSUE_TERMS):
            tools.append(self._issue_tool)
        self._context.set_tools(tools)
        logger.info(
            "voice_tools exposed={} query={!r}",
            [getattr(tool, "__name__", str(tool)) for tool in tools],
            text[:120],
        )
        return tools

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
            tools = self.route()
            if tools:
                if self._latency_state:
                    self._latency_state.tool_used = True
                    self._latency_state.mark_stage("tool_routed")
                filler_emitted = bool(
                    self._retrieval and self._retrieval.tool_filler_emitted
                )
                if not filler_emitted:
                    await self.push_frame(
                        TTSSpeakFrame("Let me check that.", append_to_context=False),
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
        elif (
            direction == FrameDirection.DOWNSTREAM
            and self._capture == "assistant"
            and isinstance(frame, FunctionCallResultFrame)
        ):
            payload = {
                "tool_call_id": frame.tool_call_id,
                "function_name": frame.function_name,
                "arguments": frame.arguments,
                "result": frame.result,
            }
            from core.task_queue import task_queue
            task_queue.enqueue(
                save_conversation_message,
                self._conversation_id,
                "ToolCall",
                json.dumps(payload, default=str),
                key=self._conversation_id,
            )

        await self.push_frame(frame, direction)


class ToolFillerProcessor(FrameProcessor):
    def __init__(
        self,
        latency_state: TurnLatencyState | None = None,
        delay_seconds: float | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._latency_state = latency_state
        self._delay_seconds = tool_filler_delay_seconds() if delay_seconds is None else delay_seconds
        self._active_calls: set[str] = set()
        self._filler_task: asyncio.Task | None = None

    def _cancel_filler(self):
        if self._filler_task and not self._filler_task.done():
            self._filler_task.cancel()
        self._filler_task = None

    async def _delayed_filler(self, direction: FrameDirection):
        try:
            await asyncio.sleep(self._delay_seconds)
            if self._active_calls:
                await self.push_frame(
                    TTSSpeakFrame("Let me check that.", append_to_context=False),
                    direction,
                )
        except asyncio.CancelledError:
            return

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, FunctionCallInProgressFrame):
            proactive_filler = bool(self._latency_state and self._latency_state.tool_used)
            if self._latency_state:
                self._latency_state.tool_used = True
            self._active_calls.add(frame.tool_call_id)
            if not proactive_filler and (not self._filler_task or self._filler_task.done()):
                self._filler_task = asyncio.create_task(self._delayed_filler(direction))
        elif direction == FrameDirection.DOWNSTREAM and isinstance(
            frame,
            (FunctionCallResultFrame, FunctionCallCancelFrame),
        ):
            self._active_calls.discard(frame.tool_call_id)
            if not self._active_calls:
                self._cancel_filler()
        elif isinstance(frame, (InterruptionFrame, LLMTextFrame)):
            self._active_calls.clear()
            self._cancel_filler()

        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._active_calls.clear()
        self._cancel_filler()
        await super().cleanup()


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
        web_search=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._context = context
        self._latency_state = latency_state
        self._web_search = web_search
        self._query_buffer = RollingVoiceQueryBuffer()
        self._delivery_lock = asyncio.Lock()
        self._active_task: asyncio.Task | None = None
        self._retrieval_generation = 0
        self._dynamic_messages: list[dict] = []
        self._web_search_resolved = False
        self._tool_filler_emitted = False

    @property
    def web_search_resolved(self) -> bool:
        return self._web_search_resolved

    @property
    def tool_filler_emitted(self) -> bool:
        return self._tool_filler_emitted

    def _supersede_active_retrieval(self) -> int:
        self._retrieval_generation += 1
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self._active_task = None
        return self._retrieval_generation

    def _is_current_generation(self, generation: int) -> bool:
        return generation == self._retrieval_generation

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
            needs_web = bool(
                self._web_search
                and any(term in combined_query.lower() for term in ToolRoutingProcessor.SEARCH_TERMS)
            )
            if not needs_memory and not needs_rag and not needs_web:
                self._supersede_active_retrieval()
                logger.info("voice_route route=direct query={!r}", frame.text[:120])
                await self.push_frame(frame, direction)
                return

            if needs_web and not self._tool_filler_emitted:
                self._tool_filler_emitted = True
                if self._latency_state:
                    self._latency_state.tool_used = True
                    self._latency_state.mark_stage("tool_started")
                await self.push_frame(
                    TTSSpeakFrame("Let me check that.", append_to_context=False),
                    direction,
                )
            elif is_rag_query(frame.text):
                await self.push_frame(TTSSpeakFrame("Let me look that up for you.", append_to_context=False), direction)

            generation = self._supersede_active_retrieval()
            task = asyncio.create_task(
                self._retrieve_and_push(
                    frame,
                    combined_query,
                    direction,
                    needs_memory,
                    needs_rag,
                    needs_web,
                    generation,
                )
            )
            if self._latency_state:
                self._latency_state.mark_stage("retrieval_queued")
            self._active_task = task
            task.add_done_callback(
                lambda completed: setattr(self, "_active_task", None)
                if self._active_task is completed else None
            )
            return

        await self.push_frame(frame, direction)

    async def _retrieve_and_push(
        self,
        frame,
        combined_query,
        direction,
        needs_memory,
        needs_rag,
        needs_web,
        generation,
    ):
        started = time.monotonic()
        delivered = False
        try:
            async def retrieve_and_deliver():
                nonlocal delivered
                async with self._delivery_lock:
                    if self._latency_state:
                        self._latency_state.mark_stage("retrieval_started")

                    shared_embedding = None
                    if needs_memory and needs_rag:
                        shared_embedding = asyncio.create_task(embed_text(combined_query))
                    memory_task = build_turn_memory_context(
                        self._user_id,
                        combined_query,
                        query_embedding=shared_embedding,
                        current_conversation_id=self._conversation_id,
                    ) if needs_memory else asyncio.sleep(0, result=None)
                    rag_task = build_rag_context_with_payload(
                        self._user_id,
                        combined_query,
                        query_embedding=shared_embedding,
                    ) if needs_rag else asyncio.sleep(0, result=(None, None))
                    web_task = self._web_search(combined_query) if needs_web else asyncio.sleep(0, result=None)
                    memory_context, (rag_context, rag_payload), web_payload = await asyncio.gather(
                        memory_task,
                        rag_task,
                        web_task,
                    )
                    if not self._is_current_generation(generation):
                        logger.info(
                            "voice_retrieval status=superseded generation={} query={!r}",
                            generation,
                            combined_query[:120],
                        )
                        return None
                    if self._latency_state:
                        self._latency_state.mark_stage("retrieval_finished")

                    for content in (memory_context, rag_context):
                        if content:
                            message = {"role": "developer", "content": content}
                            self._context.add_message(message)
                            self._dynamic_messages.append(message)
                    if needs_web and web_payload is not None:
                        web_context = (
                            "Live web search result for the current user request. "
                            "Use this result directly, be concise, and mention when live data was unavailable:\n"
                            + json.dumps(web_payload, default=str)
                        )
                        message = {"role": "developer", "content": web_context}
                        self._context.add_message(message)
                        self._dynamic_messages.append(message)
                        self._web_search_resolved = True
                        if self._latency_state:
                            self._latency_state.mark_stage("tool_finished")
                    if rag_payload and self._latency_state:
                        self._latency_state.rag_used = True
                    await self.push_frame(frame, direction)
                    delivered = True
                    return rag_payload

            # The deadline covers delivery-lock queueing, provider work,
            # context installation, and release of the transcription.
            rag_payload = await asyncio.wait_for(
                retrieve_and_deliver(),
                timeout=RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS,
            )
            # Diagnostics are deliberately outside the critical section.
            if rag_payload and self._is_current_generation(generation):
                from core.task_queue import task_queue
                task_queue.enqueue(save_conversation_message, self._conversation_id, "RagCall", json.dumps(rag_payload), key=self._conversation_id)
                await self.push_frame(OutputTransportMessageFrame({"label": "rtvi-ai", "type": "server-message", "data": {"type": "rag_call", "payload": rag_payload}}), direction)
        except TimeoutError:
            logger.warning(
                "voice_retrieval status=timeout budget_ms={} query={!r}",
                round(RAG_VOICE_RETRIEVAL_TIMEOUT_SECONDS * 1000), combined_query[:120],
            )
        except asyncio.CancelledError:
            logger.info(
                "voice_retrieval status=cancelled generation={} query={!r}",
                generation,
                combined_query[:120],
            )
            raise
        except Exception as e:
            logger.error(f"Context retrieval error: {e}")
        finally:
            logger.info(
                "voice_retrieval status=complete duration_ms={} rag={} memory={} query={!r}",
                round((time.monotonic() - started) * 1000, 1), needs_rag, needs_memory, combined_query[:120],
            )
            if not delivered and self._is_current_generation(generation):
                await self.push_frame(frame, direction)

    def clear_dynamic_context(self):
        if self._dynamic_messages:
            ids = {id(message) for message in self._dynamic_messages}
            self._context.messages[:] = [message for message in self._context.messages if id(message) not in ids]
            self._dynamic_messages.clear()

    def start_user_turn(self) -> None:
        """Reset query-scoped state at the aggregator's authoritative boundary."""
        self._supersede_active_retrieval()
        self._query_buffer.clear()
        self.clear_dynamic_context()
        self._web_search_resolved = False
        self._tool_filler_emitted = False

    def finish_response(self):
        # Response completion is not a user-turn boundary: an interrupted old
        # response may finish after barge-in has already started a new turn.
        pass

    async def cleanup(self):
        task = self._active_task
        self._supersede_active_retrieval()
        if task:
            await asyncio.gather(task, return_exceptions=True)
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
