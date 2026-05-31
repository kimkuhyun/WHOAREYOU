"""Playwright 중심 기업조사 파이프라인.

설계 원칙:
1. 모든 웹 인터랙션은 Playwright (모든 사이트가 SPA/CSR — httpx 한계 명확)
2. 독립적 작업은 asyncio.gather로 병렬 실행
3. 컨텍스트는 짧고 가볍게 (block_resources=True, domcontentloaded, 짧은 타임아웃)
4. 진행도는 stage별 명시적 발행

스테이지:
- discover  : 네이버 검색으로 회사 공식 홈페이지 도메인 찾기
- reviews   : 네이버 검색으로 회사 평판 문장 수집
- homepage  : 발견된 도메인 크롤 + about 페이지 1-2개
- transit   : ODsay 대중교통 (httpx, 별도)
- geocode   : 카카오 geocode (httpx, 별도)
- llm_*     : Ollama LLM 구조화/감정 분석
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urljoin, urlparse

from playwright.async_api import BrowserContext, Page, TimeoutError as PWTimeout

from app.analysis.emotion import analyze_reviews
from app.analysis.keywords import extract_from_titles, keywords_to_wordcloud
from app.companies.community import ReviewSnippet, fetch_review_snippets
from app.crawler.browser import BrowserPool, get_pool
from app.crawler.llm import COMPANY_HOMEPAGE_SCHEMA, OllamaClient, build_client_from_settings

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, str], Awaitable[None]]


async def _noop(stage: str, pct: int, msg: str) -> None:
    return None


# ───────────────────────────── 도메인 발견 ─────────────────────────────
#
# ❌ 기존 휴리스틱 (블랙리스트 + 첫 비블랙 도메인 채택) 폐기.
#    문제: nicebizinfo, jobplanet, thevc 같은 "기업 정보 집계 사이트"가 검색 상위에 떠
#    잘못 잡힘. 회사명-도메인 의미 매칭이 안 됨.
#
# ✅ 새 방식: 후보 N개 수집 → 각 후보 메인 페이지 fetch → 회사명 일치 점수 → 최고 점수 선택.
#    1. 검색 결과에서 후보 도메인 수집 (검색엔진/SNS 같은 명백한 noise만 제외)
#    2. 각 후보의 title + h1 + meta description에서 회사명 검출 점수
#    3. 도메인 자체에 회사명 일부 포함되면 가산점
#    4. 최저 점수 threshold 미달이면 None 반환 (잘못된 도메인 잡지 않음)

# 검색 결과 페이지가 자기 자신을 링크하는 것만 명백히 제외 (검증으로 못 거르는 케이스)
_NEVER_COMPANY_DOMAINS = {
    "naver.com", "naver.net", "pstatic.net", "daum.net", "kakao.com",
    "google.com", "youtube.com", "youtu.be",
    "googletagmanager.com", "google-analytics.com", "googleapis.com",
    "gstatic.com", "doubleclick.net",
    "facebook.com", "facebook.net", "fbcdn.net",
    "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "wikipedia.org", "namu.wiki",
    # 잡사이트 자체 (회사 도메인일 수 없음)
    "saramin.co.kr", "jobkorea.co.kr", "wanted.co.kr", "wantedlab.com",
    "jobplanet.co.kr", "rocketpunch.com", "rallit.com", "programmers.co.kr",
    # 기업 정보 집계/신용평가 — 회사 도메인일 수 없는데 검색 상위에 자주 뜸
    "nicebizinfo.com", "nicebizinfo.co.kr", "nicednr.co.kr",
    "thevc.kr", "thevc.co.kr",
    "innoforest.co.kr", "innopolis.or.kr",
    "bizinfo.go.kr", "data.go.kr", "opendart.fss.or.kr",
    "fnguide.com", "company.fnguide.com",
    "creditok.co.kr", "dnb.com",
    # 채용 부가 (블로그·미디어)
    "incruit.com", "people.kr",
}


def _is_noise_domain(host: str) -> bool:
    """명백한 noise (검색엔진/SNS/잡사이트 자체)만 제외."""
    h = host.lower()
    if h.startswith("www."):
        h = h[4:]
    return any(h == b or h.endswith("." + b) for b in _NEVER_COMPANY_DOMAINS)


def _normalize_company_token(name: str) -> str:
    """회사명을 검색용 토큰으로 정규화 — 법인 접두/접미사·공백·특수문자 제거 후 소문자."""
    from app.crawler.adapters.base import normalize_company_name
    n = normalize_company_name(name)
    return "".join(c for c in n.lower() if c.isalnum() or "가" <= c <= "힣")


async def _fetch_domain_signals(ctx, host: str) -> dict[str, str]:
    """후보 도메인의 메인 페이지에서 회사명 검증에 쓸 텍스트만 추출 (가벼움)."""
    page = await ctx.new_page()
    try:
        await page.goto(f"https://{host}", wait_until="domcontentloaded", timeout=8_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=2_500)
        except PWTimeout:
            pass
        info = await page.evaluate(
            """() => ({
                title: document.title || '',
                h1: Array.from(document.querySelectorAll('h1, h2, .logo, .brand, [class*="logo"], [class*="brand"]'))
                    .slice(0, 5).map(e => (e.innerText || e.textContent || '').trim()).join(' | '),
                meta_desc: (document.querySelector('meta[name="description"]') || {}).content || '',
                meta_site: (document.querySelector('meta[property="og:site_name"]') || {}).content || '',
                meta_title: (document.querySelector('meta[property="og:title"]') || {}).content || '',
            })"""
        )
        return info or {}
    except Exception:
        return {}
    finally:
        try: await page.close()
        except Exception: pass


def _score_domain_match(host: str, signals: dict[str, str], name_token: str) -> int:
    """도메인+페이지 신호로 회사명 매칭 점수.

    - 도메인 자체에 회사명 토큰 포함: +5
    - meta og:site_name과 일치: +6 (가장 강한 신호)
    - title/h1에 회사명 포함: +3
    - meta description에 회사명 포함: +2
    """
    if not name_token or len(name_token) < 2:
        return 0
    score = 0
    host_token = "".join(c for c in host.lower() if c.isalnum())
    if name_token in host_token:
        score += 5

    def _has(field: str) -> bool:
        v = (signals.get(field) or "").lower()
        v_tok = "".join(c for c in v if c.isalnum() or "가" <= c <= "힣")
        return name_token in v_tok

    if _has("meta_site"): score += 6
    if _has("title"): score += 3
    if _has("h1"): score += 2
    if _has("meta_title"): score += 2
    if _has("meta_desc"): score += 2
    return score


_DOMAIN_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_official": {"type": "boolean", "description": "도메인이 회사의 공식 홈페이지가 맞으면 true, 정보집계/뉴스/SNS 등이면 false."},
        "reason": {"type": "string", "description": "판단 근거 한 줄."},
    },
    "required": ["is_official"],
}


async def _llm_verify_domain(
    llm: OllamaClient | None,
    company_name: str,
    host: str,
    signals: dict[str, str],
) -> bool | None:
    """borderline 후보를 LLM에 한 번 더 검증. 실패 시 None (= 판단 보류)."""
    if llm is None:
        return None
    snippet = (
        f"회사명: {company_name}\n"
        f"도메인: {host}\n"
        f"og:site_name: {signals.get('meta_site', '')[:120]}\n"
        f"title: {signals.get('title', '')[:160]}\n"
        f"h1/logo: {signals.get('h1', '')[:160]}\n"
        f"meta description: {signals.get('meta_desc', '')[:200]}\n"
    )
    try:
        parsed = await llm.structure_text(
            snippet,
            _DOMAIN_VERIFY_SCHEMA,
            instruction=(
                "위 정보를 보고, 이 도메인이 해당 회사의 '공식 홈페이지'인지 판단하라. "
                "기업 정보 집계 사이트(nicebizinfo, thevc, fnguide 등), 뉴스 매체, 채용 사이트, "
                "SNS는 false. 회사가 직접 운영하는 메인 사이트만 true."
            ),
            max_chars=2_000,
            num_ctx=4_096,
        )
    except Exception as exc:
        logger.info("LLM 도메인 검증 실패 (%s, %s): %s", company_name, host, exc)
        return None
    if not isinstance(parsed, dict) or "is_official" not in parsed:
        return None
    return bool(parsed.get("is_official"))


async def _log_discovery_attempt(
    company_id: int | None,
    company_name: str,
    scored: list[tuple[int, str, dict[str, str]]],
    chosen_host: str | None,
    chosen_score: int | None,
    chosen_via: str,
    rejection_reason: str | None = None,
) -> None:
    """audit log 1 row 추가. 실패해도 본 흐름엔 영향 없음."""
    if company_id is None:
        return  # 호출자가 id 없이 부르면 audit 생략 (현재 신경 안 씀)
    try:
        from app.db import async_session_maker
        from app.models import DomainDiscoveryAttempt
        candidates_payload = [
            {"host": h, "score": s, "title": (sig.get("title") or "")[:120]}
            for s, h, sig in scored[:8]
        ]
        async with async_session_maker() as session:
            session.add(
                DomainDiscoveryAttempt(
                    company_id=company_id,
                    company_name=company_name,
                    candidates_json=json.dumps(candidates_payload, ensure_ascii=False),
                    chosen_host=chosen_host,
                    chosen_score=chosen_score,
                    chosen_via=chosen_via,
                    rejection_reason=rejection_reason,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("DomainDiscoveryAttempt 저장 실패 — 무시")


async def step_discover_domain(
    pool: BrowserPool,
    company_name: str,
    llm: OllamaClient | None = None,
    company_id: int | None = None,
) -> tuple[str, int] | None:
    """네이버 검색 → 후보 도메인 수집 → 회사명 매칭 점수 검증 → (borderline은 LLM 재확인) → 최고 점수 선택.

    Returns:
        (https URL, score) 튜플 — 발견 성공. 잘못된 도메인(정보집계 사이트)을 잡지 않도록 검증.
        None — 신뢰 임계 미달. UI는 "도메인 미발견"으로 처리해야 함.
    """
    name = company_name.strip()
    if not name:
        return None
    name_token = _normalize_company_token(name)
    if len(name_token) < 2:
        return None

    # 1) 네이버 검색 → 후보 도메인 수집 (등장 순서 보존, 3개 쿼리 다양화)
    #    - "회사명 공식 홈페이지"  → 공식 사이트 직접 노출 노림
    #    - "회사명"                 → 가장 자연스러운 1차 결과 (블로그·뉴스도 섞임)
    #    - "회사명 채용"            → 영세 회사도 잡코리아/원티드보다 자체 채용페이지가 위에 뜨는 경우 있음
    queries = [
        f"{name} 공식 홈페이지",
        name,
        f"{name} 채용",
    ]
    candidates: list[str] = []
    seen_hosts: set[str] = set()

    async def _gather_from_query(ctx, q: str) -> list[str]:
        page = await ctx.new_page()
        try:
            await page.goto(
                f"https://search.naver.com/search.naver?query={quote(q)}",
                wait_until="domcontentloaded",
                timeout=12_000,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=3_500)
            except PWTimeout:
                pass
            return await page.evaluate(
                """() => {
                    const out = [];
                    const seen = new Set();
                    for (const a of document.querySelectorAll('a[href]')) {
                        const h = a.href || '';
                        if (!h.startsWith('http')) continue;
                        try {
                            const u = new URL(h);
                            const host = u.hostname.toLowerCase();
                            if (!seen.has(host)) { seen.add(host); out.push(host); }
                        } catch(_) {}
                    }
                    return out;
                }"""
            )
        finally:
            await page.close()

    async with pool.context(block_resources=True) as ctx:
        for q in queries:
            try:
                hosts = await _gather_from_query(ctx, q)
            except Exception:
                logger.exception("query 실패: %s", q)
                continue
            for h in hosts:
                if _is_noise_domain(h) or h in seen_hosts:
                    continue
                seen_hosts.add(h)
                candidates.append(h)

    if not candidates:
        logger.info("도메인 후보 0건 (%s)", name)
        return None

    # 2) 상위 8개 후보의 메인 페이지 fetch → 회사명 매칭 점수
    #    (3개 쿼리에서 모은 후보 풀에서 dedupe된 등장순)
    top_candidates = candidates[:8]
    logger.info("도메인 후보 검증 시작 (%s): %s", name, top_candidates)

    scored: list[tuple[int, str, dict[str, str]]] = []
    async with pool.context(block_resources=True) as ctx:
        for host in top_candidates:
            signals = await _fetch_domain_signals(ctx, host)
            score = _score_domain_match(host, signals, name_token)
            logger.info("  %s → score=%d (signals: title=%r)",
                        host, score, (signals.get("title") or "")[:60])
            scored.append((score, host, signals))

    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_host, best_signals = scored[0] if scored else (0, None, {})

    # 3) 점수 기반 판정 — 보수적 임계 (잘못 잡는 것보다 "모름" 반환이 낫다)
    #    - >=11점: 강한 신호 (도메인 매칭 + meta og:site_name 등) → 바로 채택
    #    - 7~10점:  중간 신호 → LLM에 한 번 더 검증, 통과해야 채택
    #    - <7점:    신뢰 불가 → None (UI는 "도메인 미발견"으로 정직하게 표시)
    STRONG_SCORE = 11
    WEAK_FLOOR = 7              # 5 → 7 상향: 도메인 토큰(+5) + h1(+2) 정도는 와야 함
    LLM_VERIFIED_MIN = 9        # LLM이 보류(None) 반환 시 점수만으로 채택할 최소선 (7 → 9)

    if best_score >= STRONG_SCORE:
        logger.info("도메인 발견 (%s): %s (score=%d, 강한 신호)", name, best_host, best_score)
        await _log_discovery_attempt(company_id, name, scored, best_host, best_score, "strong")
        return f"https://{best_host}", best_score

    if best_score < WEAK_FLOOR:
        logger.info("도메인 검증 실패 (%s): 최고점 %d < %d", name, best_score, WEAK_FLOOR)
        await _log_discovery_attempt(
            company_id, name, scored, None, best_score,
            "rejected", f"max_score {best_score} < WEAK_FLOOR {WEAK_FLOOR}",
        )
        return None

    # borderline — LLM에 검증 요청 (없거나 실패하면 보수적으로 채택 보류)
    if llm is None:
        # LLM 없을 땐 강한 신호(STRONG_SCORE) 외엔 거부 — "모름"이 잘못된 답보다 낫다
        logger.info("도메인 검증 실패 (%s): 최고점 %d, LLM 미사용 → 보수적 거부", name, best_score)
        await _log_discovery_attempt(
            company_id, name, scored, None, best_score,
            "rejected", f"borderline {best_score}, LLM unavailable",
        )
        return None

    # LLM에 상위 2개 후보까지 순차 검증 (1등이 가짜면 2등 채택)
    for score, host, signals in scored[:2]:
        if score < WEAK_FLOOR:
            break
        verdict = await _llm_verify_domain(llm, name, host, signals)
        if verdict is True:
            logger.info("도메인 발견 (%s): %s (score=%d, LLM 확인 OK)", name, host, score)
            await _log_discovery_attempt(company_id, name, scored, host, score, "llm_verified")
            return f"https://{host}", score
        if verdict is False:
            logger.info("LLM이 가짜 도메인으로 판단 (%s): %s", name, host)
            continue
        # verdict is None → 판단 보류, 점수가 LLM_VERIFIED_MIN 이상이면 채택
        if score >= LLM_VERIFIED_MIN:
            logger.info("도메인 발견 (%s): %s (score=%d, LLM 보류)", name, host, score)
            await _log_discovery_attempt(company_id, name, scored, host, score, "llm_borderline")
            return f"https://{host}", score

    logger.info("도메인 검증 실패 (%s): 모든 후보 LLM 거부 (최고점=%d)", name, best_score)
    await _log_discovery_attempt(
        company_id, name, scored, None, best_score,
        "rejected", "모든 borderline 후보 LLM 거부",
    )
    return None


# ───────────────────────────── 홈페이지 크롤 ─────────────────────────────

# hint 키워드 + 가중치 — 회사 정체성/연혁/제품/고객/뉴스/투자 모두 노림
_HINT_WEIGHT: dict[str, int] = {
    # 핵심 정체성
    "about": 10, "company": 10, "회사": 9, "소개": 9, "기업": 8,
    "vision": 8, "mission": 8, "intro": 8, "비전": 8, "미션": 8,
    # 제품·서비스
    "service": 7, "product": 7, "business": 7, "solution": 7,
    "서비스": 7, "제품": 7, "사업": 7, "솔루션": 7, "사업영역": 7, "사업분야": 7,
    "what-we-do": 6, "who-we-are": 6,
    # 연혁·역사
    "history": 8, "milestone": 7, "timeline": 7,
    "연혁": 8, "히스토리": 7, "마일스톤": 7, "스토리": 6, "이야기": 6,
    # 뉴스·보도
    "news": 7, "press": 7, "media": 5, "blog": 4,
    "뉴스": 7, "보도자료": 7, "공지": 5,
    # 고객·파트너
    "client": 6, "customer": 6, "partner": 6, "portfolio": 5,
    "고객": 6, "파트너": 6, "도입사례": 6, "협력사": 5,
    # 위치
    "location": 5, "office": 5, "branch": 5, "global": 5, "contact": 3,
    "지사": 5, "사무소": 5, "글로벌": 5, "위치": 4, "찾아오시는길": 4,
    # 투자·매출
    "investor": 7, "ir": 6, "funding": 7,
    "투자": 7, "투자유치": 7,
    # 기술·팀 (보조)
    "tech": 4, "기술": 4, "team": 3, "팀": 3,
    "사람": 3, "멤버": 3, "구성원": 3,
}


def _score_link(href: str, text: str) -> int:
    blob = f"{href} {text}".lower()
    return sum(w for h, w in _HINT_WEIGHT.items() if h in blob)


# 누적 텍스트 한도 (LLM context 60KB — qwen3.5 num_ctx 32K 여유)
_MAX_COMBINED_CHARS = 60_000
# 페이지당 한도 (한 페이지가 너무 길어서 다른 페이지를 못 넣는 일 방지)
_MAX_PER_PAGE_CHARS = 15_000
# 1단계 BFS 최대 페이지 수
_MAX_PAGES_LEVEL_1 = 10
# 2단계 BFS 최대 페이지 수
_MAX_PAGES_LEVEL_2 = 6


def _normalize_domain(d: str) -> str:
    d = d.strip()
    if not d.startswith(("http://", "https://")):
        d = "https://" + d
    return d


async def _collect_page_text(page: Page) -> str:
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        return (text or "")[:_MAX_PER_PAGE_CHARS]
    except Exception as exc:
        logger.info("innerText 추출 실패: %s", exc)
        return ""


async def _collect_anchor_links(page: Page) -> list[dict[str, str]]:
    """페이지에서 모든 a[href] + 텍스트 수집."""
    try:
        return await page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                for (const a of document.querySelectorAll('a[href]')) {
                    const href = a.getAttribute('href') || '';
                    const text = ((a.innerText || a.textContent || '').trim()).slice(0, 100);
                    if (!href || href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) continue;
                    if (seen.has(href)) continue;
                    seen.add(href);
                    out.push({href, text});
                }
                return out;
            }"""
        )
    except Exception:
        return []


