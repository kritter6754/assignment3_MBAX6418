"""HTTP server: JSON API + the single-page player in jukebox/static.

All calls to the class services happen here on the server; the browser only ever sees
song metadata and media URLs, never credentials or service URLs.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, install_log_redaction, load_settings
from .creative import LANGUAGES, MAX_DURATION, MIN_DURATION, SongRequest
from .dj import DJ, VoiceStore
from .jobs import JobManager
from .library import Library
from .playlists import PlaylistStore
from .services import ServiceError, Services, make_services

log = logging.getLogger("jukebox")
STATIC = Path(__file__).parent / "static"

# Style dropdown, in display order. Users can also type any custom style.
STYLE_GROUPS: dict[str, list[str]] = {
    "Rock": ["Classic Rock", "Grunge", "Punk Rock", "Indie Rock", "Psychedelic Rock", "Heavy Metal",
             "Shoegaze", "Pop-Punk"],
    "Pop & Dance": ["Synth-Pop", "Disco", "Bubblegum Pop", "Hyperpop", "Dream Pop", "Eurodance"],
    "Soul, Funk & R&B": ["Motown / Northern Soul", "Neo-Soul", "Funk", "Gospel", "Contemporary R&B", "Doo-Wop"],
    "Hip Hop": ["Boom Bap", "Trap", "Conscious Rap", "G-Funk", "Drill"],
    "Electronic": ["House", "Techno", "Drum and Bass", "Dubstep", "Ambient", "Trance"],
    "Country & Folk": ["Bluegrass", "Outlaw Country", "Honky-Tonk", "Traditional Folk", "Americana"],
    "Blues & Jazz": ["Delta Blues", "Bebop", "Smooth Jazz", "Chicago Blues"],
    "Latin & Caribbean": ["Bossa Nova", "Reggae", "Salsa", "Reggaeton", "Bachata", "Ska"],
    "Other": ["Chamber Pop", "Choral / A Cappella", "Flamenco", "Torch Song"],
}
MOODS = ["Chill", "Happy", "Melancholic", "Energetic", "Dreamy", "Romantic", "Epic", "Mysterious"]


class GenerateBody(BaseModel):
    style: str = Field(min_length=1, max_length=80)
    mood: str = Field(min_length=1, max_length=80)
    duration_s: int = 60
    vocal: bool = True
    theme: str = Field(default="", max_length=300)
    language: str = "en"


class PlaylistBody(BaseModel):
    name: str = Field(max_length=200)
    track_ids: list[str] = Field(default_factory=list, max_length=500)


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    track_ids: list[str] | None = Field(default=None, max_length=500)


class DJBody(BaseModel):
    prev_id: str | None = None
    next_id: str
    dj_name: str = Field(default="DJ Nova", max_length=40)


def _versioned_index() -> str:
    """index.html with ?v=<content hash> on its script and stylesheet, so a browser never pairs a
    new page with an old cached app.js (which left the style dropdown empty after a deploy)."""
    html = (STATIC / "index.html").read_text()
    for name in ("app.js", "style.css"):
        digest = hashlib.sha1((STATIC / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f"/static/{name}", f"/static/{name}?v={digest}")
    return html


def create_app(settings: Settings | None = None, services: Services | None = None,
               samples_dir: Path | None = None) -> FastAPI:
    settings = settings or load_settings()
    services = services or make_services(settings)
    data_dir = settings.data_dir
    library = Library(data_dir, samples_dir) if samples_dir else Library(data_dir)
    jobs = JobManager(services, library)
    voices = VoiceStore(data_dir)
    dj = DJ(services, library, voices, data_dir)
    playlists = PlaylistStore(data_dir)

    app = FastAPI(title="AI Jukebox", docs_url=None, redoc_url=None)
    index_html = _versioned_index()
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
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/config")
    def config():
        return {"configured": settings.configured, "mock": settings.mock,
                "style_groups": STYLE_GROUPS, "moods": MOODS, "languages": LANGUAGES,
                "min_duration": MIN_DURATION, "max_duration": MAX_DURATION}

    @app.get("/api/status")
    def status():
        """Which class services this server can reach (hosts only, never credentials)."""
        if not settings.configured:
            return {"configured": False, "services": {}}
        return {"configured": True, "services": services.status()}

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

    # -- playlists (library folders) -------------------------------------------
    def known(ids: list[str]) -> list[str]:
        unknown = [i for i in ids if library.get(i) is None]
        if unknown:
            raise HTTPException(404, "One of those songs is no longer in the library.")
        return ids

    def visible(p: dict) -> dict:
        # Songs can vanish (e.g. a redeploy without a volume); hide ids that no longer exist.
        return {**p, "track_ids": [i for i in p["track_ids"] if library.get(i) is not None]}

    @app.get("/api/playlists")
    def list_playlists():
        return [visible(p) for p in playlists.list()]

    @app.post("/api/playlists")
    def create_playlist(body: PlaylistBody):
        return visible(playlists.create(body.name, known(body.track_ids)))

    @app.patch("/api/playlists/{pid}")
    def update_playlist(pid: str, body: PlaylistPatch):
        try:
            return visible(playlists.update(pid, body.name,
                                            known(body.track_ids) if body.track_ids is not None else None))
        except KeyError:
            raise HTTPException(404, "That playlist no longer exists.") from None

    @app.put("/api/playlists/{pid}/tracks/{track_id}")
    def add_to_playlist(pid: str, track_id: str):
        known([track_id])
        try:
            return visible(playlists.add_track(pid, track_id))
        except KeyError:
            raise HTTPException(404, "That playlist no longer exists.") from None

    @app.delete("/api/playlists/{pid}/tracks/{track_id}")
    def remove_from_playlist(pid: str, track_id: str):
        try:
            return visible(playlists.remove_track(pid, track_id))
        except KeyError:
            raise HTTPException(404, "That playlist no longer exists.") from None

    @app.delete("/api/playlists/{pid}")
    def delete_playlist(pid: str):
        playlists.delete(pid)
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
