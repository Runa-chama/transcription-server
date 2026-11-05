"""FastAPI application for the transcription web service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .jobs import JobManager, QueueFull
from .uploads import UploadError, UploadManager
from .whisper_engine import Engine, TranscribeOptions

logger = logging.getLogger(__name__)

engine = Engine()
uploads = UploadManager()
jobs = JobManager(engine)


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(600)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(jobs.cleanup, config.TTL_HOURS * 3600)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for directory in (config.UPLOAD_DIR, config.JOB_DIR, config.LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        await asyncio.to_thread(jobs.cleanup, config.TTL_HOURS * 3600)
    jobs.start()
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        engine.shutdown()


app = FastAPI(
    title="Transcription Server",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


class InitUpload(BaseModel):
    filename: str
    size: int
    content_type: str = ""


class CompleteUpload(BaseModel):
    model: str = config.DEFAULT_MODEL
    language: str = "ja"
    task: str = "transcribe"
    vad: bool = True
    beam_size: int = Field(default=1, ge=1, le=10)
    initial_prompt: str = Field(default="", max_length=500)
    formats: list[str] = ["srt", "txt"]
    txt_timestamps: bool = False


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        config.STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> JSONResponse:
    return JSONResponse({}, status_code=204)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True, "queue": jobs.queue_size()}


@app.get("/api/system")
async def system() -> dict:
    return {
        "max_upload_bytes": config.MAX_UPLOAD_BYTES,
        "default_model": config.DEFAULT_MODEL,
        "models": [{"id": model, "label": label} for model, label in config.MODEL_CHOICES],
        "languages": [{"id": code, "label": label} for code, label in config.LANGUAGE_CHOICES],
        "tasks": [{"id": code, "label": label} for code, label in config.TASK_CHOICES],
        "formats": config.FORMAT_CHOICES,
    }


@app.post("/api/uploads/init")
async def init_upload(payload: InitUpload) -> dict:
    try:
        return await asyncio.to_thread(
            uploads.init, payload.filename, payload.size, payload.content_type
        )
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/uploads/{upload_id}/chunks/{index}")
async def put_chunk(upload_id: str, index: int, request: Request) -> dict:
    data = await request.body()
    if len(data) > config.CHUNK_SIZE:
        raise HTTPException(status_code=413, detail="チャンクが大きすぎます")
    try:
        return await asyncio.to_thread(uploads.save_chunk, upload_id, index, data)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str, payload: CompleteUpload) -> dict:
    if payload.model not in config.MODEL_IDS:
        raise HTTPException(status_code=400, detail="不明なモデルです")
    if payload.language not in config.LANGUAGE_IDS:
        raise HTTPException(status_code=400, detail="不明な言語です")
    if payload.task not in config.TASK_IDS:
        raise HTTPException(status_code=400, detail="不明なタスクです")
    formats = [fmt for fmt in payload.formats if fmt in config.FORMAT_CHOICES]
    if not formats:
        raise HTTPException(status_code=400, detail="出力形式を1つ以上選択してください")

    try:
        source, meta = await asyncio.to_thread(uploads.complete, upload_id)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    options = TranscribeOptions(
        language=payload.language,
        task=payload.task,
        vad=payload.vad,
        beam_size=payload.beam_size,
        initial_prompt=payload.initial_prompt,
        formats=tuple(formats),
        txt_timestamps=payload.txt_timestamps,
    )
    try:
        job = await asyncio.to_thread(
            jobs.create_job,
            source,
            meta["filename"],
            meta["stem"],
            meta["ext"],
            payload.model,
            options,
        )
    except QueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001
        logger.exception("failed to create job for upload %s", upload_id)
        uploads.discard(upload_id)
        raise HTTPException(
            status_code=500,
            detail="ジョブを作成できませんでした。時間をおいて再度お試しください。",
        ) from None

    uploads.discard(upload_id)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return job.snapshot(include_segments=True)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    jobs.cancel(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download/{fmt}")
async def download(job_id: str, fmt: str) -> FileResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    if fmt not in config.FORMAT_CHOICES:
        raise HTTPException(status_code=400, detail="不明な形式です")
    path = job.directory / f"{job.stem}.{fmt}"
    try:
        path.resolve().relative_to(job.directory.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="不正なパスです") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="出力ファイルがありません")
    return FileResponse(
        path,
        filename=f"{job.stem}.{fmt}",
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
