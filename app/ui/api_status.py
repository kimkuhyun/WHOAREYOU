"""외부 API 호출 결과 추적.

각 API 호출 지점에서 record_ok/record_error를 호출하면
ApiKeyStatus 테이블에 마지막 결과가 캐시되고, 설정 페이지/대시보드에서 조회할 수 있다.

DB 실패가 본 호출을 막아서는 안 되므로 모든 함수는 예외를 삼킨다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from app.db import async_session_maker
from app.models import ApiKeyStatus

logger = logging.getLogger(__name__)

# 표시용 라벨 (UI에서 사용)
KEY_LABELS: dict[str, str] = {
    "kakao_rest": "Kakao REST",
    "kakao_js": "Kakao JS",
    "odsay": "ODsay",
    "dart": "DART",
    "ollama": "Ollama",
}

KEY_CODES: tuple[str, ...] = tuple(KEY_LABELS.keys())


async def _upsert(key_code: str, ok: bool, message: str | None) -> None:
    try:
        async with async_session_maker() as session:
            row = await session.get(ApiKeyStatus, key_code)
            now = datetime.now(timezone.utc)
            if row is None:
                row = ApiKeyStatus(
                    key_code=key_code,
                    ok=ok,
                    message=message,
                    last_check_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.ok = ok
                row.message = message
                row.last_check_at = now
                row.updated_at = now
            await session.commit()
    except Exception as exc:
        # DB lock 등 일시적 에러는 traceback 없이 한 줄로 (API 호출 자체엔 영향 없음)
        msg = str(exc)
        if "database is locked" in msg or "OperationalError" in type(exc).__name__:
            logger.warning("ApiKeyStatus upsert skip (%s): DB busy — 상태 기록 생략", key_code)
        else:
            logger.exception("ApiKeyStatus upsert 실패: %s", key_code)


async def record_ok(key_code: str, message: str | None = None) -> None:
    await _upsert(key_code, True, message)


async def record_error(key_code: str, message: str) -> None:
    # 메시지 너무 길면 자름
    msg = (message or "").strip()
    if len(msg) > 500:
        msg = msg[:497] + "..."
    await _upsert(key_code, False, msg or "(상세 정보 없음)")


async def get_all() -> dict[str, dict]:
    """모든 키 상태를 dict[key_code -> {ok, message, last_check_at, label}] 형태로."""
    out: dict[str, dict] = {}
    try:
        async with async_session_maker() as session:
            rows = (await session.execute(select(ApiKeyStatus))).scalars().all()
            by_code = {r.key_code: r for r in rows}
    except Exception:
        logger.exception("ApiKeyStatus 조회 실패")
        by_code = {}

    for code in KEY_CODES:
        row = by_code.get(code)
        out[code] = {
            "code": code,
            "label": KEY_LABELS[code],
            "ok": bool(row.ok) if row else False,
            "checked": row is not None,
            "message": row.message if row else None,
            "last_check_at": row.last_check_at.isoformat() if row and row.last_check_at else None,
        }
    return out


async def get_codes(codes: Iterable[str]) -> dict[str, dict]:
    all_ = await get_all()
    return {c: all_[c] for c in codes if c in all_}