def _resolve_and_score(
    link_data: list[dict[str, str]],
    *,
    base_origin: str,
    base_netloc: str,
    main_url_norm: str,
    visited: set[str],
) -> dict[str, int]:
    """후보 링크들을 점수화 + 같은 도메인 + 미방문 + 0점 초과만 통과."""
    scored: dict[str, int] = {}
    for o in link_data:
        href = o.get("href", "")
        text = o.get("text", "")
        if not href:
            continue
        full = urljoin(base_origin, href)
        p = urlparse(full)
        if p.netloc != base_netloc:
            continue
        norm = f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"
        if not norm or norm == main_url_norm or norm in visited:
            continue
        score = _score_link(full, text)
        if score <= 0:
            continue
        if norm not in scored or scored[norm] < score:
            scored[norm] = score
    return scored


async def step_homepage_audit(
    pool: BrowserPool, domain: str
) -> dict[str, Any]:
    """회사 홈페이지를 2단계 BFS로 깊이 크롤.

    0단계: 메인 페이지
    1단계: 메인에서 hint 점수 ≥1 페이지 상위 10개 (about/연혁/뉴스/고객사/투자 등)
    2단계: 1단계 페이지에서 발견한 새 링크 중 상위 6개 (서브 페이지)
    누적 60KB 도달 시 조기 종료.
    """
    main_url = _normalize_domain(domain)
    combined = ""
    visited: set[str] = set()
    visited_ordered: list[str] = []

    base = urlparse(main_url)
    base_origin = f"{base.scheme}://{base.netloc}"
    main_url_norm = main_url.rstrip("/")

    async with pool.context(block_resources=True) as ctx:
        page = await ctx.new_page()
        try:
            # ─── 0단계: 메인 페이지 ───
            try:
                await page.goto(main_url, wait_until="domcontentloaded", timeout=18_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4_000)
                except PWTimeout:
                    pass
                await page.wait_for_timeout(800)
            except Exception as exc:
                return {"error": f"메인 진입 실패: {type(exc).__name__}: {exc}", "domain": domain}

            main_text = await _collect_page_text(page)
            visited.add(main_url_norm)
            visited_ordered.append(main_url)
            combined = f"## [메인 페이지: {main_url}]\n" + main_text

            # ─── 1단계: 메인에서 발견된 상위 10개 ───
            main_links = await _collect_anchor_links(page)
            scored_l1 = _resolve_and_score(
                main_links,
                base_origin=base_origin,
                base_netloc=base.netloc,
                main_url_norm=main_url_norm,
                visited=visited,
            )
            sorted_l1 = sorted(scored_l1.items(), key=lambda x: -x[1])[:_MAX_PAGES_LEVEL_1]
            l1_links_acc: list[dict[str, str]] = []

            for cand_url, score in sorted_l1:
                if len(combined) >= _MAX_COMBINED_CHARS:
                    break
                try:
                    await page.goto(cand_url, wait_until="domcontentloaded", timeout=12_000)
                    await page.wait_for_timeout(500)
                    t = await _collect_page_text(page)
                    if t and len(t.strip()) > 80:
                        combined += f"\n\n---\n## [페이지: {cand_url}] (score={score})\n" + t
                        visited.add(cand_url)
                        visited_ordered.append(cand_url)
                        # 2단계 후보로 이 페이지 링크들도 모음
                        try:
                            l1_links_acc.extend(await _collect_anchor_links(page))
                        except Exception:
                            pass
                except Exception as exc:
                    logger.info("1단계 페이지 실패 (%s): %s", cand_url, exc)

            # ─── 2단계: 1단계 페이지에서 발견한 새 링크 중 상위 6개 ───
            if l1_links_acc and len(combined) < _MAX_COMBINED_CHARS:
                scored_l2 = _resolve_and_score(
                    l1_links_acc,
                    base_origin=base_origin,
                    base_netloc=base.netloc,
                    main_url_norm=main_url_norm,
                    visited=visited,
                )
                sorted_l2 = sorted(scored_l2.items(), key=lambda x: -x[1])[:_MAX_PAGES_LEVEL_2]
                for cand_url, score in sorted_l2:
                    if len(combined) >= _MAX_COMBINED_CHARS:
                        break
                    try:
                        await page.goto(cand_url, wait_until="domcontentloaded", timeout=12_000)
                        await page.wait_for_timeout(500)
                        t = await _collect_page_text(page)
                        if t and len(t.strip()) > 80:
                            combined += f"\n\n---\n## [서브 페이지: {cand_url}] (score={score})\n" + t
                            visited.add(cand_url)
                            visited_ordered.append(cand_url)
                    except Exception as exc:
                        logger.info("2단계 페이지 실패 (%s): %s", cand_url, exc)
        finally:
            await page.close()

    return {
        "domain": domain,
        "main_url": main_url,
        "pages_visited": visited_ordered,
        "text": combined,
        "text_chars": len(combined),
    }


