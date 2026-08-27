"""Baseline: raw htdemucs_6s stems from the current Replicate path.

Calls processor._separate_stems_replicate() directly — the same function the
live pipeline uses — and stops there. The riffd stereo-refinement and labelling
stages that normally run afterwards are deliberately NOT applied: this eval
compares separators, and those stages would apply equally to either one.

Nothing in processor.py or app.py is modified; this only imports them.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv

load_dotenv()
os.environ["USE_HOSTED_SEPARATION"] = "true"       # force the production path

import processor  # noqa: E402

AUDIO = Path(__file__).parent / "audio"
OUT = Path(__file__).parent / "out" / "incumbent"


def main(slugs):
    OUT.mkdir(parents=True, exist_ok=True)
    timings = {}
    for slug in slugs:
        src = next(AUDIO.glob(f"{slug}.*"))
        dest_dir = OUT / slug
        if (dest_dir / "_timing.json").exists():
            print(f"[incumbent] {slug}: already done, skipping")
            timings[slug] = json.loads((dest_dir / "_timing.json").read_text())
            continue
        work = OUT / f"_work_{slug}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        report = {}
        t0 = time.time()
        raw, model = processor._separate_stems_replicate(
            src, work, progress_callback=lambda m: print(f"   {m}"), report=report)
        wall = time.time() - t0

        dest_dir.mkdir(parents=True, exist_ok=True)
        for name, path in raw.items():
            shutil.move(path, dest_dir / f"{name}.wav")
        rec = {"wall_s": round(wall, 1), "model": model,
               "stems": sorted(raw), "report": report}
        (dest_dir / "_timing.json").write_text(json.dumps(rec, indent=1))
        timings[slug] = rec
        shutil.rmtree(work, ignore_errors=True)
        print(f"[incumbent] {slug}: {wall:.1f}s  model={model}  stems={sorted(raw)}")
    print(json.dumps(timings, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or [p.stem for p in sorted(AUDIO.iterdir()) if p.is_file()])
