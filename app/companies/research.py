"""대시보드 워드클라우드 — 전체 잡 제목 키워드 집계.

회사 조사 파이프라인은 app/companies/playwright_research.py로 통합됨.
이 파일에는 `collect_overall_keywords` (전체 잡 키워드)만 남김.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.keywords import extract_from_titles
from app.models import Job


async def collect_overall_keywords(
    session: AsyncSession, limit: int = 1000
) -> list[tuple[str, int]]:
    """전체 잡 제목에서 키워드 집계 — 대시보드 워드클라우드용."""
    jobs = (await session.execute(select(Job).limit(limit))).scalars().all()
    return extract_from_titles([j.title for j in jobs], top_n=60)
