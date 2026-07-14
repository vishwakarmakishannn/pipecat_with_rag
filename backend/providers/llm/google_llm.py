import os
from pipecat.services.google.llm import GoogleLLMService
from core.prompt_config import load_system_prompt

def get_google_llm():
    return GoogleLLMService(
        api_key=os.getenv("GOOGLE_API_KEY"),
        settings=GoogleLLMService.Settings(
            model=os.getenv("GOOGLE_MODEL", "gemini-3.1-flash-lite"),
            system_instruction=load_system_prompt(),
        ),
    )
