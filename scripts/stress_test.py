"""Stress test: 40 songs (5 genres x 8), all >= 90 s, submitted at once to a running app.

    uv run python scripts/stress_test.py http://localhost:8000 stress_results.json

Uses the class services (about 8 minutes). Writes per-job results to the JSON file.
"""
import json, statistics, sys, time
import httpx

B = sys.argv[1]
OUT = sys.argv[2]
c = httpx.Client(timeout=60, headers={"X-Session": "stresstest0001"})
GENRES = {
    "Synth-Pop": ("Dreamy", "Energetic", "Romantic", "Happy", "Melancholic", "Dreamy", "Energetic", "Mysterious"),
    "Classic Rock": ("Energetic", "Epic", "Happy", "Melancholic", "Energetic", "Romantic", "Epic", "Happy"),
    "Bossa Nova": ("Chill", "Romantic", "Dreamy", "Happy", "Chill", "Melancholic", "Romantic", "Chill"),
    "House": ("Energetic", "Happy", "Dreamy", "Energetic", "Mysterious", "Chill", "Happy", "Epic"),
    "Neo-Soul": ("Romantic", "Chill", "Melancholic", "Dreamy", "Romantic", "Happy", "Chill", "Melancholic"),
}
THEMES = ["late night city lights", "first summer love", "a long road home", "dancing in the kitchen",
          "rain on the window", "an old photograph", "running toward the sunrise", "a quiet beach at dusk"]
DURS = [90, 100, 110, 120, 135, 150, 95, 105]
specs = []
for g, moods in GENRES.items():
    for i, mood in enumerate(moods):
        vocal = (i % 8) not in ((1, 4, 6) if g != "House" else (0, 2, 4, 6))
        specs.append({"style": g, "mood": mood, "duration_s": DURS[i], "vocal": vocal, "theme": THEMES[i], "language": "en"})

def log(*a):
    print(*a, flush=True)

t0 = time.time()
jobs = {}
for sp in specs:
    r = c.post(B + "/api/generate", json=sp)
    r.raise_for_status()
    jobs[r.json()["id"]] = {"spec": sp, "submitted": time.time(), "attempts": 1}
log(f"submitted {len(jobs)} jobs in {time.time()-t0:.1f}s")

lat, retried, done_at = [], {}, {}
while True:
    t = time.time(); listing = c.get(B + "/api/jobs").json(); c.get(B + "/api/tracks"); lat.append(time.time() - t)
    by = {j["id"]: j for j in listing if j["id"] in jobs}
    for jid, j in by.items():
        if j["status"] == "done" and jid not in done_at:
            done_at[jid] = time.time()
            log(f"  done {len(done_at):2d}/40  {time.time()-t0:6.0f}s  {jobs[jid]['spec']['style']:<12} {j['title']}")
        if j["status"] == "failed" and jobs[jid]["attempts"] < 3:
            log(f"  FAILED {jobs[jid]['spec']['style']} (attempt {jobs[jid]['attempts']}): {j['error'][:160]} -> retry")
            retried.setdefault(jid, []).append(j["error"])
            jobs[jid]["attempts"] += 1
            c.post(B + f"/api/jobs/{jid}/retry")
    if all(by[j]["status"] in ("done", "failed") and (by[j]["status"] == "done" or jobs[j]["attempts"] >= 3) for j in jobs):
        break
    time.sleep(3)
total = time.time() - t0
final = {j["id"]: j for j in c.get(B + "/api/jobs").json() if j["id"] in jobs}
ok = [jid for jid in jobs if final[jid]["status"] == "done"]
log(f"\nfinished: {len(ok)}/40 done in {total:.0f}s ({total/60:.1f} min); jobs that needed a retry: {len(retried)}")
log(f"library/jobs request latency under load: median {statistics.median(lat)*1000:.0f} ms, max {max(lat)*1000:.0f} ms")
json.dump({"specs": specs, "jobs": {k: {**v, "final": final[k], "errors": retried.get(k, [])} for k, v in jobs.items()},
           "total_s": total, "latency_ms": [x * 1000 for x in lat]}, open(OUT, "w"), indent=1)
