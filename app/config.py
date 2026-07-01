# -*- coding: utf-8 -*-
"""WHOAREYOU 2.0 — 설정/상수 (매직넘버 금지: 모든 수치는 여기). app/은 자립(archive 무의존)."""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent        # app/
PROJECT = ROOT.parent                          # WHOAREYOU
ATS_PATH = ROOT / "ats.py"                     # 자립(app 로컬)


def _data_dir() -> Path:
    """쓰기 가능한 데이터 폴더 — frozen(exe)=%APPDATA%\\WHOAREYOU, 개발=app/."""
    d = (Path(os.environ.get("APPDATA", str(PROJECT))) / "WHOAREYOU"
         if getattr(sys, "frozen", False) else ROOT)
    d.mkdir(parents=True, exist_ok=True)
    return d


APP_DATA = _data_dir()
# 키(kakao/odsay/home)는 user_settings.json으로 이관 완료(v1 archive 폐기). settings()는 남은 폴백만.
DB_PATH = APP_DATA / "settings.db"
RESUME_PDF = ""    # 개발 기본값(비움). 실제 이력서는 설정에서 선택 → user_settings.resume_path

# 의미 매칭(§6): 경량 임베딩(MiniLM)은 한국어 스킬 변별 실패(파이썬vs식자재>머신러닝vsML) → OFF.
# 동의어는 skills.py 결정론 사전(ML=머신러닝 등)이 담당(가볍고 확실). 임베딩 쓰려면 bge-m3(torch~3GB) 필요.
# 의미 매칭: 리랭커(cross-encoder, 전 직군 일반화) — 실측 '이력서 스킬요약↔JD'라야 변별됨.
# 무거움(torch+2.3GB) → sentence-transformers 있으면 켜지고 없으면 어휘+스킬사전만(경량 폴백).
USE_RERANKER = True
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_SCALE = 500       # 로짓→점수 보정(좋은 매칭 ~0.15 → 약 75)
MATCH_RERANK_W = 0.5     # 최종 = 어휘/스킬 0.5 + 리랭커(의미) 0.5
# 이미지 공고 OCR(easyocr·한국어). easyocr(torch) 있으면 켜짐, 없으면 자동 폴백(제목매칭). 느림(CPU)
USE_OCR = True

KEYWORD = "AI 엔지니어"
PER_SITE = 5       # 사이트당 "필터 통과분" 목표 개수(수집 개수 설정)
SCAN_CAP = 50      # 스캔 깊이 하한(필터 통과 n개 못 채워도 최소 이만큼은 훑음)
SCAN_PER_TARGET = 10   # 스캔 깊이 = max(SCAN_CAP, per_site×이값). 통과율 낮은 필터에서 뒤쪽 공고까지 파서 목표 채움(실측: 사람인 깊이50→4·100→12). ⚠ 크게 잡으면 검색 느려짐
THRESHOLD = 70
DAILY_TIME = "09:00"   # 수집 자동 실행 = 하루 1회(기본). + 수동 트리거/검색
KAKAO_OAUTH_PORT = 8599  # 카톡 '나에게 보내기' OAuth 로컬 루프백(콘솔 Redirect URI에 등록)

# 4신호 가중치 (합 100)
WEIGHTS = {"match": 30, "commute": 25, "jotso": 25, "reputation": 20}

# 통근 정규화(분): 30분 이하=1.0 → 90분 이상=0.0
COMMUTE_BEST_MIN = 30
COMMUTE_WORST_MIN = 90

# enrich(좋소·평판) 비용 통제 — 90개 폭격 금지 + jotso 보수적 호출
ENRICH_TOP_N = PER_SITE * 3   # 좋소·평판 조회 상한 = 기본 3사×per_site 전량 → 모든 추천에 좋소 도달. per_site 크게 잡으면 상한 밖 꼬리는 좋소 '표본 부족'(요율 통제)
ENRICH_CONCURRENCY = 1     # 동시 1 = 병렬 안 함(jotso에 한 번에 1요청만)
COMMUTE_CONCURRENCY = 4    # 통근(카카오/ODsay)은 jotso와 별개 — 후보 많을 때 병렬로 빠르게
ENRICH_DELAY_S = 1.5       # 회사 간 기본 지연
ENRICH_JITTER_S = 0.6      # + 랜덤 지터(0~0.6s, 휴먼라이크·패턴 회피)
JOTSO_INTRA_DELAY_S = 0.5  # jotso 검색 → 회사페이지 사이 지연
JOTSO_CACHE_DAYS = 30      # 좋소는 월 갱신 → 30일 캐시(재요청 최소화)
CACHE_MISS_DAYS = 7        # "미등록"(jotso에 없음)도 캐시 → 재조회 방지(짧게)
CACHE_DB = APP_DATA / "cache.db"

# HTTP
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
TIMEOUT = 25


def settings() -> dict:
    """저장된 키(kakao/odsay/home_lat 등). DB 없으면(exe 첫 실행) 빈 dict → 앱은 키 없이도 뜸."""
    if not Path(DB_PATH).exists():
        return {}
    con = sqlite3.connect(str(DB_PATH))
    try:
        return dict(con.execute("select key, value from usersetting").fetchall())
    except Exception:
        return {}
    finally:
        con.close()


class Cache:
    """회사별 좋소·평판 결과 캐시(SQLite, per-entry 만료). 재실행 시 jotso/잡플래닛 재요청 방지."""
    def __init__(self):
        import json
        import threading
        self._json = json
        self._lock = threading.Lock()
        # enrich를 스레드(to_thread)에서 호출하므로 check_same_thread=False + 락
        self.con = sqlite3.connect(str(CACHE_DB), check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT, exp REAL)")
        self.con.commit()

    def get(self, key: str):
        import time
        with self._lock:
            row = self.con.execute("select v, exp from kv where k=?", (key,)).fetchone()
        if not row or time.time() > row[1]:
            return None
        return self._json.loads(row[0])

    def set(self, key: str, value, ttl_days: int = JOTSO_CACHE_DAYS) -> None:
        import time
        exp = time.time() + ttl_days * 86400
        with self._lock:
            self.con.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)",
                             (key, self._json.dumps(value, ensure_ascii=False), exp))
            self.con.commit()
