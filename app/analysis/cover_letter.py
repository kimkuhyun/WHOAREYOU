"""LLM 기반 맞춤 자기소개서·지원동기 생성.

입력(전부 기존 데이터 재활용):
- 내 이력서 통합 텍스트 (analysis.resume_text.build_resume_text)
- 회사 조사 결과 (Company.homepage_summary_json / emotion_json)
- 공고 본문 (Job.jd_md)
- ATS 매칭 결과 (Job.ats_match_json) — 보유/부족 키워드를 근거로 활용

환각 방지: 이력서에 있는 사실·경험만 사용하고 없는 경력/수치는 절대 지어내지 않는다
(crawler.jd_fetcher의 "원문 사실만" 프롬프트 노하우와 동일 컨셉).
"""

from __future__ import annotations

from typing import Any

from app.crawler.llm import OllamaClient

# 사용자가 문항을 비우면 이 3개로 생성
DEFAULT_QUESTIONS: list[str] = [
    "지원 동기 (이 회사를 선택한 이유)",
    "직무 적합성 (관련 경험과 보유 역량)",
    "입사 후 포부",
]

# tone 코드 → 프롬프트 지시
_TONE_HINT: dict[str, str] = {
    "정중": "정중하고 격식 있는 문어체. 신뢰감 있고 진정성 있는 어조.",
    "간결": "군더더기 없이 간결하고 명료한 문체. 핵심 위주로 짧은 문장.",
    "열정": "적극적이고 열정적인 어조. 단, 과장·미사여구는 지양.",
}

COVER_LETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "각 문항별 자기소개서 본문",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "자소서 문항(입력받은 문항을 그대로)"},
                    "answer": {
                        "type": "string",
                        "description": (
                            "해당 문항에 대한 자기소개서 본문. 한국어, 공백 포함 약 400~700자. "
                            "이력서에 실제로 있는 경험·역량만 근거로 사용하고, 회사의 사업/비전과 구체적으로 연결할 것."
                        ),
                    },
                },
                "required": ["question", "answer"],
            },
        },
        "highlight": {
            "type": "string",
            "description": "이 회사/공고에 특히 강하게 어필되는 지원자의 강점을 1~2문장으로 요약 (작성 팁)",
        },
    },
    "required": ["items"],
}


def _summarize_homepage(homepage: dict[str, Any] | None) -> str:
    """homepage_summary_json dict → 자소서 grounding용 회사 컨텍스트 평문."""
    if not homepage:
        return ""
    parts: list[str] = []

    def _add(label: str, key: str) -> None:
        v = homepage.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(f"- {label}: {v.strip()}")

    _add("사업 내용", "business_summary")
    _add("산업", "industry")
    _add("비전/미션", "vision_mission")
    _add("창업 배경", "founding_story")
    _add("주 시장/고객", "target_market")

    for label, key in (("주요 제품/서비스", "products_services"),
                        ("주요 고객사", "main_clients"),
                        ("핵심 키워드", "keywords"),
                        ("최근 소식", "recent_news")):
        v = homepage.get(key)
        if isinstance(v, list) and v:
            joined = ", ".join(str(x) for x in v[:8] if str(x).strip())
            if joined:
                parts.append(f"- {label}: {joined}")

    return "\n".join(parts).strip()


def _summarize_ats(ats_match: dict[str, Any] | None) -> str:
    """ats_match_json → 보유/부족 키워드 힌트."""
    if not ats_match:
        return ""
    matched = list(ats_match.get("matched_required") or []) + list(ats_match.get("matched_preferred") or [])
    missing = list(ats_match.get("missing_required") or [])
    lines: list[str] = []
    if matched:
        lines.append(f"- 지원자가 이미 보유한(공고가 요구하는) 강점 키워드: {', '.join(matched[:15])}")
    if missing:
        lines.append(
            f"- 공고가 요구하지만 이력서에서 약한 키워드: {', '.join(missing[:10])} "
            "(이 부분은 무리해서 지어내지 말고, 학습 의지/연관 경험으로 자연스럽게 보완)"
        )
    return "\n".join(lines).strip()


