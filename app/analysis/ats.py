"""ATS(Applicant Tracking System) 알고리즘을 역방향으로 차용한 매칭 엔진.

한국 채용 시장의 ATS (그리팅·사람인 채용솔루션·나인하이어 등)는 다음 패턴을 공유:
1. JD에서 필수/우대 키워드 셋 추출
2. 이력서 텍스트에서 키워드 등장 여부 검사 (exact + fuzzy)
3. 가중치 점수 = 필수×3 + 우대×1, 정규화해서 0~100점
4. 점수 내림차순 정렬 → HR이 상위 N명만 검토

우리는 이걸 **구직자 입장에서 역으로 돌려서** "이 공고가 ATS를 통과시킬 확률"을
계산한다. LLM 사용 X — 정규식 + kiwi(한국어 명사) + rapidfuzz(편집거리).
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

try:
    from kiwipiepy import Kiwi
    _KIWI: Kiwi | None = None

    def _get_kiwi() -> Kiwi:
        global _KIWI
        if _KIWI is None:
            _KIWI = Kiwi()
        return _KIWI
except Exception:
    _get_kiwi = None  # type: ignore


# 직무·기술 키워드가 아닌 일반 단어 제외 (한국어 명사 + 영문 일반어)
STOPWORDS: set[str] = {
    # 일반
    "경험", "경력", "능력", "지식", "이상", "이하", "관련", "활용", "사용", "수행",
    "업무", "분야", "기반", "환경", "방법", "또는", "그리고", "통한", "위한",
    "필요", "필수", "우대", "포함", "다음", "사항", "기본", "주요", "다양", "확장",
    "운영", "관리", "구축", "구현", "지속", "추진", "수립", "지원", "참여", "협업",
    "처리", "분석", "설계", "개발", "구성", "선택", "이용", "보유", "수상", "이용자",
    "회사", "기업", "조직", "프로젝트", "서비스", "시스템", "팀워크", "커뮤니케이션",
    "역량", "성과", "결과", "수준", "정도", "방향", "내용", "사람", "여부", "최신",
    "전체", "일부", "모든", "기간", "기준", "단위", "전반", "전공", "학력", "학사",
    "석사", "박사", "정규직", "계약직", "인턴", "신입", "지원자", "지원", "모집",
    "채용", "공고", "직무", "직원", "사원", "근무", "출근", "복지", "혜택", "급여",
    # 영문 일반어
    "and", "or", "the", "with", "for", "you", "your", "our", "we", "of", "in", "to",
    "a", "an", "is", "are", "be", "as", "on", "at", "by", "this", "that", "these",
    "have", "has", "will", "can", "must", "should",
}

# 명시적 헤더 패턴 (마크다운 #~####)
_HDR_REQ = re.compile(
    r"^#{1,4}\s*("
    r"자격\s*요건|필수\s*요건|필수\s*자격|자격\s*조건|지원\s*자격|모집\s*자격"
    r"|requirements|qualifications|required"
    r")", re.IGNORECASE,
)
_HDR_PREF = re.compile(
    r"^#{1,4}\s*("
    r"우대\s*사항|우대|선호\s*사항|선호|plus|nice\s*to\s*have|preferred"
    r")", re.IGNORECASE,
)
_HDR_DUTY = re.compile(
    r"^#{1,4}\s*("
    r"주요\s*업무|담당\s*업무|업무\s*내용|업무\s*소개|업무\s*및|responsibilities|what\s*you'll\s*do"
    r")", re.IGNORECASE,
)
_HDR_ANY = re.compile(r"^#{1,4}\s")
# 다양한 bullet 문자 — 한국 공고에 흔한 ㆍ(U+318D), ·(U+00B7), 그리고 일반 -/*/• 등
_BULLET = re.compile(r"^\s*[-*•‣⁃▪◦∙ㆍ·]\s*")

# 영문 기술명 + 약어 (Python, FastAPI, AWS, CI/CD 등)
_RE_EN = re.compile(r"[A-Za-z][A-Za-z0-9+#./\-]{1,}")


def _normalize(word: str) -> str:
    return word.strip().rstrip(".,;:)(").lower()


def _korean_nouns(text: str, min_len: int = 2) -> list[str]:
    """한국어 텍스트에서 명사(NNG/NNP) 추출. kiwi 없으면 한글 2자+ 휴리스틱."""
    if _get_kiwi is None:
        # fallback — 정규식만
        return [w for w in re.findall(r"[가-힣]{%d,}" % min_len, text)]
    kiwi = _get_kiwi()
    tokens = kiwi.tokenize(text)
    return [t.form for t in tokens if t.tag in ("NNG", "NNP", "SL") and len(t.form) >= min_len]


def _extract_terms(line: str) -> list[str]:
    """한 줄에서 영문 단어 + 한글 명사 추출."""
    out: list[str] = []
    # 1. 영문 단어 (Python, FastAPI 등)
    for w in _RE_EN.findall(line):
        w = w.strip(".-")
        if len(w) >= 2 and _normalize(w) not in STOPWORDS:
            out.append(w)
    # 2. 한국어 명사
    for w in _korean_nouns(line):
        if _normalize(w) not in STOPWORDS:
            out.append(w)
    return out


def _dedup_preserve(items: list[str], limit: int = 30) -> list[str]:
    """대소문자 무시 dedup. 원본 형태 보존."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
        if len(out) >= limit:
            break
    return out


