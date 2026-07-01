# -*- coding: utf-8 -*-
"""사용자 설정 — UI 프리퍼런스 저장(JSON, %APPDATA%). 기본값은 config. 수집·채점에 반영."""
import json

import config

DEFAULTS = {
    "keyword": config.KEYWORD,
    "per_site": config.PER_SITE,
    "threshold": config.THRESHOLD,
    "weights": dict(config.WEIGHTS),
    # 필터 taxonomy (검색어 위주 · 크롤 반영은 점진). 직무는 제거 = 검색어로 대체
    "regions": ["서울 전체", "경기"],
    "career": "신입",
    "edu": ["대졸", "석사", "박사"],          # 멀티선택(학력무관·고졸·초대졸·대졸·석사·박사)
    "emp_types": ["정규직"],
    "comp_types": ["중견기업", "중소기업", "스타트업"],
    "salary_min": "3,000만",
    "exclude": ["포괄임금", "계약직"],
    "home_address": "",
    "max_commute": 50,
    "noti_desktop": True,
    "noti_kakao": False,
    "schedule_interval": 1440,          # 자동 수집·알림 주기(분): 30/60/180/360/720/1440
    "schedule_time": config.DAILY_TIME,  # (구—표시 호환용)
    "quiet_hours": "23:00~08:00",
    "resume_path": "",                 # 이력서 PDF 경로(설정에서 선택)
    # 키 관리(사용자 입력 · 비우면 개발DB/환경값 사용). home_lat/lng는 주소 지오코딩 캐시
    "kakao_rest_key": "",
    "odsay_key": "",
    "kakao_refresh_token": "",          # 카톡 나에게 보내기 OAuth(프로그램이 저장)
    "kakao_client_secret": "",          # 카카오 앱에 Client Secret 켜놨으면 필요(선택)
    "home_lat": "",
    "home_lng": "",
}

# 키 필드(민감) — get()에서 마스킹, 빈 값은 병합 시 개발DB로 폴백
KEY_FIELDS = ("kakao_rest_key", "odsay_key", "kakao_refresh_token", "kakao_client_secret")

# 숫자 필드 — 문자열('abc') 유입 시 채점/스캔 산식이 나중에 깨지므로 저장 때 int로 강제
NUMERIC_FIELDS = ("per_site", "threshold", "schedule_interval", "max_commute")


def _to_int(v, default):
    """int() 실패(빈문자·'abc'·None 등)면 DEFAULT로 폴백."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clean_weights(v):
    """저장 weights = 숫자만 담은 dict 보장. 잘못된 모양이면 DEFAULTS로 폴백."""
    if not isinstance(v, dict):
        return dict(DEFAULTS["weights"])
    out = {}
    for k, val in v.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue                       # 비숫자(문자·None·bool 등) 축은 버림
        out[k] = val
    return out or dict(DEFAULTS["weights"])  # 남은 게 없으면 기본 가중치


class UserSettings:
    def __init__(self):
        self.path = config.APP_DATA / "user_settings.json"
        self.data = dict(DEFAULTS)
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass

    def get(self) -> dict:
        """UI로 반환 — 키 원문은 숨기고 설정여부(keys_set)만 노출."""
        import os
        d = dict(self.data)
        d["keys_set"] = {k: bool(self.data.get(k)) for k in KEY_FIELDS}
        for k in KEY_FIELDS:
            d[k] = ""
        rp = self.data.get("resume_path") or ""
        d["resume_name"] = os.path.basename(rp) if rp else ""
        return d

    def save(self, patch: dict) -> dict:
        for k, v in (patch or {}).items():
            if k not in DEFAULTS:
                continue                       # 알 수 없는 키 스킵
            if k in KEY_FIELDS and not str(v or "").strip():
                continue                       # 빈 키 입력은 무시(기존값 보존) · str()로 비문자 방어
            if k in NUMERIC_FIELDS:
                v = _to_int(v, DEFAULTS[k])     # 'abc'·빈값 → DEFAULT로 강제(나중 산식 보호)
            elif k == "weights":
                v = _clean_weights(v)           # 숫자 dict 보장
            self.data[k] = v
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.get()
