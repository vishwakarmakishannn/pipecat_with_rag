import os
from pipecat.services.deepgram.tts import DeepgramTTSService

def get_deepgram_tts():
    voice = os.getenv("DEEPGRAM_VOICE_ID", "aura-asteria-en")
    return DeepgramTTSService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        settings=DeepgramTTSService.Settings(voice=voice),
    )
