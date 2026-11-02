# Requirement-by-requirement test (2026-11-01)

Each requirement from the assignment brief was tested against a **fresh install of the GitHub ZIP**:
- Commit `cfad1fd`, installed with `pip install -r requirements.txt` on Python 3.13, using only the README.
- `class-endpoints.txt` was in the folder, and `.env` was copied from `.env.example` with only the key filled in.
- Tests ran against the live class services, through a real browser (Playwright) or the app's API.
- Outages were simulated with a small relay in front of ACE-Step or Chatterbox that returned "503 service unavailable" on demand.

"Test n" refers to the **Test results** table in the README.

## Core features

| Requirement (from the brief) | How it was tested | Result |
|---|---|---|
| Choose a song's **style, mood and duration** | Dropdown style (Bossa Nova), custom style (Celtic sea shanty), several moods, 15 s and 240 s (the limits), 9 invalid inputs | Pass (tests 1, 2, 13) |
| Generate **playable audio with ACE-Step** and **add it to the library** | Audio lengths measured from the saved files; each song shown and played from the library | Pass: 15.0 s and 240.0 s as requested; 40/40 at 90–150 s in the stress test (tests 1–3) |
| Support **instrumental and vocal** tracks, with **AI-generated lyrics** for vocal | An instrumental at 240 s; vocal in Spanish and English | Pass: instrumental has no lyrics; Qwen wrote the vocal lyrics (tests 1–2) |
| A **new FLUX cover for each custom song**, shown with the **correct track**, reflecting theme, style or mood | Covers inspected next to each song's settings; 40 covers in the stress test | Pass, judged by eye (test 4) |
| **Song-specific commentary** by Qwen, **spoken by Chatterbox**, **between queued songs** | Three songs queued, then the DJ scripts checked for both song titles | Pass: each break named the song that ended and the next one (test 5) |
| **Default voice without an upload** | Test 5 with no upload | Pass |
| **Upload an MP3 or WAV** to customise the voice | AI-generated reference uploaded as MP3 and as WAV, plus 4 invalid files | Pass: uploaded voice used without fallback; invalid files rejected with clear messages (tests 6–7). Whether it *sounds* like the reference was not judged by ear. |

## Other functional requirements

| Requirement | Result |
|---|---|
| Three ready-to-play **sample songs with titles and covers**, generated with the class endpoints, with **AI lyrics** | Pass: in `assets/samples/` and in the ZIP. Each `meta.json` records the inputs, lyrics, prompts and models. |
| **New songs join the library and stay available during the session** | Pass: they survive a page reload and an app restart (saved in `data/`). |
| **Play/pause and skip** controls | Pass, including 26 rapid clicks with no broken state (test 8). Rewind was added as well. |
| **Queue advances automatically**, DJ and music **in sequence without overlap** | Pass: song → DJ → song → DJ → song, never more than one sound playing (test 5). |
| **Show generation status** | Pass: each step (lyrics/Qwen, cover/FLUX, music/ACE-Step) shows pending, running, done or failed. |
| **Understandable errors** and **recovery from failed requests** | Pass: the music outage, DJ outage, wrong key, unreachable host and restart mid-generation each show a plain message. **Retry** finished a failed song without redoing completed steps (tests 9–14). |

## Safeguards and services

| Requirement | Result |
|---|---|
| Credentials **server-side**, out of GitHub, the ZIP, browser code, logs and documentation; placeholders in examples | Pass: the key was not found in the page, scripts, API responses, server logs or the ZIP. Config examples use `replace-me` and `example.com` (test 16). |
| **Never commit user uploads or voice recordings** | Pass: `data/` (voices, generated songs, playlists) is git-ignored and not in the ZIP. Uploads can't be downloaded and are private to each browser (test 17). |
| **No third-party music, lyrics, cover art or voice samples** bundled | Pass: the only media in the repo are the 3 generated samples. |
| Use the **class services** for the required features; **no paid services**; **no port 9000** | Pass: ports 9001, 9006, 9007 and 9008 only. No paid or third-party AI packages in the dependencies. |
| **Deploy from the submitted ZIP** with the documentation and class credentials | Pass on macOS with uv (Python 3.12) and with pip (Python 3.13) (test 18). Not tested on Windows. |

## Bugs this test found (all fixed)

These are bugs 10–13 in the README's bug table:
- **10:** the startup check gave a misleading hint for a wrong key, and missed a bad key for ACE-Step.
- **11:** a song in progress vanished silently when the app restarted, and left a half-finished folder.
- **12:** technical wording for invalid input.
- **13:** a missing period in the DJ error message.

Each fix was rechecked against the live services.
