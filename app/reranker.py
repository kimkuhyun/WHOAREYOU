# -*- coding: utf-8 -*-
"""서류-공고 의미 매칭 = cross-encoder 리랭커(bge-reranker-v2-m3). 전 직군 일반화(§6).

⚠ 실측: 전체 이력서↔JD는 변별 안 됨(문서-문서). **이력서 스킬요약 ↔ JD** 순서라야 변별됨
   (AI 0.15 > 디자이너 0.08 > 주방 0.0). 무거움(torch+2.3GB) → 지연로드+graceful.
   임베딩(bi-encoder)·리랭커 모두 '짧은 질의↔문서'용이라 스킬요약을 질의로 씀.
"""
import config

_model = None
_failed = False


def _get():
    global _model, _failed
    if _failed or not config.USE_RERANKER:
        return None
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(config.RERANK_MODEL, max_length=512)
        except Exception:
            _failed = True
            return None
    return _model


def scores(query: str, passages: list[str]) -> list[float]:
    """query(이력서 스킬요약) ↔ 각 passage(JD) 적합도 0~100. 모델 없으면 [] (폴백)."""
    m = _get()
    if m is None or not query or not passages:
        return []
    try:
        import numpy as np
        raw = m.predict([(query, p or "") for p in passages])
        # 로짓 압축 보정: 좋은 매칭 ~0.15 → 약 75점 되게 스케일 + 상한
        return [float(min(100.0, max(0.0, x * config.RERANK_SCALE))) for x in np.asarray(raw)]
    except Exception:
        return []
