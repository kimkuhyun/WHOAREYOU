# CLAUDE.md — WHOAREYOU 2.0 개발 기준 문서

> **이 문서는 모든 작업의 단일 기준(Source of Truth)이다. 코드를 짜기 전·구조를 바꾸기 전 반드시 이 문서를 먼저 본다.**
> 충돌이 생기면 이 문서가 우선한다. 결정이 바뀌면 코드보다 **이 문서를 먼저 고친다.**
> 기획안 html(`../WHOAREYOU_2.0_기획안.html`)은 **삭제 예정** — 화면 목업·위생기능·디자인 등 그 내용을 이 문서에 모두 흡수했다. **이 문서가 유일 문서.** (html v0.5의 *Playwright 지연폴백·오프라인/로컬번들·"유일 ML"* 표현은 폐기 — 아래가 최신: Playwright 안 씀, 프론트 CDN, 변환모델은 임베딩+OCR.)
>
> ### 폴더 상태 (2026-07-01 — archive·___test 삭제 완료)
> 루트 = **`CLAUDE.md` · `app/` · `web/` · `run.py` · `whoareyou.spec` · `setup.iss` · `dist/`·`build/`**. **`archive/`·`___test/` 삭제됨**(v1 참고 끝 — 키는 user_settings.json으로 이관 후 폐기). `app/`은 완전 자립(archive 무의존). (`.venv`는 인프라라 유지)
> **주의**: 검증 산출물(`archive/_pipeline_test/*`, jotso 대조표)도 archive와 함께 사라짐 — 필요하면 git 이력에서. 국민연금 bulk DB는 스크래치패드에만.

---

## 0. 한 줄 정의
**설치형 백그라운드 취업 매칭 에이전트.** 최초 1회 설정 → 상주 데몬이 하루 1회(기본) 신규 공고를 수집·채점·알림 → 사용자는 알림 보고 마음에 드는 곳에 직접 지원. (v1 "열어서 검색하는 웹도구" → v2 "알아서 찾아주는 상주 프로그램")

## 1. 절대 원칙 (어기지 말 것)
1. **데이터는 로컬(프라이버시)** — 이력서·지원현황은 PC를 벗어나지 않는다. 단 **앱은 온라인 도구**(공고 크롤·국민연금·모델 다운로드에 인터넷 필요)라 "오프라인 동작"은 목표가 아니며, **프론트 자산은 CDN 사용**(간단).
2. **결정론 우선 · 추론(채팅/비전) LLM 없음** — 점수·판정은 전부 규칙(거리·산식·별점·키워드/임베딩 cosine). **결정론적 변환 모델은 허용**: bge-m3 임베딩(fastembed ONNX, CPU) · 이미지 공고용 OCR. ❌ 금지 = 추론·생성하는 **채팅/비전 LLM·Ollama**. "숫자는 사실, 모델은 변환만(텍스트→벡터, 이미지→텍스트)."
3. **단일 패키지 · 네이티브 창** — `pywebview`(브라우저 아님) 창 + `pystray` 트레이. 설치형 1개. 터미널 노출 금지.
4. **all-httpx 크롤 (Playwright 안 씀)** — 3사(사람인·잡코리아·원티드) 전부 httpx로 됨을 실측 확인. **Playwright/Chromium 도입 안 함 — 폴백도 없음.**
5. **정직성** — 근거 없으면 "판단 보류". **침묵형 실패 금지** — 데이터 소스(크롤·국민연금 매칭·잡플래닛 별점·통근 API)가 **조용히 0건/실패인데 '정상'처럼** 보이게 하지 마라(LLM과 무관). 예: 사이트 개편으로 0건, 좋소 매칭 실패, 별점 못 긁음 → 반드시 신호화. 추정치는 한계를 함께 표기.
6. **법적 안전** — 회사 **공개 랭킹·라벨링 금지**(명예훼손). "내가 지원할 회사만 로컬에서 사적 조회"만.
7. **기존 자산 재사용 · 스택 유지** — adapters·ats·geo·pipeline·settings 재사용. React 등 새 프레임워크 도입 금지.

## 2. 기술 스택
| 영역 | 라이브러리 | 상태 |
|---|---|---|
| 언어/런타임 | Python ≥3.11, `uv` | 재사용 |
| 백엔드 | FastAPI[standard], uvicorn, asyncio | 재사용 |
| 스케줄러 | **APScheduler** (하루 1회(기본)) | 신규 |
| 크롤 | **httpx** (3사 전부 httpx 확인 · Trafilatura·readability·lxml 파싱) | 재사용·통일 |
| 임베딩 | **fastembed (ONNX, bge-m3, CPU 고정)** | 신규(=Ollama 대체) |
| 이미지 공고 OCR | **Surya** (transformer OCR, GPU 있으면 자동 사용·없으면 CPU) | 신규(채팅 LLM 아님 · 한국어 최상급 로컬) |
| 이력서 매칭 | kiwipiepy + rapidfuzz **+ 스킬 정규화 사전** | 개선 |
| DB | SQLite + SQLModel + aiosqlite (WAL) | 재사용 |
| 지오/통근 | 카카오맵 + ODsay | 재사용 |
| 좋소 데이터 | 국민연금 **전국 월별 파일 bulk 적재** + 회사별 API(`data.go.kr/15083277`) | 신규(§7) |
| 평판 | 잡플래닛 **별점** — **`curl_cffi`(Chrome TLS 위장)**·로그인X | 변경(감정분석 대체) |
| 봇우회 | **curl_cffi** — 잡플래닛 Cloudflare 403을 TLS 위장으로 통과(httpx는 막힘). **브라우저 아님** | 신규 |
| 프론트 | Tailwind·Alpine.js·HTMX·SweetAlert2·Lucide·Pretendard (**전부 CDN** — 온라인 도구) | 재사용 |
| 데스크톱 | **pywebview + pystray** | 신규 |
| 알림 | **winotify**(Windows 토스트, 클릭→메인 창 추천 탭) · 카톡/이메일 | 신규 |
| 패키징 | **PyInstaller + Inno Setup** | 신규 |
| ❌ 제거 | **Ollama · 채팅 LLM · Playwright/Chromium** | — |

