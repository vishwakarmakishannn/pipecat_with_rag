import os
from pipecat.services.deepgram.stt import DeepgramSTTService

def get_deepgram_stt():
    return DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
