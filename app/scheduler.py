# -*- coding: utf-8 -*-
"""스케줄러 = APScheduler 인터벌로 파이프라인 자동 실행. 주기는 사용자 설정(분)."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

MIN_INTERVAL = 5     # 안전 하한(분) — 너무 잦은 크롤 방지


class Scheduler:
    def __init__(self, job_func, interval_min: int = 1440):
        self.sched = BackgroundScheduler(timezone="Asia/Seoul")
        self.job_func = job_func
        self.interval_min = self._clamp(interval_min)

    @staticmethod
    def _clamp(v) -> int:
        try:
            return max(MIN_INTERVAL, int(v))
        except (TypeError, ValueError):
            return 1440

    def start(self) -> None:
        self._add()
        self.sched.start()

    def _add(self) -> None:
        self.sched.add_job(self.job_func, IntervalTrigger(minutes=self.interval_min),
                           id="pipeline", replace_existing=True)

    def reschedule(self, interval_min) -> int:
        """설정에서 주기 바꾸면 호출 → 즉시 반영. 반환=적용된 분."""
        self.interval_min = self._clamp(interval_min)
        if self.sched.running:
            self._add()
        return self.interval_min

    def jobs(self):
        return self.sched.get_jobs()

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)
