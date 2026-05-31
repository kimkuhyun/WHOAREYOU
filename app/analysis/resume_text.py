"""이력서를 매칭/검색용 단일 텍스트로 통합.

여러 소스를 합쳐서 ATS 매칭에 던질 수 있는 텍스트로:
- 구조화된 sections (인적사항 빼고 경력·스킬·자기소개·자격증·어학 등)
- Resume.content_md (자유 본문, 마크다운)
- Resume.skills_csv (스킬 태그)
- Resume.role (희망 직무)
- 첨부된 PDF/Word의 content_md (kind=resume 인 것)
"""

from __future__ import annotations

import json
from typing import Any


def _sections_to_text(sections: dict[str, Any]) -> str:
    """sections_json dict → 매칭용 평문."""
    parts: list[str] = []

    # 경력 — 회사·직책·담당업무가 매칭에 가장 중요
    for exp in sections.get("experience") or []:
        line = " ".join(
            str(exp.get(k) or "") for k in ("company", "department", "position", "duties")
        ).strip()
        if line: parts.append(line)

    # 학력 — 전공·학교
    for ed in sections.get("education") or []:
        line = " ".join(
            str(ed.get(k) or "") for k in ("school", "major", "degree")
        ).strip()
        if line: parts.append(line)

    # 자격증
    for c in sections.get("certifications") or []:
        line = " ".join(str(c.get(k) or "") for k in ("name", "issuer")).strip()
        if line: parts.append(line)

    # 어학
    for l in sections.get("languages") or []:
        line = " ".join(str(l.get(k) or "") for k in ("language", "test", "score")).strip()
        if line: parts.append(line)

    # 수상
    for a in sections.get("awards") or []:
        line = " ".join(str(a.get(k) or "") for k in ("name", "issuer", "description")).strip()
        if line: parts.append(line)

    # 외부활동
    for a in sections.get("activities") or []:
        line = " ".join(str(a.get(k) or "") for k in ("title", "role", "description")).strip()
        if line: parts.append(line)

    # 논문/특허
    for p in sections.get("publications") or []:
        line = " ".join(str(p.get(k) or "") for k in ("title", "journal")).strip()
        if line: parts.append(line)
    for p in sections.get("patents") or []:
        line = " ".join(str(p.get(k) or "") for k in ("title", "role")).strip()
        if line: parts.append(line)

    # 자기소개서
    for si in sections.get("self_intros") or []:
        line = " ".join(str(si.get(k) or "") for k in ("title", "content")).strip()
        if line: parts.append(line)

    # 희망 직무·근무조건은 매칭에 큰 영향 없으니 스킵 (필요 시 추가)

    # 사용자 정의 섹션
    for c in sections.get("custom") or []:
        line = " ".join(str(c.get(k) or "") for k in ("title", "content")).strip()
        if line: parts.append(line)

    return "\n".join(parts).strip()


def build_resume_text(
    resume,
    *,
    attachment_md_list: list[str] | None = None,
) -> str:
    """Resume row + (선택) 첨부 PDF 마크다운 리스트 → 매칭용 통합 텍스트.

    호출 측에서 첨부는 별도 쿼리해서 content_md 리스트로 넘겨주면 됨.
    """
    parts: list[str] = []

    if resume.role:
        parts.append(f"희망 직무: {resume.role}")
    if resume.skills_csv:
        # 스킬은 매우 중요 — 명시적으로 한 번 더 강조
        parts.append(f"보유 스킬: {resume.skills_csv}")
    if resume.summary_md:
        parts.append(resume.summary_md)

    if resume.sections_json:
        try:
            sec = json.loads(resume.sections_json)
            sec_text = _sections_to_text(sec)
            if sec_text:
                parts.append(sec_text)
        except Exception:
            pass

    if resume.content_md:
        parts.append(resume.content_md)

    for md in attachment_md_list or []:
        if md and md.strip():
            parts.append(md)

    return "\n\n".join(parts).strip()