## 3. 폴더 구조 (2.0 목표 — 루트에 새로 구축)
> 아래는 **2.0 목표 레이아웃**(루트에 새로 만든다). v1 원본은 `archive/`에 동일 구조로 보존돼 있으니 **재사용할 모듈은 `archive/`에서 가져와 루트로** 옮기며 만든다. `재사용`=archive에서 가져옴 / `변경`=수정 / `신규`=새로 / `제거`=안 가져옴
```
WHOAREYOU/
├── CLAUDE.md                  # 이 문서
├── serve.py                   # 엔트리, 포트 8005. 변경: pywebview+pystray 부팅 추가
├── pyproject.toml / uv.lock
├── app/
│   ├── main.py                # FastAPI app + lifespan. 변경: 스케줄러 시작, Ollama 헬스 제거
│   ├── config.py db.py deps.py models.py   # 재사용(models 확장)
│   ├── crawler/
│   │   ├── adapters/
│   │   │   ├── saramin.py     # 재사용(httpx + 0건 신호화 구현됨)
│   │   │   ├── jobkorea.py    # 변경: Playwright→httpx(HTML 파싱)
│   │   │   ├── wanted.py      # 변경: API→웹 __NEXT_DATA__ 파싱(채용전형 포함)
│   │   │   └── base.py        # 재사용(JobStub·normalize_company_name)
│   │   ├── extractor.py jd_fetcher.py strategies.py   # 재사용
│   │   ├── browser.py vision_fallback.py             # 제거(Playwright/비전 안 씀)
│   │   └── llm.py llm_catalog.py                      # 제거 대상(Ollama)
│   ├── companies/
│   │   ├── pipeline.py        # 재사용: collect_jobs/CollectStats(+warnings)
│   │   ├── discover.py dart.py geocode.py community.py # 재사용
│   │   ├── sentiment.py       # 변경: 잡플래닛 별점으로 대체
│   │   ├── playwright_research.py  # 변경: LLM 제거·슬림화(또는 enrich로 흡수)
│   │   └── nps.py             # 신규: 국민연금 적재·매칭·좋소 위험도 산식
│   ├── analysis/
│   │   ├── ats.py             # 변경: 하이브리드(어휘+사전+임베딩)
│   │   ├── embeddings.py      # 신규: fastembed bge-m3 래퍼(CPU)
│   │   ├── skills.py          # 신규: 스킬 정규화 사전(동의어·약어·한영)
│   │   ├── resume_text.py cover_letter.py keywords.py categories.py  # 재사용
│   ├── scoring.py             # 신규: 4신호 종합 점수·임계점·결측 재정규화
│   ├── scheduler.py           # 신규: APScheduler 파이프라인(1x/day 기본)
│   ├── notify.py              # 신규: 토스트/카톡/이메일
│   ├── desktop.py             # 신규: pywebview 창 + pystray 트레이
│   ├── geo/ (distance kakao odsay)            # 재사용
│   ├── routes/ (api_* pages ws_progress)      # 변경: 검색/대시보드 UI 제거 → 추천·설정·상태로 재편. api_ollama 제거
│   ├── ui/ (api_status progress_bus settings_store)  # 재사용
│   └── workers/ (arq_settings tasks)          # 제거/대체: arq→APScheduler
├── templates/
│   ├── base.html             # 변경: nav 재편(추천·설정·상태). 프론트는 CDN 유지
│   ├── recommend.html        # 신규: 추천 탭(jobs.html 재활용)
│   ├── settings.html         # 변경: 추천 임계점·개수 추가, 포트/키 가이드 8005
│   ├── status.html           # 신규: 상태 탭
│   ├── onboarding.html       # 신규: 5스텝 위저드
│   └── (company/job/resume/index/crawl/placeholder.html  # 정리·제거 검토)
├── static/
│   └── js/app.js             # 프론트 라이브러리(Tailwind·Alpine·HTMX·Lucide·Pretendard)는 CDN
├── data/   → 배포 시 %APPDATA%\WHOAREYOU\ : whoareyou.db · uploads/ · backups/ · nps/
└── docs/
```

## 4. 파이프라인 (매 주기 · 자동/수동 공통)
```
수집(all-httpx 3사)
  → ① URL dedup 선(先)스킵  ← 본 공고/지원함/관심없음/블랙리스트 즉시 제거 (점수·임베딩 전 = 가장 빠름)
  → ② 4신호 채점 (통근·좋소·평판·이력서)
  → ③ 상위 후보만 enrich (좋소 정밀·평판 별점)  ← 비용 통제
  → ④ 임계점(기본 70) 이상만 Notifier → 토스트/카톡/이메일
```
- **스케줄**: APScheduler 하루 1회(기본, `config.DAILY_TIME`). 야간 방해금지·저전력·rate-limit.
- **검색 3모드**: ①자동(스케줄) ②지금 검색(현 설정 즉시 재실행) ③직접 검색(키워드·회사명 입력 → 즉석 채점).

## 4a. 자동 위생/거르기 (지시 없이 기본 탑재)
- **본 URL은 점수 전에 스킵**(가장 빠름·비용0) · 이미 지원 제외 · "관심없음" 제외 · 블랙리스트(회사·"포괄임금"·"열정페이").
- **재게시 반복 = 이직 신호로 역활용**(같은 공고 자꾸 올라옴 = 사람 자주 나가는 곳) · 마감 임박 우선.
- 야간 방해금지(조용시간) · 하루 1~2회 **묶음 알림** · rate-limit·휴먼라이크 지연 · 저전력 스케줄 · 주간 자동 백업.
- 신호 결측 = 0점 아님 → "판단 보류"로 빼고 재정규화(§5).

## 5. 4신호 스코어링 (`app/scoring.py`)
종합 = Σ(신호 × 가중치). **결측 신호는 0이 아니라 "판단 보류"로 빼고 재정규화.** 임계점↑만 추천, 상위 N(기본 10) 표시. 가중치는 사용자 슬라이더.
| 신호 | 가중 | 출처 | 정규화 |
|---|---|---|---|
| 🏠 통근 | 25 | 카카오+ODsay | 30분↓=1.0 → 90분↑=0.0 |
| 📉 좋소 위험도 | 25 | 국민연금(§7) | jotso "좋소력"(낮을수록 좋음) → **기여=(100−좋소력)/100** |
| ⭐ 평판 | 20 | 잡플래닛 별점 | 별점/5 |
| 🎯 이력서 매칭 | 30 | 하이브리드(§6) | 0~1 |

## 6. 이력서 매칭 (`app/analysis/ats.py` 하이브리드 · NO LLM)
1. **어휘**(유지) — JD 섹션 정규식 분리 + kiwipiepy 명사 + rapidfuzz(오타 흡수). 필수×3+우대×1.
2. **스킬 정규화 사전**(`skills.py`) — ML=머신러닝=기계학습, K8s=쿠버네티스, FE=프론트엔드 등.
3. **의미 임베딩**(`embeddings.py`) — bge-m3(fastembed ONNX, CPU)로 JD 요구항목↔이력서 문장 cosine → 동의어·문맥 매칭.
4. 산출: 보유/부족/의미근접 키워드 + 점수. **전부 결정론.**
- 왜 개선: 옛 `ats.py`는 rapidfuzz 편집거리(오타 수준)만 봐서 "ML=머신러닝", "K8s=쿠버네티스", "백엔드=서버개발" 같은 약어·동의어·한영을 못 잡음 → 어휘+사전+임베딩 3겹으로 해결.

## 7. 좋소 위험도 — **jotso.net 직접 연결로 확정**(2026-07-01)
> **결정: 좋소 = jotso.net 조회.** 직접 빌드(NPS 적재)는 **12개월 마이크로데이터가 공개 안 됨**(data.go.kr·m.nps·data.nps·GitHub·Kaggle 5곳 확인 — 매월 덮어쓰기)이라 보류. jotso가 이미 "최근 12개월"(jotso 원문)로 계산해둠 → **회사명으로 조회해 좋소력·등급·회전율 그대로 가져옴.** 값 100% 일치 검증됨.
> 구현: `JotsoClient`(`___test/jotso_client.py`) = `api/search`→이름매칭→회사페이지 파싱. **curl_cffi(Cloudflare 우회).**
> ⚠ **호출 통제(필수·보수적)**: jotso는 소규모 서비스 → ① **상위 N(`ENRICH_TOP_N`=`PER_SITE`×3, 기본 15)만 enrich**(싼 신호=매칭·통근으로 먼저 랭킹 후) ② **회사 dedup** ③ **캐시: 찾음 30일·미등록 7일·일시오류는 캐시 안 함**(좋소 월 갱신) ④ **throttle: 동시 1(병렬X)·실제 호출마다 1.5s+지터·검색↔회사페이지 0.5s**. ⑤ **지연은 실제 네트워크 호출에만**(캐시 적중은 즉시 — 무조건 sleep 금지). "90개 폭격 금지".
> ⚠ **좋소 조회 상한 ≠ 표시 개수(2026-07-01 분리)**: 이전엔 `top=scored[:ENRICH_TOP_N]`가 좋소 조회와 표시를 동시에 제한 → 3사×5=15 필터통과인데 12만 남기고 **3개 조용히 절단**(§1 위반). 수정: **표시는 필터통과분 전량**(`for s in scored`), **좋소·평판 조회만 상한(top)**. 기본은 `ENRICH_TOP_N=PER_SITE×3`이라 상한=전량 → 모든 추천에 좋소 도달. `per_site`를 크게 잡으면 상한 밖 꼬리는 좋소 "표본 부족"(요율 통제·정직).
> 자립 옵션(후속): 데몬이 매월 공개 파일 1개씩 누적 → ~1년 뒤 직접 계산 가능(그땐 아래 산식). 지금은 jotso 연결.

