from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import ROOT_DIR
from app.deps import SessionDep
from app.geo.kakao import KakaoGeocodeError, geocode_address
from app.ui import settings_store

router = APIRouter()
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep) -> HTMLResponse:
    settings = await settings_store.get_all(session)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"settings": settings, "active": "home"},
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"active": "jobs"},
    )


@router.get("/resumes", response_class=HTMLResponse)
async def resumes_page(request: Request) -> HTMLResponse:
    """이력서 목록 페이지 — 다중 이력서 카드 그리드."""
    return templates.TemplateResponse(
        request,
        "resumes.html",
        {"active": "resume"},
    )


@router.get("/resume", response_class=HTMLResponse)
async def resume_page(request: Request, id: int | None = None) -> HTMLResponse:
    """이력서 편집 — ?id=N이면 그 이력서, 없으면 활성(primary)."""
    return templates.TemplateResponse(
        request,
        "resume.html",
        {"active": "resume", "resume_id": id},
    )


@router.get("/job/{job_id:int}", response_class=HTMLResponse)
async def job_detail_page(job_id: int, request: Request, session: SessionDep) -> HTMLResponse:
    from app.models import Job

    job = await session.get(Job, job_id)
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(404, "공고를 찾을 수 없습니다")
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"active": "jobs", "job_id": job_id},
    )


@router.get("/companies", response_class=HTMLResponse)
async def companies_page(request: Request, session: SessionDep) -> HTMLResponse:
    """기업 페이지 — 좌측 회사 목록 + (미선택)전체 지도 / (선택)회사 상세.

    ?id=N 딥링크는 클라이언트(Alpine)가 location.search로 읽어 해당 회사 자동 선택.
    """
    settings = await settings_store.get_all(session)
    return templates.TemplateResponse(
        request,
        "companies.html",
        {
            "active": "companies",
            "kakao_js_key": settings.get("kakao_js_key", ""),
            "home_lat": settings.get("home_lat", ""),
            "home_lng": settings.get("home_lng", ""),
            "home_address": settings.get("home_address", ""),
        },
    )


@router.get("/map")
async def map_redirect() -> RedirectResponse:
    """구 경로 호환 — /map 은 /companies 로 이동."""
    return RedirectResponse("/companies", status_code=307)


@router.get("/company/{company_id}", response_class=HTMLResponse)
async def company_page(
    company_id: int, request: Request, session: SessionDep, embed: int = 0
) -> HTMLResponse:
    from app.models import Company

    company = await session.get(Company, company_id)
    if company is None:
        from fastapi import HTTPException
        raise HTTPException(404, "회사를 찾을 수 없습니다")
    settings = await settings_store.get_all(session)
    return templates.TemplateResponse(
        request,
        "company.html",
        {
            "active": "companies",
            "company": company,
            "kakao_js_key": settings.get("kakao_js_key", ""),
            "home_lat": settings.get("home_lat", ""),
            "home_lng": settings.get("home_lng", ""),
            "embed": bool(embed),  # 기업 페이지 iframe 임베드 — 헤더/드로어 숨김
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: SessionDep) -> HTMLResponse:
    settings = await settings_store.get_all(session)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "active": "settings"},
    )


@router.post("/settings", response_class=HTMLResponse)
async def settings_submit(
    request: Request,
    session: SessionDep,
    home_address: str = Form(""),
    kakao_rest_key: str = Form(""),
    kakao_js_key: str = Form(""),
    odsay_key: str = Form(""),
    odsay_referer: str = Form(""),
    dart_api_key: str = Form(""),
    ollama_text_model: str = Form("qwen3.5:9b"),
    ollama_vision_model: str = Form("qwen2.5vl:7b"),
    max_concurrent_crawls: str = Form("5"),
    chrome_cdp_url: str = Form(""),
    browser_show: str = Form(""),
) -> HTMLResponse:
    payload = {
        "kakao_rest_key": kakao_rest_key.strip(),
        "kakao_js_key": kakao_js_key.strip(),
        "odsay_key": odsay_key.strip(),
        "odsay_referer": odsay_referer.strip() or "http://localhost:8000",
        "dart_api_key": dart_api_key.strip(),
        "ollama_text_model": ollama_text_model.strip() or "qwen3.5:9b",
        "ollama_vision_model": ollama_vision_model.strip() or "qwen2.5vl:7b",
        "max_concurrent_crawls": max_concurrent_crawls.strip() or "5",
        "chrome_cdp_url": chrome_cdp_url.strip(),
        "browser_show": "true" if browser_show.strip().lower() in {"1", "true", "on", "yes"} else "false",
    }

    geocode_message: str | None = None
    geocode_ok = False
    if home_address.strip():
        payload["home_address"] = home_address.strip()
        try:
            result = await geocode_address(home_address.strip(), payload["kakao_rest_key"])
            payload["home_lat"] = f"{result.lat}"
            payload["home_lng"] = f"{result.lng}"
            payload["home_road_address"] = result.road_address or ""
            geocode_message = f"좌표 저장 완료: {result.lat:.5f}, {result.lng:.5f} ({result.address})"
            geocode_ok = True
        except KakaoGeocodeError as exc:
            geocode_message = f"주소 변환 실패: {exc}"

    await settings_store.upsert_many(session, payload)

    # 브라우저 설정이 바뀌었으면 BrowserPool 재시작 (모드 다시 선택)
    cdp_message: str | None = None
    cdp_ok = False
    try:
        from app.crawler.browser import get_pool
        new_mode = await get_pool().refresh()
        mode_label = {"cdp": "CDP attach", "persistent": "자동 (영구 프로필)", "headless": "헤드리스"}.get(new_mode, new_mode)
        cdp_message = f"✓ 브라우저 모드: {mode_label}"
        cdp_ok = True
    except Exception as exc:
        cdp_message = f"✗ 브라우저 재시작 오류: {exc}"

    settings = await settings_store.get_all(session)
    flash = geocode_message or cdp_message
    flash_ok = (geocode_ok if geocode_message else cdp_ok)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "settings": settings,
            "active": "settings",
            "flash": flash,
            "flash_ok": flash_ok,
        },
    )
