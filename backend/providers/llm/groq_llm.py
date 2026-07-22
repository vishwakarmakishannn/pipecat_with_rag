import os
import asyncio
from loguru import logger
from openai import BadRequestError
from pipecat.services.groq.llm import GroqLLMService, GroqLLMSettings
from core.llm_config import first_token_timeout_seconds, total_timeout_seconds
from core.prompt_config import load_system_prompt
from core.tool_config import tool_timeout_seconds
from .stream_timeout import LLMStreamDeadlineError, bounded_openai_stream


class LatencyBoundGroqLLMService(GroqLLMService):
    async def get_chat_completions(self, context):
        """Apply deadlines at Pipecat's actual Groq request hook.

        Groq sometimes invents a tool name that was not included in the
        request. Its API rejects the entire completion before streaming. Retry
        that provider-specific validation failure once with tools disabled so
        the user still receives a spoken answer.
        """
        first_timeout = first_token_timeout_seconds()
        total_timeout = total_timeout_seconds()
        started = asyncio.get_running_loop().time()
        try:
            stream = await asyncio.wait_for(
                super().get_chat_completions(context), timeout=first_timeout
            )
        except BadRequestError as exc:
            if "tool call validation failed" not in str(exc).lower():
                raise
            configured_tools = context.tools
            logger.warning(
                "voice_llm provider=groq status=invalid_tool_call action=retry_without_tools"
            )
            context.set_tools()
            try:
                stream = await asyncio.wait_for(
                    super().get_chat_completions(context), timeout=first_timeout
                )
            finally:
                context.set_tools(configured_tools)
        except TimeoutError as exc:
            raise LLMStreamDeadlineError("Groq stream creation deadline exceeded") from exc
        elapsed = asyncio.get_running_loop().time() - started
        return bounded_openai_stream(
            stream,
            max(0.001, first_timeout - elapsed),
            max(0.001, total_timeout - elapsed),
        )

def get_groq_llm():
    client_kwargs = {
        "extra_body": {
            "parallel_tool_calls": False 
        }
    }
    return LatencyBoundGroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        settings=GroqLLMSettings(
            model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            system_instruction=load_system_prompt(),
        ),
        client_kwargs=client_kwargs,
        function_call_timeout_secs=tool_timeout_seconds(),
        enable_async_tool_cancellation=True,
    )
