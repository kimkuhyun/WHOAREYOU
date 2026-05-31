"""작업 진행도 버스.

- 작업 단위(task_id) 별 asyncio.Queue 팬아웃: 1개 task에 N개 WebSocket이 붙어도 OK.
- 다른 프로세스(Arq 워커)에서 발행되는 이벤트도 받기 위해 Redis pub/sub 미러를 옵션으로 제공.
  Redis 미가용 환경에서는 in-memory만으로 동작.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    task_id: str
    stage: str
    pct: int
    message: str = ""
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class ProgressBus:
    """프로세스 내 팬아웃 + 선택적 Redis pub/sub 미러."""

    REDIS_CHANNEL = "whoareyou:progress"

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[ProgressEvent]]] = {}
        self._lock = asyncio.Lock()
        self._redis = None
        self._redis_task: asyncio.Task | None = None

    def new_task_id(self, prefix: str = "task") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    async def attach_redis(self, redis_url: str) -> None:
        """Redis pub/sub 구독을 활성화. 워커 측이 발행한 이벤트도 in-memory 큐로 흘려보낸다."""
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            await self._redis.ping()
        except Exception as exc:
            logger.warning("Redis 연결 실패 — in-memory only로 진행: %s", exc)
            self._redis = None
            return
        self._redis_task = asyncio.create_task(self._redis_subscriber_loop())

    async def _redis_subscriber_loop(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.REDIS_CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                    event = ProgressEvent(**data)
                except Exception:
                    continue
                await self._fanout(event)
        except asyncio.CancelledError:
            pass
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(self.REDIS_CHANNEL)
                await pubsub.aclose()

    async def stop(self) -> None:
        if self._redis_task:
            self._redis_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._redis_task
            self._redis_task = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    async def publish(self, event: ProgressEvent) -> None:
        await self._fanout(event)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.publish(self.REDIS_CHANNEL, event.to_json())

    async def _fanout(self, event: ProgressEvent) -> None:
        async with self._lock:
            queues = list(self._subs.get(event.task_id, set()))
        for q in queues:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def subscribe(self, task_id: str) -> AsyncIterator[ProgressEvent]:
        q: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subs.setdefault(task_id, set()).add(q)
        try:
            while True:
                event = await q.get()
                yield event
                if event.stage in ("done", "error", "cancelled") and event.pct >= 100:
                    break
        finally:
            async with self._lock:
                subs = self._subs.get(task_id)
                if subs is not None:
                    subs.discard(q)
                    if not subs:
                        self._subs.pop(task_id, None)


_bus: ProgressBus | None = None


def get_bus() -> ProgressBus:
    global _bus
    if _bus is None:
        _bus = ProgressBus()
    return _bus


def make_progress_callback(task_id: str):
    """크롤 파이프라인에 넘길 콜백 생성기."""
    bus = get_bus()

    async def cb(stage: str, pct: int, message: str) -> None:
        await bus.publish(ProgressEvent(task_id=task_id, stage=stage, pct=pct, message=message))

    return cb
