# -*- coding: utf-8 -*-
"""좋소 = jotso.net 직접 연결 (좋소력·등급·회전율). curl_cffi(Cloudflare 우회) + 캐시.

좋소력 = 낮을수록 좋음(jotso 기준). 종합점수 반영은 Scorer가 (100-좋소력)로 변환.
"""
import random
import re
import time
from dataclasses import dataclass, asdict

from curl_cffi import requests as creq

import config


def _norm(s: str) -> str:
    return re.sub(r"\(주\)|\(유\)|\(재\)|주식회사|㈜|\s", "", s or "")


@dataclass
class JotsoResult:
    found: bool
    jotso_score: int | None = None   # 좋소력(0~100, 낮을수록 좋음)
    label: str = ""
    name: str = ""
    detail: str = ""


class JotsoClient:
    SEARCH = "https://jotso.net/api/search"
    COMPANY = "https://jotso.net/company/{}"

    def __init__(self, cache=None):
        self.cache = cache

    def lookup(self, company: str) -> JotsoResult:
        key = f"jotso:{_norm(company)}"
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return JotsoResult(**hit)
        res = self._fetch(company)
        if self.cache is not None:
            if res.found:
                self.cache.set(key, asdict(res), config.JOTSO_CACHE_DAYS)
            elif res.detail in ("미등록", "이름매칭실패"):
                self.cache.set(key, asdict(res), config.CACHE_MISS_DAYS)  # jotso에 없음 → 짧게 캐시
            # 검색실패·회사페이지실패(일시 네트워크) → 캐시 안 함(다음에 재시도)
        return res

    def _fetch(self, company: str) -> JotsoResult:
        # 실제 네트워크 호출 전 보수적 지연(캐시 적중 시엔 _fetch 자체를 안 부르므로 즉시)
        time.sleep(config.ENRICH_DELAY_S + random.uniform(0, config.ENRICH_JITTER_S))
        try:
            results = creq.get(self.SEARCH, params={"q": company},
                               impersonate="chrome", timeout=15).json().get("results", [])
        except Exception:
            return JotsoResult(False, detail="검색실패")
        if not results:
            return JotsoResult(False, detail="미등록")
        q = _norm(company)
        pick = next((x for x in results
                     if _norm(x.get("bizName", "")) and
                     (q in _norm(x["bizName"]) or _norm(x["bizName"]) in q)), None)
        if not pick:
            return JotsoResult(False, detail="이름매칭실패")
        time.sleep(config.JOTSO_INTRA_DELAY_S)  # 검색 → 회사페이지 사이 지연(보수적)
        try:
            t = creq.get(self.COMPANY.format(pick["bizNo"]),
                         impersonate="chrome", timeout=15).text.replace('\\"', '"')
        except Exception:
            return JotsoResult(False, detail="회사페이지실패")
        sc = re.search(r"좋소력 (?:<!-- -->)?(\d+)", t) or re.search(r'"score":(\d+)', t)
        lab = re.search(r"—\s*([^(—]{2,10})\s*\(좋소력", t) or re.search(r'"label":"([^"]{2,10})"', t)
        turn = re.search(r"회전율 (\d+)%", t)
        return JotsoResult(True,
                           int(sc.group(1)) if sc else None,
                           lab.group(1).strip() if lab else "",
                           pick["bizName"],
                           f"회전 {turn.group(1)}%" if turn else "")
