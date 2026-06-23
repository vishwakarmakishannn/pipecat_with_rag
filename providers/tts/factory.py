import os

def get_tts():
    provider = os.getenv("TTS_PROVIDER", "cartesia").lower()
    
    if provider == "cartesia":
        from .cartesia_tts import get_cartesia_tts
        return get_cartesia_tts()
    else:
        raise ValueError(f"Unsupported TTS provider: {provider}")
