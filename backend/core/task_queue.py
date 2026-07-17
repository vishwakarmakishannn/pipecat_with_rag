import asyncio
from typing import Callable, Coroutine, Any
from loguru import logger

class BackgroundTaskQueue:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._is_running = False

    async def _worker(self):
        while self._is_running:
            try:
                task_func, args, kwargs = await self._queue.get()
                try:
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
        self._is_running = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Stopped BackgroundTaskQueue.")

    def enqueue(self, task_func: Callable[..., Coroutine[Any, Any, Any]], *args, **kwargs):
        self._queue.put_nowait((task_func, args, kwargs))

task_queue = BackgroundTaskQueue()
