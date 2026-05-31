"""Ollama 클라이언트 — JSON Schema 강제 구조화 출력 + 비전.

Ollama 공식 Python 클라이언트의 `format=<json_schema>` 기능을 사용해 LLM이 정확한
스키마로 응답하도록 강제한다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ollama import AsyncClient

logger = logging.getLogger(__name__)

JOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "채용 공고의 직무명"},
        "company": {"type": "string", "description": "회사명. 모르면 빈 문자열."},
        "location": {"type": "string", "description": "근무 지역. 모르면 빈 문자열."},
        "employment_type": {"type": "string", "description": "정규직/계약직/인턴 등. 모르면 빈 문자열."},
        "experience": {"type": "string", "description": "신입/경력/무관 등. 모르면 빈 문자열."},
        "deadline": {"type": "string", "description": "마감일 (YYYY-MM-DD 또는 자유 형식). 없으면 빈 문자열."},
        "salary": {"type": "string", "description": "연봉/급여 정보. 없으면 빈 문자열."},
        "responsibilities": {"type": "array", "items": {"type": "string"}, "description": "주요 업무 bullet"},
        "qualifications": {"type": "array", "items": {"type": "string"}, "description": "자격 요건 bullet"},
        "preferred": {"type": "array", "items": {"type": "string"}, "description": "우대 사항 bullet"},
        "benefits": {"type": "array", "items": {"type": "string"}, "description": "복리후생 bullet"},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "이 공고를 대표하는 기술/직무 키워드 10개 이내"},
    },
    "required": ["title", "company"],
}

COMPANY_HOMEPAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # ─── 핵심 ───
        "business_summary": {"type": "string", "description": "회사가 무엇을 하는 곳인지 2-4문장 한국어 요약. 단순한 한 줄 소개가 아니라 실제 사업 내용·고객 가치 포함."},
        "industry": {"type": "string", "description": "산업 분야 (예: IT 서비스, 핀테크, 바이오, 제조, 게임, 헬스케어, 에듀테크 등)"},
        "products_services": {"type": "array", "items": {"type": "string"}, "description": "주요 제품/서비스명 8개 이내. 각 항목은 '제품명 — 한 줄 설명' 형식 권장."},
        "vision_mission": {"type": "string", "description": "기업 이념/미션/비전 1-3문장. 페이지에 명시되어 있을 때만."},

        # ─── 회사 기본 정보 ───
        "founded_year": {"type": "string", "description": "설립 연도 (YYYY 또는 YYYY-MM). 페이지에서 못 찾으면 빈 문자열. 추측 금지."},
        "founding_story": {"type": "string", "description": "창업 배경/창업자/창업 동기. 페이지에 있을 때만 1-3문장으로."},
        "team_size": {"type": "string", "description": "직원 수 또는 규모. 예: '50명 내외', '시리즈 B 단계 스타트업'. 모르면 빈 문자열."},
        "headquarter_location": {"type": "string", "description": "본사 위치 (시·구·도로명 수준). 모르면 빈 문자열."},
        "office_locations": {"type": "array", "items": {"type": "string"}, "description": "지사·해외 사무소·연구소 위치 배열. 본사만 있으면 빈 배열."},

        # ─── 시장 & 고객 ───
        "target_market": {"type": "string", "description": "주 활동 시장. 예: 'B2B SaaS, 국내 제조 대기업', 'B2C, 글로벌 (북미·동남아 중심)'. 모르면 빈 문자열."},
        "main_clients": {"type": "array", "items": {"type": "string"}, "description": "주요 고객사·파트너사·도입 기업명 10개 이내. 페이지에 로고·언급이 있을 때만."},

        # ─── 기술 & 키워드 ───
        "tech_stack": {"type": "array", "items": {"type": "string"}, "description": "사용 기술 스택 (IT 회사인 경우). 명시되어 있을 때만."},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "회사를 대표하는 핵심 키워드 12개 이내. 사업·기술·문화 등 다양하게."},

        # ─── 연혁 & 뉴스 ───
        "timeline": {
            "type": "array",
            "items": {"type": "string"},
            "description": "회사 연혁/마일스톤 배열. 각 항목 'YYYY-MM 이벤트' 형식 권장. 예: '2023-08 시리즈 A 100억 투자 유치'. 페이지에 있을 때만 10개 이내."
        },
        "recent_news": {"type": "array", "items": {"type": "string"}, "description": "최근 이슈/뉴스/보도자료 제목 5개 이내. 날짜 있으면 함께."},
        "press_summary": {"type": "array", "items": {"type": "string"}, "description": "주요 보도자료/언론 노출 요약 5개 이내. '제목 — 1줄 요약' 형식."},

        # ─── 매출/투자 ───
        "revenue_or_funding": {"type": "string", "description": "매출·투자 유치 정보. 예: '2023 매출 50억', '시리즈 B 200억 누적'. 페이지에 있을 때만."},
    },
    "required": ["business_summary", "industry"],
}


JD_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_job_posting": {
            "type": "boolean",
            "description": "이미지가 채용 공고(주요업무·자격요건·우대사항·복리후생 등을 담은 모집공고)인지 여부. 단순 로고/배너/광고면 false.",
        },
        "markdown": {
            "type": "string",
            "description": (
                "이미지에서 인식한 채용 공고 본문을 한국어 마크다운으로 정리. "
                "## 주요업무, ## 자격요건, ## 우대사항, ## 복리후생, ## 근무조건 등의 헤더를 사용하고 "
                "각 항목은 '- ' bullet로 나열. 인식할 수 없으면 빈 문자열."
            ),
        },
    },
    "required": ["is_job_posting", "markdown"],
}


GENERIC_PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_type": {
            "type": "string",
            "description": "페이지 유형. 다음 중 하나: job_posting | company_about | product | news_article | community_post | other",
        },
        "summary": {"type": "string", "description": "페이지의 1-3문장 한국어 요약"},
        "entities": {"type": "array", "items": {"type": "string"}, "description": "본문에 등장하는 회사/기관/제품/인물 등 고유명사 최대 10개"},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "핵심 키워드 최대 10개"},
    },
    "required": ["page_type", "summary"],
}


@dataclass
class LLMConfig:
    host: str
    text_model: str
    vision_model: str
    temperature: float = 0.1
    text_max_chars: int = 14_000  # 입력 텍스트 잘라내기 기준 (대략 ~3.5k token)
    num_ctx: int = 16_384  # Ollama 기본 4096은 한국어 본문+스키마+thinking에 너무 작음
    request_timeout_s: float = 180.0


class OllamaUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client = AsyncClient(host=config.host, timeout=config.request_timeout_s)

    def _chat_options(self) -> dict[str, Any]:
        return {
            "temperature": self.config.temperature,
            "num_ctx": self.config.num_ctx,
        }

    async def health(self) -> dict[str, Any]:
        try:
            tags = await self._client.list()
            models = [m.model for m in tags.models] if hasattr(tags, "models") else []
        except Exception as exc:
            raise OllamaUnavailable(f"Ollama 서버 접속 실패 ({self.config.host}): {exc}") from exc
        return {
            "host": self.config.host,
            "models": models,
            "text_model_available": self.config.text_model in models,
            "vision_model_available": self.config.vision_model in models,
        }

    async def structure_text(
        self,
        text: str,
        schema: dict[str, Any],
        *,
        system: str | None = None,
        instruction: str | None = None,
        max_chars: int | None = None,
        num_ctx: int | None = None,
    ) -> dict[str, Any]:
        """텍스트를 JSON Schema에 맞춰 구조화.

        Args:
            max_chars: 입력 텍스트 잘라내기 기준 (기본 config.text_max_chars).
                긴 본문(회사 홈페이지 등)은 60_000자 정도까지 override 가능.
            num_ctx: Ollama num_ctx override (기본 config.num_ctx).
                긴 본문이면 32_768 이상으로 키워야 함.
        """
        if not text.strip():
            return {}
        snippet = text[: (max_chars or self.config.text_max_chars)]
        schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
        default_sys = (
            "너는 웹페이지 본문을 주어진 JSON Schema에 정확히 맞춰 한국어로 구조화하는 도구다. "
            "응답은 반드시 JSON Schema에 맞는 valid JSON 단 하나만 출력하고, 다른 설명·코드펜스·주석은 절대 쓰지 마라. "
            "모르는 필드는 빈 문자열 \"\" 또는 빈 배열 []로 둔다."
        )
        user_prompt = (
            (instruction or "다음 본문을 주어진 JSON Schema에 맞춰 구조화해라.")
            + "\n\n## JSON Schema\n"
            + schema_str
            + "\n\n## 본문\n"
            + snippet
            + "\n\n위 본문을 위 Schema에 정확히 매칭되는 JSON 객체로만 출력하라."
        )
        messages = [
            {"role": "system", "content": system or default_sys},
            {"role": "user", "content": user_prompt},
        ]
        # num_ctx override 지원
        opts = self._chat_options()
        if num_ctx is not None:
            opts = {**opts, "num_ctx": num_ctx}
        try:
            resp = await self._client.chat(
                model=self.config.text_model,
                messages=messages,
                format=schema,
                options=opts,
                think=False,
            )
        except TypeError:
            # 구버전 ollama 클라이언트는 think 인자 미지원
            resp = await self._client.chat(
                model=self.config.text_model,
                messages=messages,
                format=schema,
                options=opts,
            )
        except Exception as exc:
            raise OllamaUnavailable(f"Ollama chat 실패: {exc}") from exc
        content = (resp.message.content or "").strip() if hasattr(resp, "message") else ""
        parsed = _safe_json_loads(content)
        if not parsed:
            logger.info(
                "structure_text empty parse — content_len=%d preview=%r",
                len(content),
                content[:200],
            )
        return parsed

    async def describe_image(
        self,
        image_bytes: bytes,
        schema: dict[str, Any],
        *,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """비전 모델로 스크린샷 → 구조화."""
        prompt = instruction or (
            "이 웹페이지 스크린샷을 보고 주어진 스키마에 맞춰 한국어로 구조화해라. "
            "텍스트가 흐리면 가장 합리적인 추정을 적되 확실하지 않은 필드는 빈 값으로 두라."
        )
        try:
            resp = await self._client.chat(
                model=self.config.vision_model,
                messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
                format=schema,
                options=self._chat_options(),
            )
        except Exception as exc:
            raise OllamaUnavailable(f"Ollama vision chat 실패: {exc}") from exc
        content = (resp.message.content or "").strip() if hasattr(resp, "message") else ""
        return _safe_json_loads(content)


def _safe_json_loads(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 모델이 ```json 등 fence를 붙이는 경우 대비.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {"_raw": raw}
        return {"_raw": raw}


def build_client_from_settings(host: str, text_model: str, vision_model: str) -> OllamaClient:
    return OllamaClient(LLMConfig(host=host, text_model=text_model, vision_model=vision_model))
