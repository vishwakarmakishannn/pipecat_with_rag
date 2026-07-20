import os


def tool_timeout_seconds() -> float:
    raw = os.getenv("VOICE_TOOL_TIMEOUT_SECONDS", "5")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"VOICE_TOOL_TIMEOUT_SECONDS must be a number, got {raw!r}") from exc
    if not 0.25 <= value <= 30:
        raise ValueError(
            f"VOICE_TOOL_TIMEOUT_SECONDS must be between 0.25 and 30, got {value}"
        )
    return value


def tool_filler_delay_seconds() -> float:
    raw = os.getenv("VOICE_TOOL_FILLER_DELAY_MS", "350")
    try:
        value_ms = float(raw)
    except ValueError as exc:
        raise ValueError(f"VOICE_TOOL_FILLER_DELAY_MS must be a number, got {raw!r}") from exc
    if not 0 <= value_ms <= 5000:
        raise ValueError(
            f"VOICE_TOOL_FILLER_DELAY_MS must be between 0 and 5000, got {value_ms}"
        )
    return value_ms / 1000
