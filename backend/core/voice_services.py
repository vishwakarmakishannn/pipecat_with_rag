import asyncio
from collections.abc import Callable
from typing import Any


async def initialize_voice_services(
    stt_factory: Callable[[], Any],
    tts_factory: Callable[[], Any],
    llm_factory: Callable[[], Any],
) -> tuple[Any, Any, Any]:
    """Construct independent, potentially blocking services concurrently."""
    constructors = [
        asyncio.create_task(asyncio.to_thread(factory))
        for factory in (stt_factory, tts_factory, llm_factory)
    ]
    stt, tts, llm = await asyncio.gather(*constructors)
    return stt, tts, llm
