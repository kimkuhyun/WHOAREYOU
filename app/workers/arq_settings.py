"""Arq 워커 설정. Redis가 가동 중일 때 `uv run arq app.workers.arq_settings.WorkerSettings`로 실행.

Phase 2에서는 정의만 준비. Phase 3 파이프라인부터 본격 사용.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.config import get_settings
from app.workers import tasks as task_module


def _redis_settings_from_url(url: str) -> RedisSettings:
    return RedisSettings.from_dsn(url)


class WorkerSettings:
    functions = [
        task_module.crawl_url_task,
    ]

    @staticmethod
    def redis_settings() -> RedisSettings:  # arq calls this if attr is callable
        return _redis_settings_from_url(get_settings().redis_url)

    on_startup = task_module.on_startup
    on_shutdown = task_module.on_shutdown
    job_timeout = 600
    max_jobs = 4
    keep_result = 3600
