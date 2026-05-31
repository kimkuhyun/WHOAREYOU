"""회사 조회/조사 API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Form, HTTPException
from sqlalchemy import desc, select

from app.analysis.keywords import keywords_to_wordcloud
from app.companies.geocode import geocode_company
from app.companies.playwright_research import research_company_playwright as research_company
from app.companies.research import collect_overall_keywords
from app.geo.odsay import raw_bus_lane as odsay_raw_bus
from app.geo.odsay import raw_request as odsay_raw_request
from app.db import async_session_maker
from app.deps import SessionDep
from app.geo.distance import haversine_km
from app.models import Company, Job, SentimentSnippet
from app.ui import settings_store
from app.ui.progress_bus import ProgressEvent, get_bus, make_progress_callback

logger = logging.getLogger(__name__)
router = APIRouter()

_research_results: dict[str, dict[str, Any]] = {}


@router.get("/api/debug/ollama")
async def debug_ollama() -> dict[str, Any]:
    """Ollama 서버 상태 + 설치된 모델 리스트 반환."""
    from ollama import AsyncClient

    from app.ui import api_status as _api_status

    host = "http://localhost:11434"
    try:
        client = AsyncClient(host=host)
        listing = await client.list()
        models = []
        if hasattr(listing, "models"):
            for m in listing.models:
                models.append(
                    {
                        "name": getattr(m, "model", ""),
                        "size": getattr(m, "size", None),
                        "modified": str(getattr(m, "modified_at", "")),
                    }
                )
        await _api_status.record_ok("ollama", f"{len(models)}개 모델")
        return {"host": host, "ok": True, "count": len(models), "models": models}
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        await _api_status.record_error("ollama", msg)
        return {"host": host, "ok": False, "error": msg}


@router.post("/api/debug/odsay")
async def debug_odsay(session: SessionDep) -> dict[str, Any]:
    """저장된 ODsay 키로 즉시 두 endpoint를 호출하고 raw 응답을 반환 — 키 진단용.

    1) searchBusLane (버스노선 검색) — Basic 무료 키도 가능
    2) searchPubTransPathT (경로 검색) — 우리가 실제 사용하는 것
    """
    settings_map = await settings_store.get_all(session)
    api_key = settings_map.get("odsay_key", "").strip()
    if not api_key:
        return {"error": "ODsay 키 미설정"}
    result: dict[str, Any] = {"key_length": len(api_key), "key_head": api_key[:4], "key_tail": api_key[-4:]}
    # 1) Bus lane — 권한 최소 endpoint
    try:
        status, body, url = await odsay_raw_bus(api_key, "10")
        result["bus_lane"] = {"status": status, "url": url, "body": body}
    except Exception as exc:
        result["bus_lane"] = {"error": f"{type(exc).__name__}: {exc}"}
    # 2) Transit path — 실제 사용 endpoint (서울시청 → 강남역)
    try:
        status, body, url = await odsay_raw_request(126.9780, 37.5665, 127.0276, 37.4979, api_key)
        result["transit_path"] = {"status": status, "url": url, "body": body}
    except Exception as exc:
        result["transit_path"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


@router.patch("/api/companies/{company_id}")
async def update_company(
    company_id: int,
    session: SessionDep,
    domain: str = Form(""),
    dart_corp_code: str = Form(""),
) -> dict[str, Any]:
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(404, "회사 없음")
    if domain is not None:
        new_domain = (domain or "").strip() or None
        if new_domain != company.domain:
            company.domain = new_domain
            # 사용자가 직접 입력 → manual + confidence None (점수 기준 비활성화)
            # 도메인 비웠으면 source/confidence도 같이 초기화
            company.domain_source = "manual" if new_domain else None
            company.domain_confidence = None
            # 도메인이 바뀌었으니 기존 잘못된 요약 무효화 — 다음 조사에서 새로 채워짐
            if not new_domain:
                company.homepage_summary_json = None
    if dart_corp_code is not None:
        company.dart_corp_code = (dart_corp_code or "").strip() or None
    await session.commit()
    return {
        "id": company.id,
        "domain": company.domain,
        "domain_confidence": company.domain_confidence,
        "domain_source": company.domain_source,
        "dart_corp_code": company.dart_corp_code,
    }


# company_id → 진행 중인 research task 정보 (페이지 재진입 시 복원용)
_active_research: dict[int, dict[str, Any]] = {}


@router.post("/api/companies/{company_id}/research")
async def trigger_research(company_id: int) -> dict[str, str]:
    # 같은 회사에 진행 중인 task 있으면 새 task로 교체 (취소)
    prev = _active_research.get(company_id)
    if prev and prev.get("task") and not prev["task"].done():
        prev["task"].cancel()
    task_id = get_bus().new_task_id("research")
    task = asyncio.create_task(_run_research(task_id, company_id))
    _active_research[company_id] = {"task_id": task_id, "task": task}
    return {"task_id": task_id, "company_id": str(company_id)}


async def _run_research(task_id: str, company_id: int) -> None:
    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        report = await research_company(company_id, progress=progress)
        _research_results[task_id] = report
    except asyncio.CancelledError:
        _research_results[task_id] = {"cancelled": True}
        raise
    except Exception as exc:
        logger.exception("research 실패 (%s)", task_id)
        _research_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))
    finally:
        # 같은 task_id면 정리 (재시작된 다른 task가 덮어쓴 경우는 보존)
        entry = _active_research.get(company_id)
        if entry and entry.get("task_id") == task_id:
            _active_research.pop(company_id, None)


@router.get("/api/companies/{company_id}/active-research")
async def get_active_research(company_id: int) -> dict[str, Any]:
    """페이지 진입/재진입 시 진행 중인 research task가 있으면 task_id 반환 — WS 복원용."""
    entry = _active_research.get(company_id)
    if not entry:
        return {"active": False}
    task = entry.get("task")
    if not task or task.done():
        return {"active": False}
    return {"active": True, "task_id": entry["task_id"]}


@router.get("/api/research/{task_id}")
async def get_research_result(task_id: str) -> dict[str, Any]:
    data = _research_results.get(task_id)
    if data is None:
        raise HTTPException(404, "결과 없음")
    return data


@router.get("/api/companies/{company_id}")
async def get_company(company_id: int, session: SessionDep) -> dict[str, Any]:
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(404, "회사를 찾을 수 없습니다")

    jobs = (
        await session.execute(
            select(Job).where(Job.company_id == company_id).order_by(desc(Job.captured_at))
        )
    ).scalars().all()

    snippets = (
        await session.execute(
            select(SentimentSnippet)
            .where(SentimentSnippet.company_id == company_id)
            .order_by(desc(SentimentSnippet.captured_at))
            .limit(30)
        )
    ).scalars().all()

    settings_map = await settings_store.get_all(session)
    home_lat = settings_map.get("home_lat")
    home_lng = settings_map.get("home_lng")
    distance_km = None
    if company.kakao_lat and company.kakao_lng and home_lat and home_lng:
        try:
            distance_km = round(
                haversine_km(float(home_lat), float(home_lng), company.kakao_lat, company.kakao_lng),
                2,
            )
        except (TypeError, ValueError):
            pass

    def _maybe_json(s: str | None) -> Any:
        if not s:
            return None
        import json as _json
        try:
            return _json.loads(s)
        except Exception:
            return None

    return {
        "id": company.id,
        "name": company.name,
        "address": company.address,
        "lat": company.kakao_lat,
        "lng": company.kakao_lng,
        "domain": company.domain,
        "domain_confidence": company.domain_confidence,
        "domain_source": company.domain_source,
        "dart_corp_code": company.dart_corp_code,
        "last_researched_at": company.last_researched_at.isoformat() if company.last_researched_at else None,
        "distance_km": distance_km,
        "transit": _maybe_json(company.transit_json),
        "dart_overview": _maybe_json(company.dart_overview_json),
        "dart_financials": _maybe_json(company.dart_financials_json),
        "emotion": _maybe_json(company.emotion_json),
        "homepage": _maybe_json(company.homepage_summary_json),
        "jobs": [
            {
                "id": j.id,
                "title": j.title,
                "url": j.url,
                "source": j.source,
                "location": j.location,
                "deadline": j.deadline,
                "captured_at": j.captured_at.isoformat() if j.captured_at else None,
            }
            for j in jobs
        ],
        "sentiment_snippets": [
            {
                "source": s.source,
                "text": s.text,
                "score": s.score,
                "captured_at": s.captured_at.isoformat() if s.captured_at else None,
            }
            for s in snippets
        ],
    }


@router.post("/api/companies/geocode-missing")
async def geocode_missing(limit: int = 200) -> dict[str, str]:
    """좌표 없는 회사들을 일괄 geocode (백필)."""
    task_id = get_bus().new_task_id("geocode")
    asyncio.create_task(_run_geocode_missing(task_id, limit))
    return {"task_id": task_id}


_geocode_results: dict[str, dict[str, Any]] = {}


async def _run_geocode_missing(task_id: str, limit: int) -> None:
    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        async with async_session_maker() as session:
            settings_map = await settings_store.get_all(session)
            kakao_key = settings_map.get("kakao_rest_key", "")
            if not kakao_key:
                await progress("error", 100, "Kakao REST Key 미설정")
                _geocode_results[task_id] = {"error": "no kakao key"}
                return

            home_lat = _maybe_float(settings_map.get("home_lat"))
            home_lng = _maybe_float(settings_map.get("home_lng"))

            stmt = (
                select(Company)
                .where(Company.kakao_lat.is_(None))
                .limit(limit)
            )
            companies = (await session.execute(stmt)).scalars().all()
            total = len(companies)
            await progress("start", 5, f"좌표 없는 회사 {total}개 — 백필 시작")

            ok = 0
            fail = 0
            for i, c in enumerate(companies, 1):
                pct = 5 + int(90 * i / max(total, 1))
                _, _ = await geocode_company(
                    session, c, kakao_key, home_lat=home_lat, home_lng=home_lng
                )
                if c.kakao_lat is not None:
                    ok += 1
                else:
                    fail += 1
                if i % 5 == 0 or i == total:
                    await progress(
                        "geocode",
                        pct,
                        f"{i}/{total} · 성공 {ok} · 실패 {fail}",
                    )
                # 카카오 무료 한도 보호 — 매 호출마다 약간의 간격
                if i % 10 == 0:
                    await session.commit()
            await session.commit()

            _geocode_results[task_id] = {"total": total, "ok": ok, "fail": fail}
            await progress("done", 100, f"완료 — 성공 {ok}, 실패 {fail}")
    except Exception as exc:
        logger.exception("geocode-missing 실패 (%s)", task_id)
        _geocode_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))


@router.get("/api/geocode/{task_id}")
async def get_geocode_result(task_id: str) -> dict[str, Any]:
    data = _geocode_results.get(task_id)
    if data is None:
        raise HTTPException(404, "결과 없음")
    return data


def _maybe_float(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/api/companies")
async def list_companies(session: SessionDep, limit: int = 500) -> dict[str, Any]:
    """모든 회사 + 거리 (지도용)."""
    companies = (
        await session.execute(select(Company).limit(limit))
    ).scalars().all()
    settings_map = await settings_store.get_all(session)
    try:
        home_lat = float(settings_map.get("home_lat") or 0) or None
        home_lng = float(settings_map.get("home_lng") or 0) or None
    except (TypeError, ValueError):
        home_lat, home_lng = None, None

    items = []
    for c in companies:
        d = None
        if home_lat and home_lng and c.kakao_lat and c.kakao_lng:
            d = round(haversine_km(home_lat, home_lng, c.kakao_lat, c.kakao_lng), 2)
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "address": c.address,
                "lat": c.kakao_lat,
                "lng": c.kakao_lng,
                "distance_km": d,
                "last_researched_at": c.last_researched_at.isoformat() if c.last_researched_at else None,
            }
        )
    # 좌표 있는 것 우선, 거리순
    items.sort(key=lambda x: (x["lat"] is None, x["distance_km"] if x["distance_km"] is not None else 9999))
    return {"items": items, "home": {"lat": home_lat, "lng": home_lng}}


@router.get("/api/wordcloud")
async def get_wordcloud(session: SessionDep, company_id: int | None = None) -> dict[str, Any]:
    if company_id:
        company = await session.get(Company, company_id)
        if not company:
            raise HTTPException(404, "회사 없음")
        jobs = (
            await session.execute(select(Job).where(Job.company_id == company_id))
        ).scalars().all()
        from app.analysis.keywords import extract_from_titles

        pairs = extract_from_titles([j.title for j in jobs], top_n=40)
    else:
        pairs = await collect_overall_keywords(session)

    return {"pairs": pairs, "wordcloud": keywords_to_wordcloud(pairs)}
