"""HTTP server: JSON API + the single-page player in jukebox/static.

All calls to the class services happen here on the server; the browser only ever sees
song metadata and media URLs, never credentials or service URLs.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, install_log_redaction, load_settings
from .creative import LANGUAGES, MAX_DURATION, MIN_DURATION, SongRequest
from .dj import DJ, VoiceStore
from .jobs import JobManager
from .library import Library
from .services import ServiceError, Services, make_services

log = logging.getLogger("jukebox")
STATIC = Path(__file__).parent / "static"

STYLES = ["Lo-fi hip hop", "Synthwave", "Indie pop", "Acoustic folk", "Jazz", "Funk",
          "Rock", "EDM", "Cinematic orchestral", "Reggae", "Country", "R&B"]
MOODS = ["Chill", "Happy", "Melancholic", "Energetic", "Dreamy", "Romantic", "Epic", "Mysterious"]


class GenerateBody(BaseModel):
    style: str = Field(min_length=1, max_length=80)
    mood: str = Field(min_length=1, max_length=80)
    duration_s: int = 60
    vocal: bool = True
    theme: str = Field(default="", max_length=300)
    language: str = "en"


class DJBody(BaseModel):
    prev_id: str | None = None
    next_id: str
    dj_name: str = Field(default="DJ Nova", max_length=40)


def create_app(settings: Settings | None = None, services: Services | None = None,
               samples_dir: Path | None = None) -> FastAPI:
    settings = settings or load_settings()
    services = services or make_services(settings)
    data_dir = settings.data_dir
    library = Library(data_dir, samples_dir) if samples_dir else Library(data_dir)
    jobs = JobManager(services, library)
    voices = VoiceStore(data_dir)
    dj = DJ(services, library, voices, data_dir)

    app = FastAPI(title="AI Jukebox", docs_url=None, redoc_url=None)
    app.state.jobs, app.state.library = jobs, library

    if settings.app_password:
        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/healthz":
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            ok = False
            if auth.startswith("Basic "):
                try:
                    _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                    ok = secrets.compare_digest(pw, settings.app_password)
                except ValueError:
                    ok = False
            if not ok:
                return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="AI Jukebox"'})
            return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, e: ServiceError):
        return JSONResponse({"detail": str(e)}, status_code=502)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # -- pages & health ----------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/config")
    def config():
        return {"configured": settings.configured, "mock": settings.mock,
                "styles": STYLES, "moods": MOODS, "languages": LANGUAGES,
                "min_duration": MIN_DURATION, "max_duration": MAX_DURATION}

    # -- library & generation ----------------------------------------------
    @app.get("/api/tracks")
    def tracks():
        return [Library.public(t) for t in library.list()]

    @app.post("/api/generate")
    def generate(body: GenerateBody):
        if not settings.configured:
            raise HTTPException(503, "The server has no class-service credentials configured "
                                     "(set CLASS_API_KEY and the *_BASE_URL variables).")
        return jobs.submit(SongRequest(**body.model_dump())).public()

    @app.get("/api/jobs")
    def list_jobs():
        return [j.public() for j in jobs.list()]

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        try:
            return jobs.retry(job_id).public()
        except KeyError:
            raise HTTPException(404, "That generation job no longer exists.") from None

    @app.delete("/api/jobs/{job_id}")
    def dismiss_job(job_id: str):
        jobs.dismiss(job_id)
        return {"ok": True}

    # -- DJ & voice ----------------------------------------------------------
    @app.post("/api/dj")
    def dj_clip(body: DJBody, x_session: str | None = Header(default=None)):
        if not settings.configured:
            raise HTTPException(503, "The DJ needs class-service credentials on the server.")
        try:
            return dj.clip(body.prev_id, body.next_id, x_session, body.dj_name.strip() or "DJ Nova")
        except KeyError:
            raise HTTPException(404, "Unknown track.") from None

    @app.get("/api/voice")
    def voice_status(x_session: str = Header()):
        p = voices.path(x_session)
        return {"has_voice": p is not None, "format": p.suffix[1:].upper() if p else None}

    @app.post("/api/voice")
    async def voice_upload(file: UploadFile = File(...), x_session: str = Header()):
        data = await file.read(10 * 1024 * 1024 + 1)
        return voices.save(x_session, file.filename or "", data)

    @app.delete("/api/voice")
    def voice_delete(x_session: str = Header()):
        voices.delete(x_session)
        return {"has_voice": False}

    # -- media ------------------------------------------------------------------
    roots = {"samples": library.samples_dir, "tracks": library.tracks_dir, "dj": dj.dir}

    @app.get("/media/{kind}/{path:path}", include_in_schema=False)
    def media(kind: str, path: str):
        root = roots.get(kind)
        if root is None:
            raise HTTPException(404)
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file() or target.name == "meta.json":
            raise HTTPException(404)
        return FileResponse(target, headers={"Cache-Control": "public, max-age=86400"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()
    settings = load_settings()
    if settings.mock:
        log.warning("MOCK_SERVICES=true: songs, covers and DJ speech are FAKE placeholders.")
    elif not settings.configured:
        log.warning("Class-service credentials are missing: sample songs play, generation and the DJ are disabled.")
    uvicorn.run(create_app(settings), host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")), log_level="info")


if __name__ == "__main__":
    main()
