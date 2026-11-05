"""Whisper inference with automatic processor selection.

The desktop app this project is based on lets the user pick a processor.  The
web version deliberately does not: it uses the NVIDIA GPU (CUDA) when it is
usable and falls back to CPU automatically.  int8_float16 is used on CUDA to
keep the VRAM footprint small enough to coexist with the local LLM server.
"""

from __future__ import annotations

import gc
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config
from .models import Segment


class Cancelled(Exception):
    """Raised when a job is cancelled."""


class OutOfMemory(RuntimeError):
    """Raised when CUDA cannot allocate enough memory."""


@dataclass(slots=True)
class TranscribeOptions:
    language: str = "ja"
    task: str = "transcribe"
    vad: bool = True
    beam_size: int = 1
    initial_prompt: str = ""
    formats: tuple[str, ...] = ("srt", "txt")
    txt_timestamps: bool = False


@dataclass(slots=True)
class TranscribeResult:
    segments: list[Segment]
    language: str
    language_probability: float
    duration: float
    elapsed: float
    model: str
    device: str
    compute_type: str


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def _is_out_of_memory(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "cuda_error_out_of_memory" in message


class Engine:
    """Keeps one model loaded and unloads it after an idle period."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model = None
        self._model_id: str | None = None
        self._device = "cpu"
        self._compute_type = "int8"
        self._last_used = time.time()
        self._force_cpu = False
        self._stop = threading.Event()
        self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True)
        self._idle_thread.start()

    # -- lifecycle ---------------------------------------------------------

    def _idle_loop(self) -> None:
        while not self._stop.wait(30):
            if config.MODEL_IDLE_SECONDS <= 0:
                continue
            if not self._lock.acquire(blocking=False):
                continue
            try:
                idle = time.time() - self._last_used
                if self._model is not None and idle > config.MODEL_IDLE_SECONDS:
                    self._unload_locked()
            finally:
                self._lock.release()

    def _unload_locked(self) -> None:
        self._model = None
        self._model_id = None
        gc.collect()

    def unload(self) -> None:
        with self._lock:
            self._unload_locked()

    def force_cpu(self) -> None:
        with self._lock:
            self._force_cpu = True
            self._unload_locked()

    def allow_gpu(self) -> None:
        with self._lock:
            if self._force_cpu:
                self._force_cpu = False
                self._unload_locked()

    def shutdown(self) -> None:
        self._stop.set()
        self.unload()

    # -- loading -----------------------------------------------------------

    def _choose(self) -> tuple[str, str]:
        if not self._force_cpu and cuda_device_count() > 0:
            return "cuda", "int8_float16"
        return "cpu", "int8"

    def _load_locked(self, model_id: str, progress: Callable[[str], None] | None) -> None:
        if self._model is not None and self._model_id == model_id:
            return

        self._unload_locked()
        device, compute_type = self._choose()
        if progress:
            progress(f"{model_id} モデルを準備しています…")

        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(
                model_id,
                device=device,
                device_index=config.GPU_INDEX,
                compute_type=compute_type,
                cpu_threads=config.CPU_THREADS,
                num_workers=1,
            )
        except Exception:
            if device == "cuda":
                self._force_cpu = True
                device, compute_type = "cpu", "int8"
                self._model = WhisperModel(
                    model_id,
                    device="cpu",
                    compute_type=compute_type,
                    cpu_threads=config.CPU_THREADS,
                    num_workers=1,
                )
            else:
                raise

        self._model_id = model_id
        self._device = device
        self._compute_type = compute_type
        self._last_used = time.time()
        if progress:
            progress("モデルの準備が完了しました")

    # -- inference ---------------------------------------------------------

    def transcribe(
        self,
        path: Path,
        model_id: str,
        options: TranscribeOptions,
        on_segment: Callable[[Segment], None] | None = None,
        on_progress: Callable[[float], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> TranscribeResult:
        cancel = cancel or threading.Event()
        with self._lock:
            self._load_locked(model_id, on_status)
            self._last_used = time.time()
            started = time.time()
            try:
                segments_iter, info = self._model.transcribe(
                    str(path),
                    language=None if options.language in ("", "auto") else options.language,
                    task=options.task,
                    beam_size=max(1, int(options.beam_size)),
                    vad_filter=bool(options.vad),
                    initial_prompt=(options.initial_prompt or None),
                    condition_on_previous_text=False,
                    word_timestamps=False,
                )
            except Exception as exc:
                if _is_out_of_memory(exc):
                    raise OutOfMemory(str(exc)) from exc
                raise

            duration = float(getattr(info, "duration", 0.0) or 0.0)
            segments: list[Segment] = []
            for raw in segments_iter:
                if cancel.is_set():
                    raise Cancelled
                text = (raw.text or "").strip()
                if not text:
                    continue
                segment = Segment(float(raw.start), float(raw.end), text)
                segments.append(segment)
                if on_segment:
                    on_segment(segment)
                if on_progress and duration > 0:
                    on_progress(min(1.0, max(0.0, segment.end / duration)))
                self._last_used = time.time()

            if on_progress:
                on_progress(1.0)
            self._last_used = time.time()
            return TranscribeResult(
                segments=segments,
                language=str(getattr(info, "language", "") or ""),
                language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
                duration=duration,
                elapsed=time.time() - started,
                model=model_id,
                device=self._device,
                compute_type=self._compute_type,
            )

    def describe(self) -> dict:
        with self._lock:
            return {
                "model": self._model_id,
                "device": self._device,
                "compute_type": self._compute_type,
                "loaded": self._model is not None,
                "cuda_devices": cuda_device_count(),
                "gpu_index": config.GPU_INDEX,
                "idle_unload_seconds": config.MODEL_IDLE_SECONDS,
            }
