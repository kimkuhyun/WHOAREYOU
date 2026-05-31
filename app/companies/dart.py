"""DART OpenAPI 클라이언트 — 기업개황 + 재무 요약.

JH0103/app/sources/dart.py 패턴을 정리해서 이식. print → logger.
corpCode 매핑은 별도 캐시 파일(corp_code.csv)이 이상적이나 MVP에선 회사명 기반 search API 사용.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.ui import api_status

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"


class DartError(RuntimeError):
    pass


@dataclass
class CompanyOverview:
    corp_code: str
    corp_name: str
    ceo_name: str
    address: str
    homepage: str
    establishment_date: str
    industry_code: str
    stock_code: str
    raw: dict


async def fetch_overview_by_corp_code(corp_code: str, api_key: str) -> CompanyOverview:
    if not api_key:
        await api_status.record_error("dart", "키 미설정")
        raise DartError("DART_API_KEY 미설정 — /settings에서 입력")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{DART_BASE}/company.json",
                params={"crtfc_key": api_key, "corp_code": corp_code},
            )
    except httpx.HTTPError as e:
        await api_status.record_error("dart", f"네트워크: {e}")
        raise DartError(f"DART 네트워크 오류: {e}") from e
    if r.status_code != 200:
        await api_status.record_error("dart", f"HTTP {r.status_code}")
        raise DartError(f"DART company status={r.status_code}")
    d = r.json()
    if d.get("status") not in ("000", None):
        await api_status.record_error("dart", f"{d.get('status')}: {d.get('message')}")
        raise DartError(f"DART 응답 오류: {d.get('status')} {d.get('message')}")
    await api_status.record_ok("dart", "company.json OK")
    return CompanyOverview(
        corp_code=d.get("corp_code", corp_code),
        corp_name=d.get("corp_name", ""),
        ceo_name=d.get("ceo_nm", ""),
        address=d.get("adres", ""),
        homepage=d.get("hm_url", ""),
        establishment_date=d.get("est_dt", ""),
        industry_code=d.get("induty_code", ""),
        stock_code=d.get("stock_code", ""),
        raw=d,
    )


async def fetch_financials_summary(corp_code: str, api_key: str, year: int) -> dict:
    """단일회계기간 주요계정 (요약). reprt_code=11011 사업보고서."""
    if not api_key:
        raise DartError("DART_API_KEY 미설정")
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{DART_BASE}/fnlttSinglAcntAll.json",
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
                "fs_div": "CFS",
            },
        )
    if r.status_code != 200:
        raise DartError(f"DART finance status={r.status_code}")
    d = r.json()
    if d.get("status") != "000":
        return {"status": d.get("status"), "message": d.get("message"), "list": []}

    wanted_accounts = {"매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계"}
    summary = {}
    for item in d.get("list", []):
        account_nm = (item.get("account_nm") or "").strip()
        if account_nm in wanted_accounts:
            summary[account_nm] = {
                "current": item.get("thstrm_amount", ""),
                "previous": item.get("frmtrm_amount", ""),
                "before_previous": item.get("bfefrmtrm_amount", ""),
            }
    return {"year": year, "items": summary, "raw_count": len(d.get("list", []))}


# 회사명 → corp_code 매핑은 DART의 corpCode.xml (대량 csv) 다운로드가 필요한데,
# MVP에선 placeholder. 사용자가 직접 corp_code를 입력하거나 명시적 매핑 시 사용.
async def find_corp_code_by_name(name: str, api_key: str) -> str | None:  # pragma: no cover
    """TODO: corpCode.xml 캐시 다운로드 후 인메모리 매칭. Phase 5+."""
    return None
