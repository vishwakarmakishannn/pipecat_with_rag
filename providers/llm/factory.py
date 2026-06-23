import os

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "google").lower()
    
    if provider == "google":
        from .google_llm import get_google_llm
        return get_google_llm()
    elif provider == "groq":
        from .groq_llm import get_groq_llm
        return get_groq_llm()
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
