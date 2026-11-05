"""Configuration and shared constants."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

DATA_DIR = Path(os.environ.get("TRANSCRIPTION_DATA_DIR") or BASE_DIR / "data")
UPLOAD_DIR = DATA_DIR / "uploads"
JOB_DIR = DATA_DIR / "jobs"
LOG_DIR = DATA_DIR / "logs"

TTL_HOURS = float(os.environ.get("TRANSCRIPTION_TTL_HOURS", "12"))
MAX_UPLOAD_BYTES = int(float(os.environ.get("TRANSCRIPTION_MAX_UPLOAD_GB", "4")) * 1024**3)
CHUNK_SIZE = max(1, int(float(os.environ.get("TRANSCRIPTION_CHUNK_MB", "8")) * 1024**2))
UPLOAD_CONCURRENCY = max(1, min(8, int(os.environ.get("TRANSCRIPTION_UPLOAD_CONCURRENCY", "4"))))
MAX_QUEUE = max(1, int(os.environ.get("TRANSCRIPTION_MAX_QUEUE", "8")))

GPU_INDEX = int(os.environ.get("TRANSCRIPTION_GPU_INDEX", "0"))
DEFAULT_MODEL = os.environ.get("TRANSCRIPTION_MODEL", "small")
MODEL_IDLE_SECONDS = int(os.environ.get("TRANSCRIPTION_MODEL_IDLE_SECONDS", "600"))
CPU_THREADS = max(1, min(16, int(os.environ.get("TRANSCRIPTION_CPU_THREADS", "4"))))

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wma", ".aiff", ".aif", ".amr", ".mp4", ".mkv", ".mov", ".webm",
    ".avi", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts",
}

MODEL_CHOICES = [
    ("small", "small（軽量・標準）"),
    ("base", "base（高速・低精度）"),
    ("tiny", "tiny（最速・下書き）"),
    ("large-v3-turbo", "large-v3-turbo（高精度）"),
    ("medium", "medium（高精度・重い）"),
    ("large-v3", "large-v3（最高精度・最重）"),
]
MODEL_IDS = {model for model, _ in MODEL_CHOICES}
MODEL_LABELS = dict(MODEL_CHOICES)

LANGUAGE_CHOICES = [
    ("auto", "自動検出"),
    ("ja", "日本語"),
    ("en", "English"),
    ("zh", "中文"),
    ("ko", "한국어"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("pt", "Português"),
    ("it", "Italiano"),
    ("ru", "Русский"),
    ("nl", "Nederlands"),
    ("pl", "Polski"),
    ("tr", "Türkçe"),
    ("uk", "Українська"),
    ("ar", "العربية"),
    ("hi", "हिन्दी"),
    ("vi", "Tiếng Việt"),
    ("th", "ไทย"),
    ("id", "Bahasa Indonesia"),
    ("sv", "Svenska"),
    ("fi", "Suomi"),
    ("da", "Dansk"),
    ("cs", "Čeština"),
    ("el", "Ελληνικά"),
    ("he", "עברית"),
]
LANGUAGE_IDS = {code for code, _ in LANGUAGE_CHOICES}

TASK_CHOICES = [("transcribe", "文字起こし"), ("translate", "英語に翻訳")]
TASK_IDS = {code for code, _ in TASK_CHOICES}

FORMAT_CHOICES = ["srt", "txt", "vtt", "json"]
