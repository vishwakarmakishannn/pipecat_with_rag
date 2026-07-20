import pytest
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from core.processors import ToolRoutingProcessor, TurnLatencyState


async def search_tool(params):
    return None


async def issue_tool(params):
    return None


def _router(text):
    context = LLMContext(messages=[{"role": "user", "content": text}])
    return context, ToolRoutingProcessor(context, search_tool, issue_tool)


def test_normal_turn_exposes_no_tools():
    context, router = _router("Tell me a short joke")
    assert router.route() == []
    assert repr(context.tools) == "NOT_GIVEN"


def test_current_information_turn_exposes_only_search():
    _context, router = _router("Look up the latest weather online")
    assert router.route() == [search_tool]


def test_explicit_issue_turn_exposes_only_issue_tool():
    _context, router = _router("Please create an issue for this failure")
    assert router.route() == [issue_tool]


@pytest.mark.anyio
async def test_tool_route_emits_immediate_filler_before_llm_context(monkeypatch):
    context = LLMContext(messages=[{"role": "user", "content": "Create an issue for this"}])
    state = TurnLatencyState(session_id="test")
    state.start_turn()
    processor = ToolRoutingProcessor(context, search_tool, issue_tool, latency_state=state)
    delivered = []

    async def capture(frame, _direction):
        delivered.append(frame)

    monkeypatch.setattr(processor, "push_frame", capture)
    context_frame = LLMContextFrame(context)
    await processor.process_frame(context_frame, FrameDirection.DOWNSTREAM)

    assert isinstance(delivered[0], TTSSpeakFrame)
    assert delivered[0].append_to_context is False
    assert delivered[1] is context_frame
    assert state.tool_used is True
