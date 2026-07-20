"""Fail-fast capacity control for latency-sensitive voice sessions."""

import asyncio
import os


class VoiceAdmissionController:
    """Bound concurrent voice pipelines without queueing callers."""

    def __init__(self, limit: int | None = None):
        configured = limit if limit is not None else int(os.getenv("VOICE_MAX_CONCURRENT_SESSIONS", "8"))
        if configured < 1:
            raise ValueError("VOICE_MAX_CONCURRENT_SESSIONS must be at least 1")
        self.limit = configured
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    @property
    def has_capacity(self) -> bool:
        return self._active < self.limit

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active <= 0:
                raise RuntimeError("voice admission release without an active lease")
            self._active -= 1


voice_admission = VoiceAdmissionController()
