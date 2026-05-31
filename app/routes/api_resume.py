"""이력서 CRUD API.

is_primary=True 인 단일 레코드를 메인 이력서로 다룬다.
없으면 GET 시 빈 스켈레톤을 응답하고, PUT 첫 호출에서 생성한다.
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc, select

from app.analysis.document import ALL_UPLOAD_EXTS, convert_to_markdown, is_image, is_supported
from app.config import ROOT_DIR
from app.deps import SessionDep
from app.models import Resume, ResumeFile

router = APIRouter()
logger = logging.getLogger(__name__)

UPLOADS_DIR = ROOT_DIR / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB


# 표준 섹션 스켈레톤 — 응답 시 누락 키 채우기에 사용
_DEFAULT_SECTIONS: dict[str, Any] = {
    "personal": {
        "name_kr": "", "name_en": "", "birth_date": "",
        "gender": "", "phone": "", "email": "",
        "address": "", "road_address": "",
    },
    "education": [],
    "experience": [],
    "certifications": [],
    "languages": [],
    "awards": [],
    "activities": [],          # 외부활동(동아리, 학회 등) — [{title, role, start_date, end_date, description}]
    "volunteers": [],          # 봉사활동 — [{title, org, hours, date}]
    "publications": [],        # 논문/저서 — [{title, journal, date, link}]
    "patents": [],             # 특허/지적재산권 — [{title, number, date, role}]
    "self_intros": [],
    "preferences": {
        "desired_role": "", "desired_salary": "",
        "desired_location": "", "start_available": "",
        "work_type": "",
    },
    "custom": [],              # 사용자 정의 섹션 — [{title, content}]
    # UI 노출 토글: 각 섹션을 보일지 (양식 템플릿 적용 결과 저장)
    "visible": {
        "personal": True, "education": True, "experience": True,
        "certifications": True, "languages": True, "awards": True,
        "activities": False, "volunteers": False,
        "publications": False, "patents": False,
        "self_intros": True, "preferences": True,
    },
    # 양식 템플릿 — saramin / jobkorea / minimal / full
    "template": "saramin",
}


def _parse_sections(raw: str | None) -> dict[str, Any]:
    if not raw:
        return json.loads(json.dumps(_DEFAULT_SECTIONS))
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    out = json.loads(json.dumps(_DEFAULT_SECTIONS))
    for k, v in data.items():
        out[k] = v
    return out


def _to_dict(r: Resume | None) -> dict[str, Any]:
    if r is None:
        return {
            "id": None,
            "title": "기본 이력서",
            "role": "",
            "summary_md": "",
            "content_md": "",
            "skills_csv": "",
            "years_experience": None,
            "photo_file_id": None,
            "sections": _parse_sections(None),
            "is_primary": True,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": r.id,
        "title": r.title,
        "role": r.role or "",
        "summary_md": r.summary_md or "",
        "content_md": r.content_md or "",
        "skills_csv": r.skills_csv or "",
        "years_experience": r.years_experience,
        "photo_file_id": r.photo_file_id,
        "sections": _parse_sections(r.sections_json),
        "is_primary": bool(r.is_primary),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


async def _get_primary(session) -> Resume | None:
    return (
        await session.execute(
            select(Resume).where(Resume.is_primary == True).order_by(desc(Resume.updated_at))  # noqa: E712
        )
    ).scalars().first()


@router.get("/api/resume")
async def get_resume(session: SessionDep) -> dict[str, Any]:
    row = await _get_primary(session)
    return _to_dict(row)


@router.put("/api/resume")
async def put_resume(session: SessionDep, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    row = await _get_primary(session)
    now = datetime.now(timezone.utc)
    if row is None:
        row = Resume(is_primary=True, created_at=now, updated_at=now)
        session.add(row)

    if "title" in payload:
        row.title = (payload.get("title") or "기본 이력서").strip() or "기본 이력서"
    if "role" in payload:
        row.role = (payload.get("role") or "").strip() or None
    if "summary_md" in payload:
        row.summary_md = (payload.get("summary_md") or "") or None
    if "content_md" in payload:
        row.content_md = (payload.get("content_md") or "") or None
    if "skills_csv" in payload:
        skills = (payload.get("skills_csv") or "").strip()
        row.skills_csv = skills or None
    if "years_experience" in payload:
        yrs = payload.get("years_experience")
        try:
            row.years_experience = int(yrs) if yrs not in (None, "") else None
        except (TypeError, ValueError):
            row.years_experience = None
    if "photo_file_id" in payload:
        pid = payload.get("photo_file_id")
        try:
            row.photo_file_id = int(pid) if pid not in (None, "") else None
        except (TypeError, ValueError):
            row.photo_file_id = None
    if "sections" in payload:
        sec = payload.get("sections") or {}
        if not isinstance(sec, dict):
            raise HTTPException(400, "sections는 객체여야 합니다")
        # 기존 데이터와 병합
        merged = _parse_sections(row.sections_json)
        for k, v in sec.items():
            merged[k] = v
        row.sections_json = json.dumps(merged, ensure_ascii=False)
    row.updated_at = now

    await session.commit()
    await session.refresh(row)
    return _to_dict(row)


@router.get("/api/resumes")
async def list_resumes(session: SessionDep) -> dict[str, Any]:
    rows = (
        await session.execute(select(Resume).order_by(desc(Resume.is_primary), desc(Resume.updated_at)))
    ).scalars().all()
    return {"items": [_to_dict(r) for r in rows]}


@router.post("/api/resumes")
async def create_resume(session: SessionDep, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """새 이력서 생성. 빈 양식 또는 payload로 초기 데이터 설정.

    payload: { title?, role?, activate? } — activate=True면 primary 전환 (기존 primary는 자동 해제)
    """
    now = datetime.now(timezone.utc)
    title = (payload.get("title") or "").strip() or f"새 이력서 ({now.strftime('%m-%d %H:%M')})"
    activate = bool(payload.get("activate", False))

    if activate:
        # 기존 primary 모두 false로
        existing_primaries = (
            await session.execute(select(Resume).where(Resume.is_primary == True))  # noqa: E712
        ).scalars().all()
        for r in existing_primaries:
            r.is_primary = False

    row = Resume(
        title=title,
        role=(payload.get("role") or "").strip() or None,
        is_primary=activate,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_dict(row)


@router.get("/api/resumes/{resume_id}")
async def get_resume_by_id(resume_id: int, session: SessionDep) -> dict[str, Any]:
    row = await session.get(Resume, resume_id)
    if not row:
        raise HTTPException(404, "이력서 없음")
    return _to_dict(row)


@router.put("/api/resumes/{resume_id}")
async def put_resume_by_id(resume_id: int, session: SessionDep,
                           payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """특정 이력서 수정 (다중 이력서 편집 화면용)."""
    row = await session.get(Resume, resume_id)
    if not row:
        raise HTTPException(404, "이력서 없음")
    now = datetime.now(timezone.utc)

    if "title" in payload:
        row.title = (payload.get("title") or "기본 이력서").strip() or "기본 이력서"
    if "role" in payload:
        row.role = (payload.get("role") or "").strip() or None
    if "summary_md" in payload:
        row.summary_md = (payload.get("summary_md") or "") or None
    if "content_md" in payload:
        row.content_md = (payload.get("content_md") or "") or None
    if "skills_csv" in payload:
        skills = (payload.get("skills_csv") or "").strip()
        row.skills_csv = skills or None
    if "years_experience" in payload:
        yrs = payload.get("years_experience")
        try:
            row.years_experience = int(yrs) if yrs not in (None, "") else None
        except (TypeError, ValueError):
            row.years_experience = None
    if "photo_file_id" in payload:
        pid = payload.get("photo_file_id")
        try:
            row.photo_file_id = int(pid) if pid not in (None, "") else None
        except (TypeError, ValueError):
            row.photo_file_id = None
    if "sections" in payload:
        sec = payload.get("sections") or {}
        if not isinstance(sec, dict):
            raise HTTPException(400, "sections는 객체여야 합니다")
        merged = _parse_sections(row.sections_json)
        for k, v in sec.items():
            merged[k] = v
        row.sections_json = json.dumps(merged, ensure_ascii=False)
    row.updated_at = now
    await session.commit()
    await session.refresh(row)
    return _to_dict(row)


@router.post("/api/resumes/{resume_id}/duplicate")
async def duplicate_resume(resume_id: int, session: SessionDep) -> dict[str, Any]:
    """이력서 복제 — sections + 모든 필드 그대로, is_primary=False."""
    src = await session.get(Resume, resume_id)
    if not src:
        raise HTTPException(404, "원본 이력서 없음")
    now = datetime.now(timezone.utc)
    dup = Resume(
        title=f"{src.title} (사본)",
        role=src.role,
        summary_md=src.summary_md,
        content_md=src.content_md,
        skills_csv=src.skills_csv,
        years_experience=src.years_experience,
        photo_file_id=src.photo_file_id,
        sections_json=src.sections_json,
        is_primary=False,  # 복제본은 활성 아님
        created_at=now,
        updated_at=now,
    )
    session.add(dup)
    await session.commit()
    await session.refresh(dup)
    return _to_dict(dup)


@router.post("/api/resumes/{resume_id}/activate")
async def activate_resume(resume_id: int, session: SessionDep) -> dict[str, Any]:
    """특정 이력서를 활성(primary)으로 전환. 기존 primary는 자동 해제."""
    target = await session.get(Resume, resume_id)
    if not target:
        raise HTTPException(404, "이력서 없음")
    # 기존 primary 모두 false
    existing = (
        await session.execute(select(Resume).where(Resume.is_primary == True))  # noqa: E712
    ).scalars().all()
    for r in existing:
        if r.id != resume_id:
            r.is_primary = False
    target.is_primary = True
    target.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True, "active_id": resume_id, "title": target.title}


@router.delete("/api/resume/{resume_id}")
async def delete_resume(resume_id: int, session: SessionDep) -> dict[str, Any]:
    row = await session.get(Resume, resume_id)
    if not row:
        raise HTTPException(404, "이력서 없음")
    await session.delete(row)
    await session.commit()
    return {"deleted": 1, "id": resume_id}


# ────────────────────────────────────────────────
# 첨부 파일 (이력서 본체 첨부 + 포트폴리오) 업로드/조회/삭제
# ────────────────────────────────────────────────


def _file_to_dict(f: ResumeFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "resume_id": f.resume_id,
        "kind": f.kind,
        "original_name": f.original_name,
        "mime": f.mime,
        "size_bytes": f.size_bytes,
        "size_kb": round((f.size_bytes or 0) / 1024, 1),
        "content_md": f.content_md or "",
        "convert_error": f.convert_error,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        "download_url": f"/api/resume/files/{f.id}/download",
    }


@router.get("/api/resume/files")
async def list_resume_files(
    session: SessionDep,
    kind: str = "",
) -> dict[str, Any]:
    stmt = select(ResumeFile).order_by(desc(ResumeFile.uploaded_at))
    if kind in ("resume", "portfolio"):
        stmt = stmt.where(ResumeFile.kind == kind)
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_file_to_dict(f) for f in rows]}


@router.post("/api/resume/files")
async def upload_resume_file(
    session: SessionDep,
    file: UploadFile = File(...),
    kind: str = Form("resume"),
    resume_id: int | None = Form(None),
) -> dict[str, Any]:
    if kind not in ("resume", "portfolio", "photo", "attachment"):
        raise HTTPException(400, "kind는 resume|portfolio|photo|attachment")
    if not file.filename:
        raise HTTPException(400, "파일명이 필요합니다")
    if not is_supported(file.filename):
        raise HTTPException(
            400,
            f"지원하지 않는 형식. 지원: {', '.join(ALL_UPLOAD_EXTS)}",
        )
    # photo는 이미지 확장자만 허용
    if kind == "photo" and not is_image(file.filename):
        raise HTTPException(400, "사진은 이미지 파일(.png/.jpg 등)이어야 합니다")

    # 디스크에 저장
    ext = Path(file.filename).suffix.lower()
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex}{ext}"
    total = 0
    with stored.open("wb") as fp:
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                fp.close()
                try:
                    stored.unlink()
                except Exception:
                    pass
                raise HTTPException(413, f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // (1024*1024)}MB)")
            fp.write(chunk)

    # 변환 (이미지는 스킵)
    if is_image(file.filename):
        md_text, err = "", None
    else:
        try:
            md_text, err = convert_to_markdown(stored)
        except Exception as exc:
            logger.exception("convert_to_markdown 실패: %s", stored)
            md_text, err = "", f"변환 실패: {exc}"

    row = ResumeFile(
        resume_id=resume_id,
        kind=kind,
        original_name=file.filename,
        stored_path=str(stored.relative_to(ROOT_DIR)),
        mime=file.content_type or None,
        size_bytes=total,
        content_md=md_text or None,
        convert_error=err,
        uploaded_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _file_to_dict(row)


@router.get("/api/resume/files/{file_id}")
async def get_resume_file(file_id: int, session: SessionDep) -> dict[str, Any]:
    row = await session.get(ResumeFile, file_id)
    if not row:
        raise HTTPException(404, "파일 없음")
    return _file_to_dict(row)


@router.get("/api/resume/files/{file_id}/download")
async def download_resume_file(file_id: int, session: SessionDep) -> FileResponse:
    row = await session.get(ResumeFile, file_id)
    if not row:
        raise HTTPException(404, "파일 없음")
    path = ROOT_DIR / row.stored_path
    if not path.exists():
        raise HTTPException(410, "원본 파일이 삭제되었습니다")
    return FileResponse(
        path=str(path),
        filename=row.original_name,
        media_type=row.mime or "application/octet-stream",
    )


@router.get("/api/resume/files/{file_id}/raw")
async def raw_resume_file(file_id: int, session: SessionDep) -> FileResponse:
    """원본 파일을 inline으로 응답 — 사진/이미지 미리보기에 사용."""
    row = await session.get(ResumeFile, file_id)
    if not row:
        raise HTTPException(404, "파일 없음")
    path = ROOT_DIR / row.stored_path
    if not path.exists():
        raise HTTPException(410, "원본 파일이 삭제되었습니다")
    return FileResponse(
        path=str(path),
        media_type=row.mime or "application/octet-stream",
    )


@router.delete("/api/resume/files/{file_id}")
async def delete_resume_file(file_id: int, session: SessionDep) -> dict[str, Any]:
    row = await session.get(ResumeFile, file_id)
    if not row:
        raise HTTPException(404, "파일 없음")
    # 디스크 파일 제거 (실패해도 DB row는 삭제)
    try:
        p = ROOT_DIR / row.stored_path
        if p.exists():
            p.unlink()
    except Exception:
        logger.exception("업로드 파일 삭제 실패: %s", row.stored_path)
    await session.delete(row)
    await session.commit()
    return {"deleted": 1, "id": file_id}


@router.get("/api/resume/attachments.zip")
async def download_attachments_zip(
    session: SessionDep,
    kind: str = "",  # ""=all, "resume" / "portfolio" / "photo" / "attachment"
) -> StreamingResponse:
    """모든(또는 종류별) 첨부 파일을 ZIP으로 묶어 다운로드."""
    stmt = select(ResumeFile).order_by(desc(ResumeFile.uploaded_at))
    if kind in ("resume", "portfolio", "photo", "attachment"):
        stmt = stmt.where(ResumeFile.kind == kind)
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        raise HTTPException(404, "다운로드할 첨부 파일이 없습니다")

    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in rows:
            src = ROOT_DIR / f.stored_path
            if not src.exists():
                continue
            folder = {"resume": "이력서", "portfolio": "포트폴리오",
                      "photo": "사진", "attachment": "기타첨부"}.get(f.kind, "기타")
            # 파일명 충돌 방지
            base = Path(f.original_name).stem
            ext = Path(f.original_name).suffix
            safe_base = re.sub(r"[^\w가-힣 .\-_()]", "_", base) or "file"
            name = f"{folder}/{safe_base}{ext}"
            n = 2
            while name in used_names:
                name = f"{folder}/{safe_base}({n}){ext}"
                n += 1
            used_names.add(name)
            zf.write(src, arcname=name)
    buf.seek(0)
    filename = f"resume_attachments_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/resume/files/{file_id}/apply")
async def apply_file_to_resume(file_id: int, session: SessionDep) -> dict[str, Any]:
    """업로드한 파일의 변환된 마크다운을 메인 이력서 본문(content_md)으로 가져온다."""
    f = await session.get(ResumeFile, file_id)
    if not f:
        raise HTTPException(404, "파일 없음")
    if not (f.content_md or "").strip():
        raise HTTPException(400, "변환된 내용이 없어 적용할 수 없습니다")

    row = await _get_primary(session)
    now = datetime.now(timezone.utc)
    if row is None:
        row = Resume(is_primary=True, created_at=now, updated_at=now)
        session.add(row)
    row.content_md = f.content_md
    row.updated_at = now
    await session.commit()
    await session.refresh(row)
    return _to_dict(row)
