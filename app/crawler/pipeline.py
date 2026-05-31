"""단발 URL 크롤 파이프라인 — 페이지 진입 → 추출 → LLM 구조화 → (옵션) 비전 fallback.

진행도(progress_bus)는 호출 측에서 인자로 전달받은 콜백을 통해 발행.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from app.crawler.browser import BrowserPool, get_pool
from app.crawler.extractor import ExtractedContent, extract_content
from app.crawler.llm import GENERIC_PAGE_SCHEMA, JOB_SCHEMA, OllamaClient
from app.crawler.strategies import CrawlOptions, CrawlTrace, run_universal
from app.crawler.vision_fallback import text_is_weak, vision_describe

ProgressFn = Callable[[str, int, str], Awaitable[None]]
# (stage, percent, message) → publish to bus


async def _noop_progress(stage: str, pct: int, message: str) -> None:  # pragma: no cover
    return None


@dataclass
class CrawlResult:
    url: str
    final_url: str = ""
    title: str = ""
    extracted_text: str = ""
    extracted_method: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    used_vision: bool = False
    screenshot_b64: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


async def crawl_single_url(
    url: str,
    *,
    ollama: OllamaClient | None,
    pool: BrowserPool | None = None,
    progress: ProgressFn = _noop_progress,
    schema: dict[str, Any] | None = None,
    enable_vision_fallback: bool = True,
    take_screenshot: bool = True,
    crawl_opts: CrawlOptions | None = None,
) -> CrawlResult:
    pool = pool or get_pool()
    opts = crawl_opts or CrawlOptions(screenshot_after=take_screenshot)
    result = CrawlResult(url=url)

    await progress("navigate", 5, f"브라우저 컨텍스트 생성 — {url}")

    html = ""
    trace: CrawlTrace | None = None
    try:
        async with pool.page(block_resources=True) as page:
            await progress("load", 15, "페이지 로드 + networkidle 대기")
            html, trace = await run_universal(page, url, opts)
            await progress("scroll", 40, f"스크롤 {trace.scroll_passes}회 / 더보기 {trace.more_clicks}회")
    except Exception as exc:
        result.error = f"브라우저 단계 실패: {exc}"
        await progress("error", 100, result.error)
        return result

    if trace is not None:
        result.final_url = trace.final_url
        result.title = trace.title
        result.trace = {
            "scroll_passes": trace.scroll_passes,
            "more_clicks": trace.more_clicks,
            "html_length": trace.html_length,
            "elapsed_s": round(trace.elapsed_s, 2),
        }
        if trace.screenshot_bytes:
            result.screenshot_b64 = base64.b64encode(trace.screenshot_bytes).decode("ascii")

    await progress("extract", 55, "본문 추출 (Trafilatura/Readability)")
    extracted: ExtractedContent = extract_content(html, url=url)
    result.extracted_text = extracted.text
    result.extracted_method = extracted.method
    if extracted.title and not result.title:
        result.title = extracted.title

    if ollama is None:
        await progress("done", 100, "LLM 미설정 — 본문만 반환")
        return result

    structured_schema = schema or GENERIC_PAGE_SCHEMA
    await progress("structure", 70, f"LLM 구조화 ({ollama.config.text_model})")

    structured: dict[str, Any] = {}
    if extracted.text and not text_is_weak(extracted.text):
        try:
            structured = await ollama.structure_text(extracted.text, structured_schema)
        except Exception as exc:
            result.error = f"LLM 텍스트 구조화 실패: {exc}"
    else:
        await progress("structure", 70, "본문이 약함 — 비전 fallback 후보")

    # 비전 fallback: 텍스트가 약하거나 LLM 결과가 비었을 때
    if enable_vision_fallback and trace is not None and trace.screenshot_bytes:
        empty = not structured or all(not v for v in structured.values() if not isinstance(v, list)) or text_is_weak(extracted.text)
        if empty:
            await progress("vision", 85, f"비전 모델 분석 ({ollama.config.vision_model})")
            vis = await vision_describe(ollama, trace.screenshot_bytes, structured_schema)
            if vis:
                structured = vis
                result.used_vision = True

    result.structured = structured
    await progress("done", 100, "완료")
    return result


def result_to_dict(result: CrawlResult) -> dict[str, Any]:
    d = asdict(result)
    # 스크린샷은 별도 처리 (이미 b64).
    return d


__all__ = [
    "CrawlResult",
    "crawl_single_url",
    "result_to_dict",
    "JOB_SCHEMA",
    "GENERIC_PAGE_SCHEMA",
]
