"""Job queue and persistent job metadata."""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .exports import write_exports
from .models import Segment
from .whisper_engine import (
    Cancelled,
    Engine,
    OutOfMemory,
    TranscribeOptions,
)


class QueueFull(RuntimeError):
    pass


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Job:
    id: str
    filename: str
    stem: str
    model: str
    options: TranscribeOptions
    directory: Path
    created_at: float
    status: str = "queued"
    progress: float = 0.0
    message: str = "待機中"
    error: str = ""
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    elapsed: float = 0.0
    device: str = ""
    compute_type: str = ""
    outputs: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    retried_cpu: bool = False

    def meta(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "stem": self.stem,
            "model": self.model,
            "options": {
                "language": self.options.language,
                "task": self.options.task,
                "vad": self.options.vad,
                "beam_size": self.options.beam_size,
                "initial_prompt": self.options.initial_prompt,
                "formats": list(self.options.formats),
                "txt_timestamps": self.options.txt_timestamps,
            },
            "created_at": self.created_at,
            "updated_at": time.time(),
            "status": self.status,
            "progress": round(self.progress, 2),
            "message": self.message,
            "error": self.error,
            "language": self.language,
            "language_probability": self.language_probability,
            "duration": self.duration,
            "elapsed": self.elapsed,
            "device": self.device,
            "compute_type": self.compute_type,
            "outputs": self.outputs,
        }

    def snapshot(self, include_segments: bool = True) -> dict:
        with self.lock:
            data = self.meta()
            data["segment_count"] = len(self.segments)
            if include_segments:
                data["segments"] = [
                    {
                        "start": round(segment.start, 3),
                        "end": round(segment.end, 3),
                        "text": segment.text,
                    }
                    for segment in self.segments
                ]
            return data


