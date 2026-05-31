from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models import UserSetting, utcnow

SETTING_KEYS = (
    "home_address",
    "home_road_address",
    "home_lat",
    "home_lng",
    "kakao_rest_key",
    "kakao_js_key",
    "odsay_key",
    "odsay_referer",  # ODsay LAB에 등록한 API URI (도메인 검증용)
    "dart_api_key",
    "ollama_text_model",
    "ollama_vision_model",
    "max_concurrent_crawls",
    "chrome_cdp_url",     # 외부 Chrome attach용 (비어 있으면 앱이 자동 실행)
    "browser_show",       # "true"면 자동 Chromium 창 표시 (기본 true)
)


async def get_all(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(UserSetting))).scalars().all()
    return {row.key: row.value for row in rows}


async def get_value(session: AsyncSession, key: str, default: str = "") -> str:
    row = await session.get(UserSetting, key)
    return row.value if row else default


async def upsert(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(UserSetting, key)
    if row is None:
        session.add(UserSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()


async def upsert_many(session: AsyncSession, items: dict[str, str]) -> None:
    for k, v in items.items():
        await upsert(session, k, v)
    await session.commit()
