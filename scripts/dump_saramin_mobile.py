"""사람인 모바일 페이지 구조 확인."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.crawler.browser import BrowserPool


async def main() -> None:
    pool = BrowserPool()
    await pool.start()
    async with pool.context(block_resources=False) as ctx:
        page = await ctx.new_page()
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
        })
        url = "https://m.saramin.co.kr/search?searchword=%EB%B0%B1%EC%97%94%EB%93%9C"
        print("URL:", url)
        await page.goto(url, wait_until="commit", timeout=45_000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception as exc:
            print("domcontentloaded:", exc)
        await page.wait_for_timeout(3000)

        print("page url:", page.url)
        print("title:", await page.title())

        html = await page.content()
        Path("dumps").mkdir(exist_ok=True)
        Path("dumps/saramin_mobile.html").write_text(html[:300_000], encoding="utf-8")
        print("html len:", len(html))

        counts = await page.evaluate(
            """() => {
                const sels = [
                    'a[href*="/jobs/relayView"]',
                    'a[href*="/relay/view"]',
                    'a[href*="rec_idx="]',
                    'a[href*="/job-search"]',
                    '.item',
                    '.list_item',
                    'li.item',
                    '[class*="JobItem"]',
                    '[class*="job_list"]',
                    '[class*="recruit"]',
                    '[class*="list_recruit"]',
                ];
                return Object.fromEntries(sels.map(s => [s, document.querySelectorAll(s).length]));
            }"""
        )
        print("counts:")
        for s, c in counts.items():
            if c > 0:
                print(f"  {s:40s} {c}")

        # 일반 a 태그 샘플
        sample = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                  .map(a => ({href:a.getAttribute('href')||'', text:(a.textContent||'').trim().slice(0,50)}))
                  .filter(x => x.text && x.text.length > 3 && !x.href.startsWith('javascript') && !x.href.startsWith('#'))
                  .slice(0, 30)"""
        )
        print("link samples:")
        for s in sample:
            print(f"  [{s['text']!r}] {s['href'][:80]}")

        await page.close()
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
