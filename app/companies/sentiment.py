"""잡플래닛/블라인드 등 커뮤니티 리뷰 크롤 → 감정 스니펫 저장.

잡플래닛: 회사 검색 결과 페이지 → 리뷰 페이지로 진입 → 리뷰 텍스트 추출.
주의: 로그인 없이 접근 가능한 일부 페이지만 사용. 잡플래닛은 anti-bot이 강해서
실패할 수 있음 — 그래서 LLM 기반 범용 크롤 파이프라인을 우회로 활용.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from lxml import html as lxml_html
from playwright.async_api import BrowserContext, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)


@dataclass
class ReviewSnippet:
    source: str  # jobplanet | blind | community
    text: str
    url: str = ""


async def fetch_jobplanet_snippets(
    ctx: BrowserContext, company_name: str, *, limit: int = 20
) -> list[ReviewSnippet]:
    """잡플래닛 회사 검색 → 첫 결과의 리뷰 페이지에서 스니펫 추출."""
    search_url = f"https://www.jobplanet.co.kr/search?query={quote(company_name)}"
    snippets: list[ReviewSnippet] = []

    page = await ctx.new_page()
    try:
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            logger.info("잡플래닛 검색 실패: %s", exc)
            return snippets

        # 회사 결과 카드의 첫 링크 추출
        company_url = await page.evaluate(
            """() => {
                const links = Array.from(document.querySelectorAll('a[href*="/companies/"]'));
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (/\\/companies\\/\\d+/.test(href)) return href;
                }
                return null;
            }"""
        )
        if not company_url:
            logger.info("잡플래닛에서 회사 결과 없음: %s", company_name)
            return snippets

        if company_url.startswith("/"):
            company_url = "https://www.jobplanet.co.kr" + company_url

        # 리뷰 페이지 진입
        reviews_url = company_url.split("?")[0].rstrip("/")
        # /companies/{id} → /companies/{id}/reviews
        if not reviews_url.endswith("/reviews"):
            reviews_url = reviews_url + "/reviews"
        try:
            await page.goto(reviews_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)
        except Exception as exc:
            logger.info("잡플래닛 리뷰 페이지 실패: %s", exc)
            return snippets

        texts = await page.evaluate(
            """() => {
                const out = [];
                const sels = [
                    '[class*="review"] [class*="content"]',
                    '[class*="ReviewCard"] [class*="content"]',
                    '.us_review_list_box .content',
                    '.df1_summary_pros, .df1_summary_cons',
                    '[class*="pros"], [class*="cons"]',
                ];
                for (const s of sels) {
                    document.querySelectorAll(s).forEach(el => {
                        const t = (el.textContent || '').trim();
                        if (t.length > 10 && t.length < 500) out.push(t);
                    });
                    if (out.length > 0) break;
                }
                return out;
            }"""
        )

        for t in texts[:limit]:
            snippets.append(ReviewSnippet(source="jobplanet", text=t, url=reviews_url))
    finally:
        await page.close()

    logger.info("잡플래닛 '%s' → %d 스니펫", company_name, len(snippets))
    return snippets
