#!/usr/bin/env python3
"""
Minimal document preview service (PDF, Word, Excel, PPT -> PDF).
Single-file FastAPI app for the doc preview tool.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
    import aiofiles
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("aiofiles is required") from exc

try:
    import filetype
    FILETYPE_AVAILABLE = True
except ImportError:
    FILETYPE_AVAILABLE = False
    filetype = None


class Config:
    TMP_DIR = Path(os.getenv("PREVIEW_TMP_DIR", tempfile.gettempdir())) / "zenreader_preview"
    MAX_SIZE_MB = int(os.getenv("PREVIEW_MAX_SIZE_MB", "10"))
    TIMEOUT_SEC = int(os.getenv("PREVIEW_TIMEOUT_SEC", "60"))
    TTL_HOURS = int(os.getenv("PREVIEW_TTL_HOURS", "24"))
    MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_CONVERSIONS", "2"))

    ALLOWED_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
    OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}


@dataclass
class PreviewJob:
    id: str
    original_name: str
    mime_type: str
    size_bytes: int
    status: str
    preview_type: str
    original_path: str
    preview_path: Optional[str]
    created_at: float
    expires_at: float
    error: Optional[str] = None


app = FastAPI(title="Doc Preview Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: Dict[str, PreviewJob] = {}
conversion_semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT)


def cleanup_expired() -> None:
    now = time.time()
    expired_ids = [job_id for job_id, job in jobs.items() if job.expires_at <= now]
    for job_id in expired_ids:
        job = jobs.pop(job_id, None)
        if not job:
            continue
        try:
            job_dir = Path(job.original_path).parent
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass


def detect_mime(path: Path, fallback: str = "") -> str:
    if FILETYPE_AVAILABLE and filetype is not None:
        kind = filetype.guess(str(path))
        if kind and kind.mime:
            return kind.mime
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or fallback or "application/octet-stream"


async def save_upload(file: UploadFile, dest: Path) -> int:
    size = 0
    async with aiofiles.open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > Config.MAX_SIZE_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail="File too large")
            await out.write(chunk)
    return size


def to_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def run_soffice(input_path: Path, output_dir: Path, profile_dir: Path) -> None:
    cmd = [
        "soffice",
        "--headless",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--norestore",
        f"-env:UserInstallation={to_file_uri(profile_dir)}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(input_path),
    ]
    subprocess.run(cmd, timeout=Config.TIMEOUT_SEC, check=False)


async def convert_to_pdf(job: PreviewJob) -> None:
    input_path = Path(job.original_path)
    job_dir = input_path.parent
    output_dir = job_dir / "output"
    profile_dir = job_dir / "profile"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with conversion_semaphore:
        await asyncio.to_thread(run_soffice, input_path, output_dir, profile_dir)

    expected = output_dir / f"{input_path.stem}.pdf"
    if not expected.exists():
        pdfs = list(output_dir.glob("*.pdf"))
        if pdfs:
            expected = pdfs[0]
        else:
            raise RuntimeError("Conversion failed: no PDF output")

    preview_path = job_dir / "preview.pdf"
    expected.replace(preview_path)
    job.preview_path = str(preview_path)


async def process_job(job_id: str) -> None:
    job = jobs.get(job_id)
    if not job:
        return
    job.status = "processing"
    try:
        await convert_to_pdf(job)
        job.status = "ready"
        job.error = None
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)


@app.post("/api/preview/upload")
async def upload_preview(file: UploadFile = File(...)):
    cleanup_expired()
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file")

    ext = Path(file.filename).suffix.lower()
    if ext not in Config.ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    job_id = uuid.uuid4().hex
    job_dir = Config.TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    original_path = job_dir / f"original{ext}"

    size = await save_upload(file, original_path)
    mime_type = detect_mime(original_path, file.content_type or "")

    job = PreviewJob(
        id=job_id,
        original_name=file.filename,
        mime_type=mime_type,
        size_bytes=size,
        status="queued",
        preview_type="pdf",
        original_path=str(original_path),
        preview_path=None,
        created_at=time.time(),
        expires_at=time.time() + Config.TTL_HOURS * 3600,
    )
    jobs[job_id] = job

    if ext == ".pdf":
        job.status = "ready"
        job.preview_path = str(original_path)
    else:
        asyncio.create_task(process_job(job_id))

    return JSONResponse({"fileId": job_id, "status": job.status, "previewType": job.preview_type})


@app.get("/api/preview/{file_id}")
async def get_preview_status(file_id: str):
    cleanup_expired()
    job = jobs.get(file_id)
    if not job:
        raise HTTPException(status_code=404, detail="File not found")
    payload = {
        "status": job.status,
        "error": job.error,
        "previewUrl": f"/api/preview/{file_id}/content" if job.status == "ready" else None,
        "meta": {
            "originalName": job.original_name,
            "mimeType": job.mime_type,
            "sizeBytes": job.size_bytes,
        },
    }
    return JSONResponse(payload)


@app.get("/api/preview/{file_id}/content")
async def get_preview_content(file_id: str):
    cleanup_expired()
    job = jobs.get(file_id)
    if not job:
        raise HTTPException(status_code=404, detail="File not found")
    if job.status != "ready" or not job.preview_path:
        raise HTTPException(status_code=409, detail="Preview not ready")
    return FileResponse(job.preview_path, media_type="application/pdf", filename=f"{file_id}.pdf")


@app.get("/api/preview/{file_id}/original")
async def download_original(file_id: str):
    cleanup_expired()
    job = jobs.get(file_id)
    if not job:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(job.original_path, filename=job.original_name)


@app.delete("/api/preview/{file_id}")
async def delete_preview(file_id: str):
    job = jobs.pop(file_id, None)
    if not job:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        job_dir = Path(job.original_path).parent
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass
    return JSONResponse({"success": True})


@app.get("/api/preview/{file_id}/debug")
async def debug_job(file_id: str):
    job = jobs.get(file_id)
    if not job:
        raise HTTPException(status_code=404, detail="File not found")
    return JSONResponse(asdict(job))


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "preview_service:app",
        host=os.getenv("PREVIEW_HOST", "0.0.0.0"),
        port=int(os.getenv("PREVIEW_PORT", "8004")),
        reload=False,
    )
