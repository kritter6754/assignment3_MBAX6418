"""Background song generation: Qwen plan -> (FLUX cover || ACE-Step music) -> library.

A job remembers what already succeeded, so retrying a failed job resumes at the failed step
instead of starting over (a finished 2-minute song is not regenerated because the cover failed).
"""
from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .config import redact
from .creative import SongPlan, SongRequest, plan_song
from .library import Library, save_cover
from .services import ServiceError, Services

log = logging.getLogger(__name__)

STEPS = ("plan", "cover", "music")
STEP_LABELS = {"plan": "Writing title & lyrics (Qwen)", "cover": "Painting cover art (FLUX)",
               "music": "Composing music (ACE-Step)"}


@dataclass
class Job:
    id: str
    request: SongRequest
    status: str = "queued"  # queued | running | done | failed
    steps: dict[str, str] = field(default_factory=lambda: {s: "pending" for s in STEPS})
    error: str = ""
    plan: SongPlan | None = None
    track_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id, "status": self.status, "error": self.error,
            "steps": [{"key": k, "label": STEP_LABELS[k], "state": v} for k, v in self.steps.items()],
            "title": self.plan.title if self.plan else None,
            "request": asdict(self.request), "track_id": self.track_id,
            "elapsed_s": round(self.updated_at - self.created_at),
        }


class JobManager:
    def __init__(self, services: Services, library: Library, max_parallel: int = 2):
        self.svc = services
        self.lib = library
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_parallel, thread_name_prefix="song")

    def submit(self, req: SongRequest) -> Job:
        req.validate()
        job = Job(id=secrets.token_hex(6), request=req)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def retry(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status != "failed":
            raise ValueError("Only failed jobs can be retried.")
        job.status, job.error = "queued", ""
        for k, v in job.steps.items():
            if v == "failed":
                job.steps[k] = "pending"
        self._touch(job)
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def dismiss(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status in ("done", "failed"):
                del self._jobs[job_id]
                if job.status == "failed":
                    shutil.rmtree(self.lib.tracks_dir / job.id, ignore_errors=True)

    @staticmethod
    def _touch(job: Job) -> None:
        job.updated_at = time.time()

    def _step(self, job: Job, name: str, fn) -> None:
        if job.steps[name] == "done":
            return
        job.steps[name] = "running"
        self._touch(job)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - every failure becomes a readable job error
            job.steps[name] = "failed"
            msg = str(e) if isinstance(e, (ServiceError, ValueError)) else f"{type(e).__name__}: {e}"
            log.warning("job %s step %s failed: %s", job.id, name, redact(msg))
            raise ServiceError(redact(msg)) from None
        job.steps[name] = "done"
        self._touch(job)

    def _run(self, job: Job) -> None:
        job.status = "running"
        self._touch(job)
        req = job.request
        folder = self.lib.tracks_dir / job.id
        folder.mkdir(parents=True, exist_ok=True)

        def do_plan():
            job.plan = plan_song(self.svc, req)

        def do_cover():
            save_cover(self.svc.image(job.plan.cover_prompt), folder)

        def do_music():
            a = self.svc.music(job.plan.caption, job.plan.lyrics, req.duration_s, req.language)
            if len(a.data) < 1000:
                raise ServiceError("Music service (ACE-Step): returned an empty audio file")
            (folder / f"audio.{a.ext}").write_bytes(a.data)

        try:
            self._step(job, "plan", do_plan)
            # Cover and music are independent, so they run at the same time.
            errors: list[str] = []
            threads = []
            for name, fn in (("cover", do_cover), ("music", do_music)):
                def run(name=name, fn=fn):
                    try:
                        self._step(job, name, fn)
                    except ServiceError as e:
                        errors.append(str(e))
                threads.append(threading.Thread(target=run))
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if errors:
                raise ServiceError(" | ".join(errors))
        except ServiceError as e:
            job.status, job.error = "failed", str(e)
            self._touch(job)
            return

        audio_name = next(folder.glob("audio.*")).name
        p = job.plan
        track = self.lib.add(job.id, {
            "title": p.title, "summary": p.summary, "lyrics": p.lyrics, "caption": p.caption,
            "cover_prompt": p.cover_prompt, "style": req.style, "mood": req.mood,
            "theme": req.theme, "duration_s": req.duration_s, "vocal": req.vocal,
            "language": req.language, "audio": audio_name, "cover": "cover.jpg",
        })
        job.track_id = track["id"]
        job.status = "done"
        self._touch(job)
