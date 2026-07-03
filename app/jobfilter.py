# -*- coding: utf-8 -*-
"""공고 필터 판정 — 크롤 어댑터(수집 단계)와 파이프라인이 공용으로 사용.

핵심: "가져온 뒤 거르기"가 아니라 어댑터가 **필터 통과분만 n개** 모으도록 수집 단계에서 판정.
구조화 필드(career/edu_req/emp_type/comp_type) 우선, 없으면 제목/JD 정규식 폴백. known-only.
"""
import re

_EDU_ORDER = {"학력무관": 0, "고졸": 1, "초대졸": 2, "대졸": 3, "석사": 4, "박사": 5}

_SKILLS = None   # SkillNormalizer 지연 로드


def skill_variants(token: str) -> set[str]:
    """스킬 토큰의 동의어 그룹(소문자). substring 오폭 방지를 위해 3자 미만 변형(ml·js 등)은 제외."""
    global _SKILLS
    if _SKILLS is None:
        from skills import SkillNormalizer
        _SKILLS = SkillNormalizer()
    return {v for v in _SKILLS.variants(token) if len(v) >= 3}


def region_tokens(regions):
    out = []
    for r in regions or []:
        r = r.replace("전체", "").replace("특별시", "").replace("광역시", "").strip()
        if r:
            out.append(r)
    return out


def career_set(v) -> set:
    """설정 career 값(구버전 문자열 | 멀티선택 리스트) → {"신입","경력","무관"} 부분집합.
    구버전 단일값은 기존 동작 보존: "신입"/"경력"은 무관 공고도 통과였음 → +무관, "경력무관"은 무필터 → 공집합."""
    if isinstance(v, str):
        vals = {"신입": {"신입", "무관"}, "경력": {"경력", "무관"}}.get(v, set())
    else:
        vals = {("무관" if x in ("경력무관", "무관") else x) for x in (v or []) if x}
    return vals & {"신입", "경력", "무관"}


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
        return "파견직"
    if "프리랜" in text or "외주" in text:
        return "프리랜서"
    if "계약직" in text:
        return "계약직"
    return ""


# 고용형태 토큰 → UI 칩 라벨. 영문은 잡코리아 JSON-LD employmentType(FULL_TIME 등) 대응
_EMP_TOKENS = (("정규", "정규직"), ("계약", "계약직"), ("인턴", "인턴직"), ("파견", "파견직"),
               ("프리랜", "프리랜서"), ("외주", "프리랜서"), ("병역특례", "병역특례"),
               ("아르바이트", "아르바이트"), ("알바", "아르바이트"),
               ("full_time", "정규직"), ("fulltime", "정규직"), ("contract", "계약직"),
               ("intern", "인턴직"), ("part_time", "아르바이트"), ("temporary", "파견직"),
               ("freelance", "프리랜서"))


def emp_set(v) -> set:
    """구조화 고용형태 원문("정규직 , 계약직"·["FULL_TIME","CONTRACTOR"] 등) → 제공 형태 집합.
    한 공고가 복수 형태를 제공하면 전부 담는다 — 사용자 선택과 교집합이 있으면 통과."""
    t = str(v or "").lower()
    return {lab for tok, lab in _EMP_TOKENS if tok in t}


def edu_req(text: str) -> str:
    """제목/JD의 학력 '요구' 폴백(우대 표현 제외 — 오탈락 방지). 구조화 edu_req 없을 때 사용.
    원티드는 학력 구조화 필드가 없어 이 폴백이 유일한 방어선(실측: '학사 이상 학위' 누수 → 대졸 계열 추가)."""
    if re.search(r"박사[^가-힣]{0,4}(이상|필수|소지)", text):
        return "박사"
    if re.search(r"(석사|대학원)[^가-힣]{0,4}(이상|필수|소지|졸업)", text):
        return "석사"
    if (re.search(r"(대졸|학사)[^가-힣]{0,6}(이상|필수|소지)", text)
            or re.search(r"4년제[^\n]{0,8}(졸업|이상)", text)
            or re.search(r"대학교?\s*졸업[^가-힣]{0,4}(이상|자|예정)", text)):
        return "대졸"
    if re.search(r"(초대졸|전문대)[^가-힣]{0,6}(이상|필수|졸업)", text):
        return "초대졸"
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
    # 경력 — 멀티선택(신입·경력·경력무관 조합). 선택 클래스에 속하면 통과, 판정불가("")는 통과(known-only)
    cars = career_set(f.get("career"))
    if cars and not cars >= {"신입", "경력", "무관"}:
        jc = j.career if j.career in ("신입", "경력", "무관") else career_req(text)
        if jc and jc not in cars:
            return False
    # 고용형태 — 제공 형태 집합 ∩ 사용자 선택("정규직, 계약직" 병기 공고는 정규직 선택자에게 통과)
    emps = f.get("emp_types")
    if emps:
        offered = emp_set(j.emp_type)
        if not offered:
            et = emp_req(text)
            offered = {et} if et else set()
        if offered and not offered & set(emps):
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
    # 기술스택 — 제목+JD에 선택 스킬 중 하나라도(동의어 포함) 있으면 통과(OR).
    # 판정 불가면 통과(known-only): ①이미지 공고(OCR 전) ②정적 텍스트<300자(잡코리아 JS렌더 JD —
    # is_image 확인이 필터 뒤라 짧은 텍스트로 스킬 부재를 단정하면 오탈락. 300자=§8a 이미지 공고 판별 기준)
    sk = [str(s).strip() for s in (f.get("skills") or []) if str(s).strip()]
    if sk and not getattr(j, "is_image", False) and len(text) >= 300:
        tl = text.lower()
        if not any(v in tl for s in sk for v in skill_variants(s)):
            return False
    return True