class JobManager:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._jobs: dict[str, Job] = {}
        self._guard = threading.Lock()
        self._queue: queue.Queue[Job] = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)

    def start(self) -> None:
        self._worker.start()

    def create_job(
        self,
        source: Path,
        filename: str,
        stem: str,
        ext: str,
        model: str,
        options: TranscribeOptions,
    ) -> Job:
        with self._guard:
            pending = sum(
                1
                for job in self._jobs.values()
                if job.status in ("queued", "loading", "processing")
            )
        if pending >= config.MAX_QUEUE:
            raise QueueFull("現在混み合っています。しばらく待ってからお試しください。")

        job_id = uuid.uuid4().hex
        directory = config.JOB_DIR / job_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"source{ext}"
        shutil.move(str(source), str(destination))

        job = Job(
            id=job_id,
            filename=filename,
            stem=stem,
            model=model,
            options=options,
            directory=directory,
            created_at=time.time(),
        )
        with job.lock:
            _atomic_json(directory / "meta.json", job.meta())
        with self._guard:
            self._jobs[job_id] = job
        self._queue.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._guard:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        return self._load_from_disk(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel_event.set()
        return True

    def active_ids(self) -> set[str]:
        with self._guard:
            return {
                job.id
                for job in self._jobs.values()
                if job.status in ("queued", "loading", "processing")
            }

    def queue_size(self) -> int:
        return self._queue.qsize()

    # -- worker ------------------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._process(job)
            finally:
                self._queue.task_done()

    def _process(self, job: Job) -> None:
        while True:
            try:
                self._run(job)
                return
            except Cancelled:
                with job.lock:
                    job.status = "cancelled"
                    job.message = "キャンセルしました"
                self._persist(job)
                return
            except OutOfMemory as exc:
                if job.retried_cpu:
                    self._fail(job, f"GPU メモリ不足のため処理できませんでした: {exc}")
                    return
                job.retried_cpu = True
                with job.lock:
                    job.segments.clear()
                    job.progress = 0.0
                    job.status = "loading"
                    job.message = "GPU メモリ不足のため CPU で再試行します…"
                self._persist(job)
                self.engine.force_cpu()
            except Exception as exc:  # noqa: BLE001
                self._fail(job, f"{exc}")
                return

    def _run(self, job: Job) -> None:
        with job.lock:
            job.status = "loading"
            job.message = "モデルを準備しています…"
            job.progress = 0.0
            job.segments.clear()
        self._persist(job)

        def on_status(message: str) -> None:
            with job.lock:
                job.message = message

        def on_segment(segment: Segment) -> None:
            with job.lock:
                job.segments.append(segment)

        def on_progress(value: float) -> None:
            with job.lock:
                job.progress = max(job.progress, round(value * 100, 1))
                job.status = "processing"

        with job.lock:
            job.status = "processing"
            job.message = "文字起こし中…"

        result = self.engine.transcribe(
            self._source_path(job),
            job.model,
            job.options,
            on_segment=on_segment,
            on_progress=on_progress,
            on_status=on_status,
            cancel=job.cancel_event,
        )

        result_meta = {
            "source": job.filename,
            "language": result.language,
            "language_probability": round(result.language_probability, 4),
            "duration_seconds": round(result.duration, 3),
            "elapsed_seconds": round(result.elapsed, 3),
            "model": result.model,
            "device": result.device,
            "compute_type": result.compute_type,
            "speed_factor": round(result.duration / result.elapsed, 2)
            if result.elapsed > 0
            else 0.0,
        }
        outputs = write_exports(
            result.segments,
            result_meta,
            job.options.formats,
            job.directory,
            job.stem,
            job.options.txt_timestamps,
        )

        with job.lock:
            job.segments = list(result.segments)
            job.language = result.language
            job.language_probability = result.language_probability
            job.duration = result.duration
            job.elapsed = result.elapsed
            job.device = result.device
            job.compute_type = result.compute_type
            job.outputs = outputs
            job.progress = 100.0
            job.status = "done"
            job.message = "完了しました"
        self._persist(job)

    def _source_path(self, job: Job) -> Path:
        for path in job.directory.glob("source.*"):
            return path
        raise FileNotFoundError("入力ファイルが見つかりません")

    def _fail(self, job: Job, message: str) -> None:
        with job.lock:
            job.status = "error"
            job.error = message
            job.message = "エラーが発生しました"
        self._persist(job)

    def _persist(self, job: Job) -> None:
        try:
            _atomic_json(job.directory / "meta.json", job.snapshot(include_segments=False))
            if job.status == "done":
                payload = job.snapshot(include_segments=True)
                _atomic_json(job.directory / "result.json", payload)
        except OSError:
            pass

    def _load_from_disk(self, job_id: str) -> Job | None:
        directory = config.JOB_DIR / job_id
        meta_path = directory / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        options = TranscribeOptions(
            language=meta.get("options", {}).get("language", "ja"),
            task=meta.get("options", {}).get("task", "transcribe"),
            vad=bool(meta.get("options", {}).get("vad", True)),
            beam_size=int(meta.get("options", {}).get("beam_size", 1)),
            initial_prompt=meta.get("options", {}).get("initial_prompt", ""),
            formats=tuple(meta.get("options", {}).get("formats", ["srt", "txt"])),
            txt_timestamps=bool(meta.get("options", {}).get("txt_timestamps", False)),
        )
        job = Job(
            id=job_id,
            filename=meta.get("filename", ""),
            stem=meta.get("stem", "audio"),
            model=meta.get("model", config.DEFAULT_MODEL),
            options=options,
            directory=directory,
            created_at=float(meta.get("created_at", time.time())),
        )
        job.status = meta.get("status", "error")
        job.progress = float(meta.get("progress", 0.0))
        job.message = meta.get("message", "")
        job.error = meta.get("error", "")
        job.language = meta.get("language", "")
        job.language_probability = float(meta.get("language_probability", 0.0))
        job.duration = float(meta.get("duration", 0.0))
        job.elapsed = float(meta.get("elapsed", 0.0))
        job.device = meta.get("device", "")
        job.compute_type = meta.get("compute_type", "")
        job.outputs = list(meta.get("outputs", []))
        if job.status in ("queued", "loading", "processing"):
            job.status = "error"
            job.message = "サーバー再起動により中断されました"
            job.error = "interrupted"
        try:
            payload = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            job.segments = [
                Segment(float(item["start"]), float(item["end"]), str(item["text"]))
                for item in payload.get("segments", [])
            ]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        with self._guard:
            self._jobs.setdefault(job_id, job)
        return job

    # -- cleanup -----------------------------------------------------------

    def cleanup(self, ttl_seconds: float) -> int:
        cutoff = time.time() - ttl_seconds
        removed = 0
        active = self.active_ids()
        for root in (config.UPLOAD_DIR, config.JOB_DIR):
            try:
                entries = list(root.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir() or entry.name in active:
                    continue
                try:
                    created = entry.stat().st_mtime
                    meta_path = entry / "meta.json"
                    if meta_path.is_file():
                        try:
                            created = float(
                                json.loads(meta_path.read_text(encoding="utf-8")).get(
                                    "created_at", created
                                )
                            )
                        except (OSError, ValueError, TypeError):
                            pass
                    if created < cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                        removed += 1
                except OSError:
                    continue
        if removed:
            with self._guard:
                for job_id in list(self._jobs):
                    job = self._jobs[job_id]
                    if not job.directory.exists():
                        self._jobs.pop(job_id, None)
        return removed
