<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:047857,100:10b981&height=200&section=header&text=WHOAREYOU&fontSize=62&fontColor=ffffff&fontAlignY=36&animation=fadeIn" width="100%" />

### 🌿 취업 준비, 한 곳에서 끝내는 올인원 플랫폼

채용 공고 통합 검색부터 **이력서 ATS 매칭**, **AI 기업 조사**, **평판 감정 분석**, **통근 거리 계산**까지 —
흩어져 있던 취업 준비 과정을 하나의 로컬 서버로 묶었습니다.
**100% 로컬** — LLM은 Ollama, DB는 SQLite. 내 데이터는 내 PC에만 남습니다.

<br/>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-000000?style=for-the-badge&logo=ollama&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3366CC?style=for-the-badge&logo=htmx&logoColor=white)

</div>

---

## 주요 기능

- **통합 채용 검색** — 사람인 · 잡코리아 · 원티드 3개 사이트를 키워드 하나로 동시 수집하고 URL 기준 중복 제거. 사이트별 최적 우회를 자동 선택합니다 (사람인 `httpx`, 잡코리아 `Playwright`, 원티드 `JSON API`).
- **이력서 ATS 매칭** — 한국 채용 ATS(그리팅 · 나인하이어 등)의 평가 로직을 역설계. 이력서를 올리면 `kiwipiepy`(한국어 명사 추출) + `rapidfuzz`(편집거리)로 공고별 통과 확률을 **0~100점**으로 계산합니다. LLM 없이 빠르게.
- **AI 기업 조사** — 회사 홈페이지를 Playwright로 최대 16페이지(메인 · 소개 · 연혁 · 뉴스 · 고객사 등) 크롤 → `Ollama` LLM이 사업 내용을 구조화 요약.
- **평판 감정 분석** — 네이버(직장인 후기 · 카페 · 지식iN)와 구글(보도자료 · 투자 · B2B)을 함께 검색해 평판 스니펫을 수집 → LLM 감정 분석 → 키워드 워드클라우드.
- **맞춤 자소서 생성** — 내 이력서 · 기업 조사 · 공고 본문 · ATS 결과를 근거로 지원동기 · 자기소개서 초안을 생성. 이력서에 **있는 사실만** 사용해 환각을 차단합니다.
- **위치 · 통근 분석** — 카카오 지오코딩으로 회사를 좌표화하고, 집까지 직선거리(Haversine) + ODsay 대중교통 경로 · 소요시간 · 비용을 계산.
- **재무 · 공시** — DART API로 재무/공시 요약 (corp_code 매칭 시).
- **실시간 진행도** — 크롤 · 조사 진행률을 WebSocket으로 실시간 푸시.

---

## 🖼️ 미리보기

| 대시보드 — 취업 현황 한눈에 | 채용 검색 + ATS 매칭 점수 |
|:---:|:---:|
| ![대시보드](docs/img/dashboard.png) | ![채용 검색](docs/img/jobs.png) |
| **기업 조사 — 개요 · 공고 · 감정 · 키워드 · 지도 5탭** | **전체 기업 지도 + 집과의 거리순** |
| ![기업 조사](docs/img/company.png) | ![지도](docs/img/map.png) |

---

## 🚀 빠른 시작

```bash
# 1) 의존성 설치 (uv)
uv sync
uv run playwright install chromium

# 2) Ollama 모델 준비
ollama pull qwen3.5:9b          # 텍스트 LLM (기업 조사 · 감정 분석 · 자소서)
ollama pull qwen2.5vl:7b        # 비전 fallback (옵션 — 크롤 실패 페이지 OCR)

# 3) 실행
uv run python serve.py          # http://127.0.0.1:8000
```

> 💡 엔트리포인트는 `serve.py`입니다. Windows에서 `uvicorn`을 직접 띄우면 이벤트 루프가 `Selector`로 강제 전환돼 Playwright 서브프로세스가 죽습니다. `serve.py`가 `Proactor` 정책을 강제해 이 문제를 해결합니다.

### 첫 설정 (`/settings`)

