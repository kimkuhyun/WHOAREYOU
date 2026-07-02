# -*- coding: utf-8 -*-
"""위생/선스킵 — 블랙리스트(회사·포괄임금·열정페이)를 점수 전에 제거(§4·§4a).

본 공고/제외한 공고 스킵은 store.is_handled로 어댑터 수집 단계에서 처리 → 여기선 블랙리스트만.
"""


class Dedup:
    BLACKLIST_KEYWORDS = ["포괄임금", "열정페이"]

    def __init__(self, blacklist_companies=None):
        self.blacklist_companies = set(blacklist_companies or [])

    def should_skip(self, job) -> tuple[bool, str]:
        if job.company in self.blacklist_companies:
            return True, "블랙리스트 회사"
        text = f"{job.title} {job.jd}"
        for kw in self.BLACKLIST_KEYWORDS:
            if kw in text:
                return True, f"블랙리스트 키워드:{kw}"
        return False, ""

    def filter(self, jobs: list) -> tuple[list, list]:
        """반환 (통과, 스킵[(job, 사유)])."""
        passed, skipped = [], []
        for j in jobs:
            skip, why = self.should_skip(j)
            (skipped.append((j, why)) if skip else passed.append(j))
        return passed, skipped
