import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import ROOT_DIR, get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()
(ROOT_DIR / "data").mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    future=True,
)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# (table_name, column_name, column_def) — create_all로 신규 컬럼은 추가 안 되므로 수동 마이그레이션.
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("company", "transit_json", "TEXT"),
    ("company", "dart_overview_json", "TEXT"),
    ("company", "dart_financials_json", "TEXT"),
    ("company", "emotion_json", "TEXT"),
    ("company", "homepage_summary_json", "TEXT"),
    ("job", "favorite", "BOOLEAN DEFAULT 0"),
    ("job", "application_status", "VARCHAR DEFAULT 'none'"),
    ("job", "status_updated_at", "TIMESTAMP"),
    ("job", "status_note", "TEXT"),
    ("resume", "photo_file_id", "INTEGER"),
    ("resume", "sections_json", "TEXT"),
    ("resume", "title", "VARCHAR DEFAULT '기본 이력서'"),
    ("job", "jd_md", "TEXT"),
    ("job", "jd_fetched_at", "TIMESTAMP"),
    ("job", "jd_error", "TEXT"),
    ("job", "ats_keywords_json", "TEXT"),
    ("job", "ats_match_json", "TEXT"),
    ("job", "ats_match_resume_hash", "TEXT"),
    ("company", "domain_confidence", "INTEGER"),
    ("company", "domain_source", "VARCHAR"),
    ("job", "cover_letter_json", "TEXT"),
    ("job", "cover_letter_at", "TIMESTAMP"),
]


async def _apply_column_migrations(conn) -> None:
    for table, column, coldef in _MIGRATIONS:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        if column not in existing:
            logger.info("DB migration: %s.%s 추가", table, column)
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}"))


# ────────────────── 1회성 데이터 마이그레이션 ──────────────────
# (id, description, async_callable) — 적용된 id는 _schema_migrations 테이블에 기록되어 재실행 안 됨.
# 컬럼 추가가 아닌 "데이터 정리"가 필요할 때 여기 추가.

async def _data_mig_001_clear_bad_domains(conn) -> None:
    """잘못 잡힌 도메인 (정보집계 사이트 등) + 그 요약 NULL 처리.

    nicebizinfo / thevc / googletagmanager / wantedlab / fnguide 등 NEVER 리스트에 있는
    호스트가 Company.domain에 박혀있으면, 다음 조사에서 새 로직으로 다시 잡을 수 있도록
    NULL 처리. homepage_summary_json도 함께 비움 (잘못된 회사 정보였을 가능성).
    """
    bad_patterns = [
        "nicebizinfo", "thevc.kr", "thevc.co.kr", "fnguide.com",
        "googletagmanager", "wantedlab.com", "innoforest", "creditok",
        "dnb.com",
    ]
    where_clause = " OR ".join([f"domain LIKE :{f'p{i}'}" for i in range(len(bad_patterns))])
    params = {f"p{i}": f"%{p}%" for i, p in enumerate(bad_patterns)}
    rows = await conn.execute(
        text(f"SELECT id, name, domain FROM company WHERE {where_clause}"),
        params,
    )
    affected = rows.fetchall()
    if affected:
        ids = [r[0] for r in affected]
        for r in affected:
            logger.info("data_mig_001: bad domain 정리 id=%s name=%s domain=%s", r[0], r[1], r[2])
        placeholders = ",".join(str(i) for i in ids)
        await conn.execute(
            text(f"UPDATE company SET domain=NULL, homepage_summary_json=NULL WHERE id IN ({placeholders})")
        )


async def _data_mig_002_drop_bizinfo_setting(conn) -> None:
    """폐기된 bizinfo_key UserSetting row 삭제."""
    await conn.execute(text("DELETE FROM usersetting WHERE key='bizinfo_key'"))
    # apikeystatus도 함께 (혹시 record_ok로 남았을 경우)
    await conn.execute(text("DELETE FROM apikeystatus WHERE key_code='bizinfo'"))


_DATA_MIGRATIONS: list[tuple[str, str, Any]] = [
    ("001_clear_bad_domains", "잘못 잡힌 정보집계 사이트 도메인 정리", _data_mig_001_clear_bad_domains),
    ("002_drop_bizinfo_setting", "폐기된 bizinfo_key UserSetting/ApiKeyStatus 정리", _data_mig_002_drop_bizinfo_setting),
]


async def _apply_data_migrations(conn) -> None:
    # 적용 이력 테이블 (없으면 생성)
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS _schema_migrations ("
            "id VARCHAR PRIMARY KEY, "
            "description TEXT, "
            "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    rows = await conn.execute(text("SELECT id FROM _schema_migrations"))
    applied = {r[0] for r in rows.fetchall()}

    for mig_id, desc, fn in _DATA_MIGRATIONS:
        if mig_id in applied:
            continue
        logger.info("Data migration apply: %s (%s)", mig_id, desc)
        try:
            await fn(conn)
            await conn.execute(
                text("INSERT INTO _schema_migrations (id, description) VALUES (:id, :d)"),
                {"id": mig_id, "d": desc},
            )
        except Exception:
            logger.exception("Data migration 실패: %s", mig_id)
            raise


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        # SQLite WAL 모드 — writer/reader 동시 가능 (기본 rollback journal은 writer 1개)
        # + busy_timeout으로 lock 발생 시 5초 대기 후 retry → "database is locked" 에러 거의 사라짐
        if _settings.database_url.startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=5000"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))  # WAL과 함께 쓸 때 안전 + 빠름
        await conn.run_sync(SQLModel.metadata.create_all)
        await _apply_column_migrations(conn)
        await _apply_data_migrations(conn)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session
