"""본문 추출 — Trafilatura 우선, 실패 시 readability-lxml fallback."""

from __future__ import annotations

from dataclasses import dataclass

import trafilatura
from readability import Document


@dataclass
class ExtractedContent:
    title: str
    text: str
    method: str  # "trafilatura" | "readability" | "raw"
    char_count: int


def extract_content(html: str, url: str | None = None) -> ExtractedContent:
    if not html:
        return ExtractedContent(title="", text="", method="raw", char_count=0)

    # 1) Trafilatura (정확도 최고)
    try:
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            no_fallback=False,
        )
        if text and len(text.strip()) >= 200:
            meta = trafilatura.extract_metadata(html, default_url=url)
            title = (meta.title if meta and meta.title else "") or ""
            return ExtractedContent(title=title, text=text.strip(), method="trafilatura", char_count=len(text))
    except Exception:
        pass

    # 2) readability-lxml (다른 휴리스틱)
    try:
        doc = Document(html)
        title = doc.title() or ""
        summary_html = doc.summary()
        # 매우 단순한 HTML → 텍스트 변환 (trafilatura.extract는 HTML도 받음)
        text = trafilatura.extract(summary_html) or ""
        if text and len(text.strip()) >= 100:
            return ExtractedContent(title=title, text=text.strip(), method="readability", char_count=len(text))
    except Exception:
        pass

    # 3) 최후: 태그 제거된 raw text
    try:
        text = trafilatura.html2txt(html) or ""
    except Exception:
        text = ""
    return ExtractedContent(title="", text=text.strip(), method="raw", char_count=len(text))
