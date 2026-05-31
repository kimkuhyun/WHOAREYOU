"""Arq 작업. Phase 3에서 실제 enqueue 경로로 사용.

워커 프로세스에서 진행도를 Redis pub/sub으로 발행해 메인 FastAPI 프로세스의 ProgressBus가
WebSocket으로 전달.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.crawler.llm import GENERIC_PAGE_SCHEMA, JOB_SCHEMA, build_client_from_settings
from app.crawler.pipeline import crawl_single_url, result_to_dict
from app.ui.progress_bus import ProgressBus, ProgressEvent


async def on_startup(ctx: dict[str, Any]) -> None:
    # 워커 프로세스 시작 시 Redis 클라이언트를 ctx에 보관 (pub/sub 발행용).
    import redis.asyncio as aioredis

    settings = get_settings()
    ctx["redis_pub"] = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    pub = ctx.get("redis_pub")
    if pub is not None:
        try:
            await pub.aclose()
        except Exception:
            pass


async def _publish(ctx: dict[str, Any], event: ProgressEvent) -> None:
    pub = ctx.get("redis_pub")
    if pub is None:
        return
    try:
        await pub.publish(ProgressBus.REDIS_CHANNEL, event.to_json())
    except Exception:
        pass


async def crawl_url_task(ctx: dict[str, Any], task_id: str, url: str, schema_kind: str = "generic", use_vision: bool = True) -> dict[str, Any]:
    settings = get_settings()

    async def progress(stage: str, pct: int, message: str) -> None:
        await _publish(ctx, ProgressEvent(task_id=task_id, stage=stage, pct=pct, message=message))

    ollama = build_client_from_settings(
        host=settings.ollama_host,
        text_model=settings.ollama_text_model,
        vision_model=settings.ollama_vision_model,
    )
    schema = JOB_SCHEMA if schema_kind == "job" else GENERIC_PAGE_SCHEMA

    try:
        result = await crawl_single_url(
            url,
            ollama=ollama,
            progress=progress,
            schema=schema,
            enable_vision_fallback=use_vision,
        )
    except Exception as exc:
        await progress("error", 100, f"실패: {exc}")
        return {"error": str(exc), "url": url}
    return result_to_dict(result)
