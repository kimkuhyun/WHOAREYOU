# -*- coding: utf-8 -*-
"""공고 필터 판정 — 크롤 어댑터(수집 단계)와 파이프라인이 공용으로 사용.

핵심: "가져온 뒤 거르기"가 아니라 어댑터가 **필터 통과분만 n개** 모으도록 수집 단계에서 판정.
구조화 필드(career/edu_req/emp_type/comp_type) 우선, 없으면 제목/JD 정규식 폴백. known-only.
"""
import re

_EDU_ORDER = {"학력무관": 0, "고졸": 1, "초대졸": 2, "대졸": 3, "석사": 4, "박사": 5}


def region_tokens(regions):
    out = []
    for r in regions or []:
        r = r.replace("전체", "").replace("특별시", "").replace("광역시", "").strip()
        if r:
            out.append(r)
    return out


def career_req(text: str) -> str:
    if "경력무관" in text or "경력 무관" in text:
        return "무관"
    has_sin = "신입" in text
    has_gyeong = bool(re.search(r"경력\s*[:：]|경력\s*\d+\s*년|경력직|\d+\s*년\s*이상", text))
    if has_gyeong and has_sin:
        return "무관"
    if has_gyeong:
        return "경력"
    if has_sin:
        return "신입"
    return ""


def emp_req(text: str) -> str:
    if "인턴" in text:
        return "인턴직"
    if "파견" in text:
        return "파견"
    if "프리랜" in text or "외주" in text:
        return "프리랜서"
    if "계약직" in text:
        return "계약직"
    return ""


def edu_req(text: str) -> str:
    """제목/JD에서 대학원 요구만(우대 제외 — 오탈락 방지). 구조화 edu_req 없을 때 폴백."""
    if re.search(r"박사[^가-힣]{0,4}(이상|필수|소지)", text):
        return "박사"
    if re.search(r"(석사|대학원)[^가-힣]{0,4}(이상|필수|소지|졸업)", text):
        return "석사"
    return ""


def passes(j, f: dict) -> bool:
    """필터 통과 여부. j는 Job(구조화 필드 있음). f=filters dict. f 없으면 전부 통과."""
    if not f:
        return True
    text = f"{getattr(j, 'title', '')} {getattr(j, 'jd', '')}"
    # 지역
    toks = region_tokens(f.get("regions"))
    if toks and j.address and not any(t in j.address for t in toks):
        return False
    # 경력
    car = f.get("career")
    if car in ("신입", "경력"):
        jc = j.career if j.career in ("신입", "경력") else career_req(text)
        if jc in ("신입", "경력") and jc != car:
            return False
    # 고용형태
    emps = f.get("emp_types")
    if emps:
        et = j.emp_type or emp_req(text)
        if et and et not in emps:
            return False
    # 학력: 요구 > 사용자 최대 → drop
    edus = f.get("edu")
    if edus:
        req = j.edu_req or edu_req(text)
        if req:
            user_max = max((_EDU_ORDER.get(e, 0) for e in edus), default=0)
            if _EDU_ORDER.get(req, 0) > user_max:
                return False
    # 기업형태(사람인만 — known-only)
    comps = f.get("comp_types")
    if comps and j.comp_type:
        if not any(ct in j.comp_type for ct in comps):
            return False
    return True
