"""Local-only voice recording storage and Vosk transcription helpers."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


class LocalVoiceTranscriber:
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024
    DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600
    ALLOWED_SUFFIXES = {".webm", ".ogg", ".opus", ".wav", ".mp3", ".m4a", ".mp4", ".flac", ".aac"}

    def __init__(self, ffmpeg_path: Callable[[], str], model_path: Callable[[], str], root: Path):
        self._ffmpeg_path = ffmpeg_path
        self._model_path = model_path
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._cleanup_recordings()

    def _cleanup_recordings(self) -> None:
        cutoff = time.time() - self.DEFAULT_RETENTION_SECONDS
        try:
            for path in self.root.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass

    def _ffmpeg(self) -> str:
        configured = str(self._ffmpeg_path() or "").strip().strip('"')
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return str(path.resolve())
            # Permit a command name such as "ffmpeg" as well as a full path.
            found = shutil.which(configured)
            if found:
                return found
            return ""
        return shutil.which("ffmpeg") or ""

    def status(self) -> dict:
        ffmpeg = self._ffmpeg()
        configured_model = str(self._model_path() or "").strip()
        model = Path(configured_model).expanduser() if configured_model else None
        try:
            model_ok = bool(model and model.is_dir() and any(model.iterdir()))
        except OSError:
            model_ok = False
        try:
            vosk_installed = importlib.util.find_spec("vosk") is not None
        except (ImportError, ValueError):
            vosk_installed = False
        with self._lock:
            jobs = sorted((self._public_job(x) for x in self._jobs.values()), key=lambda x: x.get("created_at", 0), reverse=True)[:5]
        return {
            "ready": bool(ffmpeg and model_ok and vosk_installed),
            "ffmpeg_available": bool(ffmpeg),
            "ffmpeg_path": ffmpeg or str(self._ffmpeg_path() or ""),
            "vosk_installed": vosk_installed,
            "model_available": bool(model_ok),
            "model_path": str(model or ""),
            "recordings_dir": str(self.root),
            "jobs": jobs,
            "max_upload_bytes": self.MAX_UPLOAD_BYTES,
        }

    @staticmethod
    def _public_job(job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in {"path", "cancel"}}

    def start(self, data: bytes, filename: str, content_type: str = "") -> dict:
        raw = bytes(data or b"")
        if not raw:
            raise ValueError("صوتی برای پردازش دریافت نشد.")
        if len(raw) > self.MAX_UPLOAD_BYTES:
            raise ValueError("حجم صدا از سقف ۲۵ مگابایت بیشتر است.")
        status = self.status()
        if not status["ffmpeg_available"]:
            raise RuntimeError("مسیر FFmpeg را در تنظیمات صوت وارد کنید یا FFmpeg را در PATH قرار دهید.")
        if not status["vosk_installed"]:
            raise RuntimeError("بستهٔ Vosk نصب نیست. requirements-audio.txt را نصب کنید.")
        if not status["model_available"]:
            raise RuntimeError("مسیر پوشهٔ مدل Vosk را در تنظیمات صوت وارد کنید.")
        basename = Path(str(filename or "voice.webm")).name
        suffix = Path(basename).suffix.lower()
        if suffix not in self.ALLOWED_SUFFIXES:
            suffix = ".webm" if "webm" in str(content_type).lower() else ".audio"
        job_id = "voice_" + uuid.uuid4().hex[:16]
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(basename).stem).strip("._-")[:80] or "voice"
        audio_path = self.root / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_stem}-{job_id[-6:]}{suffix}"
        audio_path.write_bytes(raw)
        cancel = threading.Event()
        job = {
            "id": job_id, "state": "queued", "progress": 0.0, "text": "", "error": "",
            "filename": audio_path.name, "saved_path": str(audio_path), "bytes": len(raw),
            "created_at": time.time(), "updated_at": time.time(), "content_type": str(content_type)[:120],
            "path": str(audio_path), "cancel": cancel,
        }
        with self._lock:
            if any(item.get("state") in {"queued", "transcribing", "cancelling"} for item in self._jobs.values()):
                try: audio_path.unlink(missing_ok=True)
                except OSError: pass
                raise RuntimeError("Another local voice transcription is already running.")
            self._jobs[job_id] = job
            self._cancel[job_id] = cancel
            for old_id, old in list(self._jobs.items()):
                if len(self._jobs) <= 20:
                    break
                if old.get("state") in {"done", "error", "cancelled"} and old_id != job_id:
                    self._jobs.pop(old_id, None)
                    self._cancel.pop(old_id, None)
                    # Metadata retention and disk retention should agree; completed
                    # jobs evicted from the in-memory history no longer need audio.
                    for candidate in (old.get("path"), old.get("transcript_path")):
                        try:
                            if candidate: Path(str(candidate)).unlink(missing_ok=True)
                        except OSError:
                            pass
        threading.Thread(target=self._run, args=(job_id,), name="local-voice-transcription", daemon=True).start()
        return {"ok": True, "job": job_id, "filename": audio_path.name, "state": "queued"}

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            cancel = job["cancel"]
            audio_path = Path(job["path"])
            job["state"] = "transcribing"
            job["updated_at"] = time.time()
        process = None
        reader = None
        try:
            from vosk import KaldiRecognizer, Model

            model_path = Path(str(self._model_path() or "")).expanduser()
            model = Model(str(model_path))
            recognizer = KaldiRecognizer(model, 16000)
            recognizer.SetWords(True)
            ffmpeg = self._ffmpeg()
            if not ffmpeg:
                raise RuntimeError("FFmpeg در دسترس نیست.")
            with tempfile.TemporaryFile() as error_stream:
                process = subprocess.Popen(
                    [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(audio_path),
                     "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
                    stdout=subprocess.PIPE, stderr=error_stream,
                )
                audio_chunks: queue.Queue = queue.Queue(maxsize=8)
                def pump_stdout():
                    try:
                        stream=process.stdout
                        while stream is not None and not cancel.is_set():
                            chunk=stream.read(8192)
                            if not chunk: break
                            while not cancel.is_set():
                                try: audio_chunks.put(chunk,timeout=0.2);break
                                except queue.Full: continue
                    finally:
                        while not cancel.is_set():
                            try: audio_chunks.put(None,timeout=0.2);break
                            except queue.Full: continue
                reader=threading.Thread(target=pump_stdout,name=f'voice-reader-{job_id[-6:]}',daemon=True)
                reader.start()
                parts: list[str] = []
                while True:
                    if cancel.is_set():
                        if process.poll() is None: process.terminate()
                        raise InterruptedError("Transcription cancelled")
                    try: chunk=audio_chunks.get(timeout=0.2)
                    except queue.Empty: continue
                    if chunk is None: break
                    if recognizer.AcceptWaveform(chunk):
                        text = json.loads(recognizer.Result()).get("text", "")
                        if text:
                            parts.append(text)
                    with self._lock:
                        current = self._jobs.get(job_id)
                        if current:
                            current["bytes_processed"] = int(current.get("bytes_processed", 0)) + len(chunk)
                            current["updated_at"] = time.time()
                return_code = process.wait(timeout=15)
                reader.join(timeout=1)
                if return_code:
                    error_stream.seek(0)
                    detail = error_stream.read(2400).decode("utf-8", errors="replace").strip()
                    raise RuntimeError("FFmpeg نتوانست فایل صوتی را بخواند." + (f" {detail}" if detail else ""))
                tail = json.loads(recognizer.FinalResult()).get("text", "")
                if tail:
                    parts.append(tail)
            text = " ".join(" ".join(parts).split()).strip()
            transcript_path = audio_path.with_suffix(audio_path.suffix + ".txt")
            transcript_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
            with self._lock:
                current = self._jobs.get(job_id)
                if current:
                    current.update({"state": "done", "progress": 1.0, "text": text,
                                    "transcript_path": str(transcript_path), "updated_at": time.time()})
        except InterruptedError:
            with self._lock:
                current = self._jobs.get(job_id)
                if current:
                    current.update({"state": "cancelled", "error": "لغو شد", "updated_at": time.time()})
        except Exception as exc:
            with self._lock:
                current = self._jobs.get(job_id)
                if current:
                    current.update({"state": "error", "error": str(exc)[:1500], "updated_at": time.time()})
        finally:
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except Exception:
                    pass
            if reader is not None and reader.is_alive():
                reader.join(timeout=0.5)

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            if not job:
                raise KeyError("Voice transcription job not found")
            return self._public_job(job)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            if not job:
                raise KeyError("Voice transcription job not found")
            if job.get("state") in {"done", "error", "cancelled"}:
                return self._public_job(job)
            job["cancel"].set()
            job["state"] = "cancelling"
            job["updated_at"] = time.time()
            return self._public_job(job)
