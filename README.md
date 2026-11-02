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

> **Status:** sections marked _TODO_ are still being filled in from tests against the class endpoints.

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

## Setup (run it on your own computer)

**You need:** Python 3.12 or newer, and internet access to the class services. [uv](https://docs.astral.sh/uv/) is recommended, but plain `pip` also works. No other software is needed (no ffmpeg, no Node, no Docker).

### 1. Get the code

Download the ZIP from GitHub and extract it, or `git clone` the repository. Open a terminal **in the extracted folder** (the one containing `README.md`).

### 2. Supply the class credentials

The app reads credentials only on the server side. Your browser never sees them.

1. Copy the example settings file:
   - macOS/Linux: `cp .env.example .env`
   - Windows: `copy .env.example .env`
2. Open `.env` in a text editor and set `CLASS_API_KEY=` to the class key.
3. Give the app the four service URLs. Either:
   - **easiest:** put the class's `class-endpoints.txt` into this folder. The app reads the Qwen (9001), FLUX (9006), ACE-Step (9007) and Chatterbox (9008) URLs from it, or
   - fill in `LLM_BASE_URL`, `IMAGE_BASE_URL`, `MUSIC_BASE_URL` and `TTS_BASE_URL` in `.env`.

URLs work with or without a trailing `/v1`, and spaces around them are ignored. Values in `.env` take priority over `class-endpoints.txt`. Unedited `example.com` placeholders count as "not set", so they never hide the real URLs. Both `.env` and `class-endpoints.txt` are git-ignored.

Example `.env` (placeholders, not real values):

```ini
CLASS_API_KEY=replace-me
# Only needed if class-endpoints.txt is not in the folder:
LLM_BASE_URL=http://example.com:9001/v1
IMAGE_BASE_URL=http://example.com:9006/v1
MUSIC_BASE_URL=http://example.com:9007
TTS_BASE_URL=http://example.com:9008/v1
```

### 3. Install and start

With uv:

```bash
uv sync              # installs the exact dependency versions from uv.lock
uv run ai-jukebox    # starts the app
```

Without uv:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m jukebox                  # starts the app
```

Then open **http://localhost:8000**. On startup, the terminal checks each class service:

```
INFO jukebox: Checking the class services...
INFO jukebox:   OK    Qwen (text)            dobolyi.com:9001 (170 ms)
INFO jukebox:   OK    FLUX (cover art)       dobolyi.com:9006 (130 ms)
INFO jukebox:   OK    ACE-Step (music)       dobolyi.com:9007 (120 ms)
INFO jukebox:   OK    Chatterbox (DJ voice)  dobolyi.com:9008 (130 ms)
```

Stop the app with **Ctrl+C**. Songs, covers and playlists you create are saved in the `data/` folder and are still there the next time you start the app. To start fresh, stop the app and delete `data/`. The three sample songs are never deleted.

### Troubleshooting

| What you see | What to do |
|---|---|
| Red banner "Class-service credentials missing" | `CLASS_API_KEY` or a service URL is not set. Check `.env`, or that `class-endpoints.txt` is in the same folder as `README.md`. Restart the app after editing. |
| `FAIL` lines in the startup check | That service's URL is wrong or the service is down. The line names the host it tried. **http://localhost:8000/api/status** shows the same check. |
| "address already in use" | Another program is using port 8000. Start with another port: `PORT=8001 uv run ai-jukebox` (Windows: `set PORT=8001` first). |
| Generation fails with "rejected our credentials" | The key is wrong or expired. |
| A song or DJ break fails | The message says which service failed. Press **Retry** (generation), or playback simply continues (DJ). The class services are shared, so a busy moment can cause a timeout. |

### Optional: Docker

If you prefer a container, use the same `.env`:

```bash
docker build -t ai-jukebox .
docker run --env-file .env -v "$PWD/data:/data" -p 8080:8080 ai-jukebox
```

Then open http://localhost:8080. The `-v` option keeps generated songs and playlists in the local `data/` folder. `class-endpoints.txt` is not copied into the image, so with Docker put the four URLs in `.env`.

## Using the app

**1. Custom song generation** (Create a song, right column):
- Pick a **style** from the dropdown (50 genres in 9 groups), or choose **Custom** to type any style.
- Type a **mood**, and optionally a **theme**.
- Choose **Vocal** (Qwen writes original lyrics in the language you pick) or **Instrumental**, and set the **duration** (15–240 s).
- Click **Generate song + cover**. **Generation status** shows each step: lyrics (Qwen), cover (FLUX), music (ACE-Step). A 60-second song usually takes 10–20 seconds.
- The finished song appears in the library marked **NEW**.

**2. Matching cover art:** every new song gets its own FLUX cover, painted from a prompt Qwen writes from the song's theme, style and mood. The cover appears on the song's card, in the player, and in the playlist pickers.

**3. Integrated AI DJ:**
- Press **▶ Play all**, or queue two or more songs.
- Between songs, the DJ talks about the song that just ended and the one coming up: Qwen writes the script and Chatterbox speaks it. The script appears in the player and in the Lyrics card.
- To give the DJ a different voice, upload an MP3 or WAV recording (10–20 s of clear speech) under **AI DJ**. **Use default** switches back.
- Untick **Talk between songs** to turn the DJ off. **DJ name** changes what the DJ calls itself.

**Player and library:**
- **⏮ / ▶⏸ / ⏭:** rewind restarts the song, or goes to the previous song if pressed in its first 3 seconds. Play/pause also works with the space bar. Skip goes to the DJ break, then the next song.
- **Card buttons:** the ▶ on a song card turns into ⏸ while that song plays. Cards also have **+ Queue**, **Lyrics/Details**, and 🗂 (add to playlist).
- **Playlists:** **+ Create Playlist** makes a named folder and lets you pick its songs. The chips above the library switch between **All songs** and each playlist, and **▶ Play all** plays what is shown. **✎ Edit** renames a playlist or changes its songs, and **🗑 Delete** removes it (the songs stay in the library).
- **Lyrics card:** follows the playing song and highlights the current line. Click any line to jump there. ACE-Step returns no timing data, so line times are **estimated** from the song's length (see Design judgment).

**Recovering from errors:**
- **Failed generation:** the job shows a plain-language error and a **Retry** button. A retry resumes at the failed step and keeps the parts that already succeeded (a finished cover is not regenerated).
- **DJ failure or timeout:** a message explains it, and playback continues with the next song.
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

| # | Title | Style / mood | Type | Length | Theme given |
|---|---|---|---|---|---|
| 1 | Rainy Pages | Lo-fi hip hop / Chill | instrumental | 1:00 | a rainy afternoon in a quiet study café |
| 2 | Golden Hour Drive | Indie pop / Happy | vocal (AI lyrics) | 1:15 | road trip with friends at the start of summer |
| 3 | Neon Horizon Cruise | Synthwave / Dreamy | vocal (AI lyrics) | 1:15 | driving through a neon city at midnight |

The three cover one instrumental and two vocal tracks in different styles, so the DJ, the lyrics card and the playlists all have something to show right after install. Each took 10–12 s to generate.

## Tests

```bash
uv run pytest    # offline: API, retry, DJ caching, voice checks, playlists, settings, request formats
```

The automated tests replace the class services with mocks, so they run without network or credentials. The request formats in `jukebox/services.py` were checked against each service's published API description (`/openapi.json`) and against live calls.

### Test results

_TODO: record inputs, expected and actual results for each core feature and at least one failure case, from runs against the class endpoints, and separate completed tests from unchecked claims._

| # | Feature | Input | Expected | Actual | Status |
|---|---|---|---|---|---|
| 1 | Song generation | | | | not yet run |
| 2 | Cover art | | | | not yet run |
| 3 | AI DJ (default voice) | | | | not yet run |
| 4 | AI DJ (uploaded voice) | | | | not yet run |
| 5 | Failure case | | | | not yet run |

### Bugs found during testing

Every bug below was found by running the app against the live class services, by a stress test, or by a browser test. The **How found** column says which. Each one is fixed in the current code, and a regression test now covers most of them (`uv run pytest`).

| # | Bug | How found | Cause | Fix |
|---|---|---|---|---|
| 1 | **Lyrics with no line breaks.** 2 of 24 vocal songs had their whole song in one run-on string (`[Verse]Golden light spills across the sand,Waves whisper soft…`). The lyrics card showed one giant line, and ACE-Step got no line structure. | 40-song stress test | Qwen occasionally drops the newline characters inside the JSON `lyrics` field. | `normalize_lyrics()` puts section tags on their own lines and splits lines where punctuation runs straight into a capital letter. The prompt now asks for one line per row. The repair also runs when older songs are displayed, so the two affected songs now show 32 and 41 lines. |
| 2 | **Repeated song titles.** 3 titles appeared twice among 40 songs ("Neon Horizon", "Sepia Memories", "Golden Hour Glow"). | 40-song stress test | Each song is planned independently, so songs with the same theme got the same title. | Qwen now receives the titles already in the library and is told to pick a new one. If it still repeats one, the app adds "II", "III", and so on. A retest with the theme that caused repeats produced a new title. |
| 3 | **DJ voice upload rejected (HTTP 400).** | First live test against Chatterbox | The request format was guessed before we had the service's documentation (wrong field names and model id). | Matched the format in the service's published API description: model `chatterbox-v3`, `language_id`, and a multipart `file` + `request` upload. Verified with a live call. |
| 4 | **DJ broke when the Chatterbox URL had no `/v1`.** The class endpoints file lists `http://…:9008` without it. | Comparing `class-endpoints.txt` with the working settings | The app built `/audio/speech` onto the URL as given, which needs the `/v1` prefix. | All service URLs are normalised. They work with or without `/v1`, and spaces are ignored. The app can also read `class-endpoints.txt` directly. |
| 5 | **Song generation hung for 3 minutes, then failed** on a temporary cloud copy the team used for demos (since removed). | Testing that cloud deployment | Its settings still held the `example.com` placeholder URLs from `.env.example`, so every connection attempt waited until it timed out. | Connection attempts now give up after 10 s, and the error names the host it tried to reach. Unedited placeholders are treated as "not set". `/api/status` and the startup check show which services the app can reach. |
| 6 | **Error messages said only "HTTP 400"**, not why. | The same deployment, when an old model name was set | Only the status code was shown. | Errors now include the service's own explanation, e.g. "Input should be 'chatterbox-v3'". |
| 7 | **The DJ invented a song.** Introducing the first song, it promised a "Midnight Drive" that didn't exist. | Reading DJ scripts in a live check | The intro prompt didn't forbid naming other songs. | The prompt now says the next song is the only one the DJ knows about and that it must never invent titles. Rechecked live. |
| 8 | **Empty style dropdown after an update.** | Our own browser, right after a deploy | The browser reused a cached old `app.js` with the new page, and the old script crashed before filling the list (`c.styles is not iterable`). | `app.js` and `style.css` are now loaded with a version tag based on their contents, so every change forces a fresh download. Verified by feeding a browser the old cached script. |
| 9 | **Server log errors** (`TypeError: %d format`). | Live end-to-end run | The filter that scrubs the API key from logs turned numeric log values into text. | Only text values are scrubbed now. |

### Known limitations (not fixed)

- **Lyric timing is estimated.** ACE-Step returns no timing data, and none of the class services can recover it from the audio. The lyrics card spreads the lines over the song's length and labels this "timing estimated". Clicking a line jumps there, which also works as a manual resync. We have not measured how closely the estimate follows the singing.
- **One shared library.** Generated songs and playlists are shared by everyone using the same running app. Only voice uploads are private to each browser.
- **Not tested:** the musical quality of every generated song by ear, and several people generating at the same time.

The full stress-test numbers are in [docs/stress-test.md](docs/stress-test.md).

## Design judgment

_TODO (team, in your own words, with evidence from testing):_
- _One important design choice (candidates: one audio element plus a prefetched DJ clip; resuming a failed job from its failed step; cover and music generated in parallel)._
- _One limitation or failure you investigated and what you learned._

## Screenshots

_TODO: add to `docs/screenshots/` and link them here (library, generation status, DJ playing, voice upload, error/retry)._

## Safeguards

- **Credentials:** read on the server only (from `.env`, environment variables or `class-endpoints.txt`), never sent to the browser, and scrubbed from logs and error messages. `.env` and `class-endpoints.txt` are git-ignored and excluded from Docker builds.
- **Generated songs, DJ clips and voice uploads:** stored under `data/`, which is git-ignored. Each voice recording belongs to one browser session, is never served to any browser, and is deleted by **Use default**.

## Project layout

```
jukebox/app.py         FastAPI routes, media serving, optional password
jukebox/services.py    class-service clients (all request formats) + offline mocks
jukebox/creative.py    Qwen prompts: song plan and DJ script
jukebox/jobs.py        background generation with per-step status and retry
jukebox/dj.py          DJ clips (cached, deduplicated) and voice uploads
jukebox/library.py     samples + generated songs
jukebox/playlists.py   playlists (library folders), saved in DATA_DIR
jukebox/static/        single-page player (HTML/CSS/JS, no build step)
scripts/               sample generation
tests/                 pytest suite
```
