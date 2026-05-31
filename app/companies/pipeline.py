"""채용 자동 수집 파이프라인.

검색어 → 3개 어댑터 병렬 search → dedupe → Company upsert + Job upsert → 진행도 발행.
LLM 상세 추출은 옵션 (느림). 기본은 search 결과의 stub 정보만 저장.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.adapters import ADAPTERS, JobAdapter, JobStub, normalize_company_name
from app.crawler.adapters.base import SearchFilters
from app.crawler.browser import BrowserPool, get_pool
from app.db import async_session_maker
from app.geo.kakao import KakaoGeocodeError, geocode_address
from app.models import Company, Job, SearchHistory, utcnow
from app.ui import settings_store

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, str], Awaitable[None]]


@dataclass
class CollectStats:
    keyword: str
    per_source: dict[str, int]
    total_unique: int
    new_jobs: int
    new_companies: int
    errors: dict[str, str]


async def _noop(stage: str, pct: int, msg: str) -> None:
    return None


async def _search_one(
    adapter: JobAdapter,
    pool: BrowserPool,
    keyword: str,
    max_per_source: int,
    progress: ProgressFn,
    filters: SearchFilters | None = None,
) -> tuple[str, list[JobStub], str | None]:
    name = adapter.source
    try:
        async with pool.context(block_resources=False) as ctx:
            await progress("search", 10, f"{name} 검색 시작")
            stubs = await adapter.search(ctx, keyword, max_results=max_per_source, filters=filters)
        await progress("search", 20, f"{name}: {len(stubs)}건")
        return name, stubs, None
    except Exception as exc:
        logger.exception("어댑터 %s 검색 실패", name)
        return name, [], str(exc)


async def collect_jobs(
    keyword: str,
    *,
    max_per_source: int = 30,
    progress: ProgressFn | None = None,
    sources: list[str] | None = None,
    filters: SearchFilters | None = None,
) -> CollectStats:
    progress = progress or _noop
    pool = get_pool()

    source_list = sources or list(ADAPTERS.keys())
    adapters = [ADAPTERS[s]() for s in source_list]

    filter_summary = ""
    if filters and not filters.is_empty():
        parts = []
        if filters.career: parts.append(f"경력={filters.career}")
        if filters.region: parts.append(f"지역={filters.region}")
        if filters.employment: parts.append(f"고용={filters.employment}")
        if filters.education: parts.append(f"학력={filters.education}")
        filter_summary = f" · 필터: {', '.join(parts)}"

    await progress("init", 5, f"키워드='{keyword}' · 어댑터 {len(adapters)}개 순차 실행 시작{filter_summary}")

    # 검색 히스토리 upsert (별도 트랜잭션 — 실패해도 본 수집은 진행)
    try:
        async with async_session_maker() as session:
            await _bump_search_history(session, keyword)
            await session.commit()
    except Exception:
        logger.exception("SearchHistory 기록 실패 (무시): %s", keyword)

    # 어댑터를 순차 실행 (Wanted 등 SPA는 동시 실행 시 카드 렌더 대기 실패하는 케이스 다수).
    results: list[tuple[str, list[JobStub], str | None]] = []
    base_pct = 10
    span = 40  # init~50% 사이에서 어댑터 진행 분할
    for i, adapter in enumerate(adapters):
        local_pct = base_pct + int(span * i / max(len(adapters), 1))
        await progress("search", local_pct, f"{adapter.source} 검색 시작")
        result = await _search_one(adapter, pool, keyword, max_per_source, progress, filters)
        results.append(result)

    per_source: dict[str, int] = {}
    errors: dict[str, str] = {}
    all_stubs: list[JobStub] = []
    for name, stubs, err in results:
        per_source[name] = len(stubs)
        if err:
            errors[name] = err
        all_stubs.extend(stubs)

    # URL 단위 dedupe (어댑터 간 동일 URL은 거의 없지만 안전장치)
    by_url: dict[str, JobStub] = {}
    for s in all_stubs:
        if s.url not in by_url:
            by_url[s.url] = s
    unique = list(by_url.values())

    await progress("save", 60, f"총 {len(unique)}건 (중복제거) → DB 저장")

    new_jobs = 0
    new_companies = 0
    geocoded = 0
    async with async_session_maker() as session:
        settings_map = await settings_store.get_all(session)
        kakao_key = settings_map.get("kakao_rest_key", "")
        for s in unique:
            created_company, created_job, company = await _upsert_stub(session, s)
            if created_company:
                new_companies += 1
            if created_job:
                new_jobs += 1
            # 신규 회사 + location 정보 있고 kakao 키 있으면 즉시 geocode
            if (
                created_company
                and company is not None
                and kakao_key
                and s.location
                and not (company.kakao_lat and company.kakao_lng)
            ):
                try:
                    g = await geocode_address(s.location, kakao_key)
                    company.kakao_lat = g.lat
                    company.kakao_lng = g.lng
                    company.address = g.address
                    geocoded += 1
                except KakaoGeocodeError:
                    pass
        await session.commit()

    await progress("done", 100, f"신규 공고 {new_jobs}건, 신규 회사 {new_companies}개 (좌표 {geocoded})")

    return CollectStats(
        keyword=keyword,
        per_source=per_source,
        total_unique=len(unique),
        new_jobs=new_jobs,
        new_companies=new_companies,
        errors=errors,
    )


async def _bump_search_history(session: AsyncSession, keyword: str) -> None:
    keyword = (keyword or "").strip()
    if not keyword:
        return
    row = (
        await session.execute(select(SearchHistory).where(SearchHistory.keyword == keyword))
    ).scalar_one_or_none()
    if row is None:
        session.add(SearchHistory(keyword=keyword, hit_count=1, last_searched_at=utcnow()))
    else:
        row.hit_count = (row.hit_count or 0) + 1
        row.last_searched_at = utcnow()


async def _upsert_stub(session: AsyncSession, stub: JobStub) -> tuple[bool, bool, Company | None]:
    """Returns (created_company, created_job, company_object_or_None)."""
    company_id: int | None = None
    created_company = False
    company: Company | None = None
    company_name_norm = normalize_company_name(stub.company)

    if company_name_norm:
        result = await session.execute(select(Company).where(Company.name == company_name_norm))
        company = result.scalar_one_or_none()
        if company is None:
            company = Company(name=company_name_norm)
            session.add(company)
            await session.flush()
            created_company = True
        company_id = company.id

    # Job upsert by URL
    result = await session.execute(select(Job).where(Job.url == stub.url))
    job = result.scalar_one_or_none()
    extra_json = json.dumps(
        {"badges": stub.badges, "extra": stub.extra},
        ensure_ascii=False,
    )
    if job is None:
        job = Job(
            company_id=company_id,
            title=stub.title,
            url=stub.url,
            source=stub.source,
            location=stub.location or None,
            deadline=stub.deadline or None,
            extracted_json=extra_json,
            captured_at=utcnow(),
        )
        session.add(job)
        return created_company, True, company

    # 기존이면 일부 필드만 업데이트
    job.title = stub.title or job.title
    if stub.location:
        job.location = stub.location
    if stub.deadline:
        job.deadline = stub.deadline
    if company_id and not job.company_id:
        job.company_id = company_id
    return created_company, False, company
