"""업로드된 이력서/포트폴리오 파일을 마크다운으로 변환.

기본 백엔드: Microsoft markitdown (docx/pdf/pptx/xlsx 등 폭넓게 지원).
markitdown이 없거나 실패하면 plain text 추출만 시도해서 fallback.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


SUPPORTED_EXTS: tuple[str, ...] = (
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx",
    ".txt", ".md", ".html", ".htm",
)
IMAGE_EXTS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".webp")
ALL_UPLOAD_EXTS: tuple[str, ...] = SUPPORTED_EXTS + IMAGE_EXTS


def convert_to_markdown(path: str | Path) -> tuple[str, str | None]:
    """파일 → markdown 텍스트.

    Returns (markdown_text, error_message_or_None).
    예외는 삼키고 (텍스트가 안 나와도) 빈 문자열 + 에러 메시지를 반환한다.
    """
    p = Path(path)
    if not p.exists():
        return "", f"파일이 존재하지 않습니다: {p}"

    ext = p.suffix.lower()

    # 1) markitdown — Microsoft 공식. docx/pdf/pptx 등 한 번에.
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(p))
        text = (getattr(result, "text_content", None) or "").strip()
        if text:
            return text, None
        # 빈 결과인 경우 fallback 시도
    except ImportError:
        logger.info("markitdown 미설치 — fallback 파서 시도: %s", p.name)
    except Exception as exc:
        logger.warning("markitdown 변환 실패 (%s): %s", p.name, exc)

    # 2) Fallback — 단순 텍스트 형식만
    if ext in (".txt", ".md"):
        try:
            return p.read_text(encoding="utf-8", errors="replace"), None
        except Exception as exc:
            return "", f"텍스트 읽기 실패: {exc}"

    if ext in (".html", ".htm"):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            # 매우 단순한 태그 제거 (전체 변환은 markitdown에 의존)
            import re
            stripped = re.sub(r"<[^>]+>", " ", raw)
            stripped = re.sub(r"\s+", " ", stripped).strip()
            return stripped, None
        except Exception as exc:
            return "", f"HTML 읽기 실패: {exc}"

    return "", f"이 파일 형식({ext})은 markitdown 설치 후 변환할 수 있습니다. uv add 'markitdown[docx,pdf,pptx]'"


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALL_UPLOAD_EXTS


def is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTS
