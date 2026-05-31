"""ODsay 대중교통 경로 API.

집 좌표(SX,SY) → 회사 좌표(EX,EY) 경로 검색.
- API 키는 UserSetting의 odsay_key.
- 대중교통, 도보 포함 전체 경로 중 첫 번째 결과를 요약 반환.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.ui import api_status

logger = logging.getLogger(__name__)

ODSAY_BASE = "https://api.odsay.com/v1/api/searchPubTransPathT"


class ODsayError(RuntimeError):
    pass


# ODsay 에러 코드별 한국어 안내 — 진단 응답에 포함됨.
# 출처: https://lab.odsay.com (자주 등장하는 코드 위주)
ODSAY_ERROR_HELP: dict[int, str] = {
    -8: (
        "도메인 검증 실패 — ODsay LAB 마이페이지(lab.odsay.com)의 "
        "내 API → API URI 등록 메뉴에 현재 호출 도메인을 추가해야 합니다. "
        "기본값은 'http://localhost:8000'입니다. 설정 페이지에서 'ODsay Referer'를 "
        "ODsay LAB에 등록한 값과 정확히 동일하게 맞춰 주세요."
    ),
    -1:  "ODsay 내부 응답 오류 — 잠시 후 재시도해 주세요.",
    300: "잘못된 API 키 형식 — 키를 다시 복사해 붙여넣어 주세요.",
    301: "등록되지 않은 API 키 — 발급 후 활성화까지 5~30분 걸릴 수 있습니다.",
    302: "사용 정지된 API 키 — ODsay LAB 마이페이지에서 키 상태를 확인하세요.",
    303: "이용기간 만료 — ODsay LAB에서 새로 발급하세요.",
    304: "한도 초과 — 무료 등급은 일 5,000건 제한입니다.",
    500: (
        "유료/상위 등급이 필요한 API — searchPubTransPathT는 Basic(무료)로 가능합니다. "
        "키 등급을 확인하세요."
    ),
    501: "일일 호출 한도 초과 — 다음 날 다시 시도하세요.",
    9999: "ODsay 시스템 오류 — 잠시 후 재시도해 주세요.",
}


def odsay_help_for(code: int | str | None) -> str:
    """ODsay error code → 한국어 안내. 매칭 안 되면 일반 안내."""
    try:
        c = int(code) if code is not None else None
    except (TypeError, ValueError):
        c = None
    if c is not None and c in ODSAY_ERROR_HELP:
        return ODSAY_ERROR_HELP[c]
    return (
        "응답에 오류가 포함되어 있습니다. ODsay LAB 마이페이지에서 "
        "(1) 키가 활성 상태인지, (2) API URI에 호출 도메인이 등록돼 있는지 확인하세요."
    )


@dataclass
class TransitSummary:
    total_minutes: int
    total_distance_m: int
    transfer_count: int
    payment: int  # 비용 원
    first_start_station: str
    last_end_station: str
    route_type: str  # 지하철+버스 / 지하철 / 버스 등


async def raw_request(
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    api_key: str,
    *,
    referer: str = "http://localhost:8000",
) -> tuple[int, dict, str]:
    """ODsay 경로검색 raw 호출 — (status, parsed_json, redacted_url) 반환.

    parsed_json에는 ODsay 원본 응답 + (오류 시) '_help'(한국어 안내) 키를 덧붙인다.
    """
    if not api_key:
        await api_status.record_error("odsay", "키 미설정")
        raise ODsayError("ODsay API 키가 비어있습니다.")
    params = {
        "apiKey": api_key,
        "SX": sx, "SY": sy, "EX": ex, "EY": ey, "OPT": 0,
    }
    headers = {"Referer": referer, "Origin": referer}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(ODSAY_BASE, params=params, headers=headers)
    except httpx.HTTPError as e:
        await api_status.record_error("odsay", f"네트워크: {e}")
        raise
    redacted = str(r.url).replace(api_key, "***REDACTED***")
    try:
        body = r.json()
    except Exception:
        body = {"_raw_text": r.text[:1000]}

    if r.status_code != 200:
        msg = f"HTTP {r.status_code}"
        if isinstance(body, dict):
            body["_help"] = "ODsay 서버 응답이 정상이 아닙니다. 잠시 후 재시도하세요."
        await api_status.record_error("odsay", msg)
    elif isinstance(body, dict) and "error" in body:
        err = body["error"]
        if isinstance(err, list):
            err = err[0] if err else {}
        msg = (err or {}).get("msg") or (err or {}).get("message") or str(err)
        code = (err or {}).get("code") if isinstance(err, dict) else None
        body["_help"] = odsay_help_for(code)
        body["_referer_used"] = referer
        await api_status.record_error("odsay", f"code={code} {msg}")
    else:
        body["_help"] = "응답 정상"
        body["_referer_used"] = referer
        await api_status.record_ok("odsay", f"응답 OK (referer={referer})")
    return r.status_code, body, redacted


async def raw_bus_lane(api_key: str, bus_no: str = "10", *, referer: str = "http://localhost:8000") -> tuple[int, dict, str]:
    """ODsay 버스노선 검색 raw 호출 — 권한 진단용 (Basic 키도 가능한 가벼운 endpoint)."""
    if not api_key:
        raise ODsayError("ODsay API 키가 비어있습니다.")
    url = "https://api.odsay.com/v1/api/searchBusLane"
    params = {"apiKey": api_key, "busNo": bus_no}
    headers = {"Referer": referer, "Origin": referer}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params, headers=headers)
    redacted = str(r.url).replace(api_key, "***REDACTED***")
    try:
        return r.status_code, r.json(), redacted
    except Exception:
        return r.status_code, {"_raw_text": r.text[:1000]}, redacted


async def search_transit(
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    api_key: str,
    *,
    referer: str = "http://localhost:8000",
) -> TransitSummary | None:
    if not api_key:
        return None
    params = {
        "apiKey": api_key,
        "SX": sx,
        "SY": sy,
        "EX": ex,
        "EY": ey,
        "OPT": 0,  # 0: 추천경로 (시간/환승 종합)
    }
    # ODsay LAB에 등록한 도메인과 호스트:포트가 일치해야 검증 통과 (스킴은 LAB이 자동 매칭)
    headers = {"Referer": referer, "Origin": referer}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(ODSAY_BASE, params=params, headers=headers)
        except Exception as exc:
            raise ODsayError(f"ODsay 호출 실패: {exc}") from exc
    if r.status_code != 200:
        raise ODsayError(f"ODsay status={r.status_code}")
    try:
        data = r.json()
    except Exception as exc:
        raise ODsayError(f"ODsay JSON 파싱 실패: {exc}") from exc

    # ODsay는 status 200이어도 body에 error 필드 들어옴. error는 dict 또는 list.
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, list):
            err = err[0] if err else {}
        if isinstance(err, dict):
            msg = err.get("msg") or err.get("message") or str(err)
            code = err.get("code", "?")
        else:
            msg, code = str(err), "?"
        raise ODsayError(f"ODsay error code={code}: {msg}")
    if not isinstance(data, dict) or "result" not in data:
        logger.warning("ODsay 비표준 응답: %s", str(data)[:300])
        return None
    result = data["result"]
    if not isinstance(result, dict):
        logger.warning("ODsay result가 dict 아님: %s", str(result)[:200])
        return None
    if "path" not in result:
        logger.info("ODsay result에 path 없음 (%s)", str(result)[:200])
        return None
    paths = result["path"]
    if not isinstance(paths, list) or not paths:
        return None
    p = paths[0]
    info = p.get("info") or {}
    sub_path = p.get("subPath") or []
    route_type_int = p.get("pathType", 0)
    type_map = {1: "지하철", 2: "버스", 3: "지하철+버스"}
    route_type = type_map.get(route_type_int, "기타")

    first_station = ""
    last_station = ""
    for sp in sub_path:
        if sp.get("trafficType") in (1, 2) and sp.get("startName"):
            first_station = sp["startName"]
            break
    for sp in reversed(sub_path):
        if sp.get("trafficType") in (1, 2) and sp.get("endName"):
            last_station = sp["endName"]
            break

    return TransitSummary(
        total_minutes=int(info.get("totalTime", 0)),
        total_distance_m=int(info.get("totalDistance", 0)),
        transfer_count=int(info.get("subwayTransitCount", 0) + info.get("busTransitCount", 0)),
        payment=int(info.get("payment", 0)),
        first_start_station=first_station,
        last_end_station=last_station,
        route_type=route_type,
    )
