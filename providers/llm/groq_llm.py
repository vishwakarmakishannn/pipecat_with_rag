import os
from pipecat.services.groq.llm import GroqLLMService, GroqLLMSettings
from prompt_config import load_system_prompt

def get_groq_llm():
    return GroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        settings=GroqLLMSettings(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            system_instruction=load_system_prompt(),
        ),
    )
