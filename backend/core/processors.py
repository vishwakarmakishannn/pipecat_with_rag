import asyncio
import json
import time
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
    FunctionCallInProgressFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from services.memory import build_turn_memory_context, save_conversation_message
from services.rag import build_rag_context_with_payload, is_rag_query
from core.rag_config import RAG_VOICE_QUERY_WINDOW_SECONDS

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
            task_queue.enqueue(save_conversation_message, self._conversation_id, "You", frame.text)
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
            task_queue.enqueue(save_conversation_message, self._conversation_id, "Aura", assistant_text)

        await self.push_frame(frame, direction)


class ToolFillerProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, FunctionCallInProgressFrame):
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
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._context = context
        self._query_buffer = RollingVoiceQueryBuffer()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            direction == FrameDirection.DOWNSTREAM
            and self._user_id
            and isinstance(frame, TranscriptionFrame)
        ):
            combined_query = self._query_buffer.add(frame.text)
            
            if is_rag_query(frame.text):
                await self.push_frame(TTSSpeakFrame("Let me look that up for you.", append_to_context=False), direction)
            
            # Spawn background task instead of blocking process_frame
            asyncio.create_task(self._retrieve_and_push(frame, combined_query, direction))
            return # DO NOT push the frame yet, we will push it when the context is ready
            
        elif direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseEndFrame):
            self._query_buffer.clear()

        await self.push_frame(frame, direction)

    async def _retrieve_and_push(self, frame: TranscriptionFrame, combined_query: str, direction: FrameDirection):
        try:
            memory_task = asyncio.create_task(build_turn_memory_context(self._user_id, frame.text))
            rag_task = asyncio.create_task(build_rag_context_with_payload(self._user_id, combined_query))
            
            memory_context, (rag_context, rag_payload) = await asyncio.gather(memory_task, rag_task)
            
            if memory_context:
                self._context.add_message({"role": "developer", "content": memory_context})
            if rag_context:
                self._context.add_message({"role": "developer", "content": rag_context})
            if rag_payload:
                from core.task_queue import task_queue
                task_queue.enqueue(
                    save_conversation_message,
                    self._conversation_id,
                    "RagCall",
                    json.dumps(rag_payload),
                )
                await self.push_frame(
                    OutputTransportMessageFrame(
                        {
                            "label": "rtvi-ai",
                            "type": "server-message",
                            "data": {
                                "type": "rag_call",
                                "payload": rag_payload,
                            },
                        }
                    ),
                    direction,
                )
        except Exception as e:
            logger.error(f"Context retrieval error: {e}")
        finally:
            # Push the original transcription frame down to the LLM
            await self.push_frame(frame, direction)
