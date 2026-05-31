"""회사 위치 좌표화 — 채용공고의 location 텍스트를 Kakao geocode로 좌표 변환."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.distance import haversine_km
from app.geo.kakao import KakaoGeocodeError, geocode_address
from app.models import Company, Job

logger = logging.getLogger(__name__)


def _pick_location_query(jobs: Iterable[Job], company_name: str) -> str | None:
    """채용공고의 location 중 가장 구체적인 것을 우선 선택, 없으면 회사명 자체로."""
    candidates: list[str] = []
    for j in jobs:
        if j.location and j.location.strip():
            candidates.append(j.location.strip())
    if candidates:
        # 가장 긴(구체적인) 위치 선택
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    return company_name or None


async def geocode_company(
    session: AsyncSession,
    company: Company,
    kakao_key: str,
    *,
    home_lat: float | None = None,
    home_lng: float | None = None,
) -> tuple[Company, float | None]:
    """회사 좌표 채우기 + 집과의 직선거리(km) 반환.

    이미 좌표가 있으면 geocode 호출 안 함. company 객체는 session에 attach된 상태.
    """
    if company.kakao_lat is not None and company.kakao_lng is not None:
        if home_lat is not None and home_lng is not None:
            return company, haversine_km(home_lat, home_lng, company.kakao_lat, company.kakao_lng)
        return company, None

    jobs = (
        await session.execute(select(Job).where(Job.company_id == company.id).limit(5))
    ).scalars().all()

    query = _pick_location_query(jobs, company.name)
    if not query:
        return company, None

    try:
        result = await geocode_address(query, kakao_key)
    except KakaoGeocodeError as exc:
        logger.info("회사 geocode 실패 (%s, query=%r): %s", company.name, query, exc)
        return company, None

    company.kakao_lat = result.lat
    company.kakao_lng = result.lng
    company.address = result.address
    # NOTE: last_researched_at은 실제 기업 조사(playwright_research.py)에서만 갱신.
    # 좌표 변환은 "조사"가 아님 — 여기서 찍으면 "조사한 회사만" 필터가 오염됨.

    distance = None
    if home_lat is not None and home_lng is not None:
        distance = haversine_km(home_lat, home_lng, result.lat, result.lng)
    return company, distance
