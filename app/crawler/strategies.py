"""범용 크롤 전략 — 무한 스크롤, "더보기" 자동 클릭, 안정화 대기."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from playwright.async_api import Page, TimeoutError as PWTimeout

MORE_BUTTON_REGEX = r"더\s*보기|상세\s*정보\s*더|see more|load more|view more|show more|expand|펼치기"


@dataclass
class CrawlOptions:
    wait_until: str = "domcontentloaded"  # domcontentloaded | networkidle | load
    networkidle_timeout_ms: int = 8_000
    infinite_scroll: bool = True
    scroll_max_passes: int = 8
    scroll_pause_ms: int = 600
    auto_click_more: bool = True
    more_click_max: int = 5
    overall_timeout_s: float = 60.0
    screenshot_after: bool = False


async def goto_and_settle(page: Page, url: str, opts: CrawlOptions) -> None:
    await page.goto(url, wait_until=opts.wait_until, timeout=int(opts.overall_timeout_s * 1000))
    # Best-effort networkidle (개별 사이트가 SSE/polling 쓰면 영원히 안 끝남 → 짧은 타임아웃)
    try:
        await page.wait_for_load_state("networkidle", timeout=opts.networkidle_timeout_ms)
    except PWTimeout:
        pass


async def auto_scroll(page: Page, opts: CrawlOptions) -> int:
    """무한 스크롤. 콘텐츠 높이가 변하지 않으면 종료. 반환: 실제 스크롤 패스 수."""
    if not opts.infinite_scroll:
        return 0
    prev_height = -1
    passes = 0
    for _ in range(opts.scroll_max_passes):
        height = await page.evaluate("() => document.body ? document.body.scrollHeight : 0")
        if height == prev_height:
            break
        prev_height = height
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(opts.scroll_pause_ms)
        passes += 1
    return passes


async def auto_click_more(page: Page, opts: CrawlOptions) -> int:
    """'더 보기' 류 버튼 자동 클릭. 반환: 실제 클릭 수.

    원티드의 "상세 정보 더 보기"처럼 <div role="button"> 같이 비표준 element도 잡도록
    button/a/role=button 외에 div·span까지 fallback.
    """
    if not opts.auto_click_more:
        return 0
    clicks = 0
    # 시도 우선순위: <button>·<a> → [role="button"] → 일반 <div>/<span>
    locator_selectors = [
        f"button:visible:has-text(/{MORE_BUTTON_REGEX}/i)",
        f"a:visible:has-text(/{MORE_BUTTON_REGEX}/i)",
        f"[role='button']:visible:has-text(/{MORE_BUTTON_REGEX}/i)",
        f"div:visible:has-text(/{MORE_BUTTON_REGEX}/i)",
        f"span:visible:has-text(/{MORE_BUTTON_REGEX}/i)",
    ]
    for _ in range(opts.more_click_max):
        clicked_this_pass = False
        for sel in locator_selectors:
            button = page.locator(sel).first
            try:
                if await button.count() == 0:
                    continue
                # has-text는 substring 매칭 → 너무 큰 컨테이너(전체 body 등) 잡힐 수 있음.
                # bounding_box로 합리적 크기 검증 (높이 500px 초과면 컨테이너로 간주, skip).
                box = await button.bounding_box(timeout=1500)
                if not box or box.get("height", 0) > 500:
                    continue
                await button.scroll_into_view_if_needed(timeout=2000)
                await button.click(timeout=2000)
                await page.wait_for_timeout(700)
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except PWTimeout:
                    pass
                clicks += 1
                clicked_this_pass = True
                break  # 이 패스에서 클릭 성공 → 다음 패스에서 다시 검색
            except (PWTimeout, Exception):
                continue
        if not clicked_this_pass:
            break  # 어떤 selector로도 못 잡으면 종료
    return clicks


@dataclass
class CrawlTrace:
    final_url: str = ""
    scroll_passes: int = 0
    more_clicks: int = 0
    title: str = ""
    html_length: int = 0
    elapsed_s: float = 0.0
    screenshot_bytes: bytes | None = field(default=None, repr=False)


async def run_universal(page: Page, url: str, opts: CrawlOptions | None = None) -> tuple[str, CrawlTrace]:
    """범용 크롤. 페이지 HTML과 trace 반환. (LLM 구조화는 호출 측에서 처리.)"""
    opts = opts or CrawlOptions()
    started = asyncio.get_event_loop().time()
    trace = CrawlTrace()
    async with asyncio.timeout(opts.overall_timeout_s):
        await goto_and_settle(page, url, opts)
        trace.scroll_passes = await auto_scroll(page, opts)
        trace.more_clicks = await auto_click_more(page, opts)
        # 마지막에 한 번 더 networkidle 대기 (클릭 후)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except PWTimeout:
            pass
        if opts.screenshot_after:
            trace.screenshot_bytes = await page.screenshot(full_page=True, type="png")
        trace.final_url = page.url
        trace.title = await page.title()
        html = await page.content()
    trace.html_length = len(html)
    trace.elapsed_s = asyncio.get_event_loop().time() - started
    return html, trace
