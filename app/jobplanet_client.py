# -*- coding: utf-8 -*-
"""평판 = 잡플래닛 별점 (curl_cffi chrome 위장 · 이름매칭 · grade) + 캐시."""
import random
import re
import time
from urllib.parse import quote

from curl_cffi import requests as creq
from rapidfuzz import fuzz

import config


def _norm(s: str) -> str:
    return re.sub(r"\(주\)|\(유\)|\(재\)|주식회사|㈜|\s", "", s or "")


class JobplanetClient:
    SEARCH = "https://www.jobplanet.co.kr/search?query={}&category=company"

    def __init__(self, cache=None):
        self.cache = cache

    def rating(self, company: str):
        """반환 (stars 0~5 | None, detail)."""
        key = f"jp:{_norm(company)}"
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit.get("s"), hit.get("d", "")
        stars, detail = self._fetch(company)
        if self.cache is not None:
            if stars is not None:
                self.cache.set(key, {"s": stars, "d": detail}, config.JOTSO_CACHE_DAYS)
            elif detail in ("미등록", "리뷰없음"):
                self.cache.set(key, {"s": None, "d": detail}, config.CACHE_MISS_DAYS)
            # 차단·오류·결과없음(일시) → 캐시 안 함
        return stars, detail

    def _fetch(self, company: str):
        time.sleep(config.ENRICH_DELAY_S + random.uniform(0, config.ENRICH_JITTER_S))  # 실제 호출 전 지연
        try:
            s = creq.get(self.SEARCH.format(quote(company)), impersonate="chrome", timeout=20)
        except Exception:
            return None, "오류"
        if s.status_code != 200 or len(s.text) < 8000:
            return None, f"차단({s.status_code})"
        raw = re.findall(r'"name":"([^"]{2,40})","grade":([0-5]\.?\d*),"grade_count":(\d+)', s.text)
        if not raw:
            return None, "결과없음"
        q = _norm(company)
        seen = {}
        for nm, g, gc in raw:
            if nm not in seen:
                seen[nm] = (float(g), int(gc))
        best = None
        for nm, (g, gc) in seen.items():
            nn = _norm(nm)
            ms = 100 if (q and (q in nn or nn in q)) else fuzz.ratio(q, nn)
            if best is None or ms > best[0]:
                best = (ms, nm, g, gc)
        if not best or best[0] < 70:
            return None, "미등록"
        _, nm, g, gc = best
        if gc == 0 or g == 0:
            return None, "리뷰없음"
        return g, f"★{g} ({nm[:10]} 리뷰{gc})"
