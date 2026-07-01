# -*- coding: utf-8 -*-
"""이력서 매칭 = 어휘(ats: kiwi+rapidfuzz+스킬사전) + 의미(리랭커 cross-encoder) 하이브리드(§6).

- 어휘/스킬사전: 용어·동의어 정밀(ML=머신러닝). 전 직군의 공통 용어 매칭.
- 리랭커: 이력서 '스킬요약' ↔ JD 적합도(전 직군 일반화). sentence-transformers 없으면 어휘만.
이력서 없음/JD 없음(이미지 OCR 실패) → None(표본부족)로 재정규화.
"""
import os

import pdfplumber

import ats
import config


class ResumeMatcher:
    def __init__(self, pdf_path: str = ""):
        self.path = pdf_path or ""
        self.text = self._read(self.path)
        self._skills = self._skill_summary(self.text)

    @staticmethod
    def _read(pdf_path: str) -> str:
        if not pdf_path or not os.path.exists(pdf_path):
            return ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:
            return ""

    @staticmethod
    def _skill_summary(text: str) -> str:
        """이력서에서 스킬/직무 키워드만 뽑은 짧은 요약(리랭커 질의용 — 전체 이력서는 변별 실패)."""
        if not text:
            return ""
        return " ".join(ats._dedup_preserve(ats._extract_terms(text), limit=50))

    def load(self, pdf_path: str) -> bool:
        self.path = pdf_path or ""
        self.text = self._read(self.path)
        self._skills = self._skill_summary(self.text)
        return bool(self.text)

    @staticmethod
    def _kw_join(jd_kw) -> str:
        """extract_jd_keywords 결과(dict of lists)를 리랭커 passage 문자열로 평탄화."""
        if isinstance(jd_kw, dict):
            terms = []
            for v in jd_kw.values():
                terms += v if isinstance(v, (list, tuple)) else [v]
            return " ".join(map(str, terms))
        if isinstance(jd_kw, (list, tuple)):
            return " ".join(map(str, jd_kw))
        return str(jd_kw or "")

    def score(self, jd: str):
        """반환 (0~100 | None, 상세). 이력서 없으면 None(표본부족)."""
        if not self.text:
            return None, {"score": None, "note": "이력서 미설정"}
        jd_kw = ats.extract_jd_keywords(jd or "")
        res = ats.ats_match(self.text, jd_kw)
        lex = res.get("score")                    # 어휘+스킬사전 0~100 | None
        rer = None                                # 리랭커 의미 0~100
        if config.USE_RERANKER and jd and jd.strip():
            try:
                import reranker
                # ⚠ 원문 JD는 회사소개·복지·지원방법 노이즈로 리랭커 신호가 희석됨(실측: 풀JD 2724자 → 4점).
                #   추출 키워드 passage와 원문을 함께 batch(1콜)로 넣고 강한 신호 채택(max) →
                #   노이즈 희석 + 키워드 추출 손실 양쪽 방어(실측 AI Agent 4→44·75, 마케팅 0 유지).
                kw_txt = self._kw_join(jd_kw)
                passages = [jd] + ([kw_txt] if kw_txt else [])
                sc = reranker.scores(self._skills, passages)
                if sc:
                    rer = max(sc)
            except Exception:
                rer = None
        if lex is not None and rer is not None:
            w = config.MATCH_RERANK_W
            score = round((1 - w) * lex + w * rer)
        else:
            score = rer if rer is not None else lex
        res["score"] = score
        res["lexical"] = lex
        res["reranker"] = rer
        return score, res
