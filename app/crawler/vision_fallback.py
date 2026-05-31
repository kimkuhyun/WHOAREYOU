"""텍스트 추출이 약하거나 의미 없을 때 비전 모델로 스크린샷 분석."""

from __future__ import annotations

from typing import Any

from app.crawler.llm import GENERIC_PAGE_SCHEMA, OllamaClient, OllamaUnavailable

MIN_TEXT_CHARS = 300


def text_is_weak(text: str) -> bool:
    """본문 텍스트가 의미 있는지 휴리스틱 판단."""
    stripped = (text or "").strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return True
    # 영문/한글 문자 비율이 너무 낮으면 (메뉴/네비뿐인 경우) 약함으로 판단.
    letters = sum(1 for c in stripped if c.isalpha())
    if letters / max(len(stripped), 1) < 0.3:
        return True
    return False


async def vision_describe(
    client: OllamaClient,
    screenshot_bytes: bytes,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not screenshot_bytes:
        return {}
    try:
        return await client.describe_image(screenshot_bytes, schema or GENERIC_PAGE_SCHEMA)
    except OllamaUnavailable:
        # 비전 모델이 미설치인 경우 — 조용히 skip
        return {}
