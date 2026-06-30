"""사람인(saramin.co.kr) 검색 어댑터.

Playwright는 Saramin의 봇 탐지로 차단당해 3KB 빈 페이지를 받음.
httpx + 정상 브라우저 헤더로 직접 GET하면 2.3MB 전체 HTML이 반환되어 안정적이다.
검색 페이지 한정으로 Playwright를 우회. 상세 페이지는 별도 처리.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

import httpx
from lxml import html as lxml_html
from playwright.async_api import BrowserContext

from app.crawler.adapters.base import JobStub, SearchFilters, normalize_company_name


def _canonical_saramin_url(url: str) -> str:
    """rec_idx 만 남기고 검색 파라미터(search_uuid 등) 제거 — DB 중복 방지."""
    try:
        u = urlparse(url)
        q = parse_qs(u.query)
        rec = q.get("rec_idx", [None])[0]
        if not rec:
            return url
        new_q = urlencode({"rec_idx": rec})
        return urlunparse((u.scheme, u.netloc, u.path, "", new_q, ""))
    except Exception:
        return url

logger = logging.getLogger(__name__)


DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class SaraminAdapter:
    source = "saramin"
    base_url = "https://www.saramin.co.kr"

    # 사이트별 코드 매핑 — URL 파라미터에 들어갈 값
    # 사람인은 모든 필터 지원 (경력/지역/고용형태/학력)
    # 경력은 "무관"(0)을 항상 OR로 포함 — 한국 채용 UX 표준 ("신입" 검색해도 무관 공고가 같이 떠야 함)
    _CAREER_CODE = {
        "신입": "1,0",                # 신입 + 무관
        "1-3": "2,3,0",               # 1-3년 + 무관
        "3-5": "4,5,0",               # 3-5년 + 무관
        "5+": "6,7,8,9,10,0",         # 7년 이상 + 무관
    }
    _REGION_CODE = {
        "서울": "101000", "경기": "102000", "인천": "108000",
        "부산": "106000", "대구": "104000", "광주": "103000",
        "대전": "105000", "울산": "107000", "세종": "117000",
        "강원": "109000", "충북": "115000", "충남": "114000",
        "전북": "113000", "전남": "112000", "경북": "111000",
        "경남": "110000", "제주": "116000",
    }
    _EMPLOYMENT_CODE = {"정규직": "1", "계약직": "2", "인턴": "4"}
    # 학력은 "무관"(0)을 항상 OR로 포함 (한국 채용 UX 표준 — 대졸 지원자도 '학력무관' 공고 봐야 함)
    _EDUCATION_CODE = {
        "고졸": "4,0", "초대졸": "5,0", "대졸": "8,0", "석사": "9,0", "박사": "10,0",
    }

    def search_url(self, keyword: str, page: int = 1, filters: "SearchFilters | None" = None) -> str:
        params = [
            f"searchType=search",
            f"searchword={quote(keyword)}",
            f"recruitPage={page}",
        ]
        if filters:
            if filters.career and filters.career in self._CAREER_CODE:
                params.append(f"exp_cd={self._CAREER_CODE[filters.career]}")
            if filters.region and filters.region in self._REGION_CODE:
                params.append(f"loc_cd={self._REGION_CODE[filters.region]}")
            if filters.employment and filters.employment in self._EMPLOYMENT_CODE:
                params.append(f"job_type={self._EMPLOYMENT_CODE[filters.employment]}")
            if filters.education and filters.education in self._EDUCATION_CODE:
                params.append(f"edu_cd={self._EDUCATION_CODE[filters.education]}")
        return f"{self.base_url}/zf_user/search/recruit?" + "&".join(params)

    async def search(
        self, ctx: BrowserContext, keyword: str, *,
        max_results: int = 30, filters: "SearchFilters | None" = None,
    ) -> list[JobStub]:
        """ctx는 호환을 위해 받지만 Saramin은 httpx로 직접 요청."""
        stubs: list[JobStub] = []
        seen: set[str] = set()
        # 수집 건강도 — 호출부(collect_jobs)가 읽어 UI 경고로 띄움.
        #   ok            : 정상 수집
        #   blocked       : 200 아님/<50KB (봇 차단 의심)
        #   parse_suspect : 정상 응답(200·50KB+)인데 0건 (사이트 개편=파서 깨짐 또는 결과 없음)
        self.last_health = "ok"
        async with httpx.AsyncClient(headers=DESKTOP_HEADERS, timeout=20.0, follow_redirects=True) as client:
            for page_num in range(1, 5):
                if len(stubs) >= max_results:
                    break
                url = self.search_url(keyword, page_num, filters=filters)
                try:
                    r = await client.get(url)
                except Exception as exc:
                    logger.warning("사람인 GET 실패 (%s): %s", url, exc)
                    if page_num == 1:
                        self.last_health = "blocked"
                    break
                # 사람인은 응답에 charset을 명시 안 해 httpx가 ISO-8859-1로 추정하는 경우 있음 → 강제 UTF-8
                r.encoding = "utf-8"
                if r.status_code != 200 or len(r.text) < 50_000:
                    logger.warning("사람인 응답 비정상 page=%d status=%d len=%d", page_num, r.status_code, len(r.text))
                    if page_num == 1:
                        self.last_health = "blocked"
                    break

                page_stubs = self._parse_listing(r.text)
                if not page_stubs:
                    logger.info("사람인 page=%d 결과 없음 — 중단", page_num)
                    if page_num == 1:
                        # 정상 응답인데 0건 → 셀렉터 깨짐 또는 실제 결과 없음
                        self.last_health = "parse_suspect"
                    break

                for stub in page_stubs:
                    if len(stubs) >= max_results:
                        break
                    if stub.url in seen:
                        continue
                    seen.add(stub.url)
                    stubs.append(stub)

                await asyncio.sleep(0.4)

        logger.info("사람인 '%s' → %d건 수집", keyword, len(stubs))
        return stubs

    def _parse_listing(self, html: str) -> list[JobStub]:
        tree = lxml_html.fromstring(html)
        items = tree.cssselect(".item_recruit")
        if not items:
            # 폴백: 사람인이 리스트 컨테이너 클래스를 바꾼 경우 대비 (개편 감지·일부 복구)
            for sel in (".list_item", ".item_list", "[class*='item_recruit']", "[class*='list_recruit']"):
                alt = tree.cssselect(sel)
                if alt:
                    items = alt
                    logger.warning("사람인 .item_recruit 0건 → 폴백 셀렉터 '%s'로 %d건", sel, len(alt))
                    break
        stubs: list[JobStub] = []
        for item in items:
            try:
                title_el = item.cssselect(".job_tit a, .area_job a")
                if not title_el:
                    continue
                a = title_el[0]
                href = a.get("href") or ""
                if not href:
                    continue
                title = re.sub(r"\s+", " ", a.text_content()).strip()
                full_url = _canonical_saramin_url(urljoin(self.base_url, href))

                corp_el = item.cssselect(".corp_name a, .area_corp .corp_name, .area_corp a")
                company = re.sub(r"\s+", " ", corp_el[0].text_content()).strip() if corp_el else ""

                loc_el = item.cssselect(".work_place, .job_condition span")
                location = re.sub(r"\s+", " ", loc_el[0].text_content()).strip() if loc_el else ""

                due_el = item.cssselect(".job_date, .date")
                deadline = re.sub(r"\s+", " ", due_el[0].text_content()).strip() if due_el else ""

                stubs.append(
                    JobStub(
                        source=self.source,
                        url=full_url,
                        title=title,
                        company=normalize_company_name(company),
                        location=location,
                        deadline=deadline,
                    )
                )
            except Exception as exc:
                logger.debug("사람인 item 파싱 실패: %s", exc)
                continue
        return stubs
