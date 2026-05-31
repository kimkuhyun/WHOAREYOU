"""LLM 기반 텍스트 감정 분류.

여러 리뷰 스니펫을 한 번에 분석해 긍/부정/중립 카운트 + 대표 키워드 추출.
"""

from __future__ import annotations

from typing import Any

from app.crawler.llm import OllamaClient

EMOTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "number", "description": "-1.0(매우 부정) ~ 1.0(매우 긍정) 평균 감정"},
        "positive_count": {"type": "integer"},
        "negative_count": {"type": "integer"},
        "neutral_count": {"type": "integer"},
        "positive_keywords": {"type": "array", "items": {"type": "string"}, "description": "회사에 대해 자주 언급되는 긍정 키워드 5개"},
        "negative_keywords": {"type": "array", "items": {"type": "string"}, "description": "부정 키워드 5개"},
        "summary": {"type": "string", "description": "2-3문장으로 종합 평가"},
    },
    "required": ["overall_score", "positive_count", "negative_count", "neutral_count", "summary"],
}


async def analyze_reviews(
    ollama: OllamaClient,
    company_name: str,
    review_snippets: list[str],
) -> dict[str, Any]:
    if not review_snippets:
        return {}
    joined = "\n---\n".join(s.strip() for s in review_snippets if s and s.strip())
    if not joined.strip():
        return {}
    instruction = (
        f"회사 '{company_name}'에 대한 다음 리뷰/댓글 스니펫들을 종합 분석해라. "
        "각 스니펫이 긍정/부정/중립인지 분류해 카운트하고, 전체 감정 점수와 자주 등장하는 긍정/부정 키워드를 뽑아라."
    )
    return await ollama.structure_text(joined, EMOTION_SCHEMA, instruction=instruction)
