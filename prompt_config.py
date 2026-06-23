import os
from pathlib import Path


DEFAULT_SYSTEM_PROMPT = (
    "You are Aura Voice, a friendly, witty, and concise conversational AI. "
    "Your responses will be spoken aloud, so avoid formatting that does not "
    "sound natural in speech. Keep answers brief and conversational."
)


def load_system_prompt() -> str:
    prompt_path = Path(os.getenv("SYSTEM_PROMPT_FILE", "prompts/system_prompt.txt"))
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT
    return prompt or DEFAULT_SYSTEM_PROMPT
