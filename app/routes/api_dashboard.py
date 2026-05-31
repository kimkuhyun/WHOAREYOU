"""대시보드 전용 집계 API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import desc, func, select

from app.analysis.categories import all_categories, classify_title
from app.deps import SessionDep
from app.models import Company, Job, SearchHistory
from app.ui import api_status

router = APIRouter()


@router.post("/api/browser/test")
async def test_browser(session: SessionDep) -> dict[str, Any]:
    """현재 설정으로 BrowserPool을 재시작하고 활성 모드를 반환."""
    from app.crawler.browser import get_pool
    try:
        mode = await get_pool().refresh()
        label = {
            "cdp": "사용자 Chrome에 attach 성공",
            "persistent": "자동 영구 프로필 Chromium 사용 중",
            "headless": "헤드리스 Chromium 사용 중 (fallback)",
        }.get(mode, mode)
        return {"ok": mode != "uninitialized", "mode": mode, "message": label}
    except Exception as exc:
        return {"ok": False, "mode": "error", "message": f"{type(exc).__name__}: {exc}"}


@router.post("/api/browser/open-for-login")
async def open_browser_for_login(login_url: str = "https://www.google.com") -> dict[str, Any]:
    """크롤용 Chrome을 visible 정상 창으로 띄움 — 사용자가 직접 로그인할 수 있게.

    로그인하면 쿠키/세션이 user-data-dir에 저장돼서 이후 크롤에 자동 활용됨.
    크롤 시작 시점에 자동으로 다시 최소화 spawn으로 전환.
    """
    from app.crawler.browser import get_pool
    return await get_pool().open_for_login(login_url=login_url)


@router.post("/api/browser/clear-profile")
async def clear_browser_profile() -> dict[str, Any]:
    """크롤용 Chrome의 모든 쿠키/세션/캐시 삭제 (user-data-dir 통째로 비움).

    다음 크롤 시 fresh 상태에서 시작.
    """
    from app.crawler.browser import get_pool
    return await get_pool().clear_profile()


@router.get("/api/api-status")
async def get_api_status() -> dict[str, Any]:
    """모든 외부 API의 마지막 호출 결과 캐시."""
    return {"items": await api_status.get_all()}


@router.post("/api/api-status/test/{code}")
async def test_one_api(code: str, session: SessionDep) -> dict[str, Any]:
    """단일 키 테스트 — 키 입력 옆 버튼에서 호출.

    내부적으로 test_all_apis 호출 후 해당 key만 반환 (간단 구현).
    개별 키만 빠르게 테스트하려면 추후 _test_xxx로 분해 필요.
    """
    SUPPORTED = {"kakao_rest", "kakao_js", "odsay", "dart", "ollama"}
    if code not in SUPPORTED:
        raise HTTPException(400, f"지원하지 않는 키: {code}")
    all_data = await test_all_apis(session)
    results = all_data.get("results", {})
    return {"code": code, "result": results.get(code, {"ok": False, "message": "결과 없음"})}


@router.post("/api/api-status/test-all")
async def test_all_apis(session: SessionDep) -> dict[str, Any]:
    """저장된 키들로 가벼운 호출을 한 번씩 날려 모든 API 상태를 갱신.

    각 키별로 (ok, message, detail) 반환. 호출 실패도 그대로 결과에 담는다.
    """
    import httpx as _httpx

    from app.geo.kakao import KakaoGeocodeError, geocode_address
    from app.geo.odsay import odsay_help_for, raw_request as odsay_raw
    from app.ui import settings_store as _ss

    cfg = await _ss.get_all(session)
    results: dict[str, dict[str, Any]] = {}

    # ── Kakao REST ──
    rest = cfg.get("kakao_rest_key", "").strip()
    if not rest:
        results["kakao_rest"] = {"ok": False, "message": "키 미설정"}
    else:
        try:
            g = await geocode_address("서울특별시청", rest)
            results["kakao_rest"] = {
                "ok": True,
                "message": f"OK · 좌표 {g.lat:.4f},{g.lng:.4f}",
            }
        except KakaoGeocodeError as e:
            results["kakao_rest"] = {"ok": False, "message": str(e)}
        except Exception as e:
            results["kakao_rest"] = {"ok": False, "message": f"{type(e).__name__}: {e}"}

    # ── Kakao JS (브라우저 SDK라 서버 검증 불가, 형식만 확인) ──
    js = cfg.get("kakao_js_key", "").strip()
    if not js:
        results["kakao_js"] = {"ok": False, "message": "키 미설정"}
    elif len(js) < 20:
        results["kakao_js"] = {"ok": False, "message": "키 형식이 너무 짧습니다"}
    else:
        # JS 키는 브라우저에서 lab에 등록된 사이트 도메인 검증을 받으므로 서버에서 정확한 검증이 불가능.
        # 형식만 OK로 표시.
        await api_status.record_ok("kakao_js", "형식 확인 OK (브라우저 검증은 별도)")
        results["kakao_js"] = {"ok": True, "message": "형식 OK (실제 검증은 지도 로딩 시)"}

    # ── ODsay ──
    odsay_key = cfg.get("odsay_key", "").strip()
    odsay_referer = cfg.get("odsay_referer", "").strip() or "http://localhost:8000"
    if not odsay_key:
        results["odsay"] = {"ok": False, "message": "키 미설정"}
    else:
        try:
            status, body, url = await odsay_raw(
                126.9780, 37.5665, 127.0276, 37.4979,
                odsay_key, referer=odsay_referer,
            )
            if status == 200 and isinstance(body, dict) and "error" not in body:
                results["odsay"] = {"ok": True, "message": f"OK (referer={odsay_referer})"}
            else:
                err = (body or {}).get("error") if isinstance(body, dict) else None
                if isinstance(err, list):
                    err = err[0] if err else {}
                code = (err or {}).get("code") if isinstance(err, dict) else None
                msg = (err or {}).get("msg") or (err or {}).get("message") or f"HTTP {status}"
                help_text = (body or {}).get("_help") if isinstance(body, dict) else None
                results["odsay"] = {
                    "ok": False,
                    "message": f"[code={code}] {msg}",
                    "detail": help_text or odsay_help_for(code),
                    "referer_used": odsay_referer,
                }
        except Exception as e:
            results["odsay"] = {"ok": False, "message": f"{type(e).__name__}: {e}"}

    # ── DART ──
    dart = cfg.get("dart_api_key", "").strip()
    if not dart:
        results["dart"] = {"ok": False, "message": "키 미설정"}
    else:
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    "https://opendart.fss.or.kr/api/list.json",
                    params={"crtfc_key": dart, "page_count": "1"},
                )
            if r.status_code != 200:
                results["dart"] = {"ok": False, "message": f"HTTP {r.status_code}"}
                await api_status.record_error("dart", f"HTTP {r.status_code}")
            else:
                d = r.json()
                st = d.get("status")
                msg = d.get("message", "")
                if st in ("000", "013"):  # 013: 정상이지만 데이터 없음 — 키 자체는 OK
                    results["dart"] = {"ok": True, "message": f"OK ({st}: {msg})"}
                    await api_status.record_ok("dart", "list.json OK")
                else:
                    results["dart"] = {"ok": False, "message": f"{st}: {msg}"}
                    await api_status.record_error("dart", f"{st}: {msg}")
        except Exception as e:
            results["dart"] = {"ok": False, "message": f"{type(e).__name__}: {e}"}

    # ── Ollama ──
    try:
        from ollama import AsyncClient
        host = "http://localhost:11434"
        client = AsyncClient(host=host)
        listing = await client.list()
        n = len(getattr(listing, "models", []) or [])
        await api_status.record_ok("ollama", f"{n}개 모델")
        results["ollama"] = {"ok": True, "message": f"{n}개 모델 설치됨"}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        await api_status.record_error("ollama", msg)
        results["ollama"] = {
            "ok": False,
            "message": msg,
            "detail": "Ollama 데스크탑이 실행 중인지 확인하세요 (http://localhost:11434).",
        }

    return {"results": results, "items": await api_status.get_all()}


@router.get("/api/search-history")
async def get_search_history(session: SessionDep, limit: int = 12) -> dict[str, Any]:
    """최근 검색어 N건 + 빈도수."""
    rows = (
        await session.execute(
            select(SearchHistory)
            .order_by(desc(SearchHistory.last_searched_at))
            .limit(max(1, min(50, limit)))
        )
    ).scalars().all()
    return {
        "items": [
            {
                "keyword": r.keyword,
                "hit_count": r.hit_count,
                "last_searched_at": r.last_searched_at.isoformat() if r.last_searched_at else None,
            }
            for r in rows
        ]
    }


@router.get("/api/jobs/categories")
async def get_job_categories(session: SessionDep) -> dict[str, Any]:
    """(레거시) 사전 정의된 직무 카테고리별 잡 개수 — 호환용으로 유지."""
    titles = (await session.execute(select(Job.title))).scalars().all()
    cats = all_categories()
    counts = {c["code"]: 0 for c in cats}
    for t in titles:
        code, _, _ = classify_title(t or "")
        counts[code] = counts.get(code, 0) + 1
    items = [
        {**c, "count": counts.get(c["code"], 0)}
        for c in cats
    ]
    items.sort(key=lambda x: (-x["count"], x["label"]))
    return {"items": items, "total": sum(counts.values())}


@router.get("/api/jobs/keyword-buckets")
async def get_keyword_buckets(session: SessionDep) -> dict[str, Any]:
    """검색 히스토리의 각 키워드별로 잡 카운트를 집계.

    "직무 카테고리"가 미리 정의돼 있지 않고, 사용자가 검색한 키워드들이
    그대로 그룹 라벨이 된다. 키워드가 잡 제목 또는 회사명에 포함된 건수를 카운트.
    """
    keywords = (
        await session.execute(
            select(SearchHistory).order_by(desc(SearchHistory.last_searched_at)).limit(40)
        )
    ).scalars().all()

    items: list[dict[str, Any]] = []
    for kw in keywords:
        like = f"%{kw.keyword}%"
        cnt = (
            await session.execute(
                select(func.count(Job.id)).where(Job.title.like(like))
            )
        ).scalar_one()
        items.append(
            {
                "keyword": kw.keyword,
                "hit_count": kw.hit_count,
                "job_count": int(cnt),
                "last_searched_at": kw.last_searched_at.isoformat() if kw.last_searched_at else None,
            }
        )

    # 매칭된 잡이 있는 것 우선, 그 다음 최근 검색순
    items.sort(key=lambda x: (-x["job_count"], -x["hit_count"]))
    return {"items": items, "total": sum(i["job_count"] for i in items)}


@router.get("/api/dashboard/applications")
async def get_application_summary(session: SessionDep) -> dict[str, Any]:
    """등록(수집)된 기업 중 지원/면접 등 진행 중인 항목 요약."""
    # 상태별 카운트
    status_rows = (
        await session.execute(
            select(Job.application_status, func.count(Job.id)).group_by(Job.application_status)
        )
    ).all()
    by_status: dict[str, int] = {}
    for s, c in status_rows:
        by_status[s or "none"] = int(c)

    # 진행 중(none이 아닌) 회사 목록 — 가장 최근 변경순
    stmt = (
        select(Job, Company)
        .join(Company, Job.company_id == Company.id, isouter=True)
        .where(Job.application_status != "none")
        .order_by(desc(Job.status_updated_at), desc(Job.captured_at))
        .limit(30)
    )
    rows = (await session.execute(stmt)).all()
    items = []
    for job, company in rows:
        items.append(
            {
                "job_id": job.id,
                "title": job.title,
                "url": job.url,
                "source": job.source,
                "status": job.application_status or "none",
                "status_updated_at": job.status_updated_at.isoformat() if job.status_updated_at else None,
                "company": {"id": company.id, "name": company.name} if company else None,
            }
        )

    total_jobs = (await session.execute(select(func.count(Job.id)))).scalar_one()
    total_companies = (await session.execute(select(func.count(Company.id)))).scalar_one()

    return {
        "by_status": by_status,
        "items": items,
        "total_jobs": int(total_jobs),
        "total_companies": int(total_companies),
        "in_progress": sum(v for k, v in by_status.items() if k != "none"),
    }
