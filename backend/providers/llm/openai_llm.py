import os

from pipecat.services.openai.llm import OpenAILLMService

from core.prompt_config import load_system_prompt
from core.tool_config import tool_timeout_seconds


def get_openai_llm():
    """Build the sole OpenAI service used by a voice pipeline."""
    return OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            system_instruction=load_system_prompt(),
        ),
        function_call_timeout_secs=tool_timeout_seconds(),
        enable_async_tool_cancellation=True,
        retry_on_timeout=False,
    )