<details><summary>참고: 직접 빌드 시 산식(jotso 역설계 — 데이터 확보되면)</summary>

> 레퍼런스 **jotso.net**을 실측·역설계. 검증·대조: `archive/_pipeline_test/compare*.md`.

### 데이터 = 국민연금 전국 월별 파일(bulk 적재)
- **회사별 API로는 불가**: `getPdAcctoSttus`는 **당월 1개월치만** 줌(시계열·분포 없음) → 업종분위·1년 인원증감·전국 백분위를 못 냄. 그래서 **전국 파일을 적재**한다(jotso도 전수 데이터 보유).
- **다운로드(로그인 불필요)**:
  - 최신 1개월: data.go.kr `cmm/cmm/fileDownload.do?atchFileId=…&fileDetailSn=1` (**CP949 CSV ~114MB, 약 55만 등록사업장**).
  - **과거 월**: NPS 공공데이터 게시판 `m.nps.or.kr/inforls/publdata/getOHAB0019M1.do`에 **월별 zip(~34MB)** 게시(예 `…_20250923.zip`). 2025년 중반부터라 약 **10~12개월 = "최근 1년"**.
- **기간: 12개월이면 충분**(jotso도 "최근 1년 월평균". 5년 불필요 — 데이터만 5배).
- 운영: 데몬이 **매월 최신 파일 받아 누적** → 12개월 윈도 유지. 적재 빠름(55만행 ~3초, SQLite).
- CSV 컬럼: 자료생성년월·사업장명·**사업자등록번호(앞6)**·가입상태(1등록)·주소/지역코드·형태(1법인/2개인)·**업종코드+명**·적용일자·탈퇴일자·**가입자수·당월고지금액·신규취득자수·상실가입자수**. (3인↑법인/10인↑개인만 수록)

### 신호 (jotso 실측, 전부 결정론)
| 신호 | 계산 | 비고 |
|---|---|---|
| **추정연봉** | 당월고지금액÷가입자수÷**0.09**×12 | **jotso 표시값과 정확히 일치 검증됨**(한올 3195=3195·공감 2555=2555). 기준소득월액 상한(~617만)이라 고연봉 캡 |
| **업종 내 연봉분위** | 같은 업종코드 회사들 중 추정연봉 백분위(절대값 아님) | jotso "업종 중앙값의 N%"·"하위 25%" |
| **회전율·퇴사율** | (신규+상실)÷가입자수의 **12개월 월평균** | ⚠ **당월 1개월만 쓰면 소표본(4~7명)에서 1~2명 변동에 폭등→false positive**(소너비스·토마토). 반드시 12개월 평균. 소표본은 업종평균으로 수축 |
| **평균 근속** | ≈ 1 ÷ 연 퇴사율(Little) | 짧을수록 좋소. jotso 최강 신호 |
| **1년 인원증감** | 현재 vs 12개월 전 가입자수 | 급감=소멸위험 |
| 제외 | 1000명+ 대기업 | jotso도 "판별 대상 아님"(좋소력 0) |

### 점수 = 전국 백분위 → 좋소력
- raw 위험도 → **전국 전체 회사 대비 백분위** → **좋소력 0~100(낮을수록 좋음)**. jotso는 백분위를 **수능 등급컷**에 압축 → 우리 raw 백분위를 **jotso 표본으로 스케일 보정(코스메틱)** 해 숫자/라벨 맞춤.
- **라벨**(jotso, 좋→나쁨): 희귀 중소 → 좋소 아님 → 간 보는 중 → 좋소 향기 → 좋소 확정.
- **매칭은 사업장명→전국파일에서 가입자수 최대 사업장**. (이름만 API 매칭하면 동명 5명짜리 가짜 '에스원'을 잡음 → 전수 파일이라 진짜 7030명 에스원을 찾아 해결. 사업자번호 있으면 1순위.)
- ⚠ **방향**: jotso 좋소력=낮을수록 좋음. §5 기여는 `(100−좋소력)/100`.
- **표기 필수**: 추정치(상한 캡)·입퇴사 정의 오염·시차·맥락(왜 퇴사) 모름.

### ⚠ 현실 제약 (2026-07-01 검증 — jotso 대조 `compare*.md`)
- **확실히 맞는 것**: 추정연봉(jotso 표시값과 일치)·업종·**대기업/고유명 매칭**(에스원 7030·디에스 11248·파수 = jotso와 정확).
- **아직 안 맞는 것 2개 + 원인**:
  1. **이름 매칭 충돌** — 동명 회사를 오매칭(예: 직원 6명 '리베타' vs 61명 '리베타'). → **사업자번호 1순위 매칭** 또는 지역+업종 보정 필수. 이름만 매칭 금지.
  2. **단월 회전율 노이즈** — 소표본(4~16명)에서 1개월 입·퇴 1~2명이 폭등 → false positive(소너비스·토마토). → **12개월 평균 필요**.
