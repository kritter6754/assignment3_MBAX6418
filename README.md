# AI Jukebox — MBAX 6418 Assignment 3

A web app that (1) generates custom songs, (2) paints matching cover art, and (3) plays the
library with an AI DJ who talks between tracks. All of it runs on the class services:

| Feature | Class service | Port |
|---|---|---|
| Song titles, lyrics, music captions, cover prompts, DJ scripts | Qwen3.6-35B-A3B | 9001 |
| Cover art | FLUX.2 klein 4B | 9006 |
| Music (instrumental and vocal) | ACE-Step 1.5 XL Turbo | 9007 |
| DJ speech (default or uploaded voice) | Chatterbox Multilingual V3 | 9008 |

No paid services are used, and port 9000 is not used.

> **Status:** work in progress. Sections marked _TODO_ are filled in from real tests against the class endpoints.

## How it works

```
Browser (jukebox/static)                 Server (FastAPI, jukebox/)                  Class services
─────────────────────────                ──────────────────────────                  ──────────────
Create-a-song form ── POST /api/generate ─▶ JobManager (background thread)
                                           1. plan_song ───────────────────────────▶ Qwen  :9001
                                              title, lyrics, music caption, cover prompt
                                           2. in parallel: cover ──────────────────▶ FLUX  :9006
                                                           music ──────────────────▶ ACE-Step :9007
Status panel ◀─ GET /api/jobs (polling) ── 3. song added to the library (DATA_DIR/tracks)
Player: song ▶ DJ ▶ song ▶ …
  next DJ clip prefetched ─ POST /api/dj ─▶ write_dj_line ─────────────────────────▶ Qwen  :9001
                                           speak (default or uploaded voice) ──────▶ Chatterbox :9008
Voice upload ─────────── POST /api/voice ─▶ DATA_DIR/voices/<browser session> (never served back)
```

- **No overlap:** a single `<audio>` element plays songs and DJ clips one after another, so music and speech can never play at the same time.
- **Commentary is ready on time:** the DJ clip for the next transition is requested as soon as a song starts.
- **Credentials stay server-side:** the API key and service URLs never reach the browser, and `redact()` scrubs the key from logs and error messages.

## Setup

