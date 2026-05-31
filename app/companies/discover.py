"""회사 홈페이지 도메인 자동 발견.

전략 (우선순위):
1. 회사의 잡 상세 페이지(saramin/jobkorea/wanted)에서 외부 홈페이지 링크 추출 — 가장 정확
2. 네이버 통합 검색 결과의 외부 도메인 (블랙리스트 적용)
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

NAVER_SEARCH = "https://search.naver.com/search.naver"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}

# 공식 홈페이지가 아닐 도메인
DOMAIN_BLACKLIST = {
    # 검색엔진
    "naver.com", "naver.net", "pstatic.net", "daum.net",
    "google.com", "duckduckgo.com", "bing.com",
    # 카카오 인프라
    "kakao.com", "pf.kakao.com", "kakaocorp.com",
    # 잡사이트
    "saramin.co.kr", "jobkorea.co.kr", "wanted.co.kr", "jobplanet.co.kr",
    "teamblind.com", "indeed.com", "kr.indeed.com", "jumpit.co.kr",
    "rallit.com", "comup.work", "programmers.co.kr", "glints.com", "remoteok.io",
    # 정보 집계 / VC / 스타트업 DB
    "rocketpunch.com", "thevc.kr", "thebigdata.co.kr", "innoforest.co.kr",
    "thingool.com", "ko.startupranking.com", "ridi.io", "platum.kr",
    "korea-startups.com", "kvic.or.kr", "startup.go.kr",
    # 공공정보
    "opendart.fss.or.kr", "data.go.kr", "bizinfo.go.kr",
    "credit.co.kr", "innobiz.or.kr", "smes.go.kr", "jobindexworld.com",
    # 위키
    "wikipedia.org", "namu.wiki", "namu.live",
    # SNS / 영상
    "youtube.com", "youtu.be", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "linkedin.com", "tiktok.com", "threads.net",
    # 블로그 / 컨텐츠 플랫폼
    "tistory.com", "brunch.co.kr", "medium.com", "velog.io", "notion.site",
    "ridicorp.com",
    # 뉴스
    "biz.chosun.com", "news.chosun.com", "joins.com", "mt.co.kr",
    "edaily.co.kr", "hankyung.com", "mk.co.kr", "yna.co.kr",
    "yonhapnews.co.kr", "news.naver.com", "post.naver.com",
    # 앱스토어
    "play.google.com", "apps.apple.com",
    # 코드 호스팅
    "github.com", "gitlab.com", "bitbucket.org",
    # ★ 추적/광고/태그 매니저 (잡 페이지 HTML에 자동 삽입되어 잘못 추출되는 도메인)
    "googletagmanager.com", "google-analytics.com", "googleadservices.com",
    "googletagservices.com", "doubleclick.net", "adservice.google.com",
    "gstatic.com", "googleapis.com", "googleusercontent.com", "googlesyndication.com",
    "recaptcha.net", "g.doubleclick.net",
    # 분석/마케팅 SaaS
    "hotjar.com", "mixpanel.com", "segment.io", "amplitude.com",
    "intercom.io", "intercomcdn.com", "fullstory.com",
    "facebook.net", "fbcdn.net", "tr.snapchat.com", "linkedinanalytics.com",
    "twimg.com", "pinimg.com",
    "criteo.com", "criteo.net", "taboola.com", "outbrain.com",
    "adobedtm.com", "demdex.net", "everesttech.net", "omtrdc.net",
    # CDN / 호스팅 (라이브러리)
    "cloudflare.com", "cloudfront.net", "akamai.net", "akamaihd.net",
    "jsdelivr.net", "unpkg.com", "bootstrapcdn.com", "fontawesome.com",
    "jquery.com", "cdnjs.cloudflare.com", "gravatar.com",
    "amazonaws.com", "s3.amazonaws.com",
    "vercel.app", "netlify.app", "herokuapp.com",
    # 폰트
    "fonts.googleapis.com", "fonts.gstatic.com", "use.fontawesome.com",
    # 기타 흔한 외부 임베드
    "wp.com", "wordpress.com", "tawk.to", "zendesk.com",
}


def _is_blacklisted(host: str) -> bool:
    h = host.lower()
    if h.startswith("www."):
        h = h[4:]
    if any(h == b or h.endswith("." + b) for b in DOMAIN_BLACKLIST):
        return True
    return False


def _extract_external_domains(html: str) -> list[str]:
    """네이버 검색 결과 HTML에서 외부 호스트 목록을 등장 순으로 반환 (중복 제거)."""
    seen: set[str] = set()
    out: list[str] = []
    for href in re.findall(r'href="(https?://[^"#?]+)"', html):
        host = urlparse(href).netloc.lower()
        if not host or host in seen:
            continue
        seen.add(host)
        out.append(host)
    return out


async def discover_from_job_pages(
    job_urls: list[str], company_name: str = "", *, timeout: float = 12.0
) -> str | None:
    """채용 사이트의 잡 상세 페이지에서 회사 공식 홈페이지 링크를 찾는다.

    잡코리아/사람인 상세 페이지 본문에는 보통 회사 홈페이지 URL이 평문/링크로 들어있음.
    httpx로 fetch 후 외부 도메인 중 첫 번째 화이트 통과 도메인을 반환.
    """
    if not job_urls:
        return None
    async with httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
        for url in job_urls[:3]:  # 최대 3개 잡만 시도
            try:
                r = await client.get(url)
            except Exception as exc:
                logger.info("잡 페이지 fetch 실패 (%s): %s", url, exc)
                continue
            if r.status_code != 200:
                continue
            r.encoding = "utf-8"
            html = r.text
            # 화이트리스트 통과한 외부 도메인 등장 순으로 수집
            for host in _extract_external_domains(html):
                if _is_blacklisted(host):
                    continue
                # 같은 잡 사이트 도메인은 제외
                source_host = urlparse(url).netloc.lower()
                if host == source_host or host.endswith("." + source_host):
                    continue
                return f"https://{host}"
    return None


async def discover_domain(company_name: str, *, timeout: float = 10.0) -> str | None:
    if not company_name or not company_name.strip():
        return None
    q = quote(f"{company_name.strip()} 공식 홈페이지")
    url = f"{NAVER_SEARCH}?query={q}"
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=timeout, follow_redirects=True
        ) as client:
            r = await client.get(url)
    except Exception as exc:
        logger.info("Naver 검색 실패 (%s): %s", company_name, exc)
        return None
    if r.status_code != 200 or len(r.text) < 5_000:
        logger.info("Naver 응답 비정상 (%s): status=%d len=%d", company_name, r.status_code, len(r.text))
        return None

    candidates = _extract_external_domains(r.text)
    for host in candidates:
        if _is_blacklisted(host):
            continue
        return f"https://{host}"
    logger.info("회사 도메인 후보 없음 (%s)", company_name)
    return None
