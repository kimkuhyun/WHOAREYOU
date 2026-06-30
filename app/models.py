from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    domain: Optional[str] = Field(default=None, index=True)
    domain_confidence: Optional[int] = None   # 도메인 발견 점수 (높을수록 신뢰)
    domain_source: Optional[str] = None       # "discover" | "dart" | "manual"
    address: Optional[str] = None
    kakao_lat: Optional[float] = None
    kakao_lng: Optional[float] = None
    dart_corp_code: Optional[str] = Field(default=None, index=True)
    last_researched_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    # 조사 결과 캐시 (research_company가 저장)
    transit_json: Optional[str] = None  # ODsay TransitSummary
    dart_overview_json: Optional[str] = None
    dart_financials_json: Optional[str] = None
    emotion_json: Optional[str] = None  # LLM 감정 분석 결과
    homepage_summary_json: Optional[str] = None  # 회사 홈페이지 LLM 요약


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)
    title: str
    url: str = Field(unique=True, index=True)
    source: str = Field(index=True)  # saramin | jobkorea | wanted | homepage | manual
    location: Optional[str] = None
    deadline: Optional[str] = None
    raw_html_path: Optional[str] = None
    extracted_json: Optional[str] = None  # JSON-encoded structured data
    keywords_json: Optional[str] = None
    captured_at: datetime = Field(default_factory=utcnow)
    favorite: bool = Field(default=False, index=True)
    # 지원상태: none | interested | applied | interview | passed | rejected
    application_status: str = Field(default="none", index=True)
    status_updated_at: Optional[datetime] = None
    status_note: Optional[str] = None
    # 본문(JD) — Playwright + trafilatura로 추출한 마크다운
    jd_md: Optional[str] = None
    jd_fetched_at: Optional[datetime] = None
    jd_error: Optional[str] = None
    # ATS 키워드 — 본문 추출 후 자동 분석 (정규식 + kiwi)
    # {"required":[...], "preferred":[...]} JSON
    ats_keywords_json: Optional[str] = None
    # ATS 매칭 결과 캐시 — 이력서 vs ats_keywords 점수/누락 계산 결과
    # {"score":N, "matched_required":[...], "missing_required":[...], ...} JSON
    # ats_match_resume_hash와 짝으로 사용: 이력서가 바뀌면 hash 달라져서 자동 invalidate
    ats_match_json: Optional[str] = None
    ats_match_resume_hash: Optional[str] = Field(default=None, index=True)
    # 자기소개서 — 공고별 1개 캐시 (LLM 생성).
    # {"items":[{"question":..,"answer":..}], "highlight":.., "tone":.., "resume_hash":..} JSON
    cover_letter_json: Optional[str] = None
    cover_letter_at: Optional[datetime] = None


class CrawlSource(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    base_url: str
    last_run_at: Optional[datetime] = None


class SentimentSnippet(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    source: str = Field(index=True)  # jobplanet | blind | community
    text: str
    score: Optional[float] = None  # -1.0 ~ 1.0
    posted_at: Optional[datetime] = None
    captured_at: datetime = Field(default_factory=utcnow)


class HomepagePage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    url: str = Field(index=True)
    content_md: Optional[str] = None
    depth: int = 0
    fetched_at: datetime = Field(default_factory=utcnow)


class SearchHistory(SQLModel, table=True):
    """잡 수집/검색 키워드 히스토리. 같은 키워드는 빈도수 증가."""

    id: Optional[int] = Field(default=None, primary_key=True)
    keyword: str = Field(index=True, unique=True)
    hit_count: int = 1
    last_searched_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Resume(SQLModel, table=True):
    """취업자(사용자)의 이력서. 다중 버전을 허용하되 is_primary=True 1건이 메인.

    sections_json 스키마 (이력서 PDF 표준 양식 기반 — 번호 섹션 + 자기소개서 표):
    {
      "personal": {"name_kr","name_en","birth_date","gender","phone","email","address","road_address","military","github"},
      "education": [{"school","major","degree","start_date","end_date","status","gpa","gpa_max","location"}],
      "experience": [{"company","department","position","start_date","end_date","is_current","duties","salary","leave_reason"}],
      "trainings": [{"start_date","end_date","name","org"}],          # 교육사항(부트캠프/과정)
      "skill_groups": [{"category","detail"}],                          # 전산관련(보유기술) 구분|사용 내용
      "certifications": [{"name","issuer","acquired_date","number"}],
      "languages": [{"language","test","score","acquired_date"}],
      "awards": [{"name","issuer","awarded_date","description"}],
      "projects": [{"name","summary","period"}],                        # 프로젝트
      "self_intros": [{"title","content"}],                             # 자기소개서(사용자 항목 추가)
      "custom": [{"title","content"}],                                  # 기타 사용자 정의 섹션
      "preferences": {"desired_role","desired_salary","desired_location","start_available","work_type"},
      "closing": {"date","author"}                                      # 확인 문구
    }
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="기본 이력서")
    role: Optional[str] = None        # 희망 직무 (대시보드 요약용)
    summary_md: Optional[str] = None  # 한 줄 자기소개
    content_md: Optional[str] = None  # 자유 본문 (마크다운; 업로드 변환 결과 등)
    skills_csv: Optional[str] = None  # 콤마 구분 스킬 태그
    years_experience: Optional[int] = None
    photo_file_id: Optional[int] = Field(default=None, foreign_key="resumefile.id")
    sections_json: Optional[str] = None
    is_primary: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ResumeFile(SQLModel, table=True):
    """이력서/포트폴리오 업로드 파일. 변환된 마크다운을 함께 저장해 미리보기에 사용."""

    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: Optional[int] = Field(default=None, foreign_key="resume.id", index=True)
    kind: str = Field(default="resume", index=True)  # resume | portfolio
    original_name: str
    stored_path: str               # data/uploads/<uuid>.<ext>
    mime: Optional[str] = None
    size_bytes: int = 0
    content_md: Optional[str] = None  # 변환된 마크다운 (미리보기/검색용)
    convert_error: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=utcnow)


class DomainDiscoveryAttempt(SQLModel, table=True):
    """도메인 발견 audit log — "왜 이 도메인이 잡혔지" 디버깅용.

    가벼운 append-only 로그. UI에서 직접 조회 안 함; 문제 발생 시 SQL로만 본다.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    company_name: str
    attempted_at: datetime = Field(default_factory=utcnow, index=True)
    candidates_json: Optional[str] = None  # [{host, score, title}] 직렬화
    chosen_host: Optional[str] = None
    chosen_score: Optional[int] = None
    chosen_via: Optional[str] = None       # "strong" | "llm_verified" | "llm_borderline" | "rejected"
    rejection_reason: Optional[str] = None


class ApiKeyStatus(SQLModel, table=True):
    """외부 API 호출 결과 캐시 (마지막 호출 시점의 ok/실패 메시지)."""

    key_code: str = Field(primary_key=True)   # kakao_rest / kakao_js / odsay / dart / ollama
    ok: bool = Field(default=False, index=True)
    message: Optional[str] = None             # 실패 사유 등
    last_check_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


class CrawlJob(SQLModel, table=True):
    """In-flight or completed background task tracker (mirrors Arq job)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    arq_job_id: Optional[str] = Field(default=None, index=True)
    kind: str  # crawl_url | collect_jobs | research_company | geocode | full_pipeline
    payload_json: Optional[str] = None
    status: str = Field(default="pending", index=True)  # pending | running | done | error | cancelled
    progress_pct: int = 0
    stage: Optional[str] = None
    message: Optional[str] = None
    result_json: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
