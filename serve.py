"""WHOAREYOU 서버 entrypoint.

Windows + uvicorn 조합의 고질적 문제 해결:
- Python 3.8+ Windows 기본 이벤트 루프는 ProactorEventLoop (subprocess 지원)
- 그런데 uvicorn의 asyncio_setup이 강제로 SelectorEventLoop로 변경 (subprocess 미지원)
- 결과: Playwright의 Chromium subprocess 실행 시 NotImplementedError

해결: uvicorn 내부 함수를 monkey-patch로 무력화 + 우리가 직접 Proactor 정책 적용.

사용:
    uv run python serve.py          # 기본 (reload off, Playwright 정상 동작)
    uv run python serve.py --reload # 자동 재로드 (Playwright 기능 비호환, UI 디버깅 전용)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# 1) 우리가 직접 ProactorEventLoop 정책 설정
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# 2) uvicorn의 정책 강제 변경 함수를 no-op으로 monkey-patch
#    (uvicorn import 전에 패치 적용해야 효과)
def _patch_uvicorn_loop_setup() -> None:
    import uvicorn.loops.asyncio as _asyncio_loop

    def _no_override(*args, **kwargs) -> None:
        pass

    _asyncio_loop.asyncio_setup = _no_override


_patch_uvicorn_loop_setup()

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reload",
        action="store_true",
        help="코드 변경 시 자동 재로드 (워커 자식 프로세스 정책 미상속으로 Playwright 비호환)",
    )
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # main.py의 _app_url()/lifespan 로그가 실제 바인딩 포트를 보도록 전파
    os.environ["APP_PORT"] = str(args.port)
    os.environ["APP_HOST"] = args.host

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop="asyncio",
    )


if __name__ == "__main__":
    main()
