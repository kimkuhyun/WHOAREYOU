"""직무 카테고리 분류 — 잡 타이틀에서 1차 카테고리를 뽑는 단순 룰 기반 매처.

룰은 우선순위가 높은 카테고리부터 평가하며, 첫 매치를 채택한다.
복잡한 분류는 LLM에 맡기고, 여기는 대시보드/필터용 빠른 라벨링만 한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# (code, label, color_class, patterns)
# color_class는 Tailwind 클래스 (border / bg / text 세트)
CATEGORIES: list[tuple[str, str, str, list[str]]] = [
    (
        "ai",
        "AI/ML",
        "bg-violet-100 text-violet-800 border-violet-200",
        ["ai", "ml", "ai engineer", "ml engineer", "machine learning", "deep learning",
         "llm", "nlp", "vision", "추천", "ai 엔지니어", "인공지능", "머신러닝", "딥러닝"],
    ),
    (
        "data",
        "데이터",
        "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-200",
        ["data engineer", "data scientist", "data analyst", "데이터 엔지니어",
         "데이터 분석", "데이터 사이언", "데이터 엔지", "bi", "etl", "분석가"],
    ),
    (
        "backend",
        "백엔드",
        "bg-brand-100 text-brand-800 border-brand-300",
        ["backend", "백엔드", "back-end", "서버 개발", "server engineer",
         "java", "spring", "kotlin", "node", "node.js", "python 개발", "go 개발",
         "django", "fastapi", "rails"],
    ),
    (
        "frontend",
        "프론트엔드",
        "bg-sky-100 text-sky-800 border-sky-200",
        ["frontend", "프론트", "front-end", "react", "vue", "next.js", "nuxt",
         "웹 퍼블리셔", "퍼블리셔", "ui 개발"],
    ),
    (
        "fullstack",
        "풀스택",
        "bg-emerald-100 text-emerald-800 border-emerald-300",
        ["풀스택", "fullstack", "full-stack", "full stack"],
    ),
    (
        "mobile",
        "모바일",
        "bg-orange-100 text-orange-800 border-orange-200",
        ["android", "ios", "안드로이드", "아이폰", "flutter", "react native",
         "kotlin android", "swift", "모바일 개발"],
    ),
    (
        "devops",
        "DevOps/SRE",
        "bg-amber-100 text-amber-800 border-amber-200",
        ["devops", "sre", "데브옵스", "infra", "인프라", "platform engineer",
         "플랫폼 엔지니어", "kubernetes", "k8s", "aws", "cloud"],
    ),
    (
        "security",
        "보안",
        "bg-rose-100 text-rose-700 border-rose-200",
        ["security", "정보보안", "보안 엔지", "siem", "soc"],
    ),
    (
        "qa",
        "QA/테스트",
        "bg-teal-100 text-teal-800 border-teal-200",
        ["qa", "tester", "테스트", "quality assurance", "품질"],
    ),
    (
        "pm",
        "PM/기획",
        "bg-indigo-100 text-indigo-800 border-indigo-200",
        ["pm", "프로덕트 매니저", "기획", "프로젝트 매니저", "product manager",
         "product owner", "po", "서비스 기획"],
    ),
    (
        "design",
        "디자인",
        "bg-pink-100 text-pink-800 border-pink-200",
        ["디자이너", "designer", "ui ux", "ui/ux", "ux", "ui ", "그래픽"],
    ),
    (
        "embedded",
        "임베디드/HW",
        "bg-slate-200 text-slate-800 border-slate-300",
        ["임베디드", "embedded", "firmware", "펌웨어", "fpga", "회로", "하드웨어"],
    ),
]

# fast lookup
_PATTERNS: list[tuple[str, str, str, re.Pattern[str]]] = [
    (
        code,
        label,
        color,
        re.compile(
            r"(?<![a-zA-Z0-9가-힣])(" + "|".join(re.escape(p) for p in pats) + r")(?![a-zA-Z0-9가-힣])",
            re.IGNORECASE,
        ),
    )
    for code, label, color, pats in CATEGORIES
]


def classify_title(title: str) -> tuple[str, str, str]:
    """제목에서 카테고리 (code, label, color_class)를 반환.

    매치 없으면 ('other', '기타', 기본 회색).
    """
    if not title:
        return ("other", "기타", "bg-slate-100 text-slate-600 border-slate-200")
    for code, label, color, pat in _PATTERNS:
        if pat.search(title):
            return code, label, color
    return ("other", "기타", "bg-slate-100 text-slate-600 border-slate-200")


def all_categories() -> list[dict[str, str]]:
    out = [{"code": c, "label": l, "color": col} for c, l, col, _ in CATEGORIES]
    out.append({"code": "other", "label": "기타", "color": "bg-slate-100 text-slate-600 border-slate-200"})
    return out


def categorize_counts(titles: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {c["code"]: 0 for c in all_categories()}
    for t in titles:
        code, _, _ = classify_title(t or "")
        counts[code] = counts.get(code, 0) + 1
    return counts