async def generate_cover_letter(
    ollama: OllamaClient,
    *,
    company_name: str,
    job_title: str,
    resume_text: str,
    jd_md: str = "",
    company_homepage: dict[str, Any] | None = None,
    company_emotion: dict[str, Any] | None = None,
    ats_match: dict[str, Any] | None = None,
    questions: list[str] | None = None,
    tone: str = "정중",
) -> dict[str, Any]:
    """이력서 + 회사 + 공고로 문항별 자기소개서 초안 생성.

    resume_text가 비어 있으면 생성 불가 → {} 반환 (호출 측에서 안내).
    """
    if not resume_text or not resume_text.strip():
        return {}

    qs = [q.strip() for q in (questions or DEFAULT_QUESTIONS) if q and q.strip()] or DEFAULT_QUESTIONS
    tone_hint = _TONE_HINT.get(tone, _TONE_HINT["정중"])

    company_ctx = _summarize_homepage(company_homepage)
    emotion_summary = ""
    if company_emotion and isinstance(company_emotion.get("summary"), str):
        emotion_summary = company_emotion["summary"].strip()
    ats_hint = _summarize_ats(ats_match)

    # ── 본문(structure_text의 text 인자): 이력서 → 회사 → 공고 순서로 합침 ──
    blocks: list[str] = [f"# 지원자 이력서\n{resume_text.strip()}"]
    if company_ctx:
        blocks.append(f"# 지원 회사 정보 ({company_name})\n{company_ctx}")
    if emotion_summary:
        blocks.append(f"# 회사 평판 요약\n{emotion_summary}")
    if jd_md.strip():
        blocks.append(f"# 채용 공고 본문\n{jd_md.strip()}")
    if ats_hint:
        blocks.append(f"# 키워드 매칭 분석\n{ats_hint}")
    combined = "\n\n".join(blocks)

    question_lines = "\n".join(f"  {i}. {q}" for i, q in enumerate(qs, 1))
    instruction = (
        f"너는 한국 채용 자기소개서 작성을 돕는 전문가다. 위 자료(지원자 이력서 + 회사 정보 + 공고)를 바탕으로, "
        f"'{company_name}'의 '{job_title}' 직무에 지원하는 자기소개서를 아래 문항별로 작성하라.\n\n"
        f"## 작성할 문항 ({len(qs)}개 — 순서·문구 그대로 items에 담아라)\n{question_lines}\n\n"
        "## 절대 규칙\n"
        " 1. **이력서에 실제로 있는 경험·역량·성과만** 근거로 사용하라. 없는 경력/회사/수치/자격증을 지어내지 마라.\n"
        " 2. 추상적 미사여구('열정을 가지고')보다 **구체적 경험과 회사의 사업/비전을 연결**하라.\n"
        " 3. 회사 정보가 제공됐다면 그 회사만의 특성(사업·제품·비전)을 반드시 1개 이상 녹여라.\n"
        " 4. 각 답변은 한국어, 공백 포함 약 400~700자. 문항 성격에 맞게.\n"
        f" 5. 문체: {tone_hint}\n"
        " 6. 같은 문장·표현을 문항마다 반복하지 마라.\n"
        "마지막으로 highlight에는 이 지원자가 이 공고에 가장 강하게 어필되는 포인트를 1~2문장으로 적어라."
    )

    result = await ollama.structure_text(
        combined,
        COVER_LETTER_SCHEMA,
        instruction=instruction,
        max_chars=40_000,   # 이력서+회사+공고 합산이 길 수 있음
        num_ctx=32_768,     # 긴 입력 + 긴 출력 → 컨텍스트 윈도우 확장
    )
    # 입력 메타 부착 (UI 표시용)
    if result and isinstance(result, dict):
        result.setdefault("tone", tone)
    return result
