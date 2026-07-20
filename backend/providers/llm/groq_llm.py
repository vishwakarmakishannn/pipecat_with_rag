import os
from pipecat.services.groq.llm import GroqLLMService, GroqLLMSettings
from core.prompt_config import load_system_prompt
from core.tool_config import tool_timeout_seconds

def get_groq_llm():
    client_kwargs = {
        "extra_body": {
            "parallel_tool_calls": False 
        }
    }
    return GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        settings=GroqLLMSettings(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            system_instruction=load_system_prompt(),
            temperature=0.0,
        ),
        client_kwargs=client_kwargs,
        function_call_timeout_secs=tool_timeout_seconds(),
        enable_async_tool_cancellation=True,
    )
