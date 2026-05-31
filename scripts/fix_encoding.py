"""기존 saramin 어댑터 인코딩 누락으로 mojibake된 Company.name 정리.

원리: 한국어 UTF-8 바이트가 Latin-1로 잘못 해석돼 저장되었을 가능성.
`name.encode('latin-1').decode('utf-8')` 시도 후, 결과가 한글이면 갱신.
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import select

from app.db import async_session_maker
from app.models import Company, Job


def looks_korean(s: str) -> bool:
    return bool(re.search(r"[가-힣]", s))


def looks_like_mojibake(s: str) -> bool:
    # 한자/희귀 문자가 다수 + 한글 거의 없음
    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    hangul = sum(1 for c in s if "가" <= c <= "힣")
    return cjk > 0 and hangul < 2 and len(s) > 1


def try_fix(s: str) -> str | None:
    if not looks_like_mojibake(s):
        return None
    try:
        repaired = s.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if looks_korean(repaired):
        return repaired
    return None


async def main() -> None:
    async with async_session_maker() as session:
        companies = (await session.execute(select(Company))).scalars().all()
        fixed_companies = 0
        for c in companies:
            new = try_fix(c.name)
            if new and new != c.name:
                print(f"  Company {c.id}: {c.name!r} -> {new!r}")
                c.name = new
                if c.address:
                    fixed_addr = try_fix(c.address)
                    if fixed_addr:
                        c.address = fixed_addr
                fixed_companies += 1

        jobs = (await session.execute(select(Job))).scalars().all()
        fixed_jobs = 0
        for j in jobs:
            new_title = try_fix(j.title)
            new_loc = try_fix(j.location or "")
            if new_title and new_title != j.title:
                j.title = new_title
                fixed_jobs += 1
            if new_loc and new_loc != j.location:
                j.location = new_loc

        await session.commit()
        print(f"\nfixed {fixed_companies} companies, {fixed_jobs} jobs")


if __name__ == "__main__":
    asyncio.run(main())
