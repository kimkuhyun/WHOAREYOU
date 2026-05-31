"""사람인 다른 진입 방법 — 네트워크 응답 확인 + 다양한 URL."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.crawler.browser import BrowserPool


async def try_url(pool: BrowserPool, name: str, url: str, ua: str | None = None) -> None:
    print(f"\n--- {name}: {url}")
    async with pool.context(block_resources=False) as ctx:
        page = await ctx.new_page()
        if ua:
            await page.set_extra_http_headers({"User-Agent": ua})
        xhr_logs: list[str] = []

        def on_response(r):
            try:
                if "saramin" in r.url and ("ajax" in r.url.lower() or "json" in r.headers.get("content-type", "")):
                    xhr_logs.append(f"  XHR {r.status} {r.url[:120]}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="commit", timeout=45_000)
            await page.wait_for_timeout(8000)  # 8초 충분히 대기
            html = await page.content()
            print(f"  html len: {len(html)}")
            counts = await page.evaluate(
                """() => Object.fromEntries(['a[href*="/jobs/relayView"]','a[href*="rec_idx="]','a[href*="/zf_user/jobs"]','article','.item','li[class]'].map(s => [s, document.querySelectorAll(s).length]))"""
            )
            for s, c in counts.items():
                if c > 0:
                    print(f"  {s:40s} {c}")
            for log in xhr_logs[:10]:
                print(log)
        except Exception as exc:
            print(f"  ERROR: {exc}")
        finally:
            await page.close()


async def main() -> None:
    pool = BrowserPool()
    await pool.start()
    desktop_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"

    try:
        # 1) 데스크탑 검색
        await try_url(pool, "desktop /search", "https://www.saramin.co.kr/zf_user/search?searchword=%EB%B0%B1%EC%97%94%EB%93%9C", desktop_ua)
        # 2) 데스크탑 /search/recruit
        await try_url(pool, "desktop /search/recruit", "https://www.saramin.co.kr/zf_user/search/recruit?searchword=%EB%B0%B1%EC%97%94%EB%93%9C&recruitPage=1", desktop_ua)
        # 3) 데스크탑 jobs list (직무 카테고리)
        await try_url(pool, "desktop /jobs/list", "https://www.saramin.co.kr/zf_user/jobs/list/job-category?cat_kewd=%EB%B0%B1%EC%97%94%EB%93%9C", desktop_ua)
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
