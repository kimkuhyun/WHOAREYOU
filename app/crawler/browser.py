"""Playwright + CDP 단일 모드.

앱 시작 시:
1. Playwright 번들 Chromium 바이너리를 `--remote-debugging-port=<port>`로 subprocess 실행
2. 별도 user_data_dir(`data/chrome-profile`)을 사용 — 사용자의 평소 Chrome과 충돌 없음
3. 포트 listening 확인 후 `connect_over_cdp`로 attach
4. 모든 크롤 작업(잡 검색·회사 조사·JD 추출)은 이 단일 Chrome 인스턴스의 새 탭을 공유

설정:
- `browser_show` ("true"/"false") — 창 표시 여부 (기본 true, 사용자가 보고 있을 수 있음)
- `chrome_cdp_url` — 외부 Chrome에 attach하고 싶을 때 (예: 사용자가 평소 쓰는 Chrome). 지정 시
   subprocess는 안 띄우고 그 URL로 바로 attach. 비어 있으면 자동 실행.

서버 종료 시 subprocess도 같이 terminate.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

LAUNCH_ARGS_BASE = [
    # 주의: `--disable-blink-features=AutomationControlled` 같은 자동화 플래그는
    # 최신 Chrome이 InfoBar로 "지원되지 않는 플래그" 경고를 띄우므로 제거.
    # navigator.webdriver 숨김은 BrowserContext init_script로 처리.
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=IsolateOrigins,site-per-process,InterestFeedContentSuggestions",
    # 자동화 InfoBar / 시그니처 알림 숨김
    "--disable-infobars",
]

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


@dataclass
class BrowserPoolConfig:
    user_agent: str = DEFAULT_USER_AGENT
    viewport_width: int = 1366
    viewport_height: int = 900


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _wait_for_port(host: str, port: int, timeout_s: float = 12.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if _port_open(host, port):
            return True
        await asyncio.sleep(0.25)
    return False


class BrowserPool:
    # idle 자동 종료 — 마지막 사용 후 N초 이상 idle이면 Chromium subprocess kill
    # (사용자가 보는 일반 Chrome에 영향 안 주려고 우리 Chrome은 작업 시만 살아있게)
    IDLE_SHUTDOWN_SECONDS = 300   # 5분
    IDLE_WATCH_INTERVAL = 30      # 30초마다 체크

    def __init__(self, config: BrowserPoolConfig | None = None) -> None:
        self.config = config or BrowserPoolConfig()
        self._playwright = None
        self._browser: Browser | None = None
        self._cdp_url: str | None = None
        self._proc: subprocess.Popen | None = None  # 우리가 띄운 Chrome (외부 attach면 None)
        self._work_ctx: BrowserContext | None = None  # 작업용 컨텍스트 (메인과 동일)
        self._main_page = None                        # 사용자가 보고 있는 우리 앱 탭 (open_in_first_tab에서 보관)
        self._lock = asyncio.Lock()
        self._new_page_lock = asyncio.Lock()          # background 탭 생성 race 방지
        # idle 추적 — 마지막 활동(context 진입/종료) 시각, 와처 task
        self._last_activity_at: float = 0.0
        self._idle_watcher_task: asyncio.Task | None = None
        self._active_contexts: int = 0  # 현재 사용 중인 context 수 (>0이면 절대 close X)

    async def _load_settings(self) -> dict[str, str]:
        try:
            from app.db import async_session_maker
            from app.ui import settings_store
            async with async_session_maker() as session:
                return await settings_store.get_all(session)
        except Exception:
            return {}

    def _resolve_chromium_executable(self) -> str | None:
        """Playwright 번들 Chromium 경로."""
        assert self._playwright is not None
        try:
            path = self._playwright.chromium.executable_path
            return path
        except Exception:
            return None

    async def _spawn_chrome(self, *, port: int, headless: bool, minimize: bool = True) -> bool:
        """Playwright 번들 Chromium을 디버그 포트로 실행."""
        from app.config import ROOT_DIR

        # 이미 떠 있으면 재사용
        if _port_open("127.0.0.1", port):
            logger.info("CDP 포트 %d 이미 열려있음 — 기존 인스턴스 재사용", port)
            return True

        exe = self._resolve_chromium_executable()
        if not exe:
            logger.error("Playwright Chromium 바이너리를 찾을 수 없습니다. 'uv run playwright install chromium' 실행 필요")
            return False

        user_data_dir = ROOT_DIR / "data" / "chrome-profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            *LAUNCH_ARGS_BASE,
        ]
        # 크롤 전용 Chrome — visible로 띄움 (headless는 사람인/잡코리아 anti-bot에 차단됨)
        # 최소화는 STARTUPINFO.SW_SHOWMINNOACTIVE로 처리 (창은 작업 표시줄에만 표시)
        # window-position을 화면 밖으로 두면 일부 anti-bot이 screenX 음수로 의심하므로 사용 안 함.
        if headless:
            args.append("--headless=new")
        args.append("--window-size=1280,800")

        creationflags = 0
        startupinfo = None
        if sys.platform == "win32":
            # 콘솔 분리 (별도 창 없이 백그라운드)
            creationflags = 0x00000008  # DETACHED_PROCESS
            if minimize:
                # 크롤용 — 최소화 + 활성화 안 함 (포커스 안 빼앗김)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
            # minimize=False면 normal show (로그인용)
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=startupinfo,
                close_fds=True,
            )
            logger.info("Chromium subprocess 실행 (pid=%s, headless=%s, minimized=%s)",
                        self._proc.pid, headless, sys.platform == "win32")
        except Exception as exc:
            logger.exception("Chromium subprocess 실행 실패: %s", exc)
            return False

        ok = await _wait_for_port("127.0.0.1", port, timeout_s=15.0)
        if not ok:
            logger.error("CDP 포트 %d listen 대기 타임아웃", port)
            return False
        logger.info("CDP 포트 %d 준비 완료", port)
        return True

    async def _attach(self, url: str) -> bool:
        try:
            assert self._playwright is not None
            self._browser = await self._playwright.chromium.connect_over_cdp(url)
            self._cdp_url = url
            return True
        except Exception as exc:
            logger.exception("CDP attach 실패 (%s): %s", url, exc)
            return False

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            cfg = await self._load_settings()
            external = (cfg.get("chrome_cdp_url") or "").strip()
            # visible 강제 — headless는 사람인/잡코리아 anti-bot에 차단됨.
            # 대신 spawn 시 창을 최소화해서 사용자 화면 방해 안 함 (_spawn_chrome 안 처리).
            show = True

            if external:
                # 외부 Chrome에 attach (사용자가 직접 띄운 경우)
                if not await self._attach(external):
                    raise RuntimeError(f"외부 Chrome에 attach 실패: {external}")
                logger.info("외부 Chrome CDP attach: %s", external)
                return

            # 우리가 직접 띄움
            port = DEFAULT_CDP_PORT
            ok = await self._spawn_chrome(port=port, headless=not show)
            if not ok:
                raise RuntimeError(
                    "Chromium 자동 실행 실패. 'uv run playwright install chromium'으로 브라우저 설치를 확인하세요."
                )
            url = f"http://127.0.0.1:{port}"
            if not await self._attach(url):
                raise RuntimeError(f"자동 실행한 Chromium에 attach 실패: {url}")

            # idle watcher 시작 — 일정 시간 idle이면 자동 종료
            self._touch_activity()
            if self._idle_watcher_task is None or self._idle_watcher_task.done():
                self._idle_watcher_task = asyncio.create_task(self._idle_watcher())

    async def stop(self) -> None:
        # idle watcher 취소
        if self._idle_watcher_task is not None and not self._idle_watcher_task.done():
            self._idle_watcher_task.cancel()
            try:
                await self._idle_watcher_task
            except (asyncio.CancelledError, Exception):
                pass
        self._idle_watcher_task = None

        async with self._lock:
            if self._work_ctx is not None:
                try:
                    await self._work_ctx.close()
                except Exception:
                    pass
                self._work_ctx = None
                self._work_anchor_page = None
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
                self._cdp_url = None
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                except Exception:
                    pass
                self._proc = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    async def refresh(self) -> str:
        """설정이 바뀐 뒤 재시작. 활성 모드명 반환."""
        async with self._lock:
            # 기존 모두 정리
            if self._work_ctx is not None:
                try: await self._work_ctx.close()
                except Exception: pass
                self._work_ctx = None
                self._work_anchor_page = None
            if self._browser is not None:
                try: await self._browser.close()
                except Exception: pass
                self._browser = None
                self._cdp_url = None
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except Exception:
                    try: self._proc.kill()
                    except Exception: pass
                self._proc = None
        await self.start()
        return "cdp"

    @property
    def mode(self) -> str:
        return "cdp" if self._browser is not None else "uninitialized"

    @property
    def cdp_url(self) -> str | None:
        return self._cdp_url

    async def open_for_login(self, login_url: str | None = None) -> dict[str, Any]:
        """사용자가 직접 로그인할 수 있도록 Chrome을 visible(정상 창)로 띄움.

        - 기존 크롤용 Chrome(최소화)이 떠있으면 stop() 후 새로 spawn (normal show)
        - 로그인 후 사용자가 창 닫거나, 다음 크롤 시작 시 idle 타이머가 알아서 정리
        - 쿠키/세션은 user-data-dir에 보존됨 → 이후 크롤 시 자동 활용

        Args:
            login_url: 띄울 URL (예: "https://google.com"). 없으면 about:blank.
        """
        # 기존 chrome 종료 (있다면)
        if self._proc is not None or self._browser is not None:
            await self.stop()

        # playwright 초기화 (start()는 minimize spawn이라 안 씀)
        if self._playwright is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()

        port = DEFAULT_CDP_PORT
        ok = await self._spawn_chrome(port=port, headless=False, minimize=False)
        if not ok:
            return {"ok": False, "error": "Chrome spawn 실패"}

        url = f"http://127.0.0.1:{port}"
        if not await self._attach(url):
            return {"ok": False, "error": f"CDP attach 실패: {url}"}

        # 로그인 페이지로 이동
        try:
            contexts = self._browser.contexts
            ctx = contexts[0] if contexts else await self._browser.new_context()
            pages = ctx.pages
            page = pages[0] if pages else await ctx.new_page()
            if login_url:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=20_000)
            try: await page.bring_to_front()
            except Exception: pass
        except Exception as exc:
            logger.warning("로그인 페이지 이동 실패: %s", exc)

        # idle watcher 시작
        self._touch_activity()
        if self._idle_watcher_task is None or self._idle_watcher_task.done():
            self._idle_watcher_task = asyncio.create_task(self._idle_watcher())
        return {"ok": True, "cdp_url": url, "login_url": login_url}

    async def clear_profile(self) -> dict[str, Any]:
        """크롤용 Chrome의 쿠키/세션/캐시 전부 정리 (user-data-dir 삭제).

        Chrome 종료 → user-data-dir 디렉터리 통째로 삭제.
        다음 spawn 시 fresh 상태에서 시작.
        """
        import shutil
        from app.config import ROOT_DIR
        # 우선 chrome 종료
        await self.stop()

        user_data_dir = ROOT_DIR / "data" / "chrome-profile"
        if not user_data_dir.exists():
            return {"ok": True, "deleted": False, "message": "프로필 디렉터리가 없음 (이미 깨끗함)"}

        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
            # 빈 디렉터리 다시 만듦 (다음 spawn 대비)
            user_data_dir.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "deleted": True, "path": str(user_data_dir)}
        except Exception as exc:
            logger.exception("프로필 정리 실패")
            return {"ok": False, "error": str(exc)}

    async def open_in_first_tab(self, url: str, *, bring_to_front: bool = True) -> bool:
        """자동 실행된 Chromium의 첫 탭에 주어진 URL을 띄우고, 그 page를 main_page로 보관.

        main_page는 이후 작업 탭이 새로 생성될 때 사용자 포커스를 복귀시킬 기준.
        """
        if self._browser is None:
            return False
        try:
            contexts = self._browser.contexts
            ctx = contexts[0] if contexts else None
            if ctx is None:
                ctx = await self._browser.new_context(
                    viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                )
            pages = ctx.pages
            page = pages[0] if pages else await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except Exception:
                await asyncio.sleep(1.0)
                await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            if bring_to_front:
                try: await page.bring_to_front()
                except Exception: pass
            self._main_page = page  # 사용자 앱 탭 — 작업 탭 생성 후 포커스 복귀에 사용
            return True
        except Exception as exc:
            logger.warning("open_in_first_tab 실패 (%s): %s", url, exc)
            return False

    async def _ensure_work_context(self) -> BrowserContext:
        """작업 컨텍스트 = 사용자 메인 컨텍스트(앱이 떠있는 그 windows) 재사용.

        - 별도 windows 안 띄움 → 사용자 화면에 새 창이 깜빡이는 일 없음
        - 작업 탭은 같은 windows 안에 새 탭으로 추가됨 (Playwright의 new_page는 자동 활성화 X)
        - 작업 후 그 탭만 close → 사용자가 보던 앱 탭은 그대로 유지
        - 쿠키/세션도 메인 컨텍스트와 공유 (사용자가 사람인에 한 번 로그인했으면 그대로 활용)
        """
        if self._work_ctx is not None:
            return self._work_ctx
        assert self._browser is not None
        contexts = self._browser.contexts
        if contexts:
            # 메인 컨텍스트 재사용 (앱이 떠 있는 그 windows의 컨텍스트)
            ctx = contexts[0]
        else:
            # 컨텍스트가 비어있는 드문 경우 — 새로 만듦
            ctx = await self._browser.new_context(
                viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                user_agent=self.config.user_agent,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                java_script_enabled=True,
            )
        try:
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
        except Exception:
            pass
        self._work_ctx = ctx
        return ctx

    def _touch_activity(self) -> None:
        """마지막 활동 시각 갱신 — idle watcher가 이 값 기준으로 종료 결정."""
        import time as _time
        self._last_activity_at = _time.monotonic()

    async def _idle_watcher(self) -> None:
        """주기적으로 idle 시간 체크 — IDLE_SHUTDOWN_SECONDS 초과 + 사용 중 context 0개면 stop()."""
        import time as _time
        try:
            while self._browser is not None:
                await asyncio.sleep(self.IDLE_WATCH_INTERVAL)
                if self._browser is None:
                    return
                if self._active_contexts > 0:
                    continue  # 작업 중이면 절대 close 안 함
                idle = _time.monotonic() - self._last_activity_at
                if idle >= self.IDLE_SHUTDOWN_SECONDS:
                    logger.info(
                        "Chromium idle %.0fs ≥ %ds → 자동 종료 (다음 작업 시 재시작)",
                        idle, self.IDLE_SHUTDOWN_SECONDS,
                    )
                    try:
                        await self.stop()
                    except Exception as exc:
                        logger.warning("idle 자동 종료 실패: %s", exc)
                    return
        except asyncio.CancelledError:
            pass

    @asynccontextmanager
    async def context(self, *, block_resources: bool = True):
        """크롤 작업 컨텍스트.

        사용자 메인 windows의 같은 BrowserContext를 공유. 각 작업은 CDP
        `Target.createTarget(background:true)`로 백그라운드 탭으로 열리고, 종료 시
        그 탭만 닫힘. 사용자 화면(현재 활성 탭)은 절대 뺏기지 않음.

        idle 추적: 진입 시 active 카운터 증가 + 활동 시각 갱신, 종료 시 카운터 감소.
        모든 context 종료 후 IDLE_SHUTDOWN_SECONDS 지나면 watcher가 자동 종료.
        """
        if self._browser is None:
            await self.start()
        self._active_contexts += 1
        self._touch_activity()
        work_ctx = await self._ensure_work_context()
        try:
            yield _SharedContextWrapper(
                work_ctx, block_resources=block_resources,
                main_page=self._main_page,
                new_page_lock=self._new_page_lock,
            )
        finally:
            self._active_contexts = max(0, self._active_contexts - 1)
            self._touch_activity()

    @asynccontextmanager
    async def page(self, *, block_resources: bool = True):
        async with self.context(block_resources=block_resources) as ctx:
            page: Page = await ctx.new_page()
            try:
                yield page
            finally:
                try: await page.close()
                except Exception: pass


class _SharedContextWrapper:
    """공유 BrowserContext에 백그라운드 탭만 열고 닫는다. ctx 자체는 안 닫음.

    new_page()는 CDP `Target.createTarget(background:true)`로 비활성 탭 생성 →
    사용자가 보고 있는 탭의 포커스를 절대 뺏지 않는다.
    """

    def __init__(
        self,
        ctx: BrowserContext,
        *,
        block_resources: bool,
        main_page=None,
        new_page_lock: asyncio.Lock | None = None,
    ) -> None:
        self._ctx = ctx
        self._block_resources = block_resources
        self._main_page = main_page
        self._new_page_lock = new_page_lock or asyncio.Lock()

    async def _create_background_page(self) -> Page:
        """CDP Target.createTarget(background:true)로 백그라운드 탭 생성.

        ctx.on('page', ...) 이벤트로 새로 생성된 Page 객체를 잡는다.
        동시 호출 race 방지를 위해 lock으로 직렬화.
        """
        # CDP session을 만들려면 기존 page가 필요. 우선 main_page, 없으면 첫 page.
        anchor_page = self._main_page
        if anchor_page is None or anchor_page.is_closed():
            pages = [p for p in self._ctx.pages if not p.is_closed()]
            anchor_page = pages[0] if pages else None
        if anchor_page is None:
            # 어떤 page도 없음 — 평범한 new_page (활성화될 가능성 있음)
            return await self._ctx.new_page()

        async with self._new_page_lock:
            loop = asyncio.get_event_loop()
            fut: asyncio.Future = loop.create_future()

            def on_page(p: Page) -> None:
                if not fut.done():
                    fut.set_result(p)

            self._ctx.on("page", on_page)
            session = None
            try:
                session = await self._ctx.new_cdp_session(anchor_page)
                await session.send("Target.createTarget", {
                    "url": "about:blank",
                    "background": True,
                })
                page = await asyncio.wait_for(fut, timeout=8.0)
                return page
            except Exception as exc:
                logger.warning("background target 생성 실패 (%s) — 일반 new_page fallback", exc)
                # fallback: 일반 new_page 후 main으로 포커스 복귀
                page = await self._ctx.new_page()
                if anchor_page is not None and not anchor_page.is_closed():
                    try: await anchor_page.bring_to_front()
                    except Exception: pass
                return page
            finally:
                try: self._ctx.remove_listener("page", on_page)
                except Exception: pass
                if session is not None:
                    try: await session.detach()
                    except Exception: pass

    async def new_page(self) -> Page:
        page = await self._create_background_page()
        if self._block_resources:
            async def _route_handler(route, request):
                if request.resource_type in BLOCKED_RESOURCE_TYPES:
                    await route.abort()
                else:
                    await route.continue_()
            try:
                await page.route("**/*", _route_handler)
            except Exception:
                pass
        return page

    async def add_init_script(self, *a, **kw):
        return await self._ctx.add_init_script(*a, **kw)

    async def route(self, *a, **kw):
        return await self._ctx.route(*a, **kw)


_pool: BrowserPool | None = None


def get_pool() -> BrowserPool:
    global _pool
    if _pool is None:
        _pool = BrowserPool()
    return _pool


async def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.stop()
        _pool = None
