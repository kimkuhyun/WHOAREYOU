# -*- coding: utf-8 -*-
"""pywebview JS↔Py 브리지 — 프론트가 호출하는 API (서버리스, 패키징 간단).

JS: window.pywebview.api.collect() / search(q) / recommendations() / set_status(url,st) / status()
"""
import asyncio
import re
import threading
import time
import webbrowser

import config
from commute import CommuteScorer
from kakao_notifier import KakaoNotifier
from notifier import Notifier
from pipeline import Pipeline
from store import Store
from user_settings import KEY_FIELDS, UserSettings


class Api:
    def __init__(self):
        self.cache = config.Cache()
        self.store = Store()
        self.user = UserSettings()
        self.settings = self._merged()
        self.pipeline = Pipeline(self.settings, self.cache, self.store)
        if self.user.data.get("resume_path"):            # 사용자 지정 이력서
            self.pipeline.matcher.load(self.user.data["resume_path"])
        self.notifier = Notifier()
        self.kakao = self._new_kakao()
        self.last_run = None
        self._last_stats = None         # 마지막 _collect 퍼널(재시작 후 recommendations에 재노출)
        self._last_query = None         # 마지막 _collect 검색줄
        self.scheduler = None           # run.py가 주입(주기 재설정용)
        self._lock = threading.Lock()   # 동시 수집 방지(수동+스케줄 겹침)
        self._geocode_home()            # 집주소만 있고 좌표 없으면 시작 시 자동 지오코딩(통근축 살림)

    def _new_kakao(self) -> KakaoNotifier:
        return KakaoNotifier(self.settings.get("kakao_rest_key", ""),
                             self.user.data.get("kakao_refresh_token", ""),
                             self.settings.get("kakao_client_secret", ""))

    # ── 설정 병합: 개발DB(config.settings) 위에 사용자 키/집주소 덮어쓰기 ──
    def _merged(self) -> dict:
        m = dict(config.settings())
        u = self.user.data
        for k in KEY_FIELDS:
            if u.get(k):
                m[k] = u[k]                       # 사용자 키 우선(비면 개발DB)
        if u.get("home_lat") and u.get("home_lng"):
            m["home_lat"], m["home_lng"] = u["home_lat"], u["home_lng"]
        return m

    def _apply(self):
        """키/집주소 바뀌면 병합값 재계산 + 통근 스코어러·카톡 재생성."""
        self.settings = self._merged()
        self.pipeline.settings = self.settings
        self.pipeline.commute = CommuteScorer(self.settings)
        self.kakao = self._new_kakao()

    def _geocode_home(self):
        """집주소 → 좌표 캐시(카카오 키 있을 때만). 실패해도 통근만 skip(§14)."""
        u = self.user.data
        if (u.get("home_lat") and u.get("home_lng")) or not u.get("home_address"):
            return
        key = self.settings.get("kakao_rest_key")
        if not key:
            return
        try:
            from geo_kakao import geocode_address
            g = asyncio.run(geocode_address(u["home_address"], key))
            if g:
                self.user.save({"home_lat": str(g.lat), "home_lng": str(g.lng)})
                self._apply()
        except Exception:
            pass

    def _collect(self, keyword=None, use_filters=True):
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "count": 0, "recos": self.store.list()}
        try:
            u = self.user.data
            self.pipeline.scorer.w = u.get("weights", config.WEIGHTS)      # 가중치 반영
            filters = ({"career": u.get("career"), "regions": u.get("regions"),
                        "emp_types": u.get("emp_types"), "edu": u.get("edu"),
                        "comp_types": u.get("comp_types"),
                        "skills": u.get("skills")} if use_filters else None)
            kw = keyword or u.get("keyword") or config.KEYWORD
            keywords = [k.strip() for k in str(kw).split(",") if k.strip()] or [config.KEYWORD]
            agg = {"crawled": 0, "filtered": 0, "shown": 0}

            async def _multi():
                out = []
                for k in keywords:                            # 키워드 여러 개 각각 검색
                    rs = await self.pipeline.run(keyword=k, per_site=u.get("per_site"),
                                                 exclude=u.get("exclude"), filters=filters)
                    for key in ("crawled", "filtered"):
                        agg[key] += self.pipeline.last_stats.get(key, 0)
                    out += rs
                return out

            all_recos = asyncio.run(_multi())
            seen, comps, recos = set(), set(), []              # URL 중복 + 회사 중복 제거 + 점수순
            for r in sorted(all_recos, key=lambda x: (x["total"] is not None, x["total"] or 0), reverse=True):
                if r["url"] in seen:
                    continue
                # 한 회사가 같은 사이트에 여러 공고 → 최고점 1건만(인피닉 ×3 → 추천 N·카톡 N 부풀림 방지)
                nc = self._norm_company(r.get("company"))
                if nc and nc in comps:
                    continue
                seen.add(r["url"])
                if nc:
                    comps.add(nc)
                recos.append(r)
            agg["shown"] = len(recos)
            self.store.prune_new([r["url"] for r in recos])   # 옛 검색 잔재 제거 → 최신만
            self.last_run = time.strftime("%m/%d %H:%M")
            threshold = u.get("threshold", config.THRESHOLD)
            good = [r for r in recos if (r["total"] or 0) >= threshold]
            if good:
                avg = round(sum(r["total"] for r in good) / len(good))
                mins = [int(m.group(1)) for r in good
                        if (m := re.search(r"(\d+)분", r.get("commute_d") or ""))]
                nearest = min(mins) if mins else 0
                if not self._in_quiet():            # 조용시간엔 알림 안 보냄(수집은 함)
                    if u.get("noti_desktop", True):
                        self.notifier.notify(len(good), avg, nearest, good[0]["company"])
                    if u.get("noti_kakao") and self.kakao.connected:
                        # 추천 전부 — 공고마다 출처·축정보·링크 한 통씩(본문에 URL 노출 + 카드 탭 링크)
                        sent_comps = set()          # 같은 회사 두 통 안 보내게(recos는 회사유일이라 이중안전)
                        for i, r in enumerate(good[:15], 1):
                            nc = self._norm_company(r.get("company"))
                            if nc and nc in sent_comps:
                                continue
                            if nc:
                                sent_comps.add(nc)
                            title = (r.get("title") or "").strip()
                            src = r.get("source") or ""
                            msg = (f"🎯 오늘의 추천 {i}/{len(good)} · 적합 {r['total']}점\n"
                                   + (f"[{src}] " if src else "") + f"{r['company']}"
                                   + (f" · {title}" if title else "") + "\n"
                                   f"{self._reco_axes(r)}\n"
                                   f"🔗 {r['url']}")
                            self.kakao.send(msg, link=r["url"])
            query = {"keyword": kw,
                     "career": (u.get("career") if use_filters else None),
                     "regions": (u.get("regions") if use_filters else None)}
            self._last_stats, self._last_query = agg, query   # 재시작 후에도 퍼널·검색줄 살리게 캐시
            return {"ok": True, "count": len(recos), "recos": recos,
                    "threshold": threshold, "good": len(good), "query": query,
                    "stats": agg}
        finally:
            self._lock.release()

    def _in_quiet(self) -> bool:
        q = (self.user.data.get("quiet_hours") or "").strip()
        m = re.match(r"(\d{1,2}):(\d{2})\s*[~\-]\s*(\d{1,2}):(\d{2})", q)
        if not m:
            return False
        sh, sm, eh, em = (int(m.group(i)) for i in range(1, 5))
        # 범위 검증 — 25:00~99:00 같은 오타로 "종일 조용"돼서 알림이 조용히 묻히지 않게(§1)
        if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
            return False
        now = time.localtime()
        cur = now.tm_hour * 60 + now.tm_min
        s = sh * 60 + sm
        e = eh * 60 + em
        if s == e:
            return False
        return (s <= cur < e) if s < e else (cur >= s or cur < e)

    @staticmethod
    def _reco_axes(r) -> str:
        """추천 카드의 축 정보(통근·좋소·평판·매칭) 한 줄 — 카톡용. 표본부족 축은 생략."""
        parts = []
        cd = r.get("commute_d") or ""
        if r.get("commute") is not None and "분" in cd:
            parts.append(f"🏠통근 {cd.split('/')[0]}")
        if r.get("jotso_score") is not None:
            lbl = r.get("jotso_label") or ""
            parts.append(f"🛡좋소 {r['jotso_score']}" + (f"({lbl})" if lbl else ""))
        rd = r.get("rep_d") or ""
        if rd.startswith("★"):
            parts.append(f"⭐평판 {rd.split(' ')[0]}")
        if r.get("match") is not None:
            parts.append(f"🎯매칭 {r['match']}%")
        return " · ".join(parts)

    @staticmethod
    def _norm_company(name) -> str:
        """회사명 정규화 — 주식회사/(주)/㈜·공백·기호 제거 후 소문자(같은 회사 폴딩용)."""
        s = str(name or "")
        s = re.sub(r"주식회사|\(주\)|㈜|\(유\)|㈜|\(재\)|\(사\)", "", s)
        s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)   # 공백·기호 제거(한글·영숫자만 남김)
        return s.lower()

    @staticmethod
    def _interval_label(mins) -> str:
        try:
            mins = int(mins)
        except (TypeError, ValueError):
            return "하루 1회"
        if mins <= 0:                       # 0/음수는 24시간마다가 아니라 자동수집 꺼짐(0%1440==0 오판 방지)
            return "자동 수집 꺼짐"
        if mins % 1440 == 0:
            return f"{mins // 1440}일마다" if mins > 1440 else "하루 1회(24시간마다)"
        if mins % 60 == 0:
            return f"{mins // 60}시간마다"
        return f"{mins}분마다"

    def open_url(self, url):
        webbrowser.open(url)   # 공고는 기본 브라우저로(앱 창 안에서 안 염)
        return True

    # ── 브리지 메서드(JS에서 호출) ──
    def collect(self):
        """수동 수집 1회(기본 키워드)."""
        return self._collect()

    def search(self, query):
        """회사명·검색어로 수동 검색 — 저장 필터(지역·경력) 미적용(회사명이 안 걸리게)."""
        q = (query or "").strip()
        # 빈/공백/기호만 = 실검색어 없음 → 저장 키워드로 조용히 풀크롤 하지 말고 힌트(§1 침묵 금지)
        if not re.search(r"[0-9A-Za-z가-힣]", q):
            return {"ok": False, "empty": True, "error": "검색어를 입력하세요",
                    "recos": self.store.list(),
                    "threshold": self.user.data.get("threshold", config.THRESHOLD)}
        return self._collect(keyword=q, use_filters=False)

    def recommendations(self):
        out = {"recos": self.store.list(),
               "threshold": self.user.data.get("threshold", config.THRESHOLD)}
        if self._last_stats:            # 재시작 후에도 '수집 N→조건 N→추천 N' 퍼널·검색줄 유지
            out["stats"] = self._last_stats
        if self._last_query:
            out["query"] = self._last_query
        return out

    def reset(self):
        """검색 초기화 — 저장된 추천 전체 삭제(재테스트용)."""
        return {"ok": True, "cleared": self.store.clear()}

    def set_status(self, url, status):
        # not_interested=목록에서 제외 + 재수집·재알림 안 함. applied는 구버전 호환용으로 같은 제외 상태로 정규화.
        if status == "applied":
            status = "not_interested"
        if status not in ("not_interested", "new"):
            return False
        self.store.set_status(url, status)
        return True

    def status(self):
        c = self.store.counts()
        return {"last_run": self.last_run, "total": c["total"], "excluded": c["excluded"],
                "keyword": self.user.data.get("keyword"),
                "schedule": self._interval_label(self.user.data.get("schedule_interval", 1440))}

    def get_settings(self):
        return self.user.get()

    def save_settings(self, patch):
        data = self.user.save(patch)
        self._apply()                       # 키 병합 재계산
        if "home_address" in (patch or {}):
            self.user.data["home_lat"] = self.user.data["home_lng"] = ""  # 주소 바뀌면 재지오코딩
            self._geocode_home()
        if self.scheduler and "schedule_interval" in (patch or {}):
            try:
                self.scheduler.reschedule(self.user.data.get("schedule_interval", 1440))
            except Exception:
                pass
        return data

    def check_home(self, address=None):
        """집주소 지오코딩 확인 — 좌표로 변환되는지 검사(설정에서 [확인])."""
        key = self.settings.get("kakao_rest_key")
        if not key:
            return {"ok": False, "error": "카카오 키를 먼저 저장하세요(키 관리 탭)."}
        addr = (address or self.user.data.get("home_address") or "").strip()
        if not addr:
            return {"ok": False, "error": "집 주소를 입력하세요."}
        try:
            from geo_kakao import geocode_address
            g = asyncio.run(geocode_address(addr, key))
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}
        if not g:
            return {"ok": False, "error": "주소를 못 찾음 — 도로명+번지로 입력해보세요."}
        return {"ok": True, "address": g.road_address or g.address, "lat": g.lat, "lng": g.lng}

    # ── 이력서 선택(pywebview 파일 다이얼로그) ──
    def pick_resume(self):
        import webview
        try:
            sel = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=("PDF 파일 (*.pdf)",))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not sel:
            return {"ok": False, "cancelled": True}
        path = sel[0]
        ok = self.pipeline.matcher.load(path)      # 즉시 텍스트 추출 검증
        if not ok:
            return {"ok": False, "error": "PDF에서 텍스트를 못 읽음(스캔본?)"}
        self.user.save({"resume_path": path})
        import os
        return {"ok": True, "name": os.path.basename(path)}

    # ── 카카오톡 '나에게 보내기' ──
    def kakao_connect(self):
        res = self.kakao.connect()
        if res.get("ok"):
            self.user.save({"kakao_refresh_token": res["refresh_token"]})
        return {"ok": bool(res.get("ok")), "error": res.get("error")}

    def kakao_test(self):
        if not self.kakao.connected:
            return {"ok": False, "error": "먼저 [연결]하세요."}
        return self.kakao.send("[WHOAREYOU] 카카오톡 알림 연결 테스트입니다 ✅")
