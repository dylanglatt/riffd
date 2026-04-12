#!/usr/bin/env python3
"""
Ingest new demo tracks: Taste, Cruel Summer, Last Night, Tondo.
Writes directly to static/demo/ — no temp workspace needed.
Run from project root: python ingest_new_tracks.py
"""

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import init_db, upsert_demo_track

OUTPUT_DIR = Path("outputs")
DEMO_DIR = Path("static/demo")


def ingest_track(track):
    job_id = track["job_id"]
    slug = track["slug"]
    dest = DEMO_DIR / slug
    dest.mkdir(parents=True, exist_ok=True)
    dest_stems = dest / "stems"
    dest_stems.mkdir(exist_ok=True)

    # Load analysis and apply fixes to the data dict (no file deletes needed)
    src_cache = OUTPUT_DIR / job_id / "result_cache.json"
    data = json.loads(src_cache.read_text())
    for key in ("_analysis_version", "_cached_at", "job_id", "track_id",
                "audio_source", "audio_mode"):
        data.pop(key, None)

    stems_data = data.get("stems", {})

    # Determine which stem keys to skip (remove) and which to rename
    remove_keys = set()
    renames = {}   # old_key -> (new_key, new_label)
    for fix in track.get("fixes", []):
        if fix[0] == "remove":
            remove_keys.add(fix[1])
            print(f"    will skip: {fix[1]}")
        elif fix[0] == "rename":
            _, old, new, label = fix
            renames[old] = (new, label)
            print(f"    will rename: {old} → {new} ({label})")

    # Apply removes to cache
    for key in remove_keys:
        if key in stems_data:
            del stems_data[key]
            print(f"    removed from cache: {key}")

    # Apply renames to cache
    for old_key, (new_key, new_label) in renames.items():
        if old_key in stems_data:
            entry = stems_data.pop(old_key)
            entry["label"] = new_label
            stems_data[new_key] = entry
            print(f"    renamed in cache: {old_key} → {new_key}")

    # Copy only the stems we want (skip removed, rename as needed)
    src_stems = OUTPUT_DIR / job_id / "stems"
    copied = 0
    for f in src_stems.iterdir():
        if f.suffix != ".mp3":
            continue
        stem_key = f.stem  # filename without extension
        if stem_key in remove_keys:
            continue  # skip removed stems entirely
        dest_key = renames[stem_key][0] if stem_key in renames else stem_key
        dest_f = dest_stems / f"{dest_key}.mp3"
        shutil.copy(f, dest_f)
        os.chmod(dest_f, 0o644)
        copied += 1
    print(f"  Copied {copied} stems to {dest_stems}")

    # Write analysis.json
    (dest / "analysis.json").write_text(json.dumps(data, indent=2))
    print(f"  Wrote analysis.json ({len(stems_data)} stems: {list(stems_data.keys())})")

    # Download cover art
    cover_path = ""
    cover_url = track.get("cover", "")
    if cover_url:
        try:
            import requests
            resp = requests.get(cover_url, timeout=15)
            resp.raise_for_status()
            cover_file = dest / "cover.jpg"
            cover_file.write_bytes(resp.content)
            os.chmod(cover_file, 0o644)
            cover_path = f"/static/demo/{slug}/cover.jpg"
            print(f"  Downloaded cover art")
        except Exception as e:
            print(f"  WARNING: cover download failed: {e}")

    # Upsert DB record
    intel = data.get("intelligence", {})
    upsert_demo_track(
        slug=slug, title=track["title"], artist=track["artist"],
        year=track["year"], genre=track["genre"],
        key_display=intel.get("key", ""), bpm=int(intel.get("bpm", 0)),
        cover_path=cover_path,
        analysis_path=str(dest / "analysis.json"),
        stems_dir=str(dest_stems),
        display_order=track["order"],
        is_visible=1,
    )
    print(f"  DB: order={track['order']}, key={intel.get('key','')}, bpm={int(intel.get('bpm',0))}")


TRACKS = [
    {
        "job_id": "45affbd0",
        "slug": "taste",
        "title": "Taste",
        "artist": "Sabrina Carpenter",
        "year": "2024",
        "genre": "Pop",
        "order": 2,
        "cover": "https://i.scdn.co/image/ab67616d0000b273fd8d7a8d96871e791cb1f626",
        "fixes": [],
    },
    {
        "job_id": "7d982296",
        "slug": "cruel_summer",
        "title": "Cruel Summer",
        "artist": "Taylor Swift",
        "year": "2019",
        "genre": "Pop",
        "order": 3,
        "cover": "https://i.scdn.co/image/ab67616d0000b273e787cffec20aa2a396a61647",
        "fixes": [
            ("remove", "guitar"),
            ("rename", "atmosphere", "synth_lead", "Synth Lead"),
            ("rename", "synth", "synth_lead_2", "Synth Lead 2"),
        ],
    },
    {
        "job_id": "8bebf261",
        "slug": "last_night",
        "title": "Last Night",
        "artist": "Morgan Wallen",
        "year": "2023",
        "genre": "Country",
        "order": 9,
        "cover": "https://i.scdn.co/image/ab67616d0000b2737a8a98924f07394314e5d47a",
        "fixes": [
            ("remove", "atmosphere"),
        ],
    },
    {
        "job_id": "9c3db362",
        "slug": "tondo",
        "title": "Tondo",
        "artist": "Disclosure, Eko Roosevelt",
        "year": "2020",
        "genre": "Electronic",
        "order": 13,
        "cover": "https://i.scdn.co/image/ab67616d0000b273355bf68fa788b6d401195b43",
        "fixes": [
            ("remove", "piano"),
        ],
    },
]


def main():
    init_db()
    for track in TRACKS:
        print(f"\n{'='*60}")
        print(f"{track['title']} — {track['artist']} (job={track['job_id']})")
        print(f"{'='*60}")
        ingest_track(track)

    print(f"\n{'='*60}")
    print(f"DONE — {len(TRACKS)} tracks ingested!")


if __name__ == "__main__":
    main()
