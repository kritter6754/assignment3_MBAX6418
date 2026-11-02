# Stress test: 40 songs, 5 genres (2026-11-01)

**Setup:** a fresh install from the GitHub ZIP of commit `ea2905b`, set up using only the README:
- `.env` was copied from `.env.example` with only the key filled in, and `class-endpoints.txt` was dropped into the folder.
- The app ran locally with `uv run ai-jukebox`. The startup check reported all four class services OK.

**Input** (`scripts/stress_test.py`):
- 40 songs, 8 for each of Synth-Pop, Classic Rock, Bossa Nova, House and Neo-Soul.
- Lengths of 90, 95, 100, 105, 110, 120, 135 and 150 s; 24 vocal and 16 instrumental.
- Moods and themes varied.
- All 40 were submitted at once; the app runs two generations at a time.

## Results

| Check | Expected | Actual |
|---|---|---|
| Songs finished | 40 / 40 | **40 / 40**, none failed, no retries needed |
| Total time | — | 476 s (7.9 min), about one finished song every 12 s |
| App responsiveness under load | stays usable | library and status requests: median 9 ms, max 44 ms |
| Audio length ≥ 1:30 | every song | **all 40**, between 90.0 and 150.0 s, each within 0.1 s of the requested length |
| Cover art | one per song | **40 / 40** (768×768 JPEG) |
| Instrumental tracks | no lyrics | 16 / 16 marked `[Instrumental]` |
| Vocal tracks | lyrics split into lines | **22 / 24**: two songs came back as one run-on string (see below) |
| Unique titles | no repeats | **3 titles appeared twice** ("Neon Horizon", "Sepia Memories", "Golden Hour Glow") |
| Genre playlists | 5 playlists × 8 songs | created through the API; all 5 shown as library chips |

## Problems found and fixed

1. **Run-on lyrics (2 of 24 vocal songs).**
   - **What happened:** Qwen sometimes returned a whole song with no line breaks, e.g. `[Verse]Golden light spills across the sand,Waves whisper soft…`. ACE-Step then received no line structure, and the lyrics card had nothing to highlight.
   - **Fix:** `normalize_lyrics()` puts section tags on their own lines and splits lines where punctuation is directly followed by a capital letter. The Qwen prompt now asks for one lyric line per row. The repair runs on new songs and also when existing songs are displayed.
   - **After the fix:** the two songs show 32 and 41 lines, and the three bundled samples keep their wording and line breaks (only blank lines between sections are added).
2. **Repeated titles (3 pairs out of 40).**
   - **What happened:** songs given the same theme got the same title.
   - **Fix:** Qwen now receives the titles already in the library and is told to choose another. If it repeats one anyway, the app adds "II", "III", and so on.
   - **After the fix:** a new song with the theme that caused repeats ("late night city lights", Synth-Pop) was titled "Chrome Skyline Dreams", not a repeat.

## Not checked

- Musical quality was not judged by ear for all 40 songs.
- How closely the estimated lyric timing matches the singing was not measured.
- No concurrent use by several browsers was tested.
