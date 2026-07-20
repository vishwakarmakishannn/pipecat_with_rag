import os
from pipecat.services.deepgram.tts import DeepgramTTSService
from .config import get_text_aggregation_mode
from core.audio_config import audio_output_sample_rate

def get_deepgram_tts():
    voice = os.getenv("DEEPGRAM_VOICE_ID", "aura-2-thalia-en")
    return DeepgramTTSService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        sample_rate=audio_output_sample_rate("deepgram"),
        settings=DeepgramTTSService.Settings(voice=voice),
        text_aggregation_mode=get_text_aggregation_mode("deepgram"),
    )
