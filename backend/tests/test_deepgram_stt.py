import asyncio

import pytest

from providers.stt.deepgram_stt import ResilientDeepgramSTTService


class _FailingConnection:
    async def send_keep_alive(self, _message):
        raise AssertionError("socket drain failed")

    async def send_close_stream(self):
        raise AssertionError("socket close failed")

    async def send_media(self, _audio):
        raise AssertionError("socket media failed")


@pytest.mark.anyio
async def test_keepalive_failure_schedules_one_reconnect(monkeypatch):
    service = object.__new__(ResilientDeepgramSTTService)
    service._name = "TestDeepgramSTT"
    service._connection = _FailingConnection()
    service._connection_ready = asyncio.Event()
    service._connection_ready.set()
    service._keepalive_recovery_task = None
    reconnects = 0

    async def reconnect():
        nonlocal reconnects
        reconnects += 1

    def create_task(coro, name=None):
        return asyncio.create_task(coro, name=name)

    async def no_wait(_seconds):
        return None

    service._request_reconnect = reconnect
    service.create_task = create_task
    monkeypatch.setattr("providers.stt.deepgram_stt.asyncio.sleep", no_wait)

    await service._keepalive_handler()
    await service._keepalive_recovery_task

    assert reconnects == 1
    assert service._connection_ready.is_set()


@pytest.mark.anyio
async def test_disconnect_continues_when_close_stream_fails():
    service = object.__new__(ResilientDeepgramSTTService)
    service._name = "TestDeepgramSTT"
    service._connection = _FailingConnection()
    service._connection_ready = asyncio.Event()
    service._connection_ready.set()
    service._connection_task = object()
    cancelled = []

    async def cancel_task(task):
        cancelled.append(task)

    service.cancel_task = cancel_task

    await service._disconnect()

    assert len(cancelled) == 1
    assert service._connection is None
    assert service._connection_task is None
    assert not service._connection_ready.is_set()


@pytest.mark.anyio
async def test_media_failure_reconnects_after_active_keepalive_recovery():
    service = object.__new__(ResilientDeepgramSTTService)
    service._name = "TestDeepgramSTT"
    service._connection = _FailingConnection()
    service._connection_ready = asyncio.Event()
    service._connection_ready.set()
    service._media_recovery_task = None
    first_recovery_finished = asyncio.Event()
    reconnects = 0

    async def active_recovery():
        await first_recovery_finished.wait()

    service._keepalive_recovery_task = asyncio.create_task(active_recovery())

    async def reconnect():
        nonlocal reconnects
        reconnects += 1

    service._request_reconnect = reconnect
    service.create_task = lambda coro, name=None: asyncio.create_task(coro, name=name)

    yielded = [item async for item in service.run_stt(b"audio")]
    assert yielded == [None]
    assert service._connection is None
    assert reconnects == 0

    first_recovery_finished.set()
    await service._media_recovery_task
    assert reconnects == 1


@pytest.mark.anyio
async def test_media_failure_schedules_only_one_follow_up_reconnect():
    service = object.__new__(ResilientDeepgramSTTService)
    service._name = "TestDeepgramSTT"
    service._connection = _FailingConnection()
    service._connection_ready = asyncio.Event()
    service._connection_ready.set()
    service._keepalive_recovery_task = None
    service._media_recovery_task = None
    release_reconnect = asyncio.Event()
    reconnects = 0

    async def reconnect():
        nonlocal reconnects
        reconnects += 1
        await release_reconnect.wait()

    service._request_reconnect = reconnect
    service.create_task = lambda coro, name=None: asyncio.create_task(coro, name=name)

    [item async for item in service.run_stt(b"first")]
    first_task = service._media_recovery_task
    service._connection = _FailingConnection()
    [item async for item in service.run_stt(b"second")]

    assert service._media_recovery_task is first_task
    release_reconnect.set()
    await first_task
    assert reconnects == 1
