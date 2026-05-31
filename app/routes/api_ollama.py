"""Ollama 모델 카탈로그 + 설치/삭제 API.

- GET /api/ollama/catalog      : 큐레이션 모델 + 설치 여부 join
- POST /api/ollama/pull        : pull 시작 (background task)
- GET /api/ollama/pull/{tid}   : pull 진행률 조회
- POST /api/ollama/pull/cancel : (베스트에포트) 진행 중 pull 취소
- DELETE /api/ollama/models/{name}: 설치된 모델 삭제
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from ollama import AsyncClient

from app.crawler.llm_catalog import CATALOG, find_in_catalog

logger = logging.getLogger(__name__)
router = APIRouter()

OLLAMA_HOST = "http://localhost:11434"


# ───────── 진행률 트래커 (in-memory) ─────────

@dataclass
class PullProgress:
    task_id: str
    model: str
    status: str = "queued"           # queued | downloading | verifying | done | error | cancelled
    completed: int = 0               # bytes
    total: int = 0                   # bytes
    last_message: str = ""           # ollama가 알려준 마지막 status (pulling manifest 등)
    error: str | None = None
    done: bool = False
    _task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_task", None)
        d["percent"] = round(self.completed * 100 / self.total, 1) if self.total else 0.0
        return d


_pulls: dict[str, PullProgress] = {}


# ───────── 헬퍼 ─────────

async def _list_installed() -> list[dict[str, Any]]:
    """Ollama 서버에서 설치된 모델 목록 (이름·크기·수정시각)."""
    client = AsyncClient(host=OLLAMA_HOST)
    listing = await client.list()
    out: list[dict[str, Any]] = []
    if hasattr(listing, "models"):
        for m in listing.models:
            out.append(
                {
                    "name": getattr(m, "model", "") or "",
                    "size": int(getattr(m, "size", 0) or 0),
                    "modified": str(getattr(m, "modified_at", "")),
                }
            )
    return out


# ───────── catalog ─────────

@router.get("/api/ollama/catalog")
async def ollama_catalog() -> dict[str, Any]:
    """카탈로그 + 현재 설치 상태 join + 진행 중 pull 표시."""
    try:
        installed = await _list_installed()
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "items": [],
            "installed": [],
            "active_pulls": [],
        }

    installed_names = {m["name"].lower() for m in installed}

    items: list[dict[str, Any]] = []
    for m in CATALOG:
        items.append(
            {
                "name": m.name,
                "role": m.role,
                "size_gb": m.size_gb,
                "label": m.label,
                "description": m.description,
                "recommended": m.recommended,
                "installed": m.name.lower() in installed_names,
            }
        )

    # 카탈로그에 없는 설치 모델 (사용자 직접 pull한 것)
    cataloged = {m.name.lower() for m in CATALOG}
    extras = [m for m in installed if m["name"].lower() not in cataloged]

    active = [p.public() for p in _pulls.values() if not p.done]

    return {
        "ok": True,
        "items": items,
        "installed": installed,
        "extras": extras,
        "active_pulls": active,
    }


# ───────── pull ─────────

async def _run_pull(progress: PullProgress) -> None:
    """ollama.pull(stream=True) iterator를 돌리며 progress 갱신."""
    client = AsyncClient(host=OLLAMA_HOST, timeout=None)
    progress.status = "downloading"
    progress.last_message = "pull 시작"
    try:
        async for event in await client.pull(progress.model, stream=True):
            # ProgressResponse: status, digest, total, completed
            status = getattr(event, "status", "") or ""
            total = int(getattr(event, "total", 0) or 0)
            completed = int(getattr(event, "completed", 0) or 0)
            if status:
                progress.last_message = status
            if total:
                progress.total = total
            if completed:
                progress.completed = completed
            # 일부 단계 (manifest/verifying)는 total/completed 없이 status만 옴
        progress.status = "done"
        progress.done = True
        progress.last_message = "설치 완료"
    except asyncio.CancelledError:
        progress.status = "cancelled"
        progress.done = True
        progress.last_message = "사용자 취소"
        raise
    except Exception as exc:
        progress.status = "error"
        progress.done = True
        progress.error = f"{type(exc).__name__}: {exc}"
        logger.exception("Ollama pull 실패: %s", progress.model)


@router.post("/api/ollama/pull")
async def start_pull(body: dict[str, Any]) -> dict[str, Any]:
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "model 필수")
    # 이미 진행 중인 동일 모델 있으면 그걸 재사용
    for tid, p in _pulls.items():
        if p.model == model and not p.done:
            return {"task_id": tid, "reused": True, **p.public()}
    tid = uuid.uuid4().hex[:12]
    prog = PullProgress(task_id=tid, model=model)
    _pulls[tid] = prog
    prog._task = asyncio.create_task(_run_pull(prog))
    return {"task_id": tid, "reused": False, **prog.public()}


@router.get("/api/ollama/pull/{task_id}")
async def get_pull(task_id: str) -> dict[str, Any]:
    p = _pulls.get(task_id)
    if p is None:
        raise HTTPException(404, "task_id 없음")
    return p.public()


@router.post("/api/ollama/pull/{task_id}/cancel")
async def cancel_pull(task_id: str) -> dict[str, Any]:
    p = _pulls.get(task_id)
    if p is None:
        raise HTTPException(404, "task_id 없음")
    if p.done:
        return {"ok": False, "message": "이미 종료됨", **p.public()}
    if p._task is not None:
        p._task.cancel()
    return {"ok": True, "message": "취소 요청", **p.public()}


# ───────── delete ─────────

@router.delete("/api/ollama/models/{name:path}")
async def delete_model(name: str) -> dict[str, Any]:
    n = name.strip()
    if not n:
        raise HTTPException(400, "model name 필수")
    client = AsyncClient(host=OLLAMA_HOST)
    try:
        result = await client.delete(n)
        status = getattr(result, "status", "")
        return {"ok": True, "model": n, "status": str(status)}
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
