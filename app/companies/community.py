"""네이버 + 구글 검색 기반 회사 평판 스니펫 수집.

잡플래닛은 anti-bot이 강해 거의 항상 0건이라 폐기.
대안: 네이버 + 구글 검색 통합 — 보완 관계라 둘 다 쓰면 정보량 2배.

- **네이버**: 블로그/카페/지식iN/뉴스 통합 — 한국 직장인 후기·카페 평판에 강함
- **구글**: 보도자료/투자/기술 블로그/글로벌 활동 — B2B·해외 정보에 강함

흐름:
1. Playwright로 검색 페이지 열기 (양 사이트 동시 호출 — asyncio.gather)
2. 동적 렌더링 대기 후 body innerText 추출
3. 문장 단위 분리 → 회사명 포함 + 적절한 길이만 선택
4. 양쪽 결과 합치고 텍스트 기반 dedup
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from playwright.async_api import TimeoutError as PWTimeout

from app.crawler.browser import BrowserPool

logger = logging.getLogger(__name__)


@dataclass
class ReviewSnippet:
    source: str  # "naver" | "google"
    text: str
    url: str = ""


# 의미없는 네비게이션/광고 텍스트 필터
_NOISE_PATTERNS = (
    "쇼핑", "광고", "더보기", "전체", "닫기", "검색", "정렬", "기간", "필터",
    "VIEW", "이미지", "지식iN", "뉴스", "블로그", "카페", "동영상",
    "회원가입", "로그인", "고객센터", "이용약관", "개인정보",
    "Copyright", "©", "All rights",
    # 구글 검색 결과 페이지 noise
    "Google", "Images", "Maps", "Shopping", "Videos", "Tools",
    "Settings", "Sign in", "Privacy", "Terms", "About",
    "page", "results", "Showing results",
)


def _is_noise(sent: str) -> bool:
    if not sent or len(sent) < 25 or len(sent) > 300:
        return True
    # 너무 많은 단어가 noise면 제외
    noise_hits = sum(1 for p in _NOISE_PATTERNS if p in sent)
    if noise_hits >= 2:
        return True
    # 한글 문자 비율이 너무 낮으면 (메뉴/광고 가능성)
    # 단, 영문 비율이 충분히 높고 길이가 적절하면 통과 (구글 영문 스니펫)
    hangul = sum(1 for c in sent if "가" <= c <= "힣")
    hangul_ratio = hangul / max(len(sent), 1)
    if hangul_ratio < 0.3:
        # 영문 평판도 일부 허용 — 영문 글자 비율 50%↑ + 너무 짧지 않으면
        ascii_letters = sum(1 for c in sent if c.isascii() and c.isalpha())
        ascii_ratio = ascii_letters / max(len(sent), 1)
        if ascii_ratio < 0.5 or len(sent) < 40:
            return True
    return False


def _split_sentences(text: str) -> list[str]:
    # 줄바꿈 + 문장 부호 기준
    parts = re.split(r"[.!?…]|\n+", text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def _norm_key(text: str) -> str:
    """dedup 키 — 공백/문장부호 무시한 정규화."""
    return re.sub(r"[\s\W_]+", "", text.lower())[:200]


async def _capture_search_text(
    pool: BrowserPool, url: str, *, wait_ms: int = 1500, timeout_ms: int = 30_000
) -> str:
    """검색 결과 페이지 진입 → 본문 innerText 추출."""
    try:
        async with pool.context(block_resources=True) as ctx:
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except PWTimeout:
                    pass
                await page.wait_for_timeout(wait_ms)
                return await page.evaluate("() => document.body.innerText || ''")
            finally:
                await page.close()
    except Exception as exc:
        logger.info("검색 페이지 실패 (%s): %r", url, exc)
        return ""


def _extract_snippets(
    text: str, *, company_name: str, source: str, url: str, limit: int
) -> list[ReviewSnippet]:
    """body innerText에서 회사명 포함 문장을 스니펫으로 분리."""
    if not text:
        return []
    snippets: list[ReviewSnippet] = []
    seen: set[str] = set()
    for sent in _split_sentences(text):
        if _is_noise(sent):
            continue
        if company_name not in sent:
            continue
        key = _norm_key(sent)
        if not key or key in seen:
            continue
        seen.add(key)
        snippets.append(ReviewSnippet(source=source, text=sent, url=url))
        if len(snippets) >= limit:
            break
    return snippets


async def fetch_naver_snippets(
    pool: BrowserPool, company_name: str, *, limit: int = 15
) -> list[ReviewSnippet]:
    """네이버 통합검색 — 블로그/카페/지식iN/뉴스 한국 직장인 후기에 강함."""
    if not company_name or not company_name.strip():
        return []
    name = company_name.strip()
    query = f"{name} 후기 평판"
    url = f"https://search.naver.com/search.naver?query={quote(query)}"
    text = await _capture_search_text(pool, url)
    snippets = _extract_snippets(text, company_name=name, source="naver", url=url, limit=limit)
    logger.info("네이버 '%s' → %d 스니펫", name, len(snippets))
    return snippets


async def fetch_google_snippets(
    pool: BrowserPool, company_name: str, *, limit: int = 15
) -> list[ReviewSnippet]:
    """구글 검색 — 보도자료/투자/기술블로그/글로벌 활동에 강함.

    한국어 결과 우선 + 안전 검색은 그대로 (default).
    `&hl=ko&gl=kr&num=20` 로 한국어 + 한국 결과 우선, 20개 가져오기.
    """
    if not company_name or not company_name.strip():
        return []
    name = company_name.strip()
    # "회사명 평판 OR 후기 OR 리뷰" — 광범위하게 잡되 회사명 포함 필터로 다시 거름
    query = f'"{name}" 평판 OR 후기 OR 리뷰 OR 분위기'
    url = (
        f"https://www.google.com/search"
        f"?q={quote(query)}&hl=ko&gl=kr&num=20"
    )
    text = await _capture_search_text(pool, url)
    snippets = _extract_snippets(text, company_name=name, source="google", url=url, limit=limit)
    logger.info("구글 '%s' → %d 스니펫", name, len(snippets))
    return snippets


async def fetch_review_snippets(
    pool: BrowserPool, company_name: str, *, limit_each: int = 12
) -> list[ReviewSnippet]:
    """네이버 + 구글 병렬 호출 → 통합 dedup.

    - 양쪽을 동시에 호출 (asyncio.gather) — 직렬보다 ~2배 빠름
    - 결과는 source별로 보존 (LLM 감정 분석 시 가중치 다르게 줄 수 있음)
    - 텍스트 기반 dedup — 같은 보도자료가 양쪽에 뜨면 1번만
    """
    if not company_name or not company_name.strip():
        return []

    naver_task = fetch_naver_snippets(pool, company_name, limit=limit_each)
    google_task = fetch_google_snippets(pool, company_name, limit=limit_each)

    # 한쪽이 실패해도 다른 쪽 결과는 살림 (return_exceptions=True)
    results = await asyncio.gather(naver_task, google_task, return_exceptions=True)
    merged: list[ReviewSnippet] = []
    seen: set[str] = set()
    for r in results:
        if isinstance(r, Exception):
            logger.info("스니펫 소스 1개 실패: %r", r)
            continue
        for sn in r:
            key = _norm_key(sn.text)
            if key in seen:
                continue
            seen.add(key)
            merged.append(sn)

    naver_n = sum(1 for s in merged if s.source == "naver")
    google_n = sum(1 for s in merged if s.source == "google")
    logger.info(
        "통합 '%s' → 총 %d (네이버 %d + 구글 %d, dedup 후)",
        company_name, len(merged), naver_n, google_n,
    )
    return merged
