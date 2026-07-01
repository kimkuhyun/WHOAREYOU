# -*- coding: utf-8 -*-
"""저장소 — 추천/상태를 SQLite에 보관(본 공고·지원함·관심없음 dedup, 랭킹 조회)."""
import json
import sqlite3
import threading
import time

import config


class Store:
    def __init__(self):
        self.con = sqlite3.connect(str(config.APP_DATA / "recos.db"), check_same_thread=False)
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS reco("
            "url TEXT PRIMARY KEY, data TEXT, status TEXT DEFAULT 'new', ts REAL)")
        self.con.commit()
        self._lock = threading.Lock()

    def is_handled(self, url: str) -> bool:
        """지원함·관심없음이면 다시 안 보여줌(선스킵)."""
        with self._lock:
            r = self.con.execute("select status from reco where url=?", (url,)).fetchone()
        return bool(r and r[0] in ("applied", "not_interested"))

    def save(self, reco: dict) -> None:
        with self._lock:
            self.con.execute(
                "INSERT INTO reco(url,data,status,ts) VALUES(?,?,'new',?) "
                "ON CONFLICT(url) DO UPDATE SET data=excluded.data, ts=excluded.ts",
                (reco["url"], json.dumps(reco, ensure_ascii=False), time.time()))
            self.con.commit()

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                "select data, status from reco where status!='not_interested' "
                "order by json_extract(data,'$.total') desc limit ?", (limit,)).fetchall()
        out = []
        for data, status in rows:
            d = json.loads(data)
            d["status"] = status
            out.append(d)
        return out

    def set_status(self, url: str, status: str) -> None:
        with self._lock:
            self.con.execute("UPDATE reco SET status=? WHERE url=?", (status, url))
            self.con.commit()

    def clear(self) -> int:
        """추천 전체 삭제(검색 초기화 — 재테스트용). 반환=삭제 건수."""
        with self._lock:
            n = self.con.execute("select count(*) from reco").fetchone()[0]
            self.con.execute("DELETE FROM reco")
            self.con.commit()
        return n

    def prune_new(self, keep_urls) -> int:
        """이번 실행에 없는 'new' 추천 삭제 → 목록이 항상 최신 검색만 반영.
        (지원함·관심없음은 이력이라 보존). 반환=삭제 건수."""
        keep = set(keep_urls or [])
        with self._lock:
            rows = self.con.execute("select url from reco where status='new'").fetchall()
            stale = [u for (u,) in rows if u not in keep]
            for u in stale:
                self.con.execute("delete from reco where url=?", (u,))
            self.con.commit()
        return len(stale)

    def counts(self) -> dict:
        with self._lock:
            total = self.con.execute("select count(*) from reco").fetchone()[0]
            applied = self.con.execute("select count(*) from reco where status='applied'").fetchone()[0]
        return {"total": total, "applied": applied}