# ───────────────────────────── 평판 수집 ─────────────────────────────
# 실제 구현은 app/companies/community.py에 위임:
#   - fetch_review_snippets: 네이버 + 구글 병렬 호출 + 텍스트 dedup
#   - source별로 ReviewSnippet 객체 반환 ("naver" | "google")


# ───────────────────────────── ODsay 대중교통 ─────────────────────────────

async def step_transit(
    home_lat: float | None,
    home_lng: float | None,
    company_lat: float | None,
    company_lng: float | None,
    odsay_key: str,
) -> dict[str, Any] | None:
    if not (home_lat and home_lng and company_lat and company_lng and odsay_key):
        return None
    from app.geo.odsay import ODsayError, search_transit

    try:
        transit = await search_transit(
            home_lng, home_lat, company_lng, company_lat, odsay_key
        )
    except ODsayError as exc:
        return {"error": str(exc)}
    if transit is None:
        return {"error": "ODsay 결과 없음"}
    return asdict(transit)


# ───────────────────────────── Geocode ─────────────────────────────

async def step_geocode(
    company_name: str,
    locations: list[str],
    kakao_key: str,
) -> dict[str, Any] | None:
    if not kakao_key:
        return None
    from app.geo.kakao import KakaoGeocodeError, geocode_address

    candidates = [loc for loc in locations if loc and loc.strip()]
    candidates.sort(key=len, reverse=True)  # 가장 구체적 주소 우선
    query = candidates[0] if candidates else company_name
    if not query:
        return None
    try:
        r = await geocode_address(query, kakao_key)
    except KakaoGeocodeError as exc:
        return {"error": str(exc)}
    return {"lat": r.lat, "lng": r.lng, "address": r.address}


