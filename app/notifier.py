# -*- coding: utf-8 -*-
"""알림 = Windows 토스트(winotify). 토스트 클릭/트레이 더블클릭 → 메인 창 추천 탭(§9·§12)."""
from winotify import Notification, audio

import config


class Notifier:
    APP_ID = "WHOAREYOU"
    ICON = str((config.PROJECT / "web" / "assets" / "icon.png").resolve())
    LAUNCH = "http://127.0.0.1:8005/recommend"   # 클릭 시 메인 창 추천 탭

    def notify(self, count: int, avg: int, nearest_min: int, top: str) -> None:
        toast = Notification(
            app_id=self.APP_ID,
            title=f"오늘의 추천 {count}건",
            msg=f"평균 적합 {avg}% · 가장 가까운 곳 {nearest_min}분 — {top} 외 {max(count-1,0)}곳",
            icon=self.ICON,
        )
        toast.set_audio(audio.Default, loop=False)
        toast.add_actions(label="추천 보기", launch=self.LAUNCH)
        toast.show()
