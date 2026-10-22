"""Generate the three bundled sample songs with the class endpoints.

Uses exactly the same pipeline as the app (Qwen plan -> FLUX cover + ACE-Step music) and
writes assets/samples/<NN-slug>/{audio.mp3, cover.jpg, meta.json}. meta.json records the
inputs, the AI-generated lyrics, the prompts sent to each model and when it was made, so the
samples' provenance can be checked.

    uv run python scripts/generate_samples.py            # skip samples that already exist
    uv run python scripts/generate_samples.py --force    # regenerate all three
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jukebox.config import SAMPLES_DIR, load_settings  # noqa: E402
from jukebox.creative import SongRequest, plan_song  # noqa: E402
from jukebox.library import save_cover  # noqa: E402
from jukebox.services import ClassServices  # noqa: E402

SAMPLES = [
    SongRequest(style="Lo-fi hip hop", mood="Chill", duration_s=60, vocal=False,
                theme="a rainy afternoon in a quiet study café"),
    SongRequest(style="Indie pop", mood="Happy", duration_s=75, vocal=True,
                theme="road trip with friends at the start of summer"),
    SongRequest(style="Synthwave", mood="Dreamy", duration_s=75, vocal=True,
                theme="driving through a neon city at midnight"),
]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate samples that already exist")
    args = ap.parse_args()
    settings = load_settings()
    if not settings.configured or settings.mock:
        print("Set CLASS_API_KEY and the *_BASE_URL variables (and MOCK_SERVICES=false) first.")
        return 1
    svc = ClassServices(settings)
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    for n, req in enumerate(SAMPLES, start=1):
        existing = sorted(SAMPLES_DIR.glob(f"{n:02d}-*"))
        if existing and not args.force:
            print(f"[{n}] exists ({existing[0].name}); skipping")
            continue
        for old in existing:
            shutil.rmtree(old)
        req.validate()
        print(f"[{n}] planning {req.style} / {req.mood} ...", flush=True)
        t0 = time.time()
        plan = plan_song(svc, req)
        folder = SAMPLES_DIR / f"{n:02d}-{slug(plan.title)}"
        folder.mkdir()
        print(f"[{n}] '{plan.title}': cover ...", flush=True)
        cover = save_cover(svc.image(plan.cover_prompt), folder)
        print(f"[{n}] music ({req.duration_s}s) ...", flush=True)
        audio = svc.music(plan.caption, plan.lyrics, req.duration_s, req.language)
        audio_name = f"audio.{audio.ext}"
        (folder / audio_name).write_bytes(audio.data)
        meta = {
            "title": plan.title, "summary": plan.summary, "lyrics": plan.lyrics,
            "caption": plan.caption, "cover_prompt": plan.cover_prompt,
            **asdict(req), "audio": audio_name, "cover": cover,
            "generated_with": {"text": settings.llm_model, "image": settings.image_model,
                               "music": settings.music_model},
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "generation_seconds": round(time.time() - t0),
        }
        (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        print(f"[{n}] done in {meta['generation_seconds']}s -> {folder.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
