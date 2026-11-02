"""Time each generation step for different song lengths, one song at a time.

    uv run python scripts/timing_benchmark.py http://localhost:8000

Uses the class services (about 2 minutes). Times are measured from the browser's point of view:
when the app reports each step (lyrics/Qwen, cover/FLUX, music/ACE-Step) as finished.
"""
import sys
import time

import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
c = httpx.Client(timeout=60)
RUNS = [(15, True), (30, True), (60, True), (60, False), (90, True), (120, True), (180, True), (240, True), (240, False)]

for dur, vocal in RUNS:
    t0 = time.time()
    job = c.post(B + "/api/generate", json={"style": "Indie Pop", "mood": "Happy", "duration_s": dur,
                                             "vocal": vocal, "theme": "timing benchmark"}).json()
    seen = {}
    while True:
        j = next(x for x in c.get(B + "/api/jobs").json() if x["id"] == job["id"])
        for s in j["steps"]:
            if s["state"] in ("done", "failed") and s["key"] not in seen:
                seen[s["key"]] = time.time() - t0
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    print(f"{dur:>4}s {'vocal' if vocal else 'instr'}: lyrics {seen.get('plan', 0):.1f}s | "
          f"cover done {seen.get('cover', 0):.1f}s | music done {seen.get('music', 0):.1f}s | "
          f"total {time.time() - t0:.1f}s | {j['status']}", flush=True)
