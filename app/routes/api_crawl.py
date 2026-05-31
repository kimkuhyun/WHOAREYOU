"""단발 URL 크롤 API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.crawler.llm import GENERIC_PAGE_SCHEMA, JOB_SCHEMA, build_client_from_settings
from app.crawler.pipeline import crawl_single_url, result_to_dict
from app.db import async_session_maker
from app.ui import settings_store
from app.ui.progress_bus import ProgressEvent, get_bus, make_progress_callback

logger = logging.getLogger(__name__)
router = APIRouter()


class CrawlRequest(BaseModel):
    url: str = Field(..., min_length=4)
    schema_kind: str = Field("generic", description="generic | job")
    use_vision: bool = True


class CrawlEnqueueResponse(BaseModel):
    task_id: str
    url: str
    schema_kind: str


# 메모리 내 결과 캐시 (Phase 2). Phase 3에서 CrawlJob 테이블로 이전.
_results: dict[str, dict[str, Any]] = {}


def _pick_schema(kind: str) -> dict[str, Any]:
    return JOB_SCHEMA if kind == "job" else GENERIC_PAGE_SCHEMA


async def _resolve_ollama_models() -> tuple[str, str, str]:
    """현재 설정에서 (host, text_model, vision_model) 반환."""
    settings = get_settings()
    async with async_session_maker() as sess:
        stored = await settings_store.get_all(sess)
    text_model = stored.get("ollama_text_model") or settings.ollama_text_model
    vision_model = stored.get("ollama_vision_model") or settings.ollama_vision_model
    return settings.ollama_host, text_model, vision_model


async def _run_crawl(task_id: str, req: CrawlRequest) -> None:
    bus = get_bus()
    progress = make_progress_callback(task_id)

    try:
        host, text_model, vision_model = await _resolve_ollama_models()
        ollama = build_client_from_settings(host, text_model, vision_model)
        # 헬스 체크 — 모델 미설치 시 안내
        try:
            health = await ollama.health()
            if not health.get("text_model_available"):
                await progress("warn", 2, f"텍스트 모델 '{text_model}' 미설치 — Ollama list에 없음. 본문만 반환됩니다.")
                ollama = None
        except Exception as exc:
            await progress("warn", 2, f"Ollama 접속 실패 — 본문만 반환: {exc}")
            ollama = None

        schema = _pick_schema(req.schema_kind)
        result = await crawl_single_url(
            req.url,
            ollama=ollama,
            progress=progress,
            schema=schema,
            enable_vision_fallback=req.use_vision,
            take_screenshot=True,
        )
        _results[task_id] = result_to_dict(result)
    except Exception as exc:
        logger.exception("크롤 실패 (%s)", task_id)
        _results[task_id] = {"error": str(exc), "url": req.url}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=f"실패: {exc}"))


@router.post("/api/crawl", response_model=CrawlEnqueueResponse)
async def enqueue_crawl(req: CrawlRequest) -> CrawlEnqueueResponse:
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="http:// 또는 https://로 시작하는 URL이어야 합니다.")
    task_id = get_bus().new_task_id("crawl")
    # Phase 2: in-process asyncio task. Phase 3에서 Arq enqueue로 교체.
    asyncio.create_task(_run_crawl(task_id, req))
    return CrawlEnqueueResponse(task_id=task_id, url=req.url, schema_kind=req.schema_kind)


@router.get("/api/crawl/{task_id}")
async def get_result(task_id: str) -> dict[str, Any]:
    data = _results.get(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail="결과 없음 — 작업이 아직 끝나지 않았거나 만료되었습니다.")
    return data
