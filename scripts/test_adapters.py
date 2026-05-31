"""각 어댑터 동작 확인용 일회성 스크립트."""

from __future__ import annotations

import asyncio
import json
import sys

from app.crawler.adapters import ADAPTERS
from app.crawler.browser import BrowserPool


async def main(keyword: str = "백엔드", per_site: int = 15) -> None:
    pool = BrowserPool()
    await pool.start()
    try:
        for name, cls in ADAPTERS.items():
            adapter = cls()
            print(f"\n=== {name} (keyword={keyword!r}) ===")
            async with pool.context(block_resources=False) as ctx:
                try:
                    stubs = await adapter.search(ctx, keyword, max_results=per_site)
                except Exception as exc:
                    print(f"  ERROR: {exc!r}")
                    continue
            print(f"  count={len(stubs)}")
            for i, s in enumerate(stubs[:5], 1):
                print(
                    f"  [{i}] {s.title[:50]!r:60s} | {s.company[:25]!r:30s} | {s.location[:30]!r}"
                )
            if stubs:
                print("  sample url:", stubs[0].url)
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "백엔드"))
