import os
from pipecat.services.piper.tts import PiperTTSService

def get_piper_tts():
    voice = os.getenv("PIPER_VOICE_ID", "en_US-lessac-medium")
    return PiperTTSService(
        download_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "piper_model"),
        settings=PiperTTSService.Settings(
            voice=voice
        )
    )
