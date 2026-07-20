import asyncio

import pytest
from pipecat.frames.frames import FunctionCallInProgressFrame, FunctionCallResultFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection

from core.processors import ToolFillerProcessor, TurnLatencyState


@pytest.mark.anyio
async def test_fast_tool_cancels_filler_before_it_speaks(monkeypatch):
    frames = []
    processor = ToolFillerProcessor(delay_seconds=0.02)

    async def capture(frame, _direction):
        frames.append(frame)

    monkeypatch.setattr(processor, "push_frame", capture)
    started = FunctionCallInProgressFrame("search", "1", {})
    result = FunctionCallResultFrame("search", "1", {}, {"ok": True})
    await processor.process_frame(started, FrameDirection.DOWNSTREAM)
    await processor.process_frame(result, FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.03)

    assert not any(isinstance(frame, TTSSpeakFrame) for frame in frames)


@pytest.mark.anyio
async def test_slow_tool_gets_one_delayed_filler(monkeypatch):
    frames = []
    processor = ToolFillerProcessor(delay_seconds=0.01)

    async def capture(frame, _direction):
        frames.append(frame)

    monkeypatch.setattr(processor, "push_frame", capture)
    await processor.process_frame(
        FunctionCallInProgressFrame("search", "1", {}),
        FrameDirection.DOWNSTREAM,
    )
    await asyncio.sleep(0.02)

    assert len([frame for frame in frames if isinstance(frame, TTSSpeakFrame)]) == 1


@pytest.mark.anyio
async def test_proactively_acknowledged_tool_does_not_emit_duplicate_filler(monkeypatch):
    frames = []
    state = TurnLatencyState(session_id="test")
    state.tool_used = True
    processor = ToolFillerProcessor(latency_state=state, delay_seconds=0.01)

    async def capture(frame, _direction):
        frames.append(frame)

    monkeypatch.setattr(processor, "push_frame", capture)
    await processor.process_frame(
        FunctionCallInProgressFrame("search", "1", {}),
        FrameDirection.DOWNSTREAM,
    )
    await asyncio.sleep(0.02)

    assert not any(isinstance(frame, TTSSpeakFrame) for frame in frames)
