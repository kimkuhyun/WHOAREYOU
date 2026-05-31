"""채용공고 제목/메타에서 키워드 추출.

KoNLPy를 설치 없이 동작하도록, 정규식 기반 휴리스틱 + 영어 기술 키워드 사전 사용.
워드클라우드 데이터 형식: [(단어, 빈도), ...]
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

# 흔한 IT/개발 키워드 (영문, 한글 혼합)
TECH_VOCAB = {
    # 언어
    "python", "java", "kotlin", "swift", "typescript", "javascript", "go", "rust",
    "c++", "c#", "scala", "ruby", "php", "r", "matlab",
    # 프레임워크
    "react", "vue", "angular", "svelte", "next.js", "nuxt", "django", "flask",
    "fastapi", "spring", "nodejs", "express", "rails", "laravel", "dotnet",
    # 인프라/데브옵스
    "aws", "gcp", "azure", "kubernetes", "docker", "terraform", "ansible", "jenkins",
    "github actions", "gitlab", "ci/cd", "linux", "nginx", "redis", "kafka", "rabbitmq",
    # DB
    "mysql", "postgresql", "mongodb", "elasticsearch", "oracle", "mariadb", "dynamodb",
    "bigquery", "snowflake", "spark", "hadoop", "airflow",
    # AI/ML
    "ai", "ml", "llm", "pytorch", "tensorflow", "huggingface", "rag", "transformer",
    "딥러닝", "머신러닝", "강화학습", "추천", "검색", "임베딩",
    # 데이터
    "sql", "etl", "elt", "data warehouse", "data lake", "tableau", "looker", "powerbi",
    # 한글 직무 키워드
    "백엔드", "프론트엔드", "풀스택", "데이터", "데브옵스", "인프라", "보안", "qa",
    "모바일", "안드로이드", "ios", "임베디드", "펌웨어", "게임", "그래픽",
    "기획", "디자인", "마케팅", "영업", "운영", "고객", "콘텐츠", "프로덕트",
    "시니어", "주니어", "신입", "경력", "리드", "매니저", "팀장",
}

NOISE_WORDS = {
    "채용", "모집", "지원", "공고", "직원", "회사", "기업", "근무", "사원", "업무",
    "관련", "필수", "우대", "사항", "년차", "이상", "이하", "경력자", "신입",
    "정규직", "계약직", "프리랜서", "외근", "재택",
    "the", "and", "for", "with", "you", "your", "our", "we", "of", "in", "to", "a", "an",
    "is", "are", "be", "as", "on", "at", "by",
}


def _normalize_token(t: str) -> str:
    return t.lower().strip()


def extract_from_titles(titles: Iterable[str], top_n: int = 50) -> list[tuple[str, int]]:
    """채용공고 제목 리스트에서 토큰을 추출하고 (단어, 빈도) 정렬 리스트 반환."""
    counter: Counter[str] = Counter()

    for title in titles:
        if not title:
            continue
        # 토큰화: 영문 단어, 한글 단어, 점이 들어간 단어(node.js 등) 인식
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#\.]+|[가-힣]+", title)
        for tok in tokens:
            t = _normalize_token(tok)
            if not t or len(t) < 2:
                continue
            if t in NOISE_WORDS:
                continue
            # 기술 사전에 있거나, 한글 3자 이상, 영문 3자 이상이면 인정
            if t in TECH_VOCAB or len(t) >= 3:
                counter[t] += 1

    return counter.most_common(top_n)


def keywords_to_wordcloud(pairs: list[tuple[str, int]]) -> list[list]:
    """wordcloud2.js 입력 형식 `[[word, weight], ...]`로 변환."""
    if not pairs:
        return []
    # weight 정규화 (10~100 사이로 스케일)
    max_count = max(c for _, c in pairs)
    if max_count == 0:
        return []
    out = []
    for word, count in pairs:
        weight = max(10, int(10 + 90 * (count / max_count)))
        out.append([word, weight])
    return out
