"""Parallel chunked uploads.

The browser splits a file into fixed-size chunks and uploads several of them at
once.  Each chunk is written directly at its final offset, so no expensive
re-assembly step is needed and interrupted uploads can resume.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class UploadError(ValueError):
    pass


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _safe_name(filename: str) -> tuple[str, str, str]:
    name = Path(filename.replace("\\", "/")).name
    name = _BAD_NAME.sub("_", name).strip().strip(".")
    if not name:
        name = "audio.wav"
    ext = Path(name).suffix.lower()
    if ext not in config.AUDIO_EXTENSIONS:
        raise UploadError(f"対応していないファイル形式です: {ext or '不明'}")
    stem = Path(name).stem.strip().strip(".") or "audio"
    stem = stem[:80]
    return name, stem, ext


class UploadManager:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock(self, upload_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(upload_id, threading.Lock())

    def _meta_path(self, upload_id: str) -> Path:
        return config.UPLOAD_DIR / upload_id / "meta.json"

    def _read(self, upload_id: str) -> dict:
        if not _ID_RE.match(upload_id):
            raise UploadError("不正なアップロード ID です")
        try:
            return json.loads(self._meta_path(upload_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UploadError("アップロードが見つかりません") from exc

    def init(self, filename: str, size: int, content_type: str = "") -> dict:
        if size <= 0:
            raise UploadError("空のファイルはアップロードできません")
        if size > config.MAX_UPLOAD_BYTES:
            limit = config.MAX_UPLOAD_BYTES / 1024**3
            raise UploadError(f"ファイルサイズの上限は {limit:.1f} GB です")

        name, stem, ext = _safe_name(filename)
        upload_id = uuid.uuid4().hex
        directory = config.UPLOAD_DIR / upload_id
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / f"source{ext}"
        with source.open("wb") as handle:
            handle.truncate(size)

        total_chunks = math.ceil(size / config.CHUNK_SIZE)
        meta = {
            "id": upload_id,
            "filename": name,
            "stem": stem,
            "ext": ext,
            "size": size,
            "content_type": content_type,
            "chunk_size": config.CHUNK_SIZE,
            "total_chunks": total_chunks,
            "received": {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _atomic_json(directory / "meta.json", meta)
        return {
            "upload_id": upload_id,
            "chunk_size": config.CHUNK_SIZE,
            "total_chunks": total_chunks,
            "concurrency": config.UPLOAD_CONCURRENCY,
            "max_upload_bytes": config.MAX_UPLOAD_BYTES,
        }

    def chunk_size(self, index: int, total_size: int) -> int:
        start = index * config.CHUNK_SIZE
        return max(0, min(config.CHUNK_SIZE, total_size - start))

    def save_chunk(self, upload_id: str, index: int, data: bytes) -> dict:
        lock = self._lock(upload_id)
        with lock:
            meta = self._read(upload_id)
            total = int(meta["total_chunks"])
            if index < 0 or index >= total:
                raise UploadError("不正なチャンク番号です")
            expected = self.chunk_size(index, int(meta["size"]))
            if len(data) != expected:
                raise UploadError(
                    f"チャンクサイズが一致しません（期待 {expected} / 実際 {len(data)}）"
                )

            source = config.UPLOAD_DIR / upload_id / f"source{meta['ext']}"
            with source.open("r+b") as handle:
                handle.seek(index * int(meta["chunk_size"]))
                handle.write(data)

            meta["received"][str(index)] = len(data)
            meta["updated_at"] = time.time()
            _atomic_json(config.UPLOAD_DIR / upload_id / "meta.json", meta)
            received = len(meta["received"])
            return {"received": received, "total": total}

    def complete(self, upload_id: str) -> tuple[Path, dict]:
        lock = self._lock(upload_id)
        with lock:
            meta = self._read(upload_id)
            total = int(meta["total_chunks"])
            received = meta.get("received") or {}
            if len(received) != total:
                raise UploadError(
                    f"アップロードが未完了です（{len(received)} / {total} チャンク）"
                )
            if sum(int(value) for value in received.values()) != int(meta["size"]):
                raise UploadError("アップロードされたサイズが一致しません")
            source = config.UPLOAD_DIR / upload_id / f"source{meta['ext']}"
            if not source.is_file() or source.stat().st_size != int(meta["size"]):
                raise UploadError("アップロードファイルが破損しています")
            return source, meta

    def discard(self, upload_id: str) -> None:
        if not _ID_RE.match(upload_id):
            return
        shutil.rmtree(config.UPLOAD_DIR / upload_id, ignore_errors=True)
        with self._guard:
            self._locks.pop(upload_id, None)
