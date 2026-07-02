# -*- coding: utf-8 -*-
"""WHOAREYOU 2.0 엔트리 — 트레이 상주 + 네이티브 창 + 하루 1회 수집 + 수동 트리거/검색.

- pywebview 창(js_api=Api) : 표준 프레임(가짜 윈도우 컨트롤 없음).
- pystray 트레이 상주 : 창 [X]=숨김, 종료는 트레이 '종료'만.
- APScheduler : 하루 1회(config.DAILY_TIME) 자동 수집. 트레이/UI에서 수동도 가능.
- 실행: .venv\\Scripts\\python.exe run.py
"""
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "app"))     # app/ 모듈 flat import

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WHOAREYOU.desktop")
    except Exception:
        pass

import pystray
import webview
from PIL import Image

import config
from api import Api
from scheduler import Scheduler

INDEX = str(HERE / "web" / "index.html")
ICON_ICO = str(HERE / "web" / "assets" / "icon.ico")
ICON_PNG = str(HERE / "web" / "assets" / "icon.png")

_window = None
_api = None
_quitting = False


def _show(icon=None, item=None):
    if _window is not None:
        _window.show()
        _js("window.pyShown && window.pyShown()")   # 숨겨진 사이 갱신됐을 수 있음 → 열 때 최신화


def _js(code: str) -> None:
    """열린 창에 JS 푸시 — 백그라운드 스레드(APScheduler·트레이)에서 호출해도 안전. 창 없으면 무시."""
    try:
        if _window is not None:
            _window.evaluate_js(code)
    except Exception:
        pass


def _collect_and_refresh():
    """스케줄·트레이 공용 수집 — 끝나면 열린 창 목록도 갱신.
    (자동 수집이 카톡만 보내고 화면은 옛 결과로 남던 문제 수정 — UI는 부팅/수동시만 렌더했음)"""
    _js("window.pyAutoStart && window.pyAutoStart()")
    try:
        res = _api._collect()
    except Exception:
        res = None
    if res and res.get("busy"):
        return          # 수동 수집이 진행 중 — 그쪽 응답이 렌더하므로 여기선 침묵
    _js("window.pyAutoDone && window.pyAutoDone()")


def _on_closing():
    if _quitting:
        return True
    if _window is not None:
        _window.hide()      # [X] → 트레이로 숨김(상주)
    return False


def _collect_now(icon=None, item=None):
    _show()
    threading.Thread(target=_collect_and_refresh, daemon=True).start()   # 수동 수집(끝나면 창 갱신)


def _quit(icon, item):
    global _quitting
    _quitting = True
    icon.stop()
    if _window is not None:
        _window.destroy()


def _run_tray():
    menu = pystray.Menu(
        pystray.MenuItem("열기", _show, default=True),
        pystray.MenuItem("지금 한 번 검색", _collect_now),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", _quit),
    )
    pystray.Icon("WHOAREYOU", Image.open(ICON_PNG), "WHOAREYOU — 상주 중", menu).run()


def main() -> None:
    global _window, _api
    _api = Api()
    _window = webview.create_window("WHOAREYOU", INDEX, js_api=_api,
                                    width=740, height=940, min_size=(560, 600))
    _window.events.closing += _on_closing

    scheduler = Scheduler(_collect_and_refresh,
                          interval_min=_api.user.data.get("schedule_interval", 1440))
    scheduler.start()
    _api.scheduler = scheduler          # 설정에서 주기 변경 시 재스케줄용

    threading.Thread(target=_run_tray, daemon=True).start()
    webview.start(icon=ICON_ICO)


if __name__ == "__main__":
    main()
