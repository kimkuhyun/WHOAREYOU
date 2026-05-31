"""채용 사이트 어댑터 공통 인터페이스."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from playwright.async_api import BrowserContext


@dataclass
class JobStub:
    """검색 결과 1건. 필수: title, url, source. 회사명은 가능한 한 채워라."""

    source: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    deadline: str = ""
    badges: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# ─── 검색 필터 — 우리쪽 통합 enum 값. 사이트별 코드는 각 어댑터가 변환. ───
# 사용자가 빈 문자열을 보내면 "필터 없음" — URL에 아무것도 추가하지 않음.

# "" = 전체 (모든 경력) / 그 외는 항상 "경력 무관" 공고를 OR로 포함
# (한국 채용 UX 표준: 신입 검색해도 무관 공고가 같이 떠야 자연스러움)
CAREER_OPTIONS = ("", "신입", "1-3", "3-5", "5+")
# 17개 시·도 (광역) — 3사 공통
REGION_OPTIONS = (
    "", "서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
)
# 3사 공통 — 정규/계약/인턴만 (파견·프리·알바는 제외해서 UI 깔끔)
EMPLOYMENT_OPTIONS = ("", "정규직", "계약직", "인턴")
# 학력 — 원티드는 미지원이라 자동 skip됨
EDUCATION_OPTIONS = ("", "고졸", "초대졸", "대졸", "석사", "박사")


@dataclass
class SearchFilters:
    """3사 공통 검색 필터. 빈 문자열 = 필터 없음."""

    career: str = ""        # CAREER_OPTIONS 중 하나
    region: str = ""        # REGION_OPTIONS 중 하나 (광역)
    employment: str = ""    # EMPLOYMENT_OPTIONS 중 하나
    education: str = ""     # EDUCATION_OPTIONS 중 하나 (원티드는 무시)

    def is_empty(self) -> bool:
        return not any((self.career, self.region, self.employment, self.education))


class JobAdapter(Protocol):
    source: str
    base_url: str

    async def search(
        self, ctx: BrowserContext, keyword: str, *,
        max_results: int = 30, filters: SearchFilters | None = None,
    ) -> list[JobStub]: ...


_NORMALIZE_PATTERNS = [
    r"\s*\(주\)\s*",
    r"\s*주식회사\s*",
    r"\s*\(유\)\s*",
    r"\s*유한회사\s*",
    r"\s*\(재\)\s*",
    r"\s*\(사\)\s*",
    r"\s*Inc\.?\s*",
    r"\s*Co\.,?\s*Ltd\.?\s*",
    r"\s*Ltd\.?\s*",
    r"\s*Corp\.?\s*",
]


def normalize_company_name(name: str) -> str:
    """회사명 정규화 — 법인 접두/접미사 제거. 'XX(주)' → 'XX'."""
    if not name:
        return ""
    n = name.strip()
    for pat in _NORMALIZE_PATTERNS:
        n = re.sub(pat, " ", n, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", n).strip()


__all__ = ["JobAdapter", "JobStub", "normalize_company_name"]
