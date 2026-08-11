#!/usr/bin/env python3
"""Async FastAPI service for low-latency PDF text/title extraction."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware

# Load .env from project root (local). Railway injects vars directly.
try:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "8"))
API_KEY = os.getenv("API_KEY", "").strip()
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "*").split(",")
    if o.strip()
]
TITLE_MAX_PAGES = int(os.getenv("TITLE_MAX_PAGES", "2"))
PDF_API_URL = os.getenv("PDF_API_URL", "http://127.0.0.1:8000").rstrip("/")
UNPROCESSABLE = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)

_job_semaphore: asyncio.Semaphore | None = None
_active_jobs = 0
_active_lock: asyncio.Lock | None = None
_started_at = time.time()
_extract_ready = False
_extract_error: str | None = None


def _load_extractors():
    """Import PyMuPDF extractors lazily so /health boots fast on Railway."""
    from extract_pdf import extract_document_title_bytes, extract_pdf_bytes

    return extract_document_title_bytes, extract_pdf_bytes


def _extractors():
    global _extract_ready, _extract_error
    try:
        extract_document_title_bytes, extract_pdf_bytes = _load_extractors()
        _extract_ready = True
        _extract_error = None
        return extract_document_title_bytes, extract_pdf_bytes
    except Exception as exc:
        _extract_ready = False
        _extract_error = str(exc)
        raise


def _ensure_runtime() -> tuple[asyncio.Semaphore, asyncio.Lock]:
    global _job_semaphore, _active_lock
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    if _active_lock is None:
        _active_lock = asyncio.Lock()
    return _job_semaphore, _active_lock


async def _warmup_extractors() -> None:
    global _extract_ready, _extract_error
    try:
        await asyncio.to_thread(_load_extractors)
        _extract_ready = True
        _extract_error = None
        print("pdf extractors ready", flush=True)
    except Exception as exc:  # noqa: BLE001
        _extract_ready = False
        _extract_error = str(exc)
        print(f"pdf extractor warmup failed: {exc}", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_runtime()
    # Do not block /health on PyMuPDF import
    asyncio.create_task(_warmup_extractors())
    print(
        f"pdf-extract-api started api_key_required={bool(API_KEY)} port={os.getenv('PORT', '8000')}",
        flush=True,
    )
    yield


app = FastAPI(
    title="PDF Extract API",
    description=(
        "Async embedded-text PDF extraction (no OCR). "
        "Title detection peeks page 1→2 only for near-zero latency."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if CORS_ORIGINS == ["*"] else CORS_ORIGINS,
    allow_credentials=CORS_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = (API_KEY or "").strip()
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )


async def _track(delta: int) -> None:
    global _active_jobs
    _, lock = _ensure_runtime()
    async with lock:
        _active_jobs += delta


async def _read_pdf_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .pdf files are supported",
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"PDF exceeds max size of {MAX_UPLOAD_MB:g} MB",
            )
        chunks.append(chunk)

    data = b"".join(chunks)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty upload",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File does not look like a PDF",
        )
    return filename, data


async def _run_job(fn, *args, **kwargs) -> Any:
    sem, _ = _ensure_runtime()
    async with sem:
        await _track(1)
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        finally:
            await _track(-1)


def _public_title_payload(result: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "filename": result.get("source"),
        "document_title": result.get("document_title"),
        "document_title_source": result.get("document_title_source"),
        "document_title_confidence": result.get("document_title_confidence"),
        "document_title_page": result.get("document_title_page"),
        "pages_scanned_for_title": result.get("pages_scanned_for_title"),
        "title_pages_checked": result.get("title_pages_checked"),
        "page_count": result.get("page_count"),
        "is_encrypted": result.get("is_encrypted"),
        "metadata": result.get("metadata"),
        "elapsed_ms": result.get("elapsed_ms"),
    }


def _public_extract_payload(
    result: dict[str, Any],
    *,
    request_id: str,
    include_pages: bool,
    include_full_text: bool,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "request_id": request_id,
        "filename": result.get("source"),
        "document_title": result.get("document_title"),
        "document_title_source": result.get("document_title_source"),
        "document_title_confidence": result.get("document_title_confidence"),
        "document_title_page": result.get("document_title_page"),
        "pages_scanned_for_title": result.get("pages_scanned_for_title"),
        "title_pages_checked": result.get("title_pages_checked"),
        "page_count": result.get("page_count"),
        "is_encrypted": result.get("is_encrypted"),
        "metadata": result.get("metadata"),
        "page_titles": result.get("page_titles"),
        "elapsed_ms": result.get("elapsed_ms"),
    }
    if include_pages:
        payload["pages"] = result.get("pages")
    if include_full_text:
        payload["full_text"] = result.get("full_text")
    return payload


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "pdf-extract-api",
        "uptime_s": round(time.time() - _started_at, 2),
        "active_jobs": _active_jobs,
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "max_upload_mb": MAX_UPLOAD_MB,
        "api_key_required": bool(API_KEY),
        "extractors_ready": _extract_ready,
        "extractors_error": _extract_error,
    }


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "pdf-extract-api",
        "docs": "/docs",
        "base_url": PDF_API_URL,
        "endpoints": {
            "health": "GET /health",
            "title": "POST /v1/title",
            "extract": "POST /v1/extract",
            "batch_title": "POST /v1/batch/title",
            "batch_extract": "POST /v1/batch/extract",
        },
    }


@app.post("/v1/title", dependencies=[Depends(require_api_key)])
async def detect_title(
    file: UploadFile = File(...),
    title_max_pages: int = Form(default=TITLE_MAX_PAGES),
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    filename, data = await _read_pdf_upload(file)
    pages = max(1, min(int(title_max_pages), 5))
    extract_document_title_bytes, _ = _extractors()

    try:
        result = await _run_job(
            extract_document_title_bytes,
            data,
            source=filename,
            max_pages=pages,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"Failed to detect title: {exc}",
        ) from exc

    return _public_title_payload(result, request_id=request_id)


@app.post("/v1/extract", dependencies=[Depends(require_api_key)])
async def extract(
    file: UploadFile = File(...),
    include_pages: bool = Form(default=True),
    include_full_text: bool = Form(default=False),
    title_max_pages: int = Form(default=TITLE_MAX_PAGES),
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    filename, data = await _read_pdf_upload(file)
    pages = max(1, min(int(title_max_pages), 5))
    _, extract_pdf_bytes = _extractors()

    try:
        result = await _run_job(
            extract_pdf_bytes,
            data,
            source=filename,
            title_max_pages=pages,
            include_full_text=include_full_text,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"Failed to extract PDF: {exc}",
        ) from exc

    return _public_extract_payload(
        result,
        request_id=request_id,
        include_pages=include_pages,
        include_full_text=include_full_text,
    )


async def _process_one_title(
    file: UploadFile,
    title_max_pages: int,
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    try:
        filename, data = await _read_pdf_upload(file)
        extract_document_title_bytes, _ = _extractors()
        result = await _run_job(
            extract_document_title_bytes,
            data,
            source=filename,
            max_pages=title_max_pages,
        )
        return _public_title_payload(result, request_id=request_id)
    except HTTPException as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "filename": file.filename,
            "error": exc.detail,
            "status_code": exc.status_code,
        }
    except Exception as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "filename": file.filename,
            "error": str(exc),
            "status_code": 422,
        }


async def _process_one_extract(
    file: UploadFile,
    *,
    include_pages: bool,
    include_full_text: bool,
    title_max_pages: int,
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    try:
        filename, data = await _read_pdf_upload(file)
        _, extract_pdf_bytes = _extractors()
        result = await _run_job(
            extract_pdf_bytes,
            data,
            source=filename,
            title_max_pages=title_max_pages,
            include_full_text=include_full_text,
        )
        return _public_extract_payload(
            result,
            request_id=request_id,
            include_pages=include_pages,
            include_full_text=include_full_text,
        )
    except HTTPException as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "filename": file.filename,
            "error": exc.detail,
            "status_code": exc.status_code,
        }
    except Exception as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "filename": file.filename,
            "error": str(exc),
            "status_code": 422,
        }


@app.post("/v1/batch/title", dependencies=[Depends(require_api_key)])
async def batch_title(
    files: list[UploadFile] = File(...),
    title_max_pages: int = Form(default=TITLE_MAX_PAGES),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Max 20 files per batch")

    pages = max(1, min(int(title_max_pages), 5))
    t0 = time.perf_counter()
    results = await asyncio.gather(*[_process_one_title(f, pages) for f in files])
    ok = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok == len(results),
        "total": len(results),
        "succeeded": ok,
        "failed": len(results) - ok,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        "results": results,
    }


@app.post("/v1/batch/extract", dependencies=[Depends(require_api_key)])
async def batch_extract(
    files: list[UploadFile] = File(...),
    include_pages: bool = Form(default=True),
    include_full_text: bool = Form(default=False),
    title_max_pages: int = Form(default=TITLE_MAX_PAGES),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > 12:
        raise HTTPException(status_code=400, detail="Max 12 files per batch")

    pages = max(1, min(int(title_max_pages), 5))
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[
            _process_one_extract(
                f,
                include_pages=include_pages,
                include_full_text=include_full_text,
                title_max_pages=pages,
            )
            for f in files
        ]
    )
    ok = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok == len(results),
        "total": len(results),
        "succeeded": ok,
        "failed": len(results) - ok,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        "results": results,
    }
