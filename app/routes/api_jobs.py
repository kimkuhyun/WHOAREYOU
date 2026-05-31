"""채용 검색/조회 API + HTMX 부분 갱신."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, delete, desc, func, or_, select

import re

import httpx

from app.analysis.categories import classify_title
from app.companies.pipeline import collect_jobs
from app.config import ROOT_DIR
from app.db import async_session_maker
from app.deps import SessionDep
from app.models import Company, Job
from app.ui.progress_bus import ProgressEvent, get_bus, make_progress_callback


# 지원상태 enum — UI와 DB가 공유하는 단일 출처
APPLICATION_STATUSES: tuple[str, ...] = (
    "none",
    "interested",
    "applied",
    "interview",
    "passed",
    "rejected",
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))
templates.env.globals["classify_title"] = classify_title


# 진행 중인 수집 작업의 마지막 결과 (간단 인메모리)
_collect_results: dict[str, dict[str, Any]] = {}


@router.post("/api/jobs/collect")
async def trigger_collect(
    keyword: str = Form(...),
    max_per_source: int = Form(30),
    career: str = Form(""),
    region: str = Form(""),
    employment: str = Form(""),
    education: str = Form(""),
) -> dict[str, str]:
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="키워드가 필요합니다.")
    from app.crawler.adapters.base import SearchFilters
    filters = SearchFilters(
        career=career.strip(), region=region.strip(),
        employment=employment.strip(), education=education.strip(),
    )
    task_id = get_bus().new_task_id("collect")
    asyncio.create_task(_run_collect(task_id, keyword, max_per_source, filters))
    return {"task_id": task_id, "keyword": keyword}


async def _run_collect(task_id: str, keyword: str, max_per_source: int,
                       filters: "SearchFilters | None" = None) -> None:
    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        stats = await collect_jobs(
            keyword,
            max_per_source=max_per_source,
            progress=progress,
            filters=filters,
        )
        _collect_results[task_id] = asdict(stats)
    except Exception as exc:
        logger.exception("collect_jobs 실패 (%s)", task_id)
        _collect_results[task_id] = {"error": str(exc), "keyword": keyword}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=f"실패: {exc}"))


@router.get("/api/jobs/collect/{task_id}")
async def get_collect_result(task_id: str) -> dict[str, Any]:
    data = _collect_results.get(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail="작업이 아직 진행 중이거나 만료되었습니다.")
    return data


_BACKFILL_HEADERS_DESKTOP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}
_BACKFILL_HEADERS_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


def _backfill_url_for(source: str, job_url: str) -> tuple[str, dict[str, str]]:
    """source별로 fetch에 사용할 URL과 헤더 반환."""
    if source == "saramin":
        # 사람인 데스크탑은 정적 HTML에 도로명 없음 — 모바일 페이지로 변환
        m = re.search(r"rec_idx=(\d+)", job_url)
        if m:
            return (
                f"https://m.saramin.co.kr/job-search/view?rec_idx={m.group(1)}",
                _BACKFILL_HEADERS_MOBILE,
            )
        return job_url, _BACKFILL_HEADERS_DESKTOP
    return job_url, _BACKFILL_HEADERS_DESKTOP

_SIDO = (
    "서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주"
)
# 우선순위 1: HTML data 속성에 들어있는 풀 주소 (예: data-address="서울 송파구 위례성대로 68 (방이동...) 401호")
_PATTERN_DATA_ATTR = re.compile(
    r"(?:주소|address|location)[\"']*\s*[:=][^,\"\n]{0,5}[\"']"
    r"((?:" + _SIDO + r")[^\"']{5,120})[\"']",
    re.IGNORECASE,
)
# 우선순위 2: 시도 + 구/시/군 + 도로명/동 (+ 번지)
_PATTERN_ROAD = re.compile(
    r"((?:" + _SIDO + r")\s*[가-힣A-Za-z0-9]{2,20}(?:구|시|군)"
    r"\s+[가-힣A-Za-z0-9]{2,40}(?:동|로|길|읍|면)"
    r"(?:\s+[0-9\-,]+)?)"
)
# 우선순위 3: 시도 + 구/시/군 (fallback)
_PATTERN_CITY = re.compile(
    r"((?:" + _SIDO + r")\s*[가-힣A-Za-z0-9]{2,20}(?:구|시|군))"
)
_PATTERNS = [_PATTERN_DATA_ATTR, _PATTERN_ROAD, _PATTERN_CITY]


def _extract_location(html: str) -> str:
    for pat in _PATTERNS:
        m = pat.search(html)
        if m:
            loc = (m.group(1) or "").strip()
            # 정리: 다중 공백, 좌우 따옴표/괄호 짤린 것 등
            loc = re.sub(r"\s+", " ", loc)
            # 닫히지 않은 괄호 잘라내기
            if loc.count("(") > loc.count(")"):
                loc = loc.rsplit("(", 1)[0].strip(" ,")
            return loc
    return ""

_backfill_results: dict[str, dict[str, Any]] = {}


@router.post("/api/jobs/backfill-locations")
async def backfill_locations(source: str = "jobkorea", limit: int = 200) -> dict[str, str]:
    """location이 비어있는 잡들의 상세 페이지를 방문해 위치 정보 백필."""
    task_id = get_bus().new_task_id("backfill")
    asyncio.create_task(_run_backfill(task_id, source, limit))
    return {"task_id": task_id}


async def _run_backfill(task_id: str, source: str, limit: int) -> None:
    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        async with async_session_maker() as session:
            from sqlalchemy import func as sa_func, or_

            # 비어있거나 12자 이하(예: "서울 송파구") — 도로명 풀 주소로 업그레이드 시도
            stmt = (
                select(Job)
                .where(Job.source == source)
                .where(
                    or_(
                        Job.location.is_(None),
                        Job.location == "",
                        sa_func.length(Job.location) <= 12,
                    )
                )
                .limit(limit)
            )
            jobs = (await session.execute(stmt)).scalars().all()
            total = len(jobs)
            await progress("start", 5, f"{source} 대상 {total}건 — 백필 시작")

            ok = 0
            fail = 0
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                for i, job in enumerate(jobs, 1):
                    pct = 5 + int(90 * i / max(total, 1))
                    location = ""
                    fetch_url, headers = _backfill_url_for(job.source, job.url)
                    try:
                        r = await client.get(fetch_url, headers=headers)
                        if r.status_code == 200:
                            r.encoding = "utf-8"
                            location = _extract_location(r.text)
                    except Exception:
                        pass

                    if location:
                        job.location = location
                        ok += 1
                    else:
                        fail += 1

                    if i % 5 == 0 or i == total:
                        await progress("backfill", pct, f"{i}/{total} · 성공 {ok} · 실패 {fail}")
                    if i % 10 == 0:
                        await session.commit()
                    await asyncio.sleep(0.2)

            await session.commit()

        _backfill_results[task_id] = {"source": source, "total": total, "ok": ok, "fail": fail}
        await progress("done", 100, f"완료 — 위치 채움 {ok}, 실패 {fail}")
    except Exception as exc:
        logger.exception("backfill 실패 (%s)", task_id)
        _backfill_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))


@router.get("/api/jobs/backfill-locations/{task_id}")
async def get_backfill_result(task_id: str) -> dict[str, Any]:
    data = _backfill_results.get(task_id)
    if data is None:
        raise HTTPException(404, "결과 없음")
    return data


# ─── ATS 매칭 — 트리거 버튼으로만 실행 (자동 계산 제거) ───
_ats_results: dict[str, dict[str, Any]] = {}

# 활성 백그라운드 task 추적 — kind('ats' | 'bulkjd') → task_id 매핑
# 새 task 시작 시 같은 kind에 이미 task가 있으면 취소
_active_tasks: dict[str, dict[str, Any]] = {}  # kind → {task_id, task}


async def _cancel_active_task(kind: str) -> bool:
    entry = _active_tasks.get(kind)
    if not entry:
        return False
    task = entry.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _active_tasks.pop(kind, None)
    return True


def _register_active_task(kind: str, task_id: str, task: asyncio.Task) -> None:
    _active_tasks[kind] = {"task_id": task_id, "task": task}


def _clear_active_task(kind: str, task_id: str) -> None:
    entry = _active_tasks.get(kind)
    if entry and entry.get("task_id") == task_id:
        _active_tasks.pop(kind, None)


@router.get("/api/jobs/active-tasks")
async def list_active_tasks() -> dict[str, Any]:
    """현재 진행 중인 백그라운드 task들 — 페이지 진입/새로고침 시 진행도 표시 복원용."""
    return {
        kind: entry.get("task_id")
        for kind, entry in _active_tasks.items()
    }


@router.post("/api/jobs/cancel-task")
async def cancel_task_endpoint(kind: str) -> dict[str, Any]:
    """현재 진행 중인 백그라운드 task 취소.

    Args:
        kind: 'ats' | 'bulkjd'
    """
    if kind not in ("ats", "bulkjd"):
        raise HTTPException(400, "kind는 'ats' 또는 'bulkjd'여야 합니다")
    cancelled = await _cancel_active_task(kind)
    return {"cancelled": cancelled, "kind": kind}


async def _build_resume_context(session) -> tuple[str, str, bool]:
    """현재 활성 이력서 → (resume_text, resume_hash, has_resume).

    매칭 대상으로 인정되는 것:
      - 표준 이력서(Resume) 행 (작성 + 저장됨), 또는
      - 첨부 PDF/Word/Hwp (ResumeFile, kind='resume')
    둘 중 하나만 있어도 매칭 가능. hash는 sha1 16자리 — 변경 시 자동 invalidate.
    """
    import hashlib
    from app.analysis.resume_text import build_resume_text
    from app.models import Resume, ResumeFile

    resume = (
        await session.execute(
            select(Resume).where(Resume.is_primary == True)  # noqa: E712
            .order_by(desc(Resume.updated_at)).limit(1)
        )
    ).scalars().first()
    att_rows = (
        await session.execute(
            select(ResumeFile.content_md)
            .where(ResumeFile.kind == "resume")
            .where(ResumeFile.content_md.is_not(None))
        )
    ).all()
    att_mds = [r[0] for r in att_rows if r[0]]

    # 표준 이력서 + 첨부 둘 다 없으면 매칭 불가
    if not resume and not att_mds:
        return "", "", False

    if resume:
        text = build_resume_text(resume, attachment_md_list=att_mds)
    else:
        # 첨부 PDF만 있는 케이스 — 첨부 텍스트만으로 매칭
        text = "\n\n---\n\n".join(att_mds)

    if not text or not text.strip():
        return "", "", False
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return text, h, True


@router.get("/api/jobs/ats-stats")
async def get_ats_stats(session: SessionDep) -> dict[str, Any]:
    """ATS 매칭 통계 — 트리거 모달의 사전 확인용.

    반환:
      - total_with_jd: 본문(jd_md) 있는 잡 수 (분석 대상)
      - already_matched: 이미 매칭 결과 캐시 + 현재 이력서 hash와 일치 (skip 가능)
      - pending: 매칭 필요한 잡 수 (= total - matched)
      - has_resume: 이력서 존재 여부
    """
    _resume_text, resume_hash, has_resume = await _build_resume_context(session)

    total_with_jd = (
        await session.execute(
            select(func.count(Job.id))
            .where(Job.jd_md.is_not(None))
            .where(func.length(Job.jd_md) > 0)
        )
    ).scalar_one() or 0

    if has_resume:
        already_matched = (
            await session.execute(
                select(func.count(Job.id))
                .where(Job.jd_md.is_not(None))
                .where(func.length(Job.jd_md) > 0)
                .where(Job.ats_match_json.is_not(None))
                .where(Job.ats_match_resume_hash == resume_hash)
            )
        ).scalar_one() or 0
    else:
        already_matched = 0

    return {
        "has_resume": has_resume,
        "total_with_jd": total_with_jd,
        "already_matched": already_matched,
        "pending": max(0, total_with_jd - already_matched),
    }


@router.post("/api/jobs/run-ats")
async def run_ats_match(mode: str = "new") -> dict[str, str]:
    """ATS 매칭 일괄 실행 (백그라운드)."""
    if mode not in ("new", "all"):
        raise HTTPException(400, "mode는 'new' 또는 'all'이어야 합니다")
    # 이미 진행 중인 ats task 있으면 취소 후 새로 시작
    await _cancel_active_task("ats")
    task_id = get_bus().new_task_id("ats")
    task = asyncio.create_task(_run_ats_match(task_id, mode))
    _register_active_task("ats", task_id, task)
    return {"task_id": task_id, "mode": mode}


async def _run_ats_match(task_id: str, mode: str) -> None:
    """ATS 매칭 백그라운드 실행 — 키워드 추출 + 매칭 점수 계산 + DB 저장.

    asyncio.CancelledError로 중단 가능. 중단 시 DB는 마지막 commit 시점까지 보존.
    """
    import json as _json  # 함수 스코프 — 모듈 상단에 import 없음
    bus = get_bus()
    progress = make_progress_callback(task_id)
    from app.analysis.ats import ats_match, extract_jd_keywords

    try:
        async with async_session_maker() as session:
            resume_text, resume_hash, has_resume = await _build_resume_context(session)
            if not has_resume:
                _ats_results[task_id] = {"error": "이력서가 없습니다. /resume에서 작성 후 다시 시도하세요."}
                await progress("error", 100, "이력서 없음 — 매칭 불가")
                return

            # 대상 잡 — 본문 있는 것
            stmt = select(Job).where(Job.jd_md.is_not(None)).where(func.length(Job.jd_md) > 0)
            if mode == "new":
                # 캐시 없거나 이력서 hash 다른 것만
                stmt = stmt.where(
                    (Job.ats_match_json.is_(None))
                    | (Job.ats_match_resume_hash != resume_hash)
                )
            jobs = (await session.execute(stmt)).scalars().all()
            total = len(jobs)
            await progress("start", 3, f"대상 {total}건 — 매칭 분석 시작 ({mode})")

            if total == 0:
                _ats_results[task_id] = {"mode": mode, "total": 0, "processed": 0, "kw_extracted": 0}
                await progress("done", 100, "처리할 잡 없음")
                return

            processed = 0
            kw_extracted = 0  # 키워드 새로 추출한 잡 수
            kw_empty = 0      # 추출했지만 키워드 0개인 잡 (자격요건 헤더 못 잡음)
            match_errors = 0  # ats_match 호출 자체가 예외 던진 잡
            cancelled = False
            for i, job in enumerate(jobs, 1):
                pct = 3 + int(94 * i / max(total, 1))

                # 1) 키워드 없으면 먼저 추출 (kiwi — 행당 ~50ms)
                kw: dict[str, Any] | None = None
                if job.ats_keywords_json:
                    try:
                        kw = _json.loads(job.ats_keywords_json)
                    except Exception:
                        logger.exception("ats_keywords_json 파싱 실패 (job_id=%s)", job.id)
                        kw = None
                if not kw or not (kw.get("required") or kw.get("preferred")):
                    try:
                        kw = extract_jd_keywords(job.jd_md or "")
                        job.ats_keywords_json = _json.dumps(kw, ensure_ascii=False)
                        kw_extracted += 1
                    except Exception:
                        logger.exception("extract_jd_keywords 실패 (job_id=%s, jd_md_len=%s)",
                                         job.id, len(job.jd_md or ""))
                        kw = None

                # 2) 매칭 계산 + 저장
                if kw and (kw.get("required") or kw.get("preferred")):
                    try:
                        match = ats_match(resume_text, kw)
                        job.ats_match_json = _json.dumps(match, ensure_ascii=False)
                        job.ats_match_resume_hash = resume_hash
                        processed += 1
                    except Exception:
                        match_errors += 1
                        logger.exception("ats_match 호출 실패 (job_id=%s, kw_req=%s, kw_pref=%s)",
                                         job.id,
                                         len((kw or {}).get("required") or []),
                                         len((kw or {}).get("preferred") or []))
                else:
                    # 키워드 자체가 비어있음 (자격요건/우대사항 헤더 못 잡은 공고)
                    kw_empty += 1
                    if i <= 3:  # 첫 3건만 로그 — 패턴 확인용
                        logger.warning(
                            "키워드 비어있음 — job_id=%s, ats_keywords_json=%r, jd_md preview=%r",
                            job.id, (job.ats_keywords_json or "")[:200],
                            (job.jd_md or "")[:150],
                        )

                # 진행도 — 매 잡마다 publish (사용자가 잡별 진행 보고 싶다고 명시)
                await progress(
                    "match", pct,
                    f"{i}/{total} · 매칭 {processed} · 키워드 추출 {kw_extracted} · {(job.title or '')[:30]}"
                )
                if i % 5 == 0:
                    await session.commit()

                # cancel 체크 — asyncio.CancelledError는 자동으로 raise됨 (sleep 안에서)
                try:
                    await asyncio.sleep(0)  # 메인 스레드 양보 + cancel point
                except asyncio.CancelledError:
                    cancelled = True
                    break

            await session.commit()

        _ats_results[task_id] = {
            "mode": mode, "total": total,
            "processed": processed, "kw_extracted": kw_extracted,
            "kw_empty": kw_empty, "match_errors": match_errors,
            "cancelled": cancelled,
        }
        # 진단 메시지 — 매칭 0이면 원인 명시
        diag = ""
        if processed == 0 and total > 0:
            if kw_empty == total:
                diag = " ⚠ 모든 공고에 키워드 없음 (자격요건/우대사항 헤더 인식 실패 — 본문 재추출 필요)"
            elif match_errors > 0:
                diag = f" ⚠ {match_errors}건 매칭 함수 예외 (서버 로그 확인)"
            else:
                diag = f" ⚠ 키워드 비어있는 공고 {kw_empty}건"
        if cancelled:
            await progress("cancelled", 100,
                           f"중단됨 — 매칭 {processed}/{total}, 키워드 추출 {kw_extracted}{diag}")
        else:
            await progress("done", 100,
                           f"완료 — 매칭 {processed}/{total}, 키워드 추출 {kw_extracted}{diag}")
        logger.info(
            "ATS 매칭 task=%s mode=%s total=%d processed=%d kw_extracted=%d kw_empty=%d match_errors=%d",
            task_id, mode, total, processed, kw_extracted, kw_empty, match_errors,
        )
    except asyncio.CancelledError:
        _ats_results[task_id] = {"cancelled": True}
        try:
            await progress("cancelled", 100, "사용자 요청으로 중단됨")
        except Exception:
            pass
        raise
    except Exception as exc:
        logger.exception("ATS 매칭 실패 (%s)", task_id)
        _ats_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))
    finally:
        _clear_active_task("ats", task_id)


@router.get("/api/jobs/ats-task/{task_id}")
async def get_ats_result(task_id: str) -> dict[str, Any]:
    data = _ats_results.get(task_id)
    if data is None:
        raise HTTPException(404, "결과 없음")
    return data


# ─── 본문 일괄 추출 — 트리거 버튼 ───
_bulk_jd_results: dict[str, dict[str, Any]] = {}


@router.get("/api/jobs/jd-stats")
async def get_jd_stats(session: SessionDep) -> dict[str, Any]:
    """본문(JD) 추출 통계 — 트리거 모달의 사전 확인용."""
    total = (await session.execute(select(func.count(Job.id)))).scalar_one() or 0
    with_jd = (
        await session.execute(
            select(func.count(Job.id))
            .where(Job.jd_md.is_not(None))
            .where(func.length(Job.jd_md) > 0)
        )
    ).scalar_one() or 0
    return {
        "total": total,
        "with_jd": with_jd,
        "pending": max(0, total - with_jd),
    }


@router.post("/api/jobs/run-jd")
async def run_bulk_jd(mode: str = "new") -> dict[str, str]:
    """본문 일괄 추출 (백그라운드)."""
    if mode not in ("new", "all"):
        raise HTTPException(400, "mode는 'new' 또는 'all'이어야 합니다")
    # 이미 진행 중인 bulkjd task 있으면 취소 후 새로 시작
    await _cancel_active_task("bulkjd")
    task_id = get_bus().new_task_id("bulkjd")
    task = asyncio.create_task(_run_bulk_jd(task_id, mode))
    _register_active_task("bulkjd", task_id, task)
    return {"task_id": task_id, "mode": mode}


async def _run_bulk_jd(task_id: str, mode: str) -> None:
    """본문 일괄 추출 백그라운드 — 각 잡마다 _run_fetch_jd 활용.

    asyncio.CancelledError로 중단 가능. 진행 중인 잡 완료 후 즉시 중단.
    """
    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        async with async_session_maker() as session:
            stmt = select(Job.id, Job.title)
            if mode == "new":
                stmt = stmt.where(
                    (Job.jd_md.is_(None)) | (func.length(Job.jd_md) == 0)
                )
            rows = (await session.execute(stmt)).all()
            total = len(rows)

        await progress("start", 2, f"대상 {total}건 — 본문 추출 시작 ({mode})")
        if total == 0:
            _bulk_jd_results[task_id] = {"mode": mode, "total": 0, "ok": 0, "fail": 0}
            await progress("done", 100, "처리할 잡 없음")
            return

        ok = 0
        fail = 0
        cancelled = False
        for i, (job_id, job_title) in enumerate(rows, 1):
            pct = 2 + int(96 * i / max(total, 1))
            sub_task_id = f"{task_id}-{job_id}"
            try:
                await _run_fetch_jd(sub_task_id, job_id)
                sub_result = _jd_results.get(sub_task_id, {})
                if sub_result.get("ok"):
                    ok += 1
                else:
                    fail += 1
            except asyncio.CancelledError:
                cancelled = True
                break
            except Exception as exc:
                logger.exception("bulk jd sub-task 실패 (%s)", job_id)
                fail += 1

            # 매 잡마다 진행도 publish (잡별 제목 포함)
            await progress(
                "jd", pct,
                f"{i}/{total} · 성공 {ok} · 실패 {fail} · {(job_title or '')[:30]}",
            )
            # CDP 부담 분산 + cancel point
            try:
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                cancelled = True
                break

        _bulk_jd_results[task_id] = {
            "mode": mode, "total": total, "ok": ok, "fail": fail,
            "cancelled": cancelled,
        }
        if cancelled:
            await progress("cancelled", 100, f"중단됨 — 본문 추출 {ok}/{total}, 실패 {fail}")
        else:
            await progress("done", 100, f"완료 — 본문 추출 {ok}, 실패 {fail}")
    except asyncio.CancelledError:
        _bulk_jd_results[task_id] = {"cancelled": True}
        try:
            await progress("cancelled", 100, "사용자 요청으로 중단됨")
        except Exception:
            pass
        raise
    except Exception as exc:
        logger.exception("bulk jd 실패 (%s)", task_id)
        _bulk_jd_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))
    finally:
        _clear_active_task("bulkjd", task_id)


# 일괄 본문추출(run-jd) 결과 조회. 단일 공고용은 아래 get_jd_task_result(/api/jobs/jd-task/{task_id}).
# ⚠️ 경로가 겹치면 먼저 등록된 쪽이 뒤쪽을 가려버리므로 bulk는 별도 경로로 분리.
@router.get("/api/jobs/bulk-jd-task/{task_id}")
async def get_bulk_jd_result(task_id: str) -> dict[str, Any]:
    data = _bulk_jd_results.get(task_id)
    if data is None:
        raise HTTPException(404, "결과 없음")
    return data


@router.get("/api/jobs", response_class=HTMLResponse)
async def list_jobs_fragment(
    request: Request,
    session: SessionDep,
    q: str = Query("", description="제목/회사명 검색"),
    source: str = Query("", description="saramin | jobkorea | wanted"),
    favorite: int = Query(0, description="1이면 즐겨찾기만"),
    status: str = Query("", description="application_status 필터"),
    researched_only: int = Query(0, description="1이면 조사 완료된 회사 공고만"),
    sort: str = Query("recent", description="recent | match"),
    view: str = Query("list", description="list | card | compact"),
    page: int = Query(1, ge=1, description="페이지 번호 (1부터)"),
    page_size: int = Query(10, ge=5, le=200, description="페이지당 건수 (기본 10 — DOM 크기 최소화)"),
    limit: int = Query(0, ge=0, le=500, description="(deprecated) 무시되고 page_size 사용"),
) -> HTMLResponse:
    """HTMX용 fragment — 잡 테이블 본체만 반환.

    ⚠️ ATS 매칭은 자동 계산 X. DB에 저장된 ats_match_json만 표시.
    매칭 실행은 트리거 버튼(/api/jobs/run-ats)으로만.
    """
    import json as _json
    from app.analysis.resume_text import build_resume_text
    from app.models import Resume, ResumeFile

    # 필터 조건 리스트 — count·list 쿼리에 동일 적용
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(or_(Job.title.like(like), Company.name.like(like)))
    if source:
        filters.append(Job.source == source)
    if favorite:
        filters.append(Job.favorite == True)  # noqa: E712
    if status and status in APPLICATION_STATUSES:
        filters.append(Job.application_status == status)
    if researched_only:
        # "조사" = 실제 조사 산출물이 하나라도 있는 회사 (좌표만 찍힌 건 제외).
        # last_researched_at 타임스탬프만으론 부정확 — geocode 등이 잘못 찍어둔 흔적이 남을 수 있음.
        filters.append(
            or_(
                and_(Company.homepage_summary_json.is_not(None), Company.homepage_summary_json != ""),
                and_(Company.dart_overview_json.is_not(None), Company.dart_overview_json != ""),
                and_(Company.transit_json.is_not(None), Company.transit_json != ""),
                and_(Company.emotion_json.is_not(None), Company.emotion_json != ""),
            )
        )

    # 총 필터링된 건수
    cnt_stmt = (
        select(func.count(Job.id))
        .join(Company, Job.company_id == Company.id, isouter=True)
    )
    for f in filters:
        cnt_stmt = cnt_stmt.where(f)
    total_filtered = (await session.execute(cnt_stmt)).scalar_one()

    # 목록 쿼리 — sort=match면 전체 가져와서 후처리, 그 외엔 SQL limit/offset
    list_stmt = (
        select(Job, Company)
        .join(Company, Job.company_id == Company.id, isouter=True)
        .order_by(desc(Job.favorite), desc(Job.captured_at))
    )
    for f in filters:
        list_stmt = list_stmt.where(f)

    if sort == "match":
        # match 정렬: 전체 가져와 매칭 점수로 재정렬 → 페이지 슬라이스 (아래에서)
        rows = (await session.execute(list_stmt)).all()
    else:
        offset = (page - 1) * page_size
        list_stmt = list_stmt.offset(offset).limit(page_size)
        rows = (await session.execute(list_stmt)).all()

    total_jobs = (await session.execute(select(func.count(Job.id)))).scalar_one()
    total_companies = (await session.execute(select(func.count(Company.id)))).scalar_one()
    favorite_count = (
        await session.execute(select(func.count(Job.id)).where(Job.favorite == True))  # noqa: E712
    ).scalar_one()

    status_rows = (
        await session.execute(
            select(Job.application_status, func.count(Job.id)).group_by(Job.application_status)
        )
    ).all()
    status_counts = {s: 0 for s in APPLICATION_STATUSES}
    for s, c in status_rows:
        key = s or "none"
        if key in status_counts:
            status_counts[key] = c
        else:
            status_counts["none"] += c

    # ─── ATS 매칭 계산 (이력서가 있을 때만) ───
    resume = (
        await session.execute(
            select(Resume).where(Resume.is_primary == True).order_by(desc(Resume.updated_at)).limit(1)  # noqa: E712
        )
    ).scalars().first()

    resume_text: str | None = None
    has_resume = False
    if resume:
        # 첨부 PDF의 content_md도 매칭에 사용 (kind=resume)
        att_rows = (
            await session.execute(
                select(ResumeFile.content_md)
                .where(ResumeFile.kind == "resume")
                .where(ResumeFile.content_md.is_not(None))
            )
        ).all()
        att_mds = [r[0] for r in att_rows if r[0]]
        resume_text = build_resume_text(resume, attachment_md_list=att_mds)
        has_resume = bool(resume_text and resume_text.strip())

    # ─── ATS 매칭 — 자동 계산 X. 트리거 버튼(POST /api/jobs/run-ats)으로 일괄 실행됨.
    # 이 함수는 DB에 저장된 매칭 결과(ats_match_json)만 읽어서 표시.
    # 페이지 응답은 가벼움 (계산·kiwi 호출 없음).
    import hashlib
    resume_hash: str = ""
    if has_resume and resume_text:
        resume_hash = hashlib.sha1(resume_text.encode("utf-8")).hexdigest()[:16]

    enriched: list[dict[str, Any]] = []
    for job, company in rows:
        match: dict[str, Any] = {}
        # 이력서 hash가 같은 캐시만 사용 (이력서 변경되면 자동 invalidate)
        if (
            has_resume
            and job.ats_match_json
            and job.ats_match_resume_hash == resume_hash
        ):
            try:
                match = _json.loads(job.ats_match_json)
            except Exception:
                match = {}
        enriched.append({"job": job, "company": company, "match": match})

    # 정렬 — sort=match면 매칭 점수 내림차순 + 페이지 슬라이스
    if sort == "match" and has_resume:
        def _score(item: dict) -> tuple[int, int, float]:
            s = item.get("match", {}).get("score")
            fav = 1 if item["job"].favorite else 0
            captured = item["job"].captured_at.timestamp() if item["job"].captured_at else 0
            return (fav, s if isinstance(s, int) else -1, captured)
        enriched.sort(key=_score, reverse=True)
        # 정렬 후 페이지 슬라이스
        start = (page - 1) * page_size
        enriched = enriched[start:start + page_size]

    # 페이지 정보 계산
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    current_page = max(1, min(page, total_pages))
    page_info = {
        "total": total_filtered,
        "page": current_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "start_idx": (current_page - 1) * page_size + (1 if total_filtered > 0 else 0),
        "end_idx": min(current_page * page_size, total_filtered),
    }

    return templates.TemplateResponse(
        request,
        "partials/jobs_table.html",
        {
            "rows": [(e["job"], e["company"], e["match"]) for e in enriched],
            "total_jobs": total_jobs,
            "total_companies": total_companies,
            "favorite_count": favorite_count,
            "status_counts": status_counts,
            "has_resume": has_resume,
            "sort": sort,
            "view": view,
            "page_info": page_info,
            "q": q,
            "source": source,
            "favorite": favorite,
            "status": status,
        },
    )


@router.post("/api/jobs/{job_id}/favorite")
async def toggle_favorite(job_id: int, session: SessionDep) -> dict[str, Any]:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "잡 없음")
    job.favorite = not bool(job.favorite)
    await session.commit()
    return {"id": job.id, "favorite": job.favorite}


# 진행 중인 JD fetch 작업 — task_id → 결과
_jd_results: dict[str, dict[str, Any]] = {}


@router.post("/api/jobs/{job_id}/fetch-jd")
async def trigger_fetch_jd(job_id: int, session: SessionDep, force: int = 0) -> dict[str, Any]:
    """공고 본문(JD) 추출 트리거. 이미 캐시 있으면 즉시 반환 (force=1이면 재추출)."""
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "잡 없음")
    if job.jd_md and not force:
        return {
            "status": "cached",
            "job_id": job.id,
            "char_count": len(job.jd_md),
            "fetched_at": job.jd_fetched_at.isoformat() if job.jd_fetched_at else None,
        }
    task_id = get_bus().new_task_id("jd")
    asyncio.create_task(_run_fetch_jd(task_id, job_id))
    return {"status": "started", "task_id": task_id, "job_id": job_id}


async def _run_fetch_jd(task_id: str, job_id: int) -> None:
    from datetime import datetime, timezone
    import json as _json

    from app.analysis.ats import extract_jd_keywords
    from app.crawler.jd_fetcher import fetch_jd

    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        await progress("start", 5, f"job {job_id} — JD 페이지 열기")
        known_title: str | None = None
        known_company: str | None = None
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if not job:
                await progress("error", 100, "잡이 사라졌습니다")
                _jd_results[task_id] = {"error": "not found"}
                return
            url = job.url
            source = job.source
            known_title = job.title
            if job.company_id is not None:
                c = await session.get(Company, job.company_id)
                if c is not None:
                    known_company = c.name
        await progress("fetch", 30, f"{source} 페이지 렌더링 (anchor: {known_company or '-'})")
        result = await fetch_jd(
            url, source=source,
            known_title=known_title, known_company=known_company,
        )
        await progress("save", 75, "DB 저장")
        ats_keywords: dict | None = None
        if result.ok and result.md:
            try:
                ats_keywords = extract_jd_keywords(result.md)
                await progress(
                    "ats", 90,
                    f"ATS 키워드 추출 (필수 {len(ats_keywords.get('required') or [])}, 우대 {len(ats_keywords.get('preferred') or [])})",
                )
            except Exception as exc:
                logger.warning("ATS 키워드 추출 실패: %s", exc)
                ats_keywords = None
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if not job:
                _jd_results[task_id] = {"error": "not found"}
                return
            job.jd_md = result.md or None
            job.jd_fetched_at = datetime.now(timezone.utc)
            job.jd_error = result.error if not result.ok else None
            if ats_keywords is not None:
                job.ats_keywords_json = _json.dumps(ats_keywords, ensure_ascii=False)
            await session.commit()
            _jd_results[task_id] = {
                "ok": result.ok,
                "job_id": job_id,
                "char_count": result.char_count,
                "error": result.error,
                "ats_keyword_count": (
                    len(ats_keywords.get("required") or []) + len(ats_keywords.get("preferred") or [])
                ) if ats_keywords else 0,
            }
        await progress(
            "done" if result.ok else "warning",
            100,
            f"완료 — {result.char_count}자" if result.ok else f"실패: {result.error}",
        )
    except Exception as exc:
        logger.exception("JD fetch 실패 (%s)", task_id)
        _jd_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))


@router.get("/api/jobs/jd-task/{task_id}")
async def get_jd_task_result(task_id: str) -> dict[str, Any]:
    data = _jd_results.get(task_id)
    if data is None:
        raise HTTPException(404, "작업이 아직 진행 중이거나 만료되었습니다")
    return data


# ─── 자기소개서 생성 — 트리거 버튼 (LLM) ───
_cover_letter_results: dict[str, dict[str, Any]] = {}


@router.post("/api/jobs/{job_id}/cover-letter")
async def trigger_cover_letter(
    job_id: int,
    session: SessionDep,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    """맞춤 자기소개서 생성 트리거 (백그라운드).

    payload: {questions?: [str], tone?: '정중'|'간결'|'열정'}
    이력서 필수. 공고 본문(jd_md)·회사 조사 데이터는 있으면 품질↑, 없어도 동작.
    """
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "잡 없음")
    raw_qs = payload.get("questions")
    questions = (
        [str(q).strip() for q in raw_qs if str(q).strip()]
        if isinstance(raw_qs, list) else None
    )
    tone = (payload.get("tone") or "정중").strip()
    task_id = get_bus().new_task_id("cl")
    asyncio.create_task(_run_cover_letter(task_id, job_id, questions, tone))
    return {"status": "started", "task_id": task_id, "job_id": job_id}


async def _run_cover_letter(
    task_id: str, job_id: int,
    questions: list[str] | None, tone: str,
) -> None:
    import json as _json
    from datetime import datetime, timezone

    from app.analysis.cover_letter import generate_cover_letter
    from app.crawler.llm import OllamaUnavailable, build_client_from_settings
    from app.ui import settings_store

    bus = get_bus()
    progress = make_progress_callback(task_id)
    try:
        await progress("start", 5, f"job {job_id} — 자소서 생성 준비")

        # 1) 자료 수집 (이력서 + 회사 조사 + 공고 + ATS 매칭) — 전부 기존 데이터 재활용
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if not job:
                _cover_letter_results[task_id] = {"error": "not found"}
                await progress("error", 100, "잡이 사라졌습니다")
                return
            jd_md = job.jd_md or ""
            job_title = job.title
            ats_match = None
            if job.ats_match_json:
                try:
                    ats_match = _json.loads(job.ats_match_json)
                except Exception:
                    ats_match = None
            company_name = ""
            homepage = None
            emotion = None
            if job.company_id is not None:
                c = await session.get(Company, job.company_id)
                if c is not None:
                    company_name = c.name
                    if c.homepage_summary_json:
                        try:
                            homepage = _json.loads(c.homepage_summary_json)
                        except Exception:
                            homepage = None
                    if c.emotion_json:
                        try:
                            emotion = _json.loads(c.emotion_json)
                        except Exception:
                            emotion = None
            resume_text, resume_hash, has_resume = await _build_resume_context(session)
            cfg = await settings_store.get_all(session)

        if not has_resume:
            _cover_letter_results[task_id] = {
                "error": "이력서가 없습니다. /resume에서 작성(또는 PDF 첨부) 후 다시 시도하세요."
            }
            await progress("error", 100, "이력서 없음 — 자소서 생성 불가")
            return

        await progress(
            "gather", 20,
            f"자료 수집 — 공고본문 {'O' if jd_md else 'X'} · 회사정보 {'O' if homepage else 'X'} · 매칭 {'O' if ats_match else 'X'}",
        )

        # 2) LLM 생성
        text_model = cfg.get("ollama_text_model", "qwen3.5:9b")
        vision_model = cfg.get("ollama_vision_model", "qwen2.5vl:7b")
        ollama = build_client_from_settings(
            cfg.get("ollama_host") or "http://localhost:11434", text_model, vision_model,
        )
        await progress("llm", 45, f"LLM 자소서 작성 중 ({text_model}) — 30초~1분 소요")
        try:
            result = await generate_cover_letter(
                ollama,
                company_name=company_name or "(회사 정보 없음)",
                job_title=job_title,
                resume_text=resume_text,
                jd_md=jd_md,
                company_homepage=homepage,
                company_emotion=emotion,
                ats_match=ats_match,
                questions=questions,
                tone=tone,
            )
        except OllamaUnavailable as exc:
            _cover_letter_results[task_id] = {"error": f"Ollama 사용 불가: {exc}"}
            await progress("error", 100, f"Ollama 사용 불가 — {exc}")
            return

        if not result or not result.get("items"):
            _cover_letter_results[task_id] = {"error": "생성 결과가 비었습니다. 다시 시도해 주세요."}
            await progress("warning", 100, "생성 실패 — 결과 없음")
            return
        result["resume_hash"] = resume_hash

        # 3) 저장 (공고별 1개 캐시)
        await progress("save", 90, "DB 저장")
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job is not None:
                job.cover_letter_json = _json.dumps(result, ensure_ascii=False)
                job.cover_letter_at = datetime.now(timezone.utc)
                await session.commit()

        n = len(result.get("items") or [])
        _cover_letter_results[task_id] = {"ok": True, "job_id": job_id, "item_count": n}
        await progress("done", 100, f"완료 — 문항 {n}개 작성")
    except Exception as exc:
        logger.exception("자소서 생성 실패 (%s)", task_id)
        _cover_letter_results[task_id] = {"error": str(exc)}
        await bus.publish(ProgressEvent(task_id=task_id, stage="error", pct=100, message=str(exc)))


@router.get("/api/jobs/cover-letter-task/{task_id}")
async def get_cover_letter_result(task_id: str) -> dict[str, Any]:
    data = _cover_letter_results.get(task_id)
    if data is None:
        raise HTTPException(404, "작업이 아직 진행 중이거나 만료되었습니다")
    return data


@router.get("/api/jobs/{job_id:int}")
async def get_job_detail(job_id: int, session: SessionDep) -> dict[str, Any]:
    """잡 디테일 — 본문(JD) + ATS 매칭 정보 포함.

    ⚠️ ATS 키워드/매칭은 자동 계산 X. 트리거 버튼(/api/jobs/run-ats)으로만 실행됨.
    DB에 저장된 결과만 반환.
    """
    import json as _json

    stmt = (
        select(Job, Company)
        .join(Company, Job.company_id == Company.id, isouter=True)
        .where(Job.id == job_id)
    )
    row = (await session.execute(stmt)).first()
    if not row:
        raise HTTPException(404, "잡을 찾을 수 없습니다")
    job, company = row

    # ATS 키워드 — DB 캐시만 사용 (즉석 추출 X)
    ats_keywords: dict | None = None
    if job.ats_keywords_json:
        try:
            ats_keywords = _json.loads(job.ats_keywords_json)
        except Exception:
            ats_keywords = None

    # 매칭 — DB 캐시만 사용 (즉석 계산 X)
    ats_score: dict | None = None
    if job.ats_match_json:
        try:
            ats_score = _json.loads(job.ats_match_json)
        except Exception:
            ats_score = None

    # 자기소개서 — DB 캐시만
    cover_letter: dict | None = None
    if job.cover_letter_json:
        try:
            cover_letter = _json.loads(job.cover_letter_json)
        except Exception:
            cover_letter = None

    return {
        "id": job.id,
        "title": job.title,
        "url": job.url,
        "source": job.source,
        "location": job.location,
        "deadline": job.deadline,
        "favorite": bool(job.favorite),
        "application_status": job.application_status or "none",
        "status_note": job.status_note,
        "status_updated_at": job.status_updated_at.isoformat() if job.status_updated_at else None,
        "captured_at": job.captured_at.isoformat() if job.captured_at else None,
        "jd_md": job.jd_md or "",
        "jd_fetched_at": job.jd_fetched_at.isoformat() if job.jd_fetched_at else None,
        "jd_error": job.jd_error,
        "ats_keywords": ats_keywords,
        "ats_match": ats_score,  # {score, matched_required, missing_required, ...}
        "cover_letter": cover_letter,  # {items:[{question,answer}], highlight, tone}
        "cover_letter_at": job.cover_letter_at.isoformat() if job.cover_letter_at else None,
        "company": {
            "id": company.id,
            "name": company.name,
            "domain": company.domain,
            "address": company.address,
            "lat": company.kakao_lat,
            "lng": company.kakao_lng,
        } if company else None,
    }


@router.post("/api/jobs/{job_id}/status")
async def update_application_status(
    job_id: int,
    session: SessionDep,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """지원상태 변경 — payload: {status: str, note?: str}."""
    new_status = (payload.get("status") or "").strip()
    if new_status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"허용되지 않은 상태: {new_status}")
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "잡 없음")
    job.application_status = new_status
    job.status_updated_at = datetime.now(timezone.utc)
    note = payload.get("note")
    if note is not None:
        job.status_note = (note or "").strip() or None
    await session.commit()
    return {
        "id": job.id,
        "application_status": job.application_status,
        "status_note": job.status_note,
        "status_updated_at": job.status_updated_at.isoformat() if job.status_updated_at else None,
    }


@router.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, session: SessionDep) -> dict[str, Any]:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "잡 없음")
    await session.delete(job)
    await session.commit()
    return {"deleted": 1, "id": job_id}


@router.post("/api/jobs/delete")
async def delete_jobs_bulk(
    session: SessionDep,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """선택삭제 — payload: {ids: [int, ...]}."""
    raw_ids = payload.get("ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "ids는 배열이어야 합니다")
    ids: list[int] = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {"deleted": 0}
    result = await session.execute(delete(Job).where(Job.id.in_(ids)))
    await session.commit()
    return {"deleted": int(result.rowcount or 0)}


@router.post("/api/jobs/delete-all")
async def delete_jobs_all(
    session: SessionDep,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    """필터 조건에 맞는 잡 전체 삭제.

    payload 예: {q, source, favorite, status, keep_favorite}
    - keep_favorite=true 이면 즐겨찾기는 제외 (안전장치).
    """
    stmt = delete(Job)
    q = (payload.get("q") or "").strip()
    source = (payload.get("source") or "").strip()
    favorite = bool(payload.get("favorite"))
    status = (payload.get("status") or "").strip()
    keep_favorite = bool(payload.get("keep_favorite"))

    conds = []
    if q:
        like = f"%{q}%"
        # 회사명 join은 delete에서 못 쓰니 title만 비교
        conds.append(Job.title.like(like))
    if source:
        conds.append(Job.source == source)
    if favorite:
        conds.append(Job.favorite == True)  # noqa: E712
    if status and status in APPLICATION_STATUSES:
        conds.append(Job.application_status == status)
    if keep_favorite:
        conds.append(Job.favorite == False)  # noqa: E712

    if conds:
        stmt = stmt.where(*conds)

    result = await session.execute(stmt)
    await session.commit()
    return {"deleted": int(result.rowcount or 0)}


@router.get("/api/jobs.json")
async def list_jobs_json(
    session: SessionDep,
    q: str = Query(""),
    source: str = Query(""),
    limit: int = Query(200, ge=1, le=1000),
) -> JSONResponse:
    stmt = (
        select(Job, Company)
        .join(Company, Job.company_id == Company.id, isouter=True)
        .order_by(desc(Job.captured_at))
        .limit(limit)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Job.title.like(like), Company.name.like(like)))
    if source:
        stmt = stmt.where(Job.source == source)

    rows = (await session.execute(stmt)).all()
    return JSONResponse(
        {
            "items": [
                {
                    "id": job.id,
                    "title": job.title,
                    "url": job.url,
                    "source": job.source,
                    "location": job.location,
                    "deadline": job.deadline,
                    "captured_at": job.captured_at.isoformat() if job.captured_at else None,
                    "company": {"id": company.id, "name": company.name} if company else None,
                }
                for job, company in rows
            ]
        }
    )