def extract_jd_keywords(jd_md: str) -> dict[str, list[str]]:
    """JD 마크다운에서 자격요건/우대사항/주요업무 섹션을 찾아 키워드 추출.

    반환: {"required": [...], "preferred": [...], "duties": [...]}.
    - 헤더 패턴이 명확하면 섹션별로 분류
    - 헤더 패턴이 약하면 fallback: 전체 본문에서 토큰 추출해 preferred로 분류
      (정확도는 떨어지지만 매칭은 동작)
    """
    if not jd_md:
        return {"required": [], "preferred": [], "duties": []}

    current: str | None = None
    bag: dict[str, list[str]] = {"required": [], "preferred": [], "duties": []}

    for raw in jd_md.splitlines():
        if _HDR_REQ.match(raw):
            current = "required"; continue
        if _HDR_PREF.match(raw):
            current = "preferred"; continue
        if _HDR_DUTY.match(raw):
            current = "duties"; continue
        if _HDR_ANY.match(raw):
            current = None; continue
        if current is None:
            continue
        if _BULLET.match(raw):
            terms = _extract_terms(raw)
            bag[current].extend(terms)

    out = {
        "required": _dedup_preserve(bag["required"], limit=25),
        "preferred": _dedup_preserve(bag["preferred"], limit=25),
        "duties": _dedup_preserve(bag["duties"], limit=25),
    }

    # Fallback 1: 자격요건/우대사항 둘 다 비어있는데 주요업무(duties)는 있음
    #   → duties를 preferred로 복사 (매칭 함수는 required/preferred만 봄)
    #   주요업무 키워드도 매칭 기준으로 의미 있음 ("LLM 개발", "AI Agent" 등)
    if not out["required"] and not out["preferred"] and out["duties"]:
        out["preferred"] = out["duties"][:]

    # Fallback 2: 모든 섹션이 빈 경우 — JD 전체에서 토큰 추출
    if not out["required"] and not out["preferred"] and not out["duties"]:
        all_terms: list[str] = []
        for raw in jd_md.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            all_terms.extend(_extract_terms(line))
        out["preferred"] = _dedup_preserve(all_terms, limit=25)

    return out


# ─────────────────────── 매칭 ───────────────────────

def _hit(kw: str, text_lower: str, en_tokens: set[str], kr_tokens: set[str]) -> bool:
    """키워드가 텍스트에 (exact 또는 fuzzy) 등장하는지."""
    k = kw.lower().strip()
    if not k:
        return False
    # exact substring
    if k in text_lower:
        return True
    # fuzzy — 영문은 영문 토큰들과, 한글은 한글 토큰들과 비교
    if re.match(r"^[a-z0-9+#./\-]+$", k):
        # 영문 키워드
        for tok in en_tokens:
            if fuzz.ratio(k, tok) >= 90:  # Levenshtein 2 이내
                return True
    else:
        for tok in kr_tokens:
            if fuzz.ratio(k, tok) >= 90:
                return True
    return False


def _tokenize_for_match(text: str) -> tuple[set[str], set[str]]:
    lower = text.lower()
    en = {m.lower() for m in _RE_EN.findall(lower)}
    kr = set(_korean_nouns(text))
    return en, kr


# 가중치 — 22skills 공개 알고리즘 차용 (필수 3배)
REQ_WEIGHT = 3
PREF_WEIGHT = 1


def ats_match(resume_text: str, kw: dict[str, list[str]]) -> dict[str, Any]:
    """이력서 텍스트와 JD 키워드 셋으로 매칭 점수 계산.

    반환:
      {
        "score": 0~100,
        "matched_required": [...],
        "missing_required": [...],
        "matched_preferred": [...],
        "missing_preferred": [...],
      }
    """
    required = list(kw.get("required") or [])
    preferred = list(kw.get("preferred") or [])
    if not required and not preferred:
        return {
            "score": None,  # 키워드 없음 → 점수 계산 불가
            "matched_required": [], "missing_required": [],
            "matched_preferred": [], "missing_preferred": [],
        }

    text_lower = (resume_text or "").lower()
    en_tokens, kr_tokens = _tokenize_for_match(resume_text or "")

    matched_r = [k for k in required if _hit(k, text_lower, en_tokens, kr_tokens)]
    missing_r = [k for k in required if k not in matched_r]
    matched_p = [k for k in preferred if _hit(k, text_lower, en_tokens, kr_tokens)]
    missing_p = [k for k in preferred if k not in matched_p]

    num = len(matched_r) * REQ_WEIGHT + len(matched_p) * PREF_WEIGHT
    den = len(required) * REQ_WEIGHT + len(preferred) * PREF_WEIGHT
    score = int(num / den * 100) if den else 0

    return {
        "score": score,
        "matched_required": matched_r,
        "missing_required": missing_r,
        "matched_preferred": matched_p,
        "missing_preferred": missing_p,
    }
