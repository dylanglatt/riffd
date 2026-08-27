"""Prove the worker survives a MAX_TRACK_MINUTES-length input (finding 6).

riffd's MAX_TRACK_MINUTES is 20, so the worker has to survive 20 minutes. The
`memory=8192` on the Cascade class is a calculation, not a measurement — this
script is what replaces it with one.

Records into eval/out/long_track/: peak container RSS, wall time, per-stage GPU
time, and the size of the returned FLACs.

    python modal_worker/eval/run_long_track.py

Currently BLOCKED — the Modal workspace is over its spend limit. See BLOCKED.md.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

AUDIO = Path(__file__).parent / "audio"
OUT = Path(__file__).parent / "out" / "long_track"

# ~20 minutes, and deliberately a real recording rather than a concatenation:
# a looped track would give the models repeated content and could flatter the
# chunked inference path.
DEFAULT_QUERY = "Pink Floyd Echoes 1971 Meddle full"
DEFAULT_SLUG = "echoes_long"
TARGET_MIN, TARGET_MAX = 17 * 60, 21 * 60


def ensure_audio(slug, query):
    existing = list(AUDIO.glob(f"{slug}.*"))
    if existing:
        return existing[0]
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    from downloader import download_audio_from_youtube
    import shutil

    src = download_audio_from_youtube(query, f"eval_{slug}")
    dest = AUDIO / f"{slug}{Path(src).suffix}"
    AUDIO.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True).stdout.strip()
    return float(out)


def main():
    import modal

    slug = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SLUG
    src = ensure_audio(slug, DEFAULT_QUERY)
    dur = duration(src)
    print(f"[long] {src.name}: {dur/60:.1f} min ({dur:.0f} s)")
    if not (TARGET_MIN <= dur <= TARGET_MAX):
        print(f"[long] WARNING: wanted {TARGET_MIN/60:.0f}-{TARGET_MAX/60:.0f} min; "
              f"this is {dur/60:.1f}. The point is to exercise MAX_TRACK_MINUTES=20.")

    OUT.mkdir(parents=True, exist_ok=True)
    cascade = modal.Cls.from_name("riffd-separation", "Cascade")()

    t0 = time.time()
    out = cascade.separate.remote(src.read_bytes(), src.name)
    wall = time.time() - t0

    meta = out.pop("_meta")
    meta["client_wall_s"] = round(wall, 1)
    meta["input_duration_s"] = round(dur, 1)
    meta["output_bytes"] = {k: len(v) for k, v in out.items()}
    meta["output_total_mb"] = round(sum(len(v) for v in out.values()) / 1e6, 1)

    for name, data in out.items():
        (OUT / f"{name}.flac").write_bytes(data)
    (OUT / "_meta.json").write_text(json.dumps(meta, indent=1))

    print(f"[long] wall={wall:.1f}s  gpu={meta['total_s']}s  "
          f"peak_rss={meta.get('peak_rss_mb')} MB of "
          f"{meta.get('memory_request_mb')} MB requested")
    print(f"[long] returned {meta['output_total_mb']} MB of FLAC")
    print(f"[long] stages: {meta['stage_s']}")
    headroom = meta.get("memory_request_mb", 0) - (meta.get("peak_rss_mb") or 0)
    print(f"[long] headroom: {headroom:.0f} MB — "
          f"{'OK' if headroom > 512 else 'TIGHT, raise memory='}")


if __name__ == "__main__":
    main()
