# -*- coding: utf-8 -*-
"""4신호 종합 (가중합 · 결측은 0 아니라 '판단 보류'로 빼고 재정규화).

- 좋소력은 낮을수록 좋음 → 기여 = (100 - 좋소력).
- 1단계(싼 신호=match·commute)만으로도 composite 가능(jotso·평판 None).
"""
import config


class Scorer:
    def __init__(self, weights: dict | None = None):
        self.w = weights or config.WEIGHTS

    @staticmethod
    def commute_norm(minutes) -> float | None:
        if minutes is None:
            return None
        best, worst = config.COMMUTE_BEST_MIN, config.COMMUTE_WORST_MIN
        return max(0.0, min(1.0, (worst - minutes) / (worst - best))) * 100

    def composite(self, *, match, commute_min, jotso_score, stars):
        signals = {
            "match": match,
            "commute": self.commute_norm(commute_min),
            "jotso": (100 - jotso_score) if jotso_score is not None else None,
            "reputation": (stars / 5 * 100) if stars is not None else None,
        }
        # 알려진 축만 순회(가중치 dict에 오타 키 있어도 KeyError로 수집 전체 죽지 않게)
        num = den = 0.0
        present = 0                        # 실제 존재하는(결측 아닌) 신호 수
        for key in signals:
            v = signals[key]
            if v is None:
                continue
            present += 1
            weight = self.w.get(key, 0)
            num += v * weight
            den += weight
        if present == 0:
            return None, signals           # 진짜 신호 없음 = 판단 보류
        if den == 0:
            # 가중치 합이 0(전부 0으로 드래그 등) → 균등 가중으로 폴백(추천이 영영 빔 방지)
            num = sum(signals[k] for k in signals if signals[k] is not None)
            den = present
        total = round(num / den)
        return total, signals
