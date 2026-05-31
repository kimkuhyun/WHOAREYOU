"""채용공고 본문(JD) 추출 — LLM 에이전트 방식.

전략 (Claude/Codex가 쓰는 방식과 동일한 컨셉):
1. Playwright로 페이지를 실제 브라우저로 렌더링 (headless이지만 모든 JS 실행)
2. 페이지의 visible text(`document.body.innerText`)를 통째로 가져옴
3. 모든 iframe들의 innerText도 함께 수집
4. 합친 텍스트를 Ollama text LLM에 던져서
   "여기서 채용 공고 본문(주요업무/자격요건/우대사항/복리후생/근무조건)만
    마크다운으로 정리해줘. 광고/추천/메뉴/푸터/사업자 정보 등은 무시." 라고 시킴
5. LLM이 마크다운으로 응답 → 그대로 저장
6. 텍스트가 거의 없는 이미지 공고면 페이지 스크린샷을 Vision LLM에 보냄

이 방식은 selector/site별 룰을 일절 안 쓴다. 사이트가 어떻게 바뀌어도 LLM이 알아서 본문을 골라낸다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.crawler.browser import get_pool
from app.crawler.llm import LLMConfig, OllamaClient, OllamaUnavailable

logger = logging.getLogger(__name__)

# Text LLM 입력 한도 — 사람인 같은 페이지가 회사소개·인재상까지 합치면 25k+ 되는 경우 많음
MAX_TEXT_CHARS = 40_000
# Text LLM 컨텍스트 윈도우 — qwen3 27B 등 대형 모델은 32k+ 지원
JD_NUM_CTX = 32_768


@dataclass
class JdResult:
    ok: bool
    md: str
    error: str | None = None
    char_count: int = 0
    extracted_from: str | None = None  # text-llm | vision-llm | hybrid


# 모든 iframe 포함 텍스트 추출
_PAGE_TEXT_JS = """
() => {
  const out = [];
  function pickFromDoc(doc) {
    try {
      // body.innerText는 visible text만 (display:none 제외)
      if (doc.body) out.push(doc.body.innerText || '');
    } catch (_) {}
  }
  pickFromDoc(document);
  for (const iframe of document.querySelectorAll('iframe')) {
    try {
      const d = iframe.contentDocument;
      if (d) pickFromDoc(d);
    } catch (_) { /* cross-origin iframe — skip */ }
  }
  return out.join('\\n\\n');
}
"""


JD_FROM_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_job_posting": {
            "type": "boolean",
            "description": "이 페이지에서 채용 공고로 보이는 내용이 있으면 true. 광고/메뉴/푸터만 있으면 false.",
        },
        "title": {"type": "string", "description": "직무명 (있으면 채움, 없으면 빈 문자열)"},
        "company": {"type": "string", "description": "회사명 (있으면)"},
        "markdown": {
            "type": "string",
            "description": (
                "채용 공고 본문을 한국어 마크다운으로 깔끔히 정리. "
                "사이트 메뉴/푸터/회사소개·이용약관·사업자번호·고객센터/광고/추천공고/지원자 통계는 모두 제외. "
                "## 주요업무 / ## 자격요건 / ## 우대사항 / ## 복리후생 / ## 근무조건 같은 헤더와 '- ' bullet으로 구조화. "
                "원문에 있는 내용만 사용하고 만들어내지 말 것."
            ),
        },
    },
    "required": ["is_job_posting", "markdown"],
}


JD_FROM_IMAGE_SCHEMA: dict[str, Any] = JD_FROM_TEXT_SCHEMA  # 동일 형태로 vision도 받음


async def _capture_page(url: str, *, timeout_ms: int = 25_000) -> tuple[str, bytes | None, str]:
    """Playwright로 페이지 캡처. (visible_text, screenshot_bytes_or_None, final_url) 반환.

    "상세 정보 더 보기" 류 펼침 버튼은 자동 클릭 (원티드/잡코리아 등 본문 일부만 노출하는 사이트 대응).
    cleanup 같은 사이트별 작업 안 함 — LLM이 알아서 본문만 골라냄.
    """
    from app.crawler.strategies import CrawlOptions, auto_click_more

    pool = get_pool()
    text = ""
    screenshot: bytes | None = None
    final_url = url
    ctx_cm = pool.context(block_resources=False)  # 이미지/폰트 살리기 (vision fallback 위해)
    try:
        ctx = await ctx_cm.__aenter__()
        try:
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                # SPA가 lazy-load 하는 경우 추가 대기
                await asyncio.sleep(1.2)
                # 스크롤 한 번 — lazy 콘텐츠 트리거
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.6)
                    await page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

                # "상세 정보 더 보기" / "펼치기" 류 자동 클릭 — 원티드 등이 본문 일부만 노출하는 패턴 대응.
                # 클릭 후 본문이 늘어나면 한 번 더 스크롤해서 lazy 영역도 트리거.
                try:
                    clicks = await auto_click_more(page, CrawlOptions(more_click_max=3, infinite_scroll=False))
                    if clicks > 0:
                        logger.info("JD 펼침 버튼 %d회 클릭: %s", clicks, url)
                        try:
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(0.5)
                            await page.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            pass
                except Exception:
                    logger.exception("auto_click_more 실패 (무시): %s", url)

                final_url = page.url

                try:
                    text = await page.evaluate(_PAGE_TEXT_JS) or ""
                except Exception:
                    text = ""

                # screenshot은 vision fallback용 (이미지 공고에 한정해 필요할 때만 캡처해도 되지만,
                # 한 번에 같이 캡처해 두면 페이지 닫고 재방문 안 해도 됨)
                try:
                    screenshot = await page.screenshot(full_page=True, type="jpeg", quality=80)
                except Exception:
                    screenshot = None
            finally:
                await page.close()
        finally:
            await ctx_cm.__aexit__(None, None, None)
    except Exception as exc:
        logger.exception("Playwright 캡처 실패: %s", url)
        raise RuntimeError(f"페이지 로딩 실패: {exc}") from exc
    return text, screenshot, final_url


async def _get_ollama_client() -> tuple[OllamaClient | None, str | None]:
    """현재 설정으로 OllamaClient를 빌드. 없으면 (None, error_msg)."""
    try:
        from app.db import async_session_maker
        from app.ui import settings_store
    except Exception as exc:
        return None, f"설정 로드 실패: {exc}"
    async with async_session_maker() as session:
        cfg = await settings_store.get_all(session)
    text_model = (cfg.get("ollama_text_model") or "").strip()
    vision_model = (cfg.get("ollama_vision_model") or "").strip()
    host = "http://localhost:11434"
    if not text_model and not vision_model:
        return None, "Ollama 모델이 설정되지 않았습니다 (/settings에서 텍스트 모델 설정 필요)"
    client = OllamaClient(LLMConfig(
        host=host,
        text_model=text_model or "qwen3.5:9b",
        vision_model=vision_model or "qwen2.5vl:7b",
        text_max_chars=MAX_TEXT_CHARS,
        num_ctx=JD_NUM_CTX,
    ))
    return client, None


def _anchor_hint(known_title: str | None, known_company: str | None) -> str:
    """LLM 환각 방지 — DB에 이미 확정된 회사/직무를 anchor로 알려준다."""
    parts: list[str] = []
    if known_company:
        parts.append(f"회사: 「{known_company}」")
    if known_title:
        parts.append(f"공고 제목: 「{known_title}」")
    if not parts:
        return ""
    return (
        "\n\n## 확정 정보 (이 값과 다른 회사/공고 내용은 절대 포함하지 마세요)\n"
        + " / ".join(parts)
        + "\n페이지에 위 회사가 아닌 다른 회사의 광고·추천 공고가 섞여 있어도 모두 무시하세요. "
        "오직 위 공고의 본문만 추출하세요.\n"
    )


async def _llm_extract_from_text(
    client: OllamaClient,
    page_text: str,
    url: str,
    *,
    known_title: str | None = None,
    known_company: str | None = None,
) -> dict[str, Any]:
    """페이지 텍스트를 LLM에 던져 본문 **전체**를 마크다운으로 받는다 (요약 X, 누락 X)."""
    instruction = (
        f"다음은 웹사이트에서 가져온 페이지 전체 텍스트입니다 (URL: {url}).\n\n"
        "이 안에는 채용 공고 본문 외에도 사이트 메뉴/푸터/이용약관/사업자번호/"
        "다른 회사의 추천 공고 광고 같은 잡음이 섞여 있습니다. "
        "그 중 **확정 정보에 명시된 그 공고의 모든 본문 섹션**을 빠짐없이 한국어 마크다운으로 옮겨 적으세요.\n\n"
        "## 절대 규칙\n"
        " 1. **요약하지 마세요. 축약·생략·재해석 금지.** 원문 문장과 bullet을 그대로 옮기세요.\n"
        " 2. **원문에 없는 내용/회사명/문구는 절대 만들지 마세요** (환각 금지).\n"
        " 3. 페이지에 다른 회사의 추천 공고가 보여도 그 내용은 **절대** 포함하지 마세요.\n"
        " 4. markdown 첫 헤더는 만들지 마세요 (제목은 시스템이 자동 추가).\n"
        " 5. 본문이 너무 길어도 잘라내지 말고 전부 옮기세요.\n\n"
        "## 반드시 포함할 섹션 (페이지에 존재하면 빠짐없이)\n"
        " - 회사 소개 / 회사 비전 / 주요 성과·실적\n"
        " - 채용팀 메시지 / 채용팀 Talk / 모집 취지\n"
        " - 인재상 / 찾는 인재\n"
        " - 모집분야 / 모집부문\n"
        " - 담당업무 / 주요업무\n"
        " - 자격요건 (필수)\n"
        " - 우대사항\n"
        " - 근무조건 (고용형태·근무요일·근무시간·근무지)\n"
        " - 복리후생 (모든 세부 항목 — Compensation Package, Work Environment, Refresh 등)\n"
        " - 전형절차 (STEP 1~N 모두)\n"
        " - 접수기간·마감일·접수방법\n"
        " - 제출서류 / 지원양식\n"
        " - 문의사항 (담당자·이메일·연락처)\n"
        " - 유의사항 / 참고사항\n"
        " - 회사 주요 기사·뉴스\n\n"
        "## 마크다운 형식\n"
        " - 각 큰 섹션은 `## 섹션명` 헤더\n"
        " - 하위 항목은 `- ` bullet\n"
        " - 강조는 `**텍스트**`\n"
        " - 표가 필요하면 markdown 표 사용\n\n"
        "## 응답 검증 기준\n"
        " - 페이지에 명시된 섹션 중 위 목록의 항목이 5개 이상 있으면 모두 포함됐는지 다시 확인\n"
        " - 확정 정보의 회사/공고와 관련된 텍스트가 페이지에서 안 보이면 is_job_posting=false\n"
        + _anchor_hint(known_title, known_company)
    )
    return await client.structure_text(
        page_text, JD_FROM_TEXT_SCHEMA,
        instruction=instruction,
    )


async def _llm_extract_from_screenshot(
    client: OllamaClient,
    screenshot: bytes,
    url: str,
    *,
    known_title: str | None = None,
    known_company: str | None = None,
) -> dict[str, Any]:
    """페이지 스크린샷을 Vision LLM에 던진다 — 이미지로 된 공고에 사용."""
    return await client.describe_image(
        screenshot, JD_FROM_IMAGE_SCHEMA,
        instruction=(
            f"이 이미지는 채용 공고 페이지 스크린샷입니다 (URL: {url}). "
            "보이는 글자 중 **확정 정보의 공고 본문(주요업무·자격요건·우대사항·복리후생·근무조건)**만 "
            "한국어 마크다운으로 정리하세요. 사이드 광고/추천/푸터/메뉴/다른 회사 공고는 제외. "
            "원문에 없는 내용은 절대 만들지 마세요."
            + _anchor_hint(known_title, known_company)
        ),
    )


def _md_from_parsed(
    parsed: dict[str, Any],
    *,
    known_title: str | None = None,
    known_company: str | None = None,
) -> str:
    """LLM 응답에서 markdown 추출. title/company는 **항상 우리 DB 값으로 강제** (LLM 환각 차단)."""
    if not isinstance(parsed, dict):
        return ""
    md = (parsed.get("markdown") or "").strip()
    if not md:
        return ""
    # LLM이 첫 줄에 헤더를 만들었으면 제거 (우리가 다시 정확한 헤더를 붙임)
    while md.startswith("#"):
        nl = md.find("\n")
        if nl < 0:
            md = ""
            break
        md = md[nl + 1 :].lstrip()
    # LLM 응답의 title/company는 무시. 우리가 가진 known_* 우선.
    header_parts: list[str] = []
    if known_title:
        header_parts.append(f"# {known_title}")
    if known_company:
        header_parts.append(f"**{known_company}**")
    if header_parts:
        md = "\n".join(header_parts) + "\n\n" + md
    return md


async def fetch_jd(
    url: str,
    source: str = "",
    timeout_ms: int = 25_000,
    *,
    known_title: str | None = None,
    known_company: str | None = None,
) -> JdResult:
    """공고 본문 추출 — LLM 에이전트 방식.

    known_title/known_company를 넘기면 LLM에게 anchor로 알려주고, 결과 헤더도 그 값으로 강제.
    이렇게 해야 LLM이 페이지의 다른 추천 공고에서 회사명을 환각으로 가져오지 않음.
    """
    if not url:
        return JdResult(ok=False, md="", error="URL이 비어 있습니다")

    # 1) 페이지 캡처
    try:
        page_text, screenshot, _ = await _capture_page(url, timeout_ms=timeout_ms)
    except Exception as exc:
        return JdResult(ok=False, md="", error=str(exc))

    # 2) LLM 클라이언트
    client, err = await _get_ollama_client()
    if client is None:
        return JdResult(
            ok=False, md="",
            error=err or "Ollama 클라이언트 빌드 실패",
            extracted_from=None,
        )

    md = ""
    extracted_from: str | None = None
    last_error: str | None = None

    # 3) 텍스트가 의미 있으면 text LLM에 던짐
    if page_text and len(page_text.strip()) > 200:
        try:
            parsed = await _llm_extract_from_text(
                client, page_text, url,
                known_title=known_title, known_company=known_company,
            )
            if parsed.get("is_job_posting") is not False:
                md = _md_from_parsed(parsed, known_title=known_title, known_company=known_company)
                if md:
                    extracted_from = "text-llm"
        except OllamaUnavailable as exc:
            last_error = f"text LLM 호출 실패: {exc}"
        except Exception as exc:
            logger.exception("text LLM 추출 실패: %s", url)
            last_error = f"text LLM 오류: {exc}"

    # 4) 텍스트 결과 빈약 + 스크린샷 있으면 vision LLM
    need_vision = (not md) or len(md) < 200
    if need_vision and screenshot:
        try:
            parsed = await _llm_extract_from_screenshot(
                client, screenshot, url,
                known_title=known_title, known_company=known_company,
            )
            if parsed.get("is_job_posting") is not False:
                v_md = _md_from_parsed(parsed, known_title=known_title, known_company=known_company)
                if v_md:
                    if md:
                        md = md + "\n\n---\n\n## 이미지에서 인식한 내용\n\n" + v_md
                    else:
                        md = v_md
                    extracted_from = (extracted_from + "+vision") if extracted_from else "vision-llm"
        except OllamaUnavailable as exc:
            last_error = (last_error or "") + f" · vision LLM 호출 실패: {exc}"
        except Exception as exc:
            logger.exception("vision LLM 추출 실패: %s", url)
            last_error = (last_error or "") + f" · vision 오류: {exc}"

    # 5) 결과 판정
    md = md.strip()
    if md and len(md) >= 80:
        return JdResult(ok=True, md=md, char_count=len(md), extracted_from=extracted_from)

    error = last_error or "LLM이 본문을 추출하지 못했습니다 (모델/페이지 모두 확인 필요)"
    return JdResult(ok=False, md=md, error=error, char_count=len(md), extracted_from=extracted_from)
