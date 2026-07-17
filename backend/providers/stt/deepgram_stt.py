import os
import asyncio
from loguru import logger
from deepgram.listen.v1.types.listen_v1keep_alive import ListenV1KeepAlive
from pipecat.services.deepgram.stt import DeepgramSTTService


class ResilientDeepgramSTTService(DeepgramSTTService):
    """Reconnect when the SDK socket can no longer send keepalives."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._keepalive_recovery_task = None
        self._media_recovery_task = None

    def _schedule_keepalive_recovery(self, error: Exception) -> None:
        if self._keepalive_recovery_task and not self._keepalive_recovery_task.done():
            return
        logger.warning("{}: Keepalive failed; scheduling STT reconnect: {!r}", self, error)
        self._keepalive_recovery_task = self.create_task(
            self._request_reconnect(), name=f"{self}::keepalive-reconnect"
        )

    async def _keepalive_handler(self):
        while True:
            await asyncio.sleep(5)
            connection = self._connection
            if not connection:
                continue
            try:
                await connection.send_keep_alive(ListenV1KeepAlive(type="KeepAlive"))
                logger.trace("{}: Sent keepalive", self)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._schedule_keepalive_recovery(error)
                return

    def _schedule_media_recovery(self, error: Exception) -> None:
        if self._media_recovery_task and not self._media_recovery_task.done():
            return
        logger.warning("{}: Media send failed; scheduling follow-up STT reconnect: {!r}", self, error)
        self._media_recovery_task = self.create_task(
            self._recover_after_active_reconnect(), name=f"{self}::media-reconnect"
        )

    async def _recover_after_active_reconnect(self) -> None:
        """Do not let a failed buffered replay strand the replacement socket."""
        active_recovery = self._keepalive_recovery_task
        if active_recovery and active_recovery is not asyncio.current_task():
            try:
                await asyncio.shield(active_recovery)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The follow-up reconnect is still required when the first
                # recovery itself failed.
                pass
        if self._connection is None:
            await self._request_reconnect()

    async def run_stt(self, audio: bytes):
        """Reconnect again when sending buffered/live media fails."""
        connection = self._connection
        if connection:
            try:
                await connection.send_media(audio)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("{}: send_media failed; connection will reconnect: {!r}", self, error)
                if self._connection is connection:
                    self._connection = None
                    self._connection_ready.clear()
                self._schedule_media_recovery(error)
        yield None

    async def _disconnect(self):
        """Tear down even when the broken socket cannot send CloseStream."""
        media_recovery = getattr(self, "_media_recovery_task", None)
        if media_recovery and media_recovery is not asyncio.current_task() and not media_recovery.done():
            await self.cancel_task(media_recovery)
            self._media_recovery_task = None

        if not self._connection_task:
            return

        logger.debug("Disconnecting resilient Deepgram STT")
        self._connection_ready.clear()
        connection = self._connection
        self._connection = None

        if connection:
            try:
                await connection.send_close_stream()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A failed keepalive commonly means all writes fail. The
                # connection task cancellation below is the authoritative
                # teardown; CloseStream is best-effort only.
                logger.warning("{}: CloseStream failed during recovery: {!r}", self, error)

        await self.cancel_task(self._connection_task)
        self._connection_task = None


def get_deepgram_stt():
    return ResilientDeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
