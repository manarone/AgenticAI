from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from libs.common.config import get_settings
from libs.common.schemas import TaskEnvelope, TaskResult


class RedisTaskBus:
    def __init__(self) -> None:
        settings = get_settings()
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.task_stream = settings.task_stream
        self.result_stream = settings.result_stream
        self.cancel_stream = settings.cancel_stream
        self.group = 'executor-group'

    async def publish_task(self, task: TaskEnvelope) -> str:
        return await self.redis.xadd(self.task_stream, {'payload': task.model_dump_json()})

    async def publish_result(self, result: TaskResult) -> str:
        return await self.redis.xadd(self.result_stream, {'payload': result.model_dump_json()})

    async def publish_cancel(self, task_id: str) -> str:
        return await self.redis.xadd(self.cancel_stream, {'task_id': task_id, 'created_at': datetime.utcnow().isoformat()})

    async def ensure_consumer_group(self, stream: str) -> None:
        try:
            await self.redis.xgroup_create(stream, self.group, id='0', mkstream=True)
        except ResponseError as exc:
            # Ignore idempotent "group already exists" failures.
            if 'BUSYGROUP' not in str(exc):
                raise

    async def read_tasks(self, consumer_name: str, count: int = 10, block_ms: int = 1000) -> list[tuple[str, TaskEnvelope]]:
        await self.ensure_consumer_group(self.task_stream)
        messages = await self.redis.xreadgroup(
            groupname=self.group,
            consumername=consumer_name,
            streams={self.task_stream: '>'},
            count=count,
            block=block_ms,
        )
        parsed: list[tuple[str, TaskEnvelope]] = []
        for _, entries in messages:
            for msg_id, values in entries:
                parsed.append((msg_id, TaskEnvelope.model_validate_json(values['payload'])))
        return parsed

    async def ack_task(self, message_id: str) -> None:
        await self.redis.xack(self.task_stream, self.group, message_id)

    async def read_results(self, consumer_name: str, count: int = 10, block_ms: int = 1000) -> list[tuple[str, TaskResult]]:
        await self.ensure_consumer_group(self.result_stream)
        messages = await self.redis.xreadgroup(
            groupname=self.group,
            consumername=consumer_name,
            streams={self.result_stream: '>'},
            count=count,
            block=block_ms,
        )
        parsed: list[tuple[str, TaskResult]] = []
        for _, entries in messages:
            for msg_id, values in entries:
                parsed.append((msg_id, TaskResult.model_validate_json(values['payload'])))
        return parsed

    async def ack_result(self, message_id: str) -> None:
        await self.redis.xack(self.result_stream, self.group, message_id)

    async def is_canceled(self, task_id: str) -> bool:
        # Redis-backed cancellation is source-of-truth in Postgres status for MVP.
        return False

    async def close(self) -> None:
        await self.redis.aclose()


class InMemoryTaskBus:
    def __init__(self) -> None:
        self.tasks: asyncio.Queue[str] = asyncio.Queue()
        self.results: asyncio.Queue[str] = asyncio.Queue()
        self.cancels: set[str] = set()
        self._task_messages: dict[str, str] = {}
        self._result_messages: dict[str, str] = {}
        self._counter = defaultdict(int)

    def _next_id(self, stream: str) -> str:
        self._counter[stream] += 1
        return f'{self._counter[stream]}-0'

    async def publish_task(self, task: TaskEnvelope) -> str:
        msg_id = self._next_id('tasks')
        payload = json.dumps({'id': msg_id, 'payload': task.model_dump(mode='json')})
        await self.tasks.put(payload)
        return msg_id

    async def publish_result(self, result: TaskResult) -> str:
        msg_id = self._next_id('results')
        payload = json.dumps({'id': msg_id, 'payload': result.model_dump(mode='json')})
        await self.results.put(payload)
        return msg_id

    async def publish_cancel(self, task_id: str) -> str:
        self.cancels.add(task_id)
        return self._next_id('cancels')

    async def read_tasks(self, consumer_name: str, count: int = 10, block_ms: int = 1000) -> list[tuple[str, TaskEnvelope]]:
        items: list[tuple[str, TaskEnvelope]] = []
        for _ in range(count):
            if self.tasks.empty():
                break
            raw = await self.tasks.get()
            parsed = json.loads(raw)
            msg_id = parsed['id']
            task = TaskEnvelope.model_validate(parsed['payload'])
            self._task_messages[msg_id] = raw
            items.append((msg_id, task))
        if not items:
            await asyncio.sleep(block_ms / 1000)
        return items

    async def ack_task(self, message_id: str) -> None:
        self._task_messages.pop(message_id, None)

    async def read_results(self, consumer_name: str, count: int = 10, block_ms: int = 1000) -> list[tuple[str, TaskResult]]:
        items: list[tuple[str, TaskResult]] = []
        for _ in range(count):
            if self.results.empty():
                break
            raw = await self.results.get()
            parsed = json.loads(raw)
            msg_id = parsed['id']
            result = TaskResult.model_validate(parsed['payload'])
            self._result_messages[msg_id] = raw
            items.append((msg_id, result))
        if not items:
            await asyncio.sleep(block_ms / 1000)
        return items

    async def ack_result(self, message_id: str) -> None:
        self._result_messages.pop(message_id, None)

    async def is_canceled(self, task_id: str) -> bool:
        return task_id in self.cancels


_INMEMORY_BUS: InMemoryTaskBus | None = None
_REDIS_BUS: RedisTaskBus | None = None


def get_task_bus():
    settings = get_settings()
    if settings.bus_backend == 'inmemory':
        global _INMEMORY_BUS
        if _INMEMORY_BUS is None:
            _INMEMORY_BUS = InMemoryTaskBus()
        return _INMEMORY_BUS
    global _REDIS_BUS
    if _REDIS_BUS is None:
        _REDIS_BUS = RedisTaskBus()
    return _REDIS_BUS


async def reset_inmemory_bus() -> None:
    global _INMEMORY_BUS, _REDIS_BUS
    _INMEMORY_BUS = None
    if _REDIS_BUS is not None:
        await _REDIS_BUS.close()
    _REDIS_BUS = None
