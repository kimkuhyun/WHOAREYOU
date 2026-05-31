"""원티드(wanted.co.kr) 검색 어댑터.

Playwright는 anti-bot으로 종종 카드 렌더링이 실패. 다행히 원티드는 공개 JSON API가 있어
훨씬 안정적이고 빠름.
- `GET https://www.wanted.co.kr/api/v4/jobs?country=kr&query=...&limit=20&offset=...`
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx
from playwright.async_api import BrowserContext

from app.crawler.adapters.base import JobStub, SearchFilters, normalize_company_name

logger = logging.getLogger(__name__)

API_BASE = "https://www.wanted.co.kr/api/v4/jobs"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.wanted.co.kr/search",
}


class WantedAdapter:
    source = "wanted"
    base_url = "https://www.wanted.co.kr"

    # 원티드 코드 — 학력 미지원이라 SearchFilters.education은 자동 무시됨
    # 원티드는 years가 단일 범위라 "신입+무관" OR 불가 — years=0이면 신입+무관 자동 포함됨 (사이트 자체 동작)
    _CAREER_CODE = {
        "신입": "0", "1-3": "1-3", "3-5": "3-5", "5+": "5-100",
    }
    _REGION_CODE = {
        "서울": "seoul.all", "경기": "gyeonggi.all", "인천": "incheon.all",
        "부산": "busan.all", "대구": "daegu.all", "광주": "gwangju.all",
        "대전": "daejeon.all", "울산": "ulsan.all", "세종": "sejong.all",
        "강원": "gangwon.all", "충북": "chungbuk.all", "충남": "chungnam.all",
        "전북": "jeonbuk.all", "전남": "jeonnam.all", "경북": "gyeongbuk.all",
        "경남": "gyeongnam.all", "제주": "jeju.all",
    }
    # 원티드 고용형태 — 정규/계약/인턴
    _EMPLOYMENT_CODE = {"정규직": "full_time", "계약직": "contract", "인턴": "intern"}

    def _api_url(self, keyword: str, offset: int, limit: int = 20,
                 filters: "SearchFilters | None" = None) -> str:
        q = quote(keyword)
        # 기본 파라미터
        years = "-1"  # 무관 (default)
        params = [
            "country=kr",
            "job_sort=job.latest_order",
            f"query={q}",
            f"limit={limit}",
            f"offset={offset}",
        ]
        if filters:
            if filters.career and filters.career in self._CAREER_CODE:
                years = self._CAREER_CODE[filters.career]
            if filters.region and filters.region in self._REGION_CODE:
                params.append(f"locations={self._REGION_CODE[filters.region]}")
            if filters.employment and filters.employment in self._EMPLOYMENT_CODE:
                params.append(f"employment_types={self._EMPLOYMENT_CODE[filters.employment]}")
            # filters.education은 원티드 미지원 — 그냥 무시 (URL에 안 넣음)
        params.append(f"years={years}")
        return f"{API_BASE}?" + "&".join(params)

    async def search(
        self, ctx: BrowserContext, keyword: str, *,
        max_results: int = 30, filters: "SearchFilters | None" = None,
    ) -> list[JobStub]:
        """ctx는 호환을 위해 받지만 사용 안 함."""
        stubs: list[JobStub] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            offset = 0
            limit = 20
            while len(stubs) < max_results:
                url = self._api_url(keyword, offset, limit, filters=filters)
                try:
                    r = await client.get(url)
                except Exception as exc:
                    logger.warning("원티드 API 호출 실패 (%s): %s", url, exc)
                    break
                if r.status_code != 200:
                    logger.warning("원티드 API 비정상 status=%d", r.status_code)
                    break
                try:
                    payload = r.json()
                except Exception as exc:
                    logger.warning("원티드 JSON 파싱 실패: %s", exc)
                    break

                data = payload.get("data") or []
                if not data:
                    break

                for it in data:
                    if len(stubs) >= max_results:
                        break
                    job_id = it.get("id")
                    if not job_id:
                        continue
                    job_url = f"{self.base_url}/wd/{job_id}"
                    if job_url in seen:
                        continue
                    seen.add(job_url)

                    company = (it.get("company") or {}).get("name", "")
                    address = it.get("address") or {}
                    location = (
                        address.get("full_location")
                        or " ".join(filter(None, [address.get("location"), address.get("district")]))
                        or ""
                    )
                    due_time = it.get("due_time") or ""
                    stubs.append(
                        JobStub(
                            source=self.source,
                            url=job_url,
                            title=it.get("position", "") or "",
                            company=normalize_company_name(company),
                            location=location,
                            deadline=due_time if isinstance(due_time, str) else "",
                            extra={"company_id": (it.get("company") or {}).get("id")},
                        )
                    )

                # 다음 페이지
                offset += limit
                if offset >= 200:  # 안전장치
                    break
                await asyncio.sleep(0.3)

        logger.info("원티드 '%s' → %d건 수집", keyword, len(stubs))
        return stubs
