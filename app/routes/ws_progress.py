"""WebSocket 엔드포인트 — 작업 진행도 스트리밍."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ui.progress_bus import get_bus

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/progress/{task_id}")
async def progress_socket(ws: WebSocket, task_id: str) -> None:
    await ws.accept()
    bus = get_bus()
    try:
        async for event in bus.subscribe(task_id):
            try:
                await ws.send_text(event.to_json())
            except (WebSocketDisconnect, RuntimeError):
                # 클라이언트가 닫은 후 발행 시도 — 조용히 종료
                return
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
    finally:
        with contextlib.suppress(Exception):
            await ws.close()