- **12개월 contiguous 풀데이터는 공개 안 됨(확정 — data.go.kr·m.nps.or.kr 게시판·data.nps.or.kr 전수 조사)**: ※ `data.nps.or.kr/pportal listWorkPlace`는 **연 단위 집계 통계(2012~, 월·다운로드 없음)** 라 마이크로데이터 아님.
  - 풀 사업장 내역(55만행) = data.go.kr **최신 1개월만**(`fileDownload.do?atchFileId=…`, 114MB CSV CP949) + NPS 게시판 **standalone 2건**(`getOHAB0019M0List.do` 목록의 `국민연금 가입 사업장 내역_2025092 3/1024`, 34MB zip, 다운: `m.nps.or.kr/fileDown.do?atchFileId={FL…}&atchFileSn=1`).
  - ⚠ 월별 "YYYY년 M월 기준 국민연금 통계" 묶음글(8개월치)은 **요약집계 19KB일 뿐 마이크로데이터 아님**(함정).
  - → **데몬이 매월 풀파일 받아 누적**해 12개월 윈도 구축(jotso도 이렇게 시간 들여 축적). 지금은 비연속 3개월(Aug·Sep'25+2026-05)만 가능.
- **코스메틱 스케일 보정(수능 등급컷)은 신호가 깨끗해진 뒤에만** 의미 — 노이즈/오매칭 위에 fit하면 overfit(가짜 일치). 12개월+사업자번호 매칭 후 적용.
- → 결론: 좋소는 **P3 구현 과제**(월별 누적 + 사업자번호 매칭 + 그 다음 스케일 보정). v2 초기엔 "추정연봉+업종분위+강댐핑(대기업 정확)" 수준으로 시작, 매월 정밀화.

### 회사별 API (보조 — 즉석 단건 조회)
`NpsBplcInfoInqireServiceV2` 3-op, 키=`settings.nps_service_key`(저장됨):
1. `getBassInfoSearchV2?wkplNm=` → 후보 seq + **레코드의 dataCrtYm**.
2. `getDetailInfoSearchV2?seq=` → `jnngpCnt`(가입자)·`crrmmNtcAmt`(고지액)·`wkplIntpCd`(업종)·`adptDt`.
3. `getPdAcctoSttusInfoSearchV2?seq=&dataCrtYm=`**(반드시 레코드 자신의 dataCrtYm)** → `nwAcqzrCnt`(입사)·`lssJnngpCnt`(퇴사). ⚠ 임의 달 넣으면 전부 빈값.
- 403 Forbidden=키 활성화 전(가짜키는 401), 신규신청 후 1~2h. UA 헤더 권장.

</details>

## 7b. 평판 (잡플래닛 별점 · `app/companies/jobplanet.py`)
- **plain httpx는 Cloudflare 403**(홈·검색 전부). → **`curl_cffi` `impersonate="chrome"`**(TLS 위장, **브라우저 없음**)로 통과 실측.
- 회사명→별점:
  1. `GET jobplanet.co.kr/api/search?q={회사명}` → `{results:[{bizNo,bizName,industry,members}]}` JSON.
  2. 검색페이지 임베디드 JSON에 `"name":"…","grade":N,"grade_count":M` — **`grade`=별점(/5)**, `grade_count`=리뷰수. **회사페이지 재요청 불필요**.
  3. **이름 매칭 필수**(검색에 광고/추천사 섞임 — 안 하면 전부 '장스푸드'로 오매칭). rapidfuzz/contains로 정답 채택. 미등록·리뷰0 = **N/A**(재정규화).
- 별점/5 → 평판 점수. 회사당 1회/일+캐시(레이트리밋 회피).

## 8. 크롤 규칙 (`app/crawler/`)
- **all-httpx 통일**: 사람인=HTML 파싱(`.item_recruit` 등, UA 우회) · 잡코리아=링크추출(`a[href*=GI_Read]`)+상세는 **JSON-LD(JobPosting) 파싱**(Tailwind 클래스라 CSS 셀렉터 회피) · 원티드=목록 API + 상세는 **웹페이지 `__NEXT_DATA__` 파싱**(⚠ API는 채용전형 누락이라 상세는 안 씀). 정확한 선택자는 §8a.
- **위치는 선택자/데이터속성으로 직접** (정규식 추출 폐기): 사람인은 `data-latitude/longitude`로 **좌표 직접 → geocode 불필요**. 잡코리아·원티드는 주소만 있어 좌표 없으면 geocode 폴백. (사이트별 정확한 위치는 아래 §8a 선택자 표 참조)
- **이미지 공고 처리** (사람인 일부는 본문이 `<img>` — httpx 텍스트로 안 읽힘. 실측: 표본 10건 중 2건이 `user_content` 텍스트 0자·이미지 다수): ① HTML 구조화 필드(직무·경력·지역·마감)+제목+위치는 이미지여도 항상 확보 → 그걸로 매칭·표시 ② JD 본문은 **Surya OCR**(img 다운로드→텍스트, GPU 자동·없으면 CPU)으로 보강. **비전 LLM·Playwright 스크린샷 안 씀**(v1 `vision_fallback`·`jd_fetcher` 비전경로 폐기). **판별 규칙**: `div.user_content` 텍스트 < 300자 AND `img` ≥ 1 → 이미지 공고 → OCR.
- **Playwright/Chromium 안 씀** — 3사 httpx 실측 확인(2026-07-01: 사람인 목록 20·상세 OK / 잡코리아 검색 24링크·상세 OK / 원티드 `__NEXT_DATA__` 전체 JD). httpx로 충분.
- 봇회피: rate-limit·휴먼라이크 지연·주기 6~12h.
- **침묵형 0건 신호화**: 200·정상길이인데 0건 → "파싱 깨짐(사이트 개편)" 플래그(saramin 구현 패턴 따를 것).

## 8a. 사이트별 선택자 레퍼런스 (실측 확정 — 2026-07-01, httpx)

> 셀렉터는 **각 어댑터 상단에 상수로 모아두고** 폴백 체인 + §8 침묵형 감지를 항상 붙인다. 사이트 개편 시 여기만 고치면 됨.

### 사람인 (HTML 파싱 · `User-Agent` 필수)
| 용도 | URL / 선택자 |
|---|---|
| 검색 목록 | `GET /zf_user/search/recruit?searchType=search&searchword={kw}&recruitPage={n}&recruitPageCount=50` |
| **서버측 필터** | `&exp_cd=1`(신입)`/2`(경력) · `&job_type=1`(정규직)`/2`(계약)`/4`(인턴)`/6`(파견)`/9`(프리)`/3`(병역)`/5`(알바)`/8`(위촉) **⚠ 대괄호 없이·단일값만** · `edu_min`(8대졸·9석사·10박사)은 방향안맞아 미사용·`loc`은 JS·`sal_min` |
| 공고 카드 | `.item_recruit` (폴백: `.list_item`, `[class*='item_recruit']`) |
| 제목+링크 | `.job_tit a` (폴백 `.area_job a`) — href에서 `rec_idx` 추출 |
| 회사 | `.corp_name a` (폴백 `.area_corp .corp_name`) |
| 지역 | `.work_place` (폴백 `.job_condition span`) |
| 마감 | `.job_date` (폴백 `.date`) |
| **상세 URL** | **`GET /zf_user/jobs/view?rec_idx={rec_idx}`** ⚠ `relay/view`는 위치 블록 없음 — 쓰지 말 것 |
| **좌표(직접)** | `#map_0` = `div.jv_cont.jv_location` 의 `data-latitude`·`data-longitude`·`data-address` → **geocode 불필요** |
| 주소 텍스트 | `address.address span.spr_jview.txt_adr` |
| 근무지역(텍스트) | `dl` 안 `dt`=「근무지역」 의 `dd` |
| JD 본문 | `div.user_content` (= `.jobsViewDetail_{rec_idx}`) |
| **경력·학력·근무형태·기업형태** | **`div.jv_summary dl` 의 dt/dd** (dt=「경력」「학력」「근무형태」「기업형태」·「기업형태」=중소/연구소 등) → 필터 구조화 소스 |
| 이미지 공고 | `div.user_content` 텍스트<300자 & `img`≥1 → Surya OCR |

### 잡코리아 (Next.js RSC · httpx · Playwright 폐기)
| 용도 | URL / 선택자 |
|---|---|
| 검색 목록 | `GET /Search/?stext={kw}&tabType=recruit` |
| 공고 링크 | `a[href*="/Recruit/GI_Read/"]` → `GI_No` 추출, **href 중복이라 URL 단위 dedup** (실측 24건) |
| ⚠ 카드 메타 | Tailwind 자동 클래스라 CSS 셀렉터 불안정 → **메타는 상세 JSON-LD에서 취득** |
| 상세 URL | `GET /Recruit/GI_Read/{GI_No}` |
| **데이터 본체** | `script[type="application/ld+json"]` 의 **JobPosting** JSON → `title` · `description`(JD 전문) · `datePosted` · `validThrough` · `employmentType` · `experienceRequirements` · `educationRequirements` · `hiringOrganization.name` · `jobLocation.address.streetAddress` · `baseSalary` |
| 좌표 | JSON-LD엔 없을 때 있음 → RSC 페이로드 정규식 `"latitude":N,"longitude":N`, 없으면 `streetAddress` geocode |
| **경력·학력·고용형태** | **모집요강 `//*[normalize-space(text())="경력"\|"학력"\|"고용형태"]/following-sibling::*[1]`** (⚠ JSON-LD `experienceRequirements`는 "신입·경력" 요약만 — 상세는 여기) → 필터 구조화 소스 |

### 원티드 (Next.js · httpx · ⚠ API 아님)
| 용도 | URL / 선택자 |
|---|---|
| 검색(목록용) | `GET /api/v4/jobs?country=kr&query={kw}&limit=20&offset={n}` → `data[].id` |
| **상세 본체** | **`GET /wd/{id}` 의 `script#__NEXT_DATA__`** → `props.pageProps.initialData` (⚠ API는 `hire_rounds`=채용전형 누락이라 상세는 반드시 웹페이지) |
| 필드 | `position`(제목) · `company.company_name` · `due_time`(마감) · `career{annual_from,annual_to,is_newbie}` · `main_tasks` · `requirements` · `preferred_points` · `intro` · `benefits` · **`hire_rounds`(채용전형)** · `category_tag` |
| 위치 | `address{location,district,full_location}` — **좌표 없음 → geocode 필요** |
| **경력·고용형태** | `career{is_newbie,annual_from,annual_to}` · **`employment_type`**(regular/contract/intern…) → 필터 구조화 소스. **⚠ 학력 필드 없음**(원티드는 학력 미요구) |

## 9. 화면 / UX (자기완결 — html 목업 흡수)
- **평소 = `pystray` 트레이 상주 + OS 토스트(주력).** 창은 보조. v1 검색·브라우징·지도 UI는 버린다.
- **트레이 우클릭**: 오늘의 추천(N) · 지금 검색 · 일시정지 · 설정 · 종료.
- **토스트 예**: "오늘의 추천 3건 — 평균 적합 87% · 가장 가까운 곳 23분" → 클릭 시 추천 창.
- **검색 3모드**: ①자동(1x/day 기본) ②지금 검색(현 설정 즉시 1회) ③직접 검색(추천 상단 바에 키워드·회사명 → 즉석 채점).
- **창(pywebview) = 3탭 + 온보딩**:
  - **① 추천(메인)**: 상단 직접검색 바 · 헤더(검색시각·평균적합·건수) · **정렬**(적합도순 / 통근 가까운순 / 좋소 안전만) · 카드 목록.
    - **카드 = 4축이 주인공**(확정): 헤더(**출처 배지**[사람인/잡코리아/원티드, 녹색 필 `.co .site`] + 회사·직무 + 적합도 큰 숫자) → **4축 타일 4개**(통근·좋소[등급색]·평판·매칭)를 카드 중앙에 크게 = 가장 먼저 보임 → **보조줄**(직원수+추세·이직률·추정연봉·리뷰수·매칭 적중키워드)로 좋소 디테일은 작게. 출처는 `Job.source`→reco dict→카드/카톡 공통.
    - **"왜 추천" 문구 없음**(군더더기 제거). 액션 [관심없음·지원함·공고↗]. 이력은 이 탭 필터로. (좋소 나쁜 회사 자동 하위·제외)
  - **② 설정**: 검색조건·필터는 **사람인 실제 필터 taxonomy와 동일하게**(추측 금지) — 직무(`cat_mcls`), 지역(`loc`/`subway`), 경력(신입/경력/경력무관 `check_career`), 학력(학력무관·고졸·초대졸·대졸·석사·박사 `check_edu`), 고용형태(정규직·계약직·인턴직·파견직·프리랜서·병역특례·아르바이트·위촉직 `job_type[]`), 기업형태(대기업·중견·중소·스타트업·외국계·공사/공기업·연구소 `company_type[]`), 연봉하한(`sal_min`), 제외키워드(`exc_keyword`). + 이력서(PDF·추출키워드·임베딩) / 집·통근(주소·핀·최대통근) / 4신호 가중치 슬라이더(합100) / 추천기준(**임계점 기본70**·수집개수·표시개수·URL 스킵) / 알림(데스크톱·카톡·주기·조용시간). (원티드·잡코리아는 부분집합 → 매핑)
  - **③ 상태("또 있나?"의 답)**: 실행중 여부·다음 검색 시각 · 이번주 수집/추천/지원 수 · 의존성 헬스(크롤러 3사·임베딩 모델 로드·국민연금 적재월) · 일시정지 · 지금 검색 · 데이터 백업.
  - **온보딩(설치 직후 1회)**: ②설정 항목을 5스텝 위저드로 — 검색조건·필터 → 이력서 → 집·통근 → 가중치 → 알림.

## 10. 디자인 시스템
- **색**: brand green `#047857`(700)·`#10b981`(500), 잉크 `#0f172a`, 라인 `#e2e8f0`. 등급: 안전=green·주의=`#f59e0b`·위험=`#ef4444`.
- **폰트** Pretendard(CDN) · **아이콘** Lucide(CDN, `data-lucide` + `createIcons()`). 최적화 시 쓰는 아이콘만 SVG 스프라이트(`<svg><use href="#i-leaf"/>`)로 — 런타임 JS 불필요(ISC, 자유 번들).
- 스파크라인=inline SVG 기본(인원추세 카드). 인터랙티브 필요 시 uPlot(~40KB) 선택.
- **프론트 자산 = CDN** (온라인 도구라 OK) — Tailwind·Alpine·HTMX·SweetAlert2·Lucide·Pretendard 전부 CDN. 별도 빌드/번들 불필요.
- 컴포넌트: rounded-2xl 카드 · 배지 · 토글 · 슬라이더 · 탭. 톤: 심플·녹색 메인·정직(추정/한계 표기).

## 11. 데이터 모델 (`app/models.py`)
- 기존: Company · Job · UserSetting · SentimentSnippet · DomainDiscoveryAttempt · SearchHistory.
- 신규/변경: `user_profile`(검색조건·필터·이력서+임베딩·집·가중치·**임계점·개수**·알림) · `nps_workplace` · `recommendation`(공고별 4신호·종합·근거·상태[신규/알림됨/지원함/관심없음]) · `skill_synonym` · `notification_log`. Company 확장(사업자번호·좋소캐시), resume 임베딩 캐시.
- SQLite WAL + busy_timeout. 마이그레이션 **ADD-only**(실패해도 부팅 안 막기).

## 12. 패키징 / 배포
- PyInstaller(단일) + Inno Setup(`WHOAREYOU_setup.exe`). pywebview+pystray+FastAPI+APScheduler 한 프로세스.
- 데이터 → `%APPDATA%\WHOAREYOU\`(설치폴더와 분리 → 업데이트 안전). 자동시작 등록.
- bge-m3 ONNX 모델 = 첫 실행 1회 다운로드(온라인, 캐시). 프론트는 CDN(번들 X). **Chromium 없음.**
- **pywebview = Windows 내장 Edge WebView2 사용**(Chromium 번들 불필요, 주소창·탭 없음 = 브라우저 아님). 기존 web UI 그대로 재사용. 더 가볍게는 FastAPI 빼고 JS↔Py 브리지(`window.pywebview.api`) — 선택.
- **앱 아이콘**: `web/assets/icon.ico`(녹색 잎). ⚠ pywebview는 **Windows에서 `.ico` 필요**(`.png`는 .NET `System.Drawing.Icon`이 거부 → 창 죽음). `webview.start(icon=...)`에 .ico 전달. ⚠ **작업표시줄 아이콘**은 python.exe로 묶여 안 바뀌므로 `ctypes ... SetCurrentProcessExplicitAppUserModelID("WHOAREYOU.desktop")`를 창 생성 전에 호출(분리). 배포 .exe는 PyInstaller `--icon`로 확정.
- **알림(Notifier)**: `winotify`로 Windows 토스트 → **토스트 클릭 / 트레이 더블클릭 → 메인 창(추천 탭) 열림**. (창은 평소 닫혀 있고 알림으로 진입 — §9) 데모: `web/notify_demo.py`.
- **트레이 상주(pystray)**: 트레이 아이콘 **상시 표시**(백그라운드 데몬). 더블클릭/'열기' → 창. **창 [X] = 종료 아님 → 트레이로 숨김**(상주 유지), 실제 종료는 트레이 메뉴 '종료'만. 트레이 이미지는 PNG, 창/작업표시줄은 .ico.
- **화면 프로토타입 실행**: `.venv\Scripts\python.exe web\run.py` → 트레이 상주 + 네이티브 창(기능 없이 화면만). 실앱 `serve.py`가 여기에 FastAPI+APScheduler(수집·채점)+Notifier 얹음.
- **포트 8005**(8000/8001=ComfyUI 충돌). serve.py가 `APP_PORT` env 전파 → main `_app_url()`이 실제 포트 로그.

## 13. 실행 / 개발 환경
- 설치: `uv sync`. (Playwright 안 씀 → chromium 설치 불필요)
- **신규 의존성**: `curl_cffi`(잡플래닛), `fastembed`(임베딩), `surya-ocr`(이미지 공고), `pdfplumber`(이력서 PDF). — `archive/.venv`엔 검증용으로 이미 일부 설치됨(curl_cffi·pdfplumber).
- **저장된 키**(`settings`/`usersetting` 테이블, archive/data/whoareyou.db): `kakao_rest_key`·`odsay_key`·`home_lat/lng`·**`nps_service_key`**(국민연금, 활성화됨). 2.0도 재사용.
- 실행(개발): `uv run python serve.py` → `http://127.0.0.1:8005`. 배포는 트레이 앱.
- **한글/UTF-8**: Windows cp949 주의 — 파일 입출력은 `encoding="utf-8"` 명시. 콘솔 mojibake는 표시만(데이터는 정상), 검증은 UTF-8 파일로.
- 백업: 설정 "데이터 백업"(DB+uploads zip, 구현됨).
- LibreOffice 없음 → 문서 PDF는 Word/PowerPoint COM (이 PC 한정 참고).

## 14. 코딩 규칙 (Do)
- **매직넘버 금지 → config** — 임계점(70)·가중치(25/25/20/30)·정규화 경계(통근 30·90분)·산식 계수(국민연금 0.09)·타임아웃·재시도·rate-limit·소표본 댐핑 상수 등 **모든 숫자는 `app/config.py`(또는 설정 테이블)에 명명 상수/`dataclass`로** 모은다. 코드에 직접 박지 말 것. UI 디자인 토큰도 CSS 변수(`:root`)로.
- **클래스로 구조화** — 도메인별 클래스로 캡슐화: `Scorer` · `ResumeMatcher` · `NpsRepository`/`NpsScorer` · `JobplanetClient` · `CrawlerAdapter`(사이트별 서브클래스) · `Notifier` · `Scheduler`. 전역 함수 난립 금지, 의존성 주입(키·세션은 생성자로).
- **결정론 우선** — 점수/판정은 규칙·임베딩. LLM 추가 금지.
- **정직성** — 근거 없으면 판단보류. 실패·0건은 사용자에게 신호(침묵 금지). 추정치 한계 표기.
- **비용 통제** — 비싼 작업(임베딩·크롤·enrich) 전에 **URL dedup 선스킵**. enrich는 상위 후보만.
- async/httpx + 타임아웃·재시도 + graceful degrade(키 없으면 그 단계만 skip).
- **테스트** — 핵심 파서·산식·매칭에 pytest(가짜 HTML·소형 정답셋). CI에 테스트 게이트.
- 기존 모듈 재사용 우선. 변경은 최소·정밀(사용자 미커밋 작업 존중).

## 15. 하지 말 것 (Don't)
- ❌ Ollama / 채팅 LLM 추가 (결정론·패키지 경량성 깨짐)
- ❌ "오프라인 동작" 가정 (온라인 도구 — 인터넷 전제, 프론트는 CDN)
- ❌ 회사 공개 랭킹·라벨링 (명예훼손) → 로컬 사적 조회만
- ❌ 화면 캡처→비전 크롤 (텍스트 httpx 크롤만)
- ❌ Playwright/Chromium 도입 (httpx로 충분 — 3사 실측 확인)
- ❌ 침묵형 실패 — 크롤·매칭·별점이 0건/실패인데 '정상'처럼 → 반드시 신호화
- ❌ 포트 8000 (ComfyUI 충돌) → 8005
- ❌ 원티드 공고를 API로만 수집 (채용전형 누락) → 웹 `__NEXT_DATA__`

## 16. 로드맵 (현재 위치)
> **✅ 모듈 검증 완료(2026-07-01, `archive/_pipeline_test/`)**: §8a 신규 크롤로 3사 15건 수집 → 4축 채점 → 랭킹 end-to-end 동작. 이력서매칭·통근(ODsay)·평판(잡플래닛 curl_cffi)·좋소(국민연금) 전부 실데이터로 작동. 좋소는 jotso 방법론 역설계+추정연봉 일치검증+전국 bulk로 매칭 해결까지 확인.
> **▶ 진행 중**: 좋소 jotso 패리티 마무리 — 국민연금 **12개월(1년)치 적재 → 12개월 평균 회전율·1년 인원증감 → 스케일 보정** → jotso와 재대조. (단일월+댐핑 상태에선 소표본 false positive 잔존)
> **▶ UI 먼저(기능 빼고 화면부터)**: 루트 `web/` 정적 프로토타입 — **추천=목업 데이터 · 설정/상태=실제 컨트롤**(Tailwind/Alpine/Lucide/Pretendard CDN, 탭 전환). 디자인 확정 후 backend 배선(→ `templates/`로 승격). `___test/`는 1차 design probe라 폐기 가능. **추천 카드는 §9대로 4축 4타일이 메인.**

> **✅ 2.0 실구현 착수(2026-07-01) — 루트 `app/` + `web/` + `run.py`**:
> - **아키텍처 = pywebview JS↔Py 브리지**(FastAPI 대신 — 서버리스·포트없음·패키징 간단). `app/api.py`가 `collect/search/recommendations/set_status/open_url`을 JS에 노출.
> - **`app/` 자립**(archive 무의존): 검증된 모듈 승격 + geo/ats/api_status 로컬화. `pipeline.py`(수집→dedup선스킵→2단계채점→저장) · `store.py`(SQLite 추천/상태).
> - **수집 = 하루 1회(기본, APScheduler) + 수동**: 트레이 '지금 검색' · UI '지금 검색' · **검색어/회사명 직접검색**(`api.search`).
> - **표준 UI**: `web/index.html`+`app.js` — 가짜 윈도우 컨트롤 제거(pywebview 네이티브 프레임만 = 더블 타이틀바 해결), 4축 타일 메인, 실데이터 바인딩. **목업 HTML 삭제.**
> - **트레이 상주**(잎 아이콘·[X]=숨김) + **winotify 알림**(추천 나오면 토스트). **좋소=jotso 직접연결**(캐시·throttle).
> - **패키징**: `whoareyou.spec`(PyInstaller) + `setup.iss`(Inno Setup, 자동시작).
> - **설정 UI(2026-07-01 개편)**: `user_settings.json`(%APPDATA%) 영속 + `api.get/save_settings`. **직무칩 제거 → 검색어로 대체**(사람인 필터 문구도 삭제). **학력=멀티칩**(학력무관·고졸·초대졸·대졸·석사·박사 동시선택). 탭 3개 = **추천·설정·키 관리**.
> - **키 관리 = 별도 탭**(이전 '상태' 탭 폐기 — 헬스체크/동작상태는 사용자가 불필요하다고 판단): kakao/odsay/nps 입력 + 발급 링크. 원문 미노출·`keys_set`만 표시·빈 입력은 기존값 보존, `api._merged`가 개발DB 위에 사용자키 덮어씀·집주소 저장 시 카카오로 지오코딩(`api._geocode_home`). 저장은 키 탭 자체 버튼(`saveKeys`).
> - **아이콘 = 나뭇잎**(사용자가 잎을 선호 — 이전 자작 잎이 '알약'처럼 보인 게 문제였을 뿐). 헤더 Tabler `ti-leaf` 그대로를 PIL 베지어로 래스터화(흰 잎+녹색 라운드사각) → `web/assets/icon.ico`(다중 프레임)·`icon.png`. 헤더 로고와 동일 실루엣.
> - **키/이관/카톡/이력서/필터 (2026-07-01 최신)**:
>   - **키 = user_settings.json으로 완전 이관**(archive DB 폐기). `api._merged`가 사용자 키 우선. 국민연금 키 UI 삭제(좋소=jotso라 미사용). **카맵·ODsay·집좌표 이관 완료**.
>   - **카카오톡 '나에게 보내기'**(`kakao_notifier.py` `KakaoNotifier`): memo API + **로컬 루프백 OAuth**(포트 `KAKAO_OAUTH_PORT=8599`). ⚠ **map REST 키만으론 불가** — 콘솔에서 1회 **카카오 로그인 ON · Redirect URI `http://localhost:8599/oauth` · 동의항목 talk_message**. UI [연결]/[테스트]. 알림 시 `noti_kakao` + connected면 토스트와 함께 카톡 발송. **메시지=추천 전부(임계점↑) 한 통씩**: `[출처] 회사 · 직무` / 4축 한 줄 / **`🔗 공고 URL`(본문 노출 — 카톡이 auto-linkify)** + `link=`로 카드 탭도 유지(memo `text[:400]` 캡 준수).
>   - **이력서 = 사용자 선택**(`api.pick_resume` → pywebview 파일다이얼로그 → `resume_path` 저장 → `matcher.load`). `ResumeMatcher`는 파일 없음/스캔본이면 빈 텍스트→매칭축 skip(침묵 방지). config.RESUME_PDF는 개발 기본값.
>   - **필터 배선(2026-07-01 실측·확정)**: `pipeline.run(filters=)` + 각 어댑터. **known-only**(필드 아는 잡만 판정, 모르면 통과 → 오탈락 방지):
>     - **경력**: 사람인=**서버 `exp_cd`**(1=신입·2=경력 실측확정) / 원티드=`__NEXT_DATA__.career`(is_newbie) 클라 / 잡코리아=JSON-LD `experienceRequirements` 클라. ("신입·경력"·"경력무관"→무관=유지)
>     - **지역**: **클라 공통**(`_region_tokens` — job.address에 지역토큰 포함). "서울 전체"→"서울".
>     - **고용형태**: 잡코리아 `employmentType`(FULL_TIME→정규직 등) 클라. 사람인·원티드는 필드 없음→통과.
>     - **제외키워드**: 클라 3사 공통(제목+회사+JD).
>     - **수동 '직접 검색'은 필터 미적용**(`search(use_filters=False)` — 회사명이 안 걸리게). 자동/지금검색만 필터.
>     - ⚠ **아직 미배선(정직)**: **학력**(이상 semantics 오탈락 위험) · **기업형태**(신뢰 신호 없음) · **연봉하한**(사람인 `sal_min` 코드매핑 불확실·타사 sparse). 실측검증: 15건→7건(부산=지역·경력잡=경력·인턴=고용형태 제거).
>   - **학력 멀티선택**(리스트) · **상태 탭 폐기 → 키 관리 탭**.
> - **매칭 고도화 결정(2026-07-01 실측)**: 사용자가 임베딩+OCR 요청 → **둘 다 경량 배포앱엔 부적합** 판명, 대안으로 해결:
>   - **의미 매칭 = skills.py 동의어 사전 배선**(ats `_hit`에 `SkillNormalizer.variants` substring). ML=머신러닝·백엔드=서버개발·k8s=쿠버네티스 등 결정론적으로 잡음(실측 0→83점). ⚠ **임베딩(fastembed)은 폐기**: bge-m3는 fastembed 미지원, 경량 대체 MiniLM은 **한국어 스킬 변별 실패**(파이썬vs식자재 0.77 > 머신러닝vsML 0.47). `config.USE_EMBEDDING=False`로 코드만 dormant(진짜 쓰려면 bge-m3+torch~3GB). `embeddings.py`는 vec/cos 캐싱 완비.
>   - **잡코리아 전문 JD(2026-07-01, Playwright로 발견)**: 전문 JD는 JS렌더라 GI_Read엔 90자뿐 → **상세요강 iframe `/Recruit/GI_Read_Comt_Ifrm?Gno=`의 CorpEditor 이미지**를 httpx로 받아 OCR(90자→1428자). iframe·이미지 다 httpx라 **런타임 Playwright 불필요**(chromium 미번들=경량). 텍스트 JS렌더 공고는 정적결합 227자.
>   - **매칭 = 어휘+스킬사전+리랭커(2026-07-01)**: ⚠**임베딩·리랭커 모두 전체이력서↔JD는 변별실패**→ **리랭커는 '이력서 스킬요약↔JD' 순서**라야 됨(bge-reranker-v2-m3: AI0.15>디자이너0.08>주방0). 최종=어휘/스킬사전 0.5+리랭커 0.5. 전직군 일반화(AI36>백엔드27>디자이너0>주방0). `reranker.py`. **바이인코더 임베딩 폐기.** heavy optional(torch+2.3GB).
>     - **⚠ 리랭커 passage = 원문+추출키워드 max(2026-07-01 실측 수정)**: 원문 JD 전체를 리랭커에 넣으면 회사소개·복지·지원방법 노이즈로 신호 희석(실측: 풀JD 2724자 AI Agent공고 리랭커 4점, 내 리치JD는 100점). 해결: `matcher._kw_join(extract_jd_keywords(jd))`로 뽑은 키워드 passage와 원문을 **batch 1콜로 함께 넣고 max** — 노이즈 희석·키워드추출 손실 양쪽 방어. 실측 개선(같은 실공고): 울산AI Agent 23→60·AI에이전트기획자 21→44·AI Agent개발자 10→30, 마케팅 12→14(최하위 유지). **낮은 매칭 원인은 리랭커 OFF가 아니라(런타임서 정상 로드) 원문 노이즈였음.**
>   - **이미지 공고 OCR = easyocr(한국어) 배선**(`ocr.py`, 지연로드+URL캐시+graceful). rapidocr는 한글 깨짐·surya는 llama.cpp 바이너리 필요 → **easyocr 채택**(실측 한국어 정상: "도담웨이브 디자이너 채용…"). `config.USE_OCR`. `Job.img_url`→pipeline이 필터통과 이미지공고만 OCR. **⚠ torch 무거움 → 프리즈 exe엔 미포함**(excludes에 easyocr/torch/surya): 경량 exe는 자동으로 제목폴백, easyocr 설치된 환경에선 실제 OCR. 전배포하려면 heavy 번들(exe~2.5GB) 별도.
>   - **수집 = "필터 통과분 정확히 per_site개"(2026-07-01)**: 어댑터가 최신공고를 훑으며 필터통과+미처리(`skip`=store.is_handled)+미중복(URL)만 카운트, per_site 채우면 정지. **스캔 깊이 = `max(SCAN_CAP=50, per_site×SCAN_PER_TARGET=10)`(2026-07-01 수정)** — 통과율 낮은 필터에서 앞 50건만 보면 뒤쪽 조건맞는 공고를 놓침(실측: 사람인 ai agent 깊이50→7·100→10, 잡코리아 3→9). 목표에 비례해 깊게 훑음(잡코리아 링크수집 `range(1,12)`·원티드 `limit`도 cap 따라). ⚠ 깊을수록 검색 느림. 남는 미달은 **진짜 공급 부족**(예: 원티드 신입 서울 agent 3건뿐). URL중복0. **중복제거는 링크단위**(어댑터 내 seen + collect 전체 URL dedup). 서버측 선필터(사람인 경력+고용·원티드 경력+지역)로 대부분 통과 → 금방 채움.
> - **배포 = 사용자 uv·Python 불필요**(PyInstaller가 런타임·의존성 전부 번들). 유일 시스템 요건 = **Edge WebView2 런타임**(Win11 내장, 없으면 `setup.iss` [Code]가 MS 부트스트래퍼 자동 설치). 첫 실행 시 bge-m3 다운로드 **없음**(현재 매칭=kiwi+rapidfuzz 어휘, 임베딩은 후속).
> - **⚠ 카카오 Local(지오코딩) API `KA` 헤더 필수 (2026-07-01 해결)**: `Authorization: KakaoAK` 만 보내면 **401 "KA Header is required"** → 통근축이 통째로 죽음(집·회사주소 지오코딩 실패 → 전부 "집주소 미설정/좌표없음" → 점수 저평가로 아무것도 임계점 못 넘음). **해결**: `geo_kakao.KA_HEADER = "sdk/1.0.0 os/web lang/ko-KR origin/{URL-encode된 origin}"` 헤더 추가. **origin은 도메인 등록 무관**(localhost·임의값 다 통과) — `os/web` + encode된 `origin/` 필드 존재가 핵심. 적용 후 통근 정상(집→강남 25분/지하철), 점수 79·72로 상승해 70 돌파.
> - **exe 데이터 = `%APPDATA%\WHOAREYOU`** (dev=app/). 키/집주소는 여기 별도 → 사용자가 키 관리 탭에서 입력. **집주소만 있고 좌표 없으면 `Api.__init__`가 시작 시 자동 지오코딩(self-heal)** → home_lat/lng 채움.
> - **추천 표시 = 임계점 반영(2026-07-01)**: `recommendations()`·`collect()`가 `threshold` 반환 → UI가 **임계점 이상만 표시**, 미만은 "N건 숨김"(0건이면 배너로 최고점 안내 — 침묵 방지 §1). **수집 퍼널**(`pipeline.last_stats`: crawled→filtered→shown) 서브메타 노출("각 5개=15개 다 돌렸나" 투명화). **검색 초기화** = `reset()`(store.clear) + 추천탭 [초기화] 버튼.
> - **결측 신호 = "표본 부족" 표시**: `Scorer.composite`는 이미 결측 축을 den에서 빼고 재정규화(§5) → 점수는 자동 조절. UI 카드는 평판/좋소/매칭/통근 축이 없으면 **"표본 부족"**(회색 `.axis.na`)으로 표시(0점·에러 아님). **집주소 확인** = `check_home(address)`(지오코딩 검사) + 설정 집주소 옆 [확인] 버튼(→ 해석된 주소·좌표).
> - **설정 반영(2026-07-01)**: 설정은 정상 전달됨(버그 아님)이었으나 ① 옛 추천이 store에 남고 ② 저장≠재검색이라 "안 바뀜"으로 보임 → **[설정 저장]=저장+자동 재검색**(추천탭 이동), **`store.prune_new`**로 매 검색마다 이전 잔재 삭제(최신만), 서브메타에 **적용 조건**(`「키워드」 경력·지역`) 표시.
> - **필터 = 3사 상세 구조화 선택자 기반(2026-07-01 최종)**: 정규식 본문파싱 폐기 → **각 사이트 상세페이지의 경력·학력·고용형태 필드를 선택자로 추출**(§8a). Job에 `career`·`edu_req`·`emp_type`(정규화 한글)·`comp_type` 저장. `_passes`가 이걸로 필터(학력은 `_EDU_ORDER` 레벨비교 — 요구>사용자최대면 drop). 구조화 없으면(원티드 학력 등) `_career_req`/`_emp_req`/`_edu_req` 정규식 **폴백만**. 실측(신입·서울/경기·정규직·고졸): 제타모빌리티(대졸)·제논(인턴)·에코브레인(석사) 정상 제거, 15→1(고졸 선택이라 대졸요구 AI잡 대부분 탈락 — 정상). `_norm_edu`/`_norm_emp`/`_norm_emp_wanted`/`_career_from_field`.
> - **서버측 필터 = "가져올 때부터 필터 걸어서"(2026-07-01 실측 확정)**: 통과율 13%→**53%**(사람인 75%·원티드 67% pre-match).
>   - **원티드(API)**: `years=0`=신입 · `locations`=지역(`LOC` 코드맵 seoul.all·gyeonggi.all…). 실측 전부 신입+서울.
>   - **사람인**: `exp_cd`(경력) + **`job_type`(고용형태 — ⚠ 대괄호 없이! `job_type=1`=정규직·`=4`=인턴. `job_type[]`은 값무시, 복수 `job_type`도 깨짐 → 단일 선택일 때만 서버측**, 복수는 클라측). ⚠ **학력 `edu_max`는 동작 불규칙**(9→고졸·10→초대졸 뒤죽박죽 — `edu_min`은 8=대졸·9=석사·10=박사로 깨끗하나 방향 안 맞음) → **학력은 클라측**. 지역 `loc`도 JS라 클라측.
>   - **잡코리아**: 서버측 param 불안정 → 전부 클라측.
>   - **수집 모델 = "필터 통과분 n개"(2026-07-01 재설계)**: `PER_SITE`(수집 개수)는 **필터 통과한 공고의 목표 개수**. 어댑터가 최신공고를 훑으며 `jobfilter.passes`로 판정해 **통과분만 n개 채우면 정지**(`SCAN_CAP=30` 상한). 실측: per_site=5 → **사이트당 정확히 5개·총 15개 전부 필터통과**. (이전 "후보 모아서 나중에 거르기" → 개수 들쭉날쭉 문제 해결). 필터 로직은 `app/jobfilter.py`(어댑터·파이프라인 공용). 통과분만 상세 fetch하는 게 아니라 상세 봐야 판정 가능하나 서버측 선필터(사람인 경력+고용·원티드 경력+지역)로 스캔 대부분 통과 → 금방 채움. 통근 `COMMUTE_CONCURRENCY=4`.
> - **수집·알림 주기 자유 설정(2026-07-01)**: `Scheduler`=APScheduler **IntervalTrigger**(하루N회 cron→인터벌). `user_settings.schedule_interval`(분: 30/60/180/360/720/1440), 설정 저장 시 `scheduler.reschedule` 즉시 반영(`run.py`가 `_api.scheduler` 주입). **조용시간**(`quiet_hours` "23:00~08:00")엔 `_in_quiet()`로 알림만 스킵(수집은 함). 상태 탭 표시=`_interval_label`("3시간마다").

- **P1 데몬화**: APScheduler + all-httpx 수집 + URL dedup 선스킵 + 결과 DB + 추천 화면(검색 UI 제거).
- **P2 매칭+알림**: 이력서 하이브리드(fastembed) + 4신호 Scorer + 가중치/임계점 + Notifier.
- **P3 좋소+평판**: 국민연금 적재·매칭·위험도·인원수 카드 + 잡플래닛 별점.
- **P4 네이티브 패키징**: pywebview+pystray + PyInstaller+Inno Setup + 자동시작. (프론트는 CDN, Chromium 없음)
- 온보딩 위저드는 P1~P2 병행(5스텝, §9). MVP=P1~P2(통근·이력서 매칭 좋은 신규 공고를 알아서 알림).
- **리스크/대응**: 봇차단→주기 6~12h·휴먼라이크·v1 파서깨짐 신호 / 임베딩 용량→첫실행 1회 캐시 / 잡플래닛 난이도→curl_cffi(§7b), 안 되면 후순위 / 좋소 법적→공개랭킹 금지·로컬 사적조회만.
