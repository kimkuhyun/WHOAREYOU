"""잡코리아(jobkorea.co.kr) 검색 어댑터.

검색 결과는 `a[href*="/Recruit/GI_Read/"]` 링크로 다수 등장. 동일 URL이 제목링크/회사링크 두 번 나오므로
URL 단위로 group해서 추출한다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote, urljoin

from playwright.async_api import BrowserContext, Page, TimeoutError as PWTimeout

from app.crawler.adapters.base import JobStub, SearchFilters, normalize_company_name

logger = logging.getLogger(__name__)


class JobkoreaAdapter:
    source = "jobkorea"
    base_url = "https://www.jobkorea.co.kr"

    # 잡코리아 코드 매핑 — 사람인과 다른 체계
    # 경력은 "무관"(9)을 항상 OR로 포함 (한국 채용 UX 표준)
    _CAREER_CODE = {
        "신입": "1,9",            # 신입 + 무관
        "1-3": "2,3,9",
        "3-5": "4,5,9",
        "5+": "5,6,7,8,9",
    }
    _REGION_CODE = {
        "서울": "I000", "경기": "I100", "인천": "I200",
        "부산": "I300", "대구": "I400", "광주": "I500",
        "대전": "I600", "울산": "I700", "세종": "I800",
        "강원": "I900", "충북": "I010", "충남": "I020",
        "전북": "I030", "전남": "I040", "경북": "I050",
        "경남": "I060", "제주": "I070",
    }
    _EMPLOYMENT_CODE = {"정규직": "1", "계약직": "2", "인턴": "4"}
    # 학력은 "무관"(0)을 항상 OR로 포함
    _EDUCATION_CODE = {
        "고졸": "1,0", "초대졸": "2,0", "대졸": "3,0", "석사": "4,0", "박사": "5,0",
    }

    def search_url(self, keyword: str, page: int = 1, filters: "SearchFilters | None" = None) -> str:
        params = [f"stext={quote(keyword)}", f"Page_No={page}"]
        if filters:
            if filters.career and filters.career in self._CAREER_CODE:
                params.append(f"careerType={self._CAREER_CODE[filters.career]}")
            if filters.region and filters.region in self._REGION_CODE:
                params.append(f"localCode={self._REGION_CODE[filters.region]}")
            if filters.employment and filters.employment in self._EMPLOYMENT_CODE:
                params.append(f"empTypeCode={self._EMPLOYMENT_CODE[filters.employment]}")
            if filters.education and filters.education in self._EDUCATION_CODE:
                params.append(f"educationType={self._EDUCATION_CODE[filters.education]}")
        return f"{self.base_url}/Search/?" + "&".join(params)

    async def search(
        self, ctx: BrowserContext, keyword: str, *,
        max_results: int = 30, filters: "SearchFilters | None" = None,
    ) -> list[JobStub]:
        page: Page = await ctx.new_page()
        try:
            return await self._scrape_pages(page, keyword, max_results, filters)
        finally:
            await page.close()

    async def _scrape_pages(self, page: Page, keyword: str, max_results: int, filters: "SearchFilters | None" = None) -> list[JobStub]:
        stubs: list[JobStub] = []
        seen: set[str] = set()
        for page_num in range(1, 5):
            if len(stubs) >= max_results:
                break
            url = self.search_url(keyword, page_num, filters=filters)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:
                    await page.wait_for_selector('a[href*="/Recruit/GI_Read/"]', timeout=10_000)
                except PWTimeout:
                    pass
            except Exception as exc:
                logger.warning("잡코리아 진입 실패 (%s): %s", url, exc)
                break

            items = await page.evaluate(
                """
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/Recruit/GI_Read/"]'));
                    const groups = new Map();
                    // 2026년 jobkorea는 Tailwind 카드 (shadow-list/rounded-2xl). closest fallback도 포함.
                    const cardOf = (a) => {
                        return (
                            a.closest('article, li, .list-item, .post, [class*="list-item"], [class*="JobCard"]')
                            || a.closest('div[class*="shadow"], div[class*="rounded-2xl"]')
                            || a.parentElement?.parentElement?.parentElement
                        );
                    };
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        const txt = (a.textContent || '').trim();
                        if (!href) continue;
                        const card = cardOf(a);
                        if (!groups.has(href)) {
                            groups.set(href, {url: href, candidates: [], card});
                        }
                        const g = groups.get(href);
                        if (txt) g.candidates.push(txt);
                        if (card && !g.card) g.card = card;
                    }

                    const REGION_RE = /(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)\\s+[가-힣A-Za-z0-9]{2,15}(?:구|시|군)/;
                    const DEADLINE_RE = /(?:~\\s*\\d{1,2}[.\\/\\-]\\d{1,2})|(?:D-\\d{1,3})|상시\\s*채용|즉시\\s*지원/;

                    const out = [];
                    for (const g of groups.values()) {
                        const sorted = [...g.candidates].sort((a,b) => b.length - a.length);
                        let title = sorted[0] || '';
                        let company = '';
                        for (const c of g.candidates) {
                            if (c !== title && (/\\(주\\)|㈜|주식회사|\\(유\\)|\\(재\\)/.test(c) || (c.length > 1 && c.length < 30 && c !== title))) {
                                company = c;
                                break;
                            }
                        }
                        let location = '', deadline = '';
                        if (g.card) {
                            const cardText = (g.card.textContent || '').replace(/\\s+/g, ' ');
                            // 명시적 클래스 우선
                            const locEl = g.card.querySelector('.loc.long, .loc, [class*="Loc"], [class*="region"]');
                            if (locEl) {
                                location = (locEl.textContent || '').trim().replace(/\\s+/g, ' ');
                            }
                            if (!location) {
                                const m = cardText.match(REGION_RE);
                                if (m) location = m[0];
                            }
                            const dm = cardText.match(DEADLINE_RE);
                            if (dm) deadline = dm[0].replace(/\\s+/g, ' ').trim();
                        }
                        out.push({url: g.url, title, company, location, deadline});
                    }
                    return out;
                }
                """
            )

            if not items:
                if page_num == 1:
                    break
                continue

            for it in items:
                if len(stubs) >= max_results:
                    break
                href = it.get("url") or ""
                if not href:
                    continue
                full_url = href if href.startswith("http") else urljoin(self.base_url, href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                title = re.sub(r"\s+", " ", it.get("title", ""))
                stubs.append(
                    JobStub(
                        source=self.source,
                        url=full_url,
                        title=title,
                        company=normalize_company_name(it.get("company", "")),
                        location=it.get("location", ""),
                        deadline=it.get("deadline", ""),
                    )
                )

            await asyncio.sleep(0.5)

        logger.info("잡코리아 '%s' → %d건 수집", keyword, len(stubs))
        return stubs
