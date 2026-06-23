import os
from pipecat.services.piper.tts import PiperTTSService

def get_piper_tts():
    voice = os.getenv("PIPER_VOICE_ID", "en_US-lessac-medium")
    return PiperTTSService(
        settings=PiperTTSService.Settings(
            voice=voice
        )
    )
