# -*- coding: utf-8 -*-
"""통근 = Kakao geocode + ODsay. 키/집주소 없으면 graceful skip(§14)."""
from geo_kakao import geocode_address
from geo_odsay import search_transit


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class CommuteScorer:
    def __init__(self, settings: dict):
        self.kakao = settings.get("kakao_rest_key", "")
        self.odsay = settings.get("odsay_key", "")
        self.home_lat = _f(settings.get("home_lat"))     # 키 없으면 None(통근축 skip)
        self.home_lng = _f(settings.get("home_lng"))
        self.referer = "http://" + settings.get("odsay_referer", "localhost:8000")

    async def minutes(self, lat, lng, address: str):
        """반환 (분 | None, detail). 집주소/키 미설정이면 N/A."""
        if self.home_lat is None or self.home_lng is None or not self.odsay:
            return None, "집주소 미설정"
        if (lat is None or lng is None) and address:
            try:
                g = await geocode_address(address, self.kakao)
                if g:
                    lat, lng = g.lat, g.lng
            except Exception:
                pass
        if lat is None or lng is None:
            return None, "좌표없음"
        try:
            ts = await search_transit(self.home_lng, self.home_lat, lng, lat, self.odsay, referer=self.referer)
        except Exception:
            return None, "ODsay오류"
        if not ts:
            return None, "경로없음"
        return ts.total_minutes, f"{ts.total_minutes}분/{ts.route_type}"
