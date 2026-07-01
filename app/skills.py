# -*- coding: utf-8 -*-
"""스킬 정규화 사전 — ML=머신러닝=기계학습 등 약어·동의어·한영을 한 표제어로(§6 보조)."""


class SkillNormalizer:
    # 표제어: [동의어/약어/영문...]
    SYN = {
        "머신러닝": ["ml", "machine learning", "기계학습"],
        "딥러닝": ["dl", "deep learning"],
        "쿠버네티스": ["k8s", "kubernetes"],
        "도커": ["docker"],
        "프론트엔드": ["fe", "front-end", "frontend", "프론트"],
        "백엔드": ["be", "back-end", "backend", "서버개발", "서버 개발"],
        "파이썬": ["python", "py"],
        "자바스크립트": ["js", "javascript"],
        "타입스크립트": ["ts", "typescript"],
        "자연어처리": ["nlp", "natural language processing"],
        "데이터엔지니어": ["data engineer", "데이터 엔지니어"],
        "데브옵스": ["devops"],
        "씨아이씨디": ["ci/cd", "cicd"],
    }

    def __init__(self):
        self._lut = {}
        for canon, syns in self.SYN.items():
            self._lut[canon.lower()] = canon
            for s in syns:
                self._lut[s.lower()] = canon

    def normalize(self, token: str) -> str:
        return self._lut.get((token or "").strip().lower(), (token or "").strip())

    def same(self, a: str, b: str) -> bool:
        return self.normalize(a) == self.normalize(b)

    def variants(self, token: str) -> set[str]:
        """토큰의 동의어 그룹 전체(표제어+약어+한영) 소문자 — 이력서 substring 매칭용."""
        canon = self.normalize(token)
        out = {canon.lower()}
        if canon in self.SYN:
            out |= {s.lower() for s in self.SYN[canon]}
        return out
