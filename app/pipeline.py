# -*- coding: utf-8 -*-
"""파이프라인 — 수집(3사) → dedup 선스킵 → 2단계 채점(싼 신호→상위 enrich) → 저장.

좋소·평판 enrich는 상위 N만 + dedup + 캐시 + throttle(§7). 결과 reco dict 리스트 반환.
"""
import asyncio

import httpx

import config
from commute import CommuteScorer
from crawler import Crawler
from dedup import Dedup
from jobfilter import passes as _passes
from jobplanet_client import JobplanetClient
from jotso_client import JotsoClient
from matcher import ResumeMatcher
from scoring import Scorer


class Pipeline:
    def __init__(self, settings: dict, cache, store):
        self.settings = settings
        self.store = store
        self.matcher = ResumeMatcher(config.RESUME_PDF)
        self.commute = CommuteScorer(settings)
        self.jotso = JotsoClient(cache)
        self.jp = JobplanetClient(cache)
        self.scorer = Scorer()
        self.dedup = Dedup()
        self.last_stats = {}

    async def run(self, keyword: str | None = None, per_site: int | None = None,
                  exclude: list[str] | None = None, filters: dict | None = None) -> list[dict]:
        kw = keyword or config.KEYWORD
        per = per_site or config.PER_SITE
        async with httpx.AsyncClient(headers=config.HEADERS, timeout=config.TIMEOUT,
                                     follow_redirects=True) as c:
            # 어댑터가 "필터통과+미처리(is_handled)+미중복"으로 사이트당 per_site개 수집(SCAN_CAP까지)
            jobs = await Crawler(c).collect(kw, per, filters, skip=self.store.is_handled)
            n_crawled = len(jobs)
            jobs, _ = self.dedup.filter(jobs)                                # 블랙리스트(회사·포괄임금 등)
            ex = [e.lower() for e in (exclude or []) if e.strip()]           # 제외키워드(3사 공통·결정론)
            if ex:
                jobs = [j for j in jobs
                        if not any(e in f"{j.title} {j.company} {j.jd}".lower() for e in ex)]
            n_filtered = len(jobs)

            # 이미지 공고 OCR(필터 통과분만 — 이미지 전부 읽어 합침. easyocr 없으면 제목폴백). URL 캐시
            if config.USE_OCR:
                import ocr as _ocr
                for j in jobs:
                    if getattr(j, "is_image", False) and j.img_urls:
                        texts = []
                        for iu in j.img_urls[:8]:              # 이미지 최대 8장까지 OCR
                            try:
                                content = (await c.get(iu)).content
                                t = await asyncio.to_thread(_ocr.image_text, content, iu)
                                if t:
                                    texts.append(t)
                            except Exception:
                                pass
                        if texts:
                            j.jd = f"{j.title}\n" + "\n".join(texts)

            # 1단계: 싼 신호(매칭·통근) — 통근은 통근 전용 동시성(후보 많아도 빠르게)
            sem = asyncio.Semaphore(config.COMMUTE_CONCURRENCY)

            async def cheap(j):
                ms, _ = self.matcher.score(j.jd)
                async with sem:
                    cmin, cd = await self.commute.minutes(j.lat, j.lng, j.address)
                partial, _ = self.scorer.composite(match=ms, commute_min=cmin, jotso_score=None, stars=None)
                return {"job": j, "match": ms, "cmin": cmin, "cd": cd, "partial": partial}

            scored = await asyncio.gather(*[cheap(j) for j in jobs]) if jobs else []
            scored.sort(key=lambda x: (x["partial"] is not None, x["partial"] or 0), reverse=True)

            # 2단계: 상위 N만 enrich(회사 dedup + throttle + 캐시)
            top = scored[:config.ENRICH_TOP_N]
            uniq = list({s["job"].company for s in top if s["job"].company})
            esem = asyncio.Semaphore(config.ENRICH_CONCURRENCY)

            async def enrich(name):
                async with esem:
                    jr = await asyncio.to_thread(self.jotso.lookup, name)
                    stars, rd = await asyncio.to_thread(self.jp.rating, name)
                    return name, (jr, stars, rd)

            enriched = dict(await asyncio.gather(*[enrich(n) for n in uniq])) if uniq else {}

        recos = []
        for s in scored:                 # 표시=필터통과분 전량(침묵 절단 금지 §1). 좋소·평판은 top(상한)만 조회 → 상한 밖은 '표본 부족'
            j = s["job"]
            jr, stars, rd = enriched.get(j.company, (None, None, ""))
            total, sig = self.scorer.composite(match=s["match"], commute_min=s["cmin"],
                                               jotso_score=(jr.jotso_score if jr else None), stars=stars)
            reco = {
                "url": j.url, "source": j.source, "company": j.company, "title": j.title,
                "total": total,
                "match": s["match"],
                "commute": round(sig["commute"]) if sig["commute"] is not None else None,
                "commute_d": s["cd"],
                "jotso_score": jr.jotso_score if jr else None,
                "jotso_label": jr.label if (jr and jr.found) else "",
                "reputation": round(sig["reputation"]) if sig["reputation"] is not None else None,
                "rep_d": rd,
            }
            self.store.save(reco)
            recos.append(reco)
        recos.sort(key=lambda r: (r["total"] is not None, r["total"] or 0), reverse=True)
        self.last_stats = {"crawled": n_crawled, "filtered": n_filtered, "shown": len(recos)}
        return recos
