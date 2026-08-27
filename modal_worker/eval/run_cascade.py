"""Run the deployed Modal cascade over the eval set.

Calls the *deployed* app by name rather than going through `modal run`, which is
both how Phase B would call it from riffd and the only way to measure a warm
request: `modal run` builds an ephemeral app per invocation, so every call it
makes is a cold one.

    modal deploy modal_worker/worker.py
    python modal_worker/eval/run_cascade.py

The first track pays container start (cold); the rest reuse it (warm).
"""

import json
import sys
import time
from pathlib import Path

import modal

APP = "riffd-separation"
CLS = "Cascade"
AUDIO = Path(__file__).parent / "audio"
OUT = Path(__file__).parent / "out" / "cascade"


def main(slugs):
    Cascade = modal.Cls.from_name(APP, CLS)
    cascade = Cascade()

    results = {}
    for i, slug in enumerate(slugs):
        src = next(AUDIO.glob(f"{slug}.*"))
        dest = OUT / slug
        dest.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        out = cascade.separate.remote(src.read_bytes(), src.name)
        wall = time.time() - t0

        meta = out.pop("_meta")
        meta["client_wall_s"] = round(wall, 1)
        # meta["container"] comes from the worker, which knows whether this was
        # the first call on its container. Position in this loop does not: a
        # container can outlive the script that started it.
        meta["startup_overhead_s"] = round(wall - meta["total_s"], 1)
        for name, data in out.items():
            (dest / f"{name}.flac").write_bytes(data)
        (dest / "_meta.json").write_text(json.dumps(meta, indent=1))

        results[slug] = meta
        print(f"[cascade] {slug:16s} {meta['container']:4s} "
              f"wall={wall:6.1f}s gpu={meta['total_s']:6.1f}s "
              f"overhead={meta['startup_overhead_s']:5.1f}s "
              f"recon(f32)={meta['reconstruction']['float32']['rms_err_db']:.0f}dB "
              f"recon(delivered)={meta['reconstruction']['delivered']['rms_err_db']:.0f}dB "
              f"stages={meta['stage_s']}")

    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(p.stem for p in AUDIO.iterdir() if p.is_file()))
