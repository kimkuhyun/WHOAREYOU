"""추천 Ollama 모델 카탈로그.

설정 페이지의 "원클릭 설치" UI가 사용하는 큐레이션된 모델 목록.
실제 설치 상태는 `ollama list`로 동적 조회해서 join.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModelRole = Literal["text", "vision", "embed"]


@dataclass(frozen=True)
class RecommendedModel:
    name: str                       # ollama pull 식별자
    role: ModelRole                 # 용도
    size_gb: float                  # 대략 크기 (UI 표시용)
    label: str                      # 사람이 읽는 짧은 이름
    description: str                # 한 줄 설명
    recommended: bool = False       # 기본 추천 여부 (UI에 ★ 표시)


# 큐레이션 — 너무 많이 넣으면 UI 복잡해짐. 실제 잘 쓰이는 것만.
CATALOG: tuple[RecommendedModel, ...] = (
    RecommendedModel(
        name="qwen3.5:9b",
        role="text",
        size_gb=6.6,
        label="Qwen 3.5 9B",
        description="한국어/구조화 출력 가장 안정 · 기본 추천",
        recommended=True,
    ),
    RecommendedModel(
        name="qwen3.6:27b",
        role="text",
        size_gb=17.4,
        label="Qwen 3.6 27B",
        description="더 정확하지만 VRAM 20GB+ 필요",
    ),
    RecommendedModel(
        name="qwen2.5:7b",
        role="text",
        size_gb=4.7,
        label="Qwen 2.5 7B",
        description="가벼운 텍스트 · 저사양 PC 대안",
    ),
    RecommendedModel(
        name="qwen2.5vl:7b",
        role="vision",
        size_gb=6.0,
        label="Qwen 2.5 VL 7B",
        description="JD 이미지/스크린샷 OCR · 기본 추천",
        recommended=True,
    ),
    RecommendedModel(
        name="llama3.2-vision:11b",
        role="vision",
        size_gb=7.8,
        label="Llama 3.2 Vision 11B",
        description="비전 대안 모델",
    ),
    RecommendedModel(
        name="bge-m3:latest",
        role="embed",
        size_gb=1.2,
        label="BGE-M3",
        description="다국어 임베딩 · 향후 검색 강화용",
    ),
)


def find_in_catalog(name: str) -> RecommendedModel | None:
    """카탈로그에서 모델 검색. 별도 이름(:latest 등) 대응."""
    n = name.strip().lower()
    if not n:
        return None
    # 정확 매칭
    for m in CATALOG:
        if m.name.lower() == n:
            return m
    # 태그 무시 매칭 (qwen3.5:9b == qwen3.5)
    base = n.split(":")[0]
    for m in CATALOG:
        if m.name.lower().split(":")[0] == base:
            return m
    return None