| 키 | 발급처 | 용도 | 필수 |
|---|---|---|:---:|
| **카카오 REST / JS Key** | [developers.kakao.com](https://developers.kakao.com) | 회사 좌표 변환, 지도 표시 | ✅ |
| **집 주소** | — | 자동 좌표 변환 → 회사까지 거리 계산 | ✅ |
| **ODsay Key** | [lab.odsay.com](https://lab.odsay.com) | 대중교통 경로 · 시간 · 비용 | ⬜ |
| **DART API Key** | [opendart.fss.or.kr](https://opendart.fss.or.kr) | 재무/공시 요약 | ⬜ |
| **Ollama 모델명** | — | 기본 `qwen3.5:9b` / `qwen2.5vl:7b` | ⬜ |

---

## 🧭 페이지

| URL | 용도 |
|---|---|
| `/` | 대시보드 — 지원 현황 칸반 + 이력서 완성도 + 채용 키워드 워드클라우드 |
| `/jobs` | 채용 검색 — 3사 통합 수집 + ATS 매칭 점수 + 필터 검색 |
| `/job/{id}` | 공고 상세 — 본문(JD) + 매칭 키워드 분석 |
| `/resumes` | 이력서 목록 — 다중 이력서 카드 그리드 |
| `/resume` | 이력서 편집 — 업로드(docx/pdf) · 파싱 · 공고 매칭 |
| `/company/{id}` | 회사 상세 — 개요 · 공고 · 감정 · 키워드 · 지도 5탭 + 조사 트리거 |
| `/map` | 전체 회사 지도 + 집과의 거리순 리스트 |
| `/settings` | API 키 · 모델 · 집 주소 |

---

## 🔄 데이터 흐름

```text
[키워드 검색]
  사람인(httpx) + 잡코리아(Playwright) + 원티드(JSON API)
     → URL 중복 제거 → Job / Company DB 저장 → 카카오 지오코딩

[이력서 업로드]  (docx · pdf)
  markitdown 파싱 → kiwipiepy 명사 추출
     → 공고별 ATS 매칭 점수(rapidfuzz) 계산

[기업 조사 트리거]
  회사 홈페이지 Playwright 크롤(최대 16p) → Ollama LLM 구조화
   + 네이버 · 구글 평판 검색 → LLM 감정 분석 → 키워드 집계
   + ODsay 대중교통 + DART 재무 + 집까지 거리
     → 진행도를 WebSocket으로 실시간 푸시
```

---

## 🛠 기술 스택

| 영역 | 사용 기술 |
|---|---|
| **백엔드** | FastAPI · SQLModel · aiosqlite · Pydantic Settings · uv |
| **크롤링** | Playwright · httpx · Trafilatura · readability-lxml · markitdown |
| **AI / NLP** | Ollama (qwen3.5) · kiwipiepy · rapidfuzz |
| **프론트엔드** | Jinja2 · Tailwind CSS · HTMX · Alpine.js · SweetAlert2 · Lucide |
| **외부 API** | 카카오(지오코딩 · 지도) · ODsay(대중교통) · DART(공시) |
| **비동기 큐** | asyncio in-memory (기본) · Arq + Redis (옵션) |

---

## 📁 디렉토리 구조

```text
app/
├── main.py · config.py · db.py · deps.py · models.py
├── routes/      pages · api_jobs · api_companies · api_dashboard · api_resume · api_ollama · ws_progress
├── crawler/     browser · strategies · extractor · llm · vision_fallback · jd_fetcher · pipeline
│   └── adapters/  saramin(httpx) · jobkorea(Playwright) · wanted(JSON API)
├── analysis/    ats(매칭) · resume_text · document(파싱) · cover_letter(자소서) · keywords · emotion · categories
├── companies/   pipeline · research · discover · geocode · dart · sentiment · community(네이버·구글) · playwright_research
├── geo/         kakao · odsay · distance(Haversine)
├── ui/          progress_bus · settings_store · api_status
└── workers/     arq_settings · tasks (Redis 활성 시)

templates/
└── base.html(녹색 Tailwind/HTMX/Alpine) · index · jobs · job_detail · resume(s) · company(5탭) · map · settings
```

---

## ⚠️ 알려진 사항

- **Windows 콘솔** — 기본 CP949라 한국어 `print`가 깨져 보입니다. 데이터는 UTF-8로 정상 저장됩니다. 콘솔도 정상으로 보려면 `chcp 65001` + `PYTHONUTF8=1`.
- **Redis 미사용** — 기본은 in-memory asyncio 진행도 버스로 동작. Redis를 띄우면 `app/workers/arq_settings.py`로 워커를 분리할 수 있습니다.
- **원티드 rate-limit** — 잦은 호출 시 일시적으로 빈 응답이 옵니다. 재시도하면 복구됩니다.
- **평판 검색** — 네이버 · 구글 검색 결과를 동적 렌더링으로 긁기 때문에, 노출이 적은 회사는 스니펫이 적게 수집될 수 있습니다. 

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:10b981,100:047857&height=120&section=footer" width="100%" />

</div>
