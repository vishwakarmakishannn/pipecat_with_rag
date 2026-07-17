import asyncio
from typing import Callable, Coroutine, Any
from loguru import logger

class BackgroundTaskQueue:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._is_running = False
        self._key_locks: dict[Any, asyncio.Lock] = {}

    async def _worker(self):
        while self._is_running:
            try:
                task_func, args, kwargs, key = await self._queue.get()
                try:
                    if key is None:
                        await task_func(*args, **kwargs)
                    else:
                        lock = self._key_locks.setdefault(key, asyncio.Lock())
                        async with lock:
                            await task_func(*args, **kwargs)
                except Exception as e:
                    logger.exception(f"Error in background task {task_func.__name__}: {e}")
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break

    def start(self, num_workers: int = 3):
        if self._is_running:
            return
        self._is_running = True
        for _ in range(num_workers):
            self._workers.append(asyncio.create_task(self._worker()))
        logger.info(f"Started BackgroundTaskQueue with {num_workers} workers.")

    async def stop(self):
        if not self._is_running:
            return
        await self._queue.join()
        self._is_running = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._key_locks.clear()
        logger.info("Stopped BackgroundTaskQueue.")

    def enqueue(self, task_func: Callable[..., Coroutine[Any, Any, Any]], *args, key=None, **kwargs):
        self._queue.put_nowait((task_func, args, kwargs, key))

task_queue = BackgroundTaskQueue()