# ───────────────────────────── 메인 파이프라인 ─────────────────────────────

async def research_company_playwright(
    company_id: int,
    *,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Playwright 중심 회사 조사 — 가능한 작업을 모두 병렬 실행."""
    progress = progress or _noop
    pool = get_pool()

    # 설정 + 회사 로드
    from sqlalchemy import select

    from app.db import async_session_maker
    from app.geo.distance import haversine_km
    from app.models import Company, Job, SentimentSnippet, utcnow
    from app.ui import settings_store

    async with async_session_maker() as session:
        company = await session.get(Company, company_id)
        if not company:
            return {"error": "회사를 찾을 수 없습니다"}
        cfg = await settings_store.get_all(session)
        jobs = (
            await session.execute(select(Job).where(Job.company_id == company_id))
        ).scalars().all()
        job_locations = [j.location for j in jobs if j.location]
        company_name = company.name
        existing_domain = company.domain
        existing_lat = company.kakao_lat
        existing_lng = company.kakao_lng
        existing_dart_corp = company.dart_corp_code
        kakao_key = cfg.get("kakao_rest_key", "")
        odsay_key = cfg.get("odsay_key", "")
        dart_key = cfg.get("dart_api_key", "")
        text_model = cfg.get("ollama_text_model", "qwen3.5:9b")
        vision_model = cfg.get("ollama_vision_model", "qwen2.5vl:7b")
        try:
            home_lat = float(cfg.get("home_lat") or 0) or None
            home_lng = float(cfg.get("home_lng") or 0) or None
        except (TypeError, ValueError):
            home_lat = home_lng = None

    ollama = build_client_from_settings("http://localhost:11434", text_model, vision_model)
    report: dict[str, Any] = {"company_id": company_id, "name": company_name}

    await progress("init", 5, f"기업 조사 시작: {company_name}")

    # ───── Stage 1: 병렬 시작 (geocode + dart + discover + reviews + transit) ─────
    geo_task = asyncio.create_task(
        step_geocode(company_name, job_locations, kakao_key)
        if (existing_lat is None or existing_lng is None)
        else _noop_value({"lat": existing_lat, "lng": existing_lng})
    )

    # DART overview — corp_code + 키 있을 때만 (homepage URL/주소/CEO 등 가장 신뢰 가능한 1차 출처)
    dart_task: asyncio.Task[dict[str, Any] | None] | None = None
    if existing_dart_corp and dart_key:
        await progress("dart", 8, f"DART 조회 — corp_code={existing_dart_corp}")
        dart_task = asyncio.create_task(_safe_dart_overview(existing_dart_corp, dart_key))

    discover_task: asyncio.Task[tuple[str, int] | None] | None = None
    if not existing_domain:
        await progress("discover", 10, "도메인 발견 — 네이버 검색")
        discover_task = asyncio.create_task(
            step_discover_domain(pool, company_name, llm=ollama, company_id=company_id)
        )

    await progress("reviews", 12, "평판 검색 — 네이버+구글 병렬")
    reviews_task = asyncio.create_task(
        fetch_review_snippets(pool, company_name, limit_each=12)
    )

    # Transit은 기존 좌표가 있을 때만 가능
    transit_task: asyncio.Task[dict[str, Any] | None] | None = None
    if existing_lat and existing_lng and home_lat and home_lng and odsay_key:
        await progress("transit", 15, "ODsay 대중교통 조회")
        transit_task = asyncio.create_task(
            step_transit(home_lat, home_lng, existing_lat, existing_lng, odsay_key)
        )

    # ───── Stage 2a: DART overview 먼저 수신 — homepage_url이 가장 신뢰 가능 ─────
    dart_overview_data: dict[str, Any] | None = None
    dart_homepage: str | None = None
    if dart_task is not None:
        try:
            dart_overview_data = await dart_task
            if dart_overview_data:
                hp = (dart_overview_data.get("homepage") or "").strip()
                # DART의 hm_url은 가끔 "http://" 누락 / 다중 URL — 첫 valid URL만
                if hp:
                    # 첫 토큰만 (공백/콤마/세미콜론 구분)
                    first = re.split(r"[\s,;]+", hp)[0]
                    if first and not first.startswith(("http://", "https://")):
                        first = "http://" + first
                    if first.startswith(("http://", "https://")):
                        dart_homepage = first
                await progress(
                    "dart", 25,
                    f"DART OK — {dart_overview_data.get('corp_name','')}"
                    + (f" · homepage={dart_homepage}" if dart_homepage else "")
                )
        except Exception:
            logger.exception("DART overview 실패")

    # ───── Stage 2b: discover 완료 후 homepage 크롤 시작 ─────
    domain_to_use = existing_domain
    discovered_confidence: int | None = None     # 새로 발견된 도메인의 점수 (저장용)
    discovered_source: str | None = None         # "discover" | "manual" | "dart" — 새로 채택된 경우만

    # DART homepage가 있고 기존 도메인이 없으면 그걸 최우선 채택 (신뢰도 100 = max)
    if dart_homepage and not existing_domain:
        domain_to_use = dart_homepage
        discovered_confidence = 100
        discovered_source = "dart"
        await progress("dart", 28, f"DART 등록 홈페이지 채택 → {dart_homepage}")
        # discover_task는 이미 시작됐을 수 있음 — cancel
        if discover_task is not None and not discover_task.done():
            discover_task.cancel()
            discover_task = None

    if discover_task is not None:
        try:
            found = await discover_task
        except (Exception, asyncio.CancelledError) as exc:
            if not isinstance(exc, asyncio.CancelledError):
                logger.exception("discover 실패")
            found = None
        if found:
            domain_to_use, discovered_confidence = found
            discovered_source = "discover"
            await progress("discover", 30, f"도메인 발견 → {domain_to_use} (신뢰도 {discovered_confidence})")
        else:
            await progress("discover", 30, "도메인 미발견 — 수동 입력 필요")

    # 홈페이지 크롤은 도메인이 충분히 신뢰될 때만 실행:
    #   - 기존 도메인(existing_domain) → 이전 조사에서 확정된 것 (또는 사용자 입력) → OK
    #   - 새로 발견된 도메인 → discovered_confidence가 STRONG_SCORE 이상이어야 OK
    #   - borderline confidence(11 미만)는 크롤·LLM 요약 자체를 skip — 잘못된 회사 정보 박히는 것 방지
    HOMEPAGE_MIN_CONFIDENCE = 11
    skip_homepage_reason: str | None = None
    if domain_to_use:
        if discovered_source == "discover" and (discovered_confidence or 0) < HOMEPAGE_MIN_CONFIDENCE:
            skip_homepage_reason = (
                f"도메인 신뢰도 {discovered_confidence} < {HOMEPAGE_MIN_CONFIDENCE} — "
                "오인된 사이트일 수 있어 요약 skip (사용자가 정정하면 다시 시도)"
            )

    homepage_task: asyncio.Task[dict[str, Any]] | None = None
    if domain_to_use and not skip_homepage_reason:
        await progress("homepage", 35, f"홈페이지 크롤 — {domain_to_use}")
        homepage_task = asyncio.create_task(step_homepage_audit(pool, domain_to_use))
    elif skip_homepage_reason:
        await progress("homepage", 35, skip_homepage_reason)
        report["homepage_skipped"] = skip_homepage_reason

    # ───── Stage 3: reviews 완료 → LLM 감정 분석 ─────
    try:
        review_snippets: list[ReviewSnippet] = await reviews_task
    except Exception as exc:
        logger.exception("reviews 실패")
        review_snippets = []
    naver_n = sum(1 for s in review_snippets if s.source == "naver")
    google_n = sum(1 for s in review_snippets if s.source == "google")
    await progress(
        "reviews", 50,
        f"평판 스니펫 {len(review_snippets)}건 (네이버 {naver_n} + 구글 {google_n})"
    )

    emotion_task: asyncio.Task[dict[str, Any]] | None = None
    if review_snippets:
        # analyze_reviews는 str 리스트만 받음 — ReviewSnippet.text만 추출
        emotion_task = asyncio.create_task(
            analyze_reviews(ollama, company_name, [s.text for s in review_snippets])
        )

    # ───── Stage 4: homepage 완료 → LLM 구조화 ─────
    homepage_audit: dict[str, Any] | None = None
    homepage_text = ""
    if homepage_task is not None:
        try:
            homepage_audit = await homepage_task
            homepage_text = homepage_audit.get("text", "")
            await progress(
                "homepage", 65,
                f"홈페이지 본문 {len(homepage_text)}자 · 페이지 {len(homepage_audit.get('pages_visited', []))}개"
                if not homepage_audit.get("error")
                else f"홈페이지 실패: {homepage_audit['error']}",
            )
        except Exception as exc:
            logger.exception("homepage 실패")
            homepage_audit = {"error": f"{type(exc).__name__}: {exc}"}
            await progress("homepage", 65, homepage_audit["error"])

    homepage_llm_task: asyncio.Task[dict[str, Any]] | None = None
    if homepage_text and len(homepage_text) >= 300:
        await progress("homepage_llm", 70, f"홈페이지 LLM 구조화 ({len(homepage_text)}자)")
        homepage_llm_task = asyncio.create_task(
            _safe_llm_structure(
                ollama,
                homepage_text,
                COMPANY_HOMEPAGE_SCHEMA,
                instruction=(
                    "다음은 한 회사의 홈페이지에서 크롤한 본문이다 "
                    "(메인 + about/연혁/뉴스/고객사/투자 등 여러 페이지). "
                    "회사 정체성·연혁·제품·고객·시장·매출·뉴스를 풍부하게 추출하라. "
                    "각 페이지는 '## [페이지: URL]' 헤더로 구분된다. "
                    "추측 금지 — 본문에 명시된 내용만 채우고 없는 필드는 빈 문자열/배열로 둔다. "
                    "timeline은 'YYYY-MM 이벤트' 형식으로 시간 순 정렬."
                ),
            )
        )

    # ───── Stage 5: geocode + transit 결과 수합 ─────
    try:
        geocode_result = await geo_task
    except Exception as exc:
        logger.exception("geocode 실패")
        geocode_result = {"error": str(exc)}

    transit_result: dict[str, Any] | None = None
    if transit_task is not None:
        try:
            transit_result = await transit_task
        except Exception as exc:
            logger.exception("transit 실패")
            transit_result = {"error": str(exc)}

    # ───── Stage 6: LLM 결과 수합 ─────
    homepage_structured: dict[str, Any] | None = None
    if homepage_llm_task is not None:
        try:
            homepage_structured = await homepage_llm_task
        except Exception as exc:
            logger.exception("homepage LLM 실패")
            homepage_structured = {"_error": str(exc)}

    emotion_result: dict[str, Any] | None = None
    if emotion_task is not None:
        try:
            emotion_result = await emotion_task
            await progress("emotion", 90, f"감정 분석 완료")
        except Exception as exc:
            logger.exception("emotion 실패")
            emotion_result = {"_error": str(exc)}

    # ───── Stage 7: 키워드 + 영구 저장 ─────
    titles = [j.title for j in jobs]
    kw_pairs = extract_from_titles(titles, top_n=30)
    report["keyword_pairs"] = kw_pairs
    report["wordcloud"] = keywords_to_wordcloud(kw_pairs)

    await progress("save", 95, "DB 저장")
    async with async_session_maker() as session:
        company = await session.get(Company, company_id)
        if company is None:
            await progress("error", 100, "회사 사라짐 (삭제됨)")
            return {"error": "company gone mid-research"}

        # 1) domain 저장 (+ confidence + source)
        if domain_to_use and not company.domain:
            company.domain = domain_to_use
            if discovered_confidence is not None:
                company.domain_confidence = discovered_confidence
            if discovered_source is not None:
                company.domain_source = discovered_source
            report["domain"] = domain_to_use
            report["domain_confidence"] = discovered_confidence
            report["domain_source"] = discovered_source

        # 1b) DART overview JSON 저장
        if dart_overview_data:
            company.dart_overview_json = json.dumps(dart_overview_data, ensure_ascii=False)
            report["dart_overview"] = dart_overview_data

        # 2) geocode
        if geocode_result and "lat" in geocode_result and company.kakao_lat is None:
            company.kakao_lat = geocode_result["lat"]
            company.kakao_lng = geocode_result["lng"]
            company.address = geocode_result.get("address") or company.address
            report["lat"] = geocode_result["lat"]
            report["lng"] = geocode_result["lng"]
            report["address"] = geocode_result.get("address")
        report["lat"] = company.kakao_lat
        report["lng"] = company.kakao_lng
        if home_lat and home_lng and company.kakao_lat and company.kakao_lng:
            report["distance_km"] = round(
                haversine_km(home_lat, home_lng, company.kakao_lat, company.kakao_lng),
                2,
            )

        # 3) transit
        if transit_result and "total_minutes" in transit_result:
            company.transit_json = json.dumps(transit_result, ensure_ascii=False)
            report["transit"] = transit_result
        elif transit_result and "error" in transit_result:
            report["transit_error"] = transit_result["error"]

        # 4) homepage structured
        if homepage_structured and "business_summary" in homepage_structured:
            homepage_payload = {
                "domain": domain_to_use,
                "pages_visited": homepage_audit.get("pages_visited", []) if homepage_audit else [],
                "text_chars": homepage_audit.get("text_chars", 0) if homepage_audit else 0,
                **homepage_structured,
            }
            company.homepage_summary_json = json.dumps(homepage_payload, ensure_ascii=False)
            report["homepage"] = homepage_payload
        elif homepage_audit and homepage_audit.get("error"):
            report["homepage_error"] = homepage_audit["error"]

        # 5) emotion + sentiment snippets — source별로 보존 (naver / google)
        if review_snippets:
            for snippet in review_snippets:
                session.add(
                    SentimentSnippet(
                        company_id=company_id,
                        source=snippet.source,  # "naver" | "google"
                        text=snippet.text,
                        captured_at=utcnow(),
                    )
                )
            report["review_snippets_added"] = len(review_snippets)
            report["review_breakdown"] = {"naver": naver_n, "google": google_n}
        if emotion_result and "overall_score" in emotion_result:
            company.emotion_json = json.dumps(emotion_result, ensure_ascii=False)
            report["emotion"] = emotion_result

        company.last_researched_at = utcnow()
        await session.commit()

    await progress("done", 100, "조사 완료")
    return report


async def _noop_value(v: Any) -> Any:
    """이미 계산된 값을 asyncio.Task로 래핑하기 위한 헬퍼."""
    return v


async def _safe_dart_overview(corp_code: str, api_key: str) -> dict[str, Any] | None:
    """DART company.json 조회 → dict (실패 시 None, 예외 안 던짐)."""
    try:
        from app.companies.dart import fetch_overview_by_corp_code
        overview = await fetch_overview_by_corp_code(corp_code, api_key)
        return {
            "corp_code": overview.corp_code,
            "corp_name": overview.corp_name,
            "ceo_name": overview.ceo_name,
            "address": overview.address,
            "homepage": overview.homepage,
            "establishment_date": overview.establishment_date,
            "industry_code": overview.industry_code,
            "stock_code": overview.stock_code,
        }
    except Exception as exc:
        logger.info("DART overview skip: %s", exc)
        return None


async def _safe_llm_structure(
    ollama: OllamaClient,
    text: str,
    schema: dict[str, Any],
    *,
    instruction: str | None = None,
) -> dict[str, Any]:
    """LLM 구조화 — 홈페이지 긴 본문(최대 60KB)에 맞게 큰 컨텍스트 사용."""
    try:
        return await ollama.structure_text(
            text,
            schema,
            instruction=instruction,
            max_chars=_MAX_COMBINED_CHARS,
            num_ctx=32_768,
        )
    except Exception as exc:
        logger.exception("LLM structure 실패")
        return {"_error": f"{type(exc).__name__}: {exc}"}