**Requirements:** Python 3.12 and [uv](https://docs.astral.sh/uv/). No other system software is needed (no ffmpeg). Docker is optional.

```bash
uv sync                      # installs dependencies from uv.lock
cp .env.example .env         # then edit .env (git-ignored) with the class credentials
uv run ai-jukebox            # http://127.0.0.1:8000
```

`.env` settings (placeholders only; take the real values from `class-endpoints.txt`):

```ini
CLASS_API_KEY=replace-me
LLM_BASE_URL=http://example.com:9001/v1
IMAGE_BASE_URL=http://example.com:9006/v1
MUSIC_BASE_URL=http://example.com:9007
TTS_BASE_URL=http://example.com:9008/v1
DATA_DIR=data
APP_PASSWORD=              # optional: password-protect a public deployment
MOCK_SERVICES=false        # true = offline fakes for UI development only
```

If the credentials are missing, the app still starts and plays the sample songs. A red banner then explains that generation and the DJ are switched off.

### Deploying on Railway

1. Create a Railway project from this GitHub repo. Railway builds it from the `Dockerfile` (see `railway.json`), with health check `/healthz`.
2. Under **Variables**, add `CLASS_API_KEY`, `LLM_BASE_URL`, `IMAGE_BASE_URL`, `MUSIC_BASE_URL` and `TTS_BASE_URL`, and optionally `APP_PASSWORD`. Never put them in the repo.
3. Optional: attach a volume at `/data`, so generated songs survive redeploys. Without one, they last until the container restarts.

### Running with Docker

```bash
docker build -t ai-jukebox . && docker run --env-file .env -p 8080:8080 ai-jukebox
```

## Using the three core features

1. **Custom song generation:**
   - In **Create a song**, pick or type a style and a mood.
   - Optionally add a theme, then set the duration (15–240 s).
   - Choose **Vocal** (Qwen writes the lyrics; you choose their language) or **Instrumental**.
   - Click **Generate**. The **Generation status** panel shows each step: writing (Qwen), cover (FLUX), music (ACE-Step).
   - When the song is done, it appears in the library marked **NEW**.
2. **Matching cover art:** each new song gets its own FLUX cover, drawn from a prompt Qwen writes using the song's theme, style and mood. The cover appears on the song's library card and in the player.
3. **AI DJ:**
   - Press **Play all**, or queue two or more songs.
   - Between songs, the DJ (Qwen script, Chatterbox voice) talks about the song that just ended and the one coming up. The script is shown in the player.
   - To change the DJ's voice, upload an MP3 or WAV recording (10–20 s of clear speech) under **AI DJ**. **Use default** switches back to the default voice.
   - The DJ checkbox turns commentary off.

**Controls:**

- **Play/pause:** the button, or the space bar.
- **Skip:** during a song, moves on to the DJ break and then the next song. During the DJ, skips straight to the next song.
- **Queue panel:** shows what is up next and lets you remove songs.

**Recovering from errors:**

- **Failed generation:** the job shows a plain-language error and a **Retry** button. A retry resumes at the failed step and keeps the parts that already succeeded (for example, a finished cover is not regenerated).
- **DJ failure or timeout:** a toast explains what went wrong and playback moves on to the next song.
- **Unusable voice upload:** the DJ falls back to the default voice and says so.

## Sample songs

`assets/samples/` holds three ready-to-play songs generated in advance with the class endpoints by `scripts/generate_samples.py`. The script uses the same pipeline as the app. Each sample's `meta.json` records:

- the inputs (style, mood, theme, duration, vocal or instrumental)
- the lyrics Qwen generated
- the music caption and cover prompt
- the models used and the generation time

All lyrics are AI-generated. No third-party music, lyrics, artwork or voice samples are bundled.

```bash
uv run python scripts/generate_samples.py          # needs .env; skips samples that exist
uv run python scripts/generate_samples.py --force  # regenerate all three
```

_TODO: describe the three samples (titles, why these styles) once they are generated._

## Tests

```bash
uv run pytest        # 22 offline tests: API, job retry, DJ caching, voice validation, request formats
```

The automated tests use mocked services. The request formats in `jukebox/services.py` still have to be confirmed against the live class endpoints.

### Test results

_TODO: record inputs, expected and actual results for each core feature and at least one failure case, from runs against the class endpoints, and separate completed tests from unchecked claims._

| # | Feature | Input | Expected | Actual | Status |
|---|---|---|---|---|---|
| 1 | Song generation | | | | not yet run |
| 2 | Cover art | | | | not yet run |
| 3 | AI DJ (default voice) | | | | not yet run |
| 4 | AI DJ (uploaded voice) | | | | not yet run |
| 5 | Failure case | | | | not yet run |

## Design judgment

_TODO (team, in your own words, with evidence from testing):_
- _One important design choice (candidates: one audio element plus a prefetched DJ clip; resuming a failed job from its failed step; cover and music generated in parallel)._
- _One limitation or failure you investigated and what you learned._

## Screenshots

_TODO: add to `docs/screenshots/` and link them here (library, generation status, DJ playing, voice upload, error/retry)._

## Safeguards

- **Credentials:** read from environment variables on the server only. `.env` and `class-endpoints.txt` are git-ignored and excluded from Docker builds.
- **Generated songs, DJ clips and voice uploads:** stored under `data/`, which is git-ignored. Each voice recording belongs to one browser session, is never served to any browser, and is deleted by **Use default**.

## Project layout

```
jukebox/app.py         FastAPI routes, media serving, optional password
jukebox/services.py    class-service clients (all request formats) + offline mocks
jukebox/creative.py    Qwen prompts: song plan and DJ script
jukebox/jobs.py        background generation with per-step status and retry
jukebox/dj.py          DJ clips (cached, deduplicated) and voice uploads
jukebox/library.py     samples + generated songs
jukebox/static/        single-page player (HTML/CSS/JS, no build step)
scripts/               sample generation
tests/                 pytest suite
```
