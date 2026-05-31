import re
from dataclasses import dataclass

import httpx

from app.ui import api_status

KAKAO_BASE = "https://dapi.kakao.com/v2/local"


def _address_candidates(raw: str) -> list[str]:
    """주소 문자열을 여러 정제 버전으로 펼친다 — 카카오가 받아주기 좋은 순서로.

    카카오 주소 검색은 "괄호 안 설명", "X역 부근/근처", "X층 X호" 같은 군더더기를
    싫어한다. 도로명+번지가 가장 잘 잡힌다.
    """
    out: list[str] = []
    base = (raw or "").strip()
    if not base:
        return out
    out.append(base)

    # 1) 괄호 안 제거: "서울 영등포구 경인로 846 (영등포동, ...) 영등포역 부근"
    no_paren = re.sub(r"\s*\([^)]*\)\s*", " ", base).strip()
    no_paren = re.sub(r"\s+", " ", no_paren)
    if no_paren and no_paren != base:
        out.append(no_paren)

    # 2) "부근/근처/인근/일대/앞/주변/역 X분" 등 뒤쪽 보조 표현 제거
    cleaned = re.sub(
        r"\s*([가-힣A-Za-z0-9·]+역|[가-힣]+(?:시장|광장|로타리))?\s*"
        r"(부근|근처|인근|일대|앞|주변|옆|뒤)\s*$",
        "",
        no_paren,
    ).strip()
    if cleaned and cleaned != no_paren:
        out.append(cleaned)

    # 3) "X층, X호" 같은 세부 위치 제거
    no_floor = re.sub(r"\s*\d+\s*층\s*(\d+\s*호)?$", "", cleaned).strip()
    no_floor = re.sub(r"\s*\d+\s*호\s*$", "", no_floor).strip()
    if no_floor and no_floor != cleaned:
        out.append(no_floor)

    # 4) 도로명 + 번지까지만 추출 (가장 카카오 친화적)
    #    예) "서울 영등포구 경인로 846"
    m = re.search(
        r"((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)\s*"
        r"[가-힣A-Za-z0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시)?\s*"
        r"[가-힣A-Za-z0-9]+(?:구|시|군)\s+"
        r"[가-힣A-Za-z0-9]+(?:로|길|동)\s*\d+(?:-\d+)?)",
        base,
    )
    if m:
        out.append(m.group(1).strip())

    # 5) 마지막 토큰 잘라낸 버전 (마지막 단어가 군더더기일 때)
    if len(no_floor.split()) > 3:
        out.append(" ".join(no_floor.split()[:-1]))

    # dedup (순서 보존)
    seen: set[str] = set()
    unique: list[str] = []
    for v in out:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


@dataclass
class GeocodeResult:
    address: str
    road_address: str | None
    lat: float
    lng: float


class KakaoGeocodeError(RuntimeError):
    pass


async def geocode_address(query: str, rest_key: str) -> GeocodeResult:
    if not rest_key:
        await api_status.record_error("kakao_rest", "키 미설정")
        raise KakaoGeocodeError("KAKAO_REST_KEY가 비어 있습니다. 설정 페이지에서 입력하세요.")
    if not query.strip():
        raise KakaoGeocodeError("주소가 비어 있습니다.")

    headers = {"Authorization": f"KakaoAK {rest_key}"}
    candidates = _address_candidates(query)

    last_no_match: str | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:

            async def _try_address_api(q: str):
                r = await client.get(
                    f"{KAKAO_BASE}/search/address.json",
                    headers=headers, params={"query": q, "size": 1},
                )
                if r.status_code != 200:
                    raise KakaoGeocodeError(f"카카오 주소 API HTTP {r.status_code}: {r.text[:120]}")
                docs = r.json().get("documents", [])
                if not docs:
                    return None
                d = docs[0]
                return GeocodeResult(
                    address=d.get("address_name") or q,
                    road_address=(d.get("road_address") or {}).get("address_name") if d.get("road_address") else None,
                    lat=float(d["y"]),
                    lng=float(d["x"]),
                )

            async def _try_keyword_api(q: str):
                r = await client.get(
                    f"{KAKAO_BASE}/search/keyword.json",
                    headers=headers, params={"query": q, "size": 1},
                )
                if r.status_code != 200:
                    raise KakaoGeocodeError(f"카카오 키워드 API HTTP {r.status_code}: {r.text[:120]}")
                kdocs = r.json().get("documents", [])
                if not kdocs:
                    return None
                d = kdocs[0]
                return GeocodeResult(
                    address=d.get("address_name") or d.get("place_name") or q,
                    road_address=d.get("road_address_name"),
                    lat=float(d["y"]),
                    lng=float(d["x"]),
                )

            # 1) 주소 검색을 후보들에 대해 순서대로 시도 (도로명 + 번지가 보통 가장 잘 잡힘)
            for cand in candidates:
                result = await _try_address_api(cand)
                if result is not None:
                    await api_status.record_ok("kakao_rest", f"주소 검색 성공 ('{cand}')")
                    return result
                last_no_match = cand

            # 2) 키워드 검색 fallback — 후보들 다시 시도
            for cand in candidates:
                result = await _try_keyword_api(cand)
                if result is not None:
                    await api_status.record_ok("kakao_rest", f"키워드 검색 성공 ('{cand}')")
                    return result

            # 매칭 자체가 안 됨 (키는 OK)
            await api_status.record_ok("kakao_rest", "응답 OK (매칭 없음)")
            raise KakaoGeocodeError(
                f"주소/장소를 찾을 수 없습니다: {query!r} (시도한 후보 {len(candidates)}개)"
            )
    except KakaoGeocodeError:
        raise
    except httpx.HTTPError as e:
        await api_status.record_error("kakao_rest", f"네트워크: {e}")
        raise KakaoGeocodeError(f"네트워크 오류: {e}") from e
