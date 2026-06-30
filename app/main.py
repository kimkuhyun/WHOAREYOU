import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

# Windows: Playwright의 Chromium subprocess는 ProactorEventLoop에서만 동작.
# uvicorn 기본은 SelectorEventLoop이므로 import 시점에 명시적으로 정책 강제.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR, get_settings
from app.crawler.browser import get_pool, shutdown_pool
from app.db import init_db
from app.routes import api_companies, api_dashboard, api_jobs, api_ollama, api_resume, pages, ws_progress
from app.ui.progress_bus import get_bus

logger = logging.getLogger(__name__)


def _app_url() -> str:
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = os.environ.get("APP_PORT") or os.environ.get("PORT") or "8005"
    return f"http://{host}:{port}/"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bus = get_bus()
    settings = get_settings()
    # Redis는 멀티 프로세스/워커 환경에서 진행도 pub/sub 동기화용 — single process엔 불필요.
    # redis_url이 명시되어 있을 때만 시도 (기본값이면 in-memory queue만 사용).
    if settings.redis_url and not settings.redis_url.startswith("redis://localhost"):
        await bus.attach_redis(settings.redis_url)
    # Chromium 자동 spawn 제거 — 사용자는 일반 Chrome으로 http://127.0.0.1:8005 열기.
    # 백그라운드 크롤 작업 시작 시점에 BrowserPool.ensure_browser()가 자동 spawn.
    # 작업 끝나면 idle 타이머가 자동 종료 (사용자 탭과 process 완전 분리).
    logger.info("Chromium 자동 spawn 비활성화 — 일반 Chrome으로 %s 접속하세요", _app_url())
    try:
        yield
    finally:
        await bus.stop()
        await shutdown_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="WHOAREYOU",
        description="범용 크롤러 + 채용/기업 조사 시스템",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")
    app.include_router(pages.router)
    app.include_router(api_jobs.router)
    app.include_router(api_companies.router)
    app.include_router(api_dashboard.router)
    app.include_router(api_resume.router)
    app.include_router(api_ollama.router)
    app.include_router(ws_progress.router)
    return app


app = create_app()
