import os
from pipecat.services.cartesia.tts import CartesiaTTSService
from .config import get_text_aggregation_mode
from core.audio_config import audio_output_sample_rate

def get_cartesia_tts():
    return CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        sample_rate=audio_output_sample_rate("cartesia"),
        settings=CartesiaTTSService.Settings(
            voice=os.getenv("CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121"),
        ),
        text_aggregation_mode=get_text_aggregation_mode("cartesia"),
    )
