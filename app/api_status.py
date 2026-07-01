# -*- coding: utf-8 -*-
"""API 상태 텔레메트리 no-op 스텁 — v1 app.ui.api_status(SQLAlchemy DB)를 자립 패키지용으로 대체.

kakao/odsay가 record_ok/record_error를 호출하지만, 2.0에선 별도 DB 불필요 → 무동작.
필요 시 store로 헬스 기록하도록 확장.
"""


async def record_ok(key_code: str, message: str | None = None) -> None:
    pass


async def record_error(key_code: str, message: str) -> None:
    pass
