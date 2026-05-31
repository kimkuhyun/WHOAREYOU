"""사람인/잡코리아 검색 페이지를 열고 카드 구조를 dump."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.crawler.browser import BrowserPool


async def dump(name: str, url: str, out_dir: Path) -> None:
    pool = BrowserPool()
    await pool.start()
    async with pool.context(block_resources=False) as ctx:
        page = await ctx.new_page()
        print(f"\n=== {name} ===")
        print(f"URL: {url}")
        try:
            await page.goto(url, wait_until="commit", timeout=60_000)
            print("  navigation commit OK")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                print("  domcontentloaded OK")
            except Exception as exc:
                print("  domcontentloaded timeout:", exc)
            await page.wait_for_timeout(3000)

            # 페이지 제목과 URL 확인
            title = await page.title()
            cur = page.url
            print(f"  title='{title[:80]}'  url='{cur}'")

            # body의 첫 5000자
            body = await page.content()
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.html").write_text(body[:200_000], encoding="utf-8")
            print(f"  HTML saved ({len(body)} chars) → {out_dir / (name + '.html')}")

            # 카드 후보 셀렉터 카운트
            counts = await page.evaluate(
                """() => {
                    const sels = [
                        '.item_recruit', '.list_item', '.box_item', '.list_recruiting li',
                        '[class*="JobItem"]', '.list-item', '.lists li', '.devloopArea li',
                        '[class*="JobCard"]', 'a[href*="/relay/view"]', 'a[href*="/Recruit/"]',
                        'a[href*="/wd/"]',
                    ];
                    return Object.fromEntries(sels.map(s => [s, document.querySelectorAll(s).length]));
                }"""
            )
            print("  selector counts:")
            for s, c in counts.items():
                if c > 0:
                    print(f"    {s:40s} {c}")

            # 첫 번째 link href 패턴
            sample = await page.evaluate(
                """() => {
                    const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 50);
                    return links.map(a => ({text:(a.textContent||'').trim().slice(0,40), href:a.getAttribute('href')||''}))
                       .filter(x => x.text && x.text.length > 5)
                       .slice(0, 10);
                }"""
            )
            print("  top link samples:")
            for s in sample:
                print(f"    [{s['text']!r}] {s['href']}")
        finally:
            await page.close()
            await pool.stop()


async def main() -> None:
    out = Path("dumps")
    kw = "%EB%B0%B1%EC%97%94%EB%93%9C"  # 백엔드 URL-encoded
    await dump("saramin", f"https://www.saramin.co.kr/zf_user/search/recruit?searchType=search&searchword={kw}&recruitPage=1", out)
    await dump("jobkorea", f"https://www.jobkorea.co.kr/Search/?stext={kw}&Page_No=1", out)


if __name__ == "__main__":
    asyncio.run(main())
