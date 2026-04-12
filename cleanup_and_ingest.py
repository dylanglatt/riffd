#!/usr/bin/env python3
"""
One-time batch script: clean up stem issues and ingest 7 demo tracks.
Copies stems to static/demo/ FIRST, then applies fixes there (avoids permission issues on outputs/).
Run from the project root: python cleanup_and_ingest.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import init_db, upsert_demo_track

OUTPUT_DIR = Path("outputs")
DEMO_DIR = Path("static/demo")
WORK_DIR = Path("/tmp/demo_ingest")  # temp workspace to avoid filesystem permission issues


def copy_stems_to_work(job_id, slug):
    """Copy all MP3 stems from outputs/ to temp workspace."""
    src = OUTPUT_DIR / job_id / "stems"
    dest = WORK_DIR / slug / "stems"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in src.iterdir():
        if f.suffix == ".mp3":
            shutil.copy2(f, dest / f.name)
            count += 1
    return count


def load_analysis(job_id, slug):
    """Load result_cache.json, strip internal fields, save to workspace."""
    src = OUTPUT_DIR / job_id / "result_cache.json"
    data = json.loads(src.read_text())
    for key in ("_analysis_version", "_cached_at", "job_id", "track_id",
                "audio_source", "audio_mode"):
        data.pop(key, None)
    return data


def remove_stem(slug, data, stem_key):
    """Remove a stem from demo dir and cache."""
    stems_dir = WORK_DIR / slug / "stems"
    for ext in (".mp3", ".wav"):
        f = stems_dir / f"{stem_key}{ext}"
        if f.exists():
            f.unlink()
            print(f"    deleted: {f.name}")
    if stem_key in data.get("stems", {}):
        del data["stems"][stem_key]
        print(f"    removed from cache: {stem_key}")


def merge_stems(slug, data, stem_a, stem_b, out_key, out_label):
    """Merge two MP3 stems into one using ffmpeg. Works in demo dir."""
    stems_dir = WORK_DIR / slug / "stems"
    a_path = stems_dir / f"{stem_a}.mp3"
    b_path = stems_dir / f"{stem_b}.mp3"
    tmp_path = stems_dir / f"_merge_tmp.mp3"
    out_path = stems_dir / f"{out_key}.mp3"

    if not a_path.exists() or not b_path.exists():
        print(f"    WARNING: can't merge, missing: a={a_path.exists()}, b={b_path.exists()}")
        return

    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(a_path), "-i", str(b_path),
         "-filter_complex", "amix=inputs=2:duration=longest:normalize=0",
         "-codec:a", "libmp3lame", "-b:a", "192k", str(tmp_path)],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"    WARNING: ffmpeg merge failed: {result.stderr[-300:]}")
        return

    # Clean up originals and rename
    a_path.unlink()
    b_path.unlink()
    tmp_path.rename(out_path)
    print(f"    merged audio: {stem_a} + {stem_b} → {out_key}")

    # Update cache
    stems = data.get("stems", {})
    energy_a = stems.get(stem_a, {}).get("energy", 0)
    energy_b = stems.get(stem_b, {}).get("energy", 0)
    stems.pop(stem_a, None)
    stems.pop(stem_b, None)
    stems[out_key] = {"label": out_label, "energy": max(energy_a, energy_b), "active": True}
    print(f"    merged cache: → {out_key} ({out_label})")


def rename_stem(slug, data, old_key, new_key, new_label):
    """Rename a stem file and cache entry."""
    stems_dir = WORK_DIR / slug / "stems"
    old_mp3 = stems_dir / f"{old_key}.mp3"
    new_mp3 = stems_dir / f"{new_key}.mp3"
    if old_mp3.exists():
        old_mp3.rename(new_mp3)
        print(f"    renamed file: {old_key}.mp3 → {new_key}.mp3")
    if old_key in data.get("stems", {}):
        entry = data["stems"].pop(old_key)
        entry["label"] = new_label
        data["stems"][new_key] = entry
        print(f"    renamed cache: {old_key} → {new_key}")


def save_analysis(slug, data):
    """Write cleaned analysis.json to workspace."""
    dest = WORK_DIR / slug / "analysis.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2))


def publish_to_demo(slug):
    """Copy finalized workspace files to static/demo/."""
    src = WORK_DIR / slug
    dest = DEMO_DIR / slug
    dest.mkdir(parents=True, exist_ok=True)
    # Copy analysis.json
    shutil.copy2(src / "analysis.json", dest / "analysis.json")
    # Copy stems
    dest_stems = dest / "stems"
    dest_stems.mkdir(parents=True, exist_ok=True)
    for f in (src / "stems").iterdir():
        if f.suffix == ".mp3":
            shutil.copy2(f, dest_stems / f.name)
    # Copy cover if exists
    cover = src / "cover.jpg"
    if cover.exists():
        shutil.copy2(cover, dest / "cover.jpg")


def download_cover(slug, url):
    """Download cover art to workspace. Returns web path or empty string."""
    if not url:
        return ""
    dest = WORK_DIR / slug / "cover.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import requests
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        print(f"  downloaded cover art")
        return f"/static/demo/{slug}/cover.jpg"
    except Exception as e:
        print(f"  WARNING: cover download failed: {e}")
        return ""


# ─── Track definitions ─────────────────────────────────────────────────────

TRACKS = [
    {
        "job_id": "a797f11e",
        "slug": "seven_nation_army",
        "title": "Seven Nation Army",
        "artist": "The White Stripes",
        "year": "2003",
        "genre": "Rock",
        "order": 4,
        "cover": "https://i.scdn.co/image/ab67616d00001e021b45e13edeee9c4b3d1c24c5",
        "fixes": [
            ("merge", "acoustic_guitar", "guitar", "guitar", "Guitar"),
        ],
    },
    {
        "job_id": "46f9b120",
        "slug": "mr_brightside",
        "title": "Mr. Brightside",
        "artist": "The Killers",
        "year": "2004",
        "genre": "Indie Rock",
        "order": 7,
        "cover": "https://i.scdn.co/image/ab67616d00001e02ccdddd46119a4ff53eaf1f5e",
        "fixes": [
            ("remove", "piano"),
            ("remove", "synth"),
        ],
    },
    {
        "job_id": "24324a27",
        "slug": "circles",
        "title": "Circles",
        "artist": "Post Malone",
        "year": "2019",
        "genre": "Pop / Rock",
        "order": 8,
        "cover": "https://i.scdn.co/image/ab67616d00001e0289a8fab8bf8cd7b06bed967a",
        "fixes": [
            ("remove", "piano"),
        ],
    },
    {
        "job_id": "30bde364",
        "slug": "kill_bill",
        "title": "Kill Bill",
        "artist": "SZA",
        "year": "2022",
        "genre": "R&B",
        "order": 10,
        "cover": "https://i.scdn.co/image/ab67616d00001e0270dbc9f47669d120e3f0f988",
        "fixes": [
            ("remove", "synth"),
            ("rename", "atmosphere", "synth_lead", "Synth Lead"),
        ],
    },
    {
        "job_id": "a1fb8c44",
        "slug": "passionfruit",
        "title": "Passionfruit",
        "artist": "Drake",
        "year": "2017",
        "genre": "R&B",
        "order": 11,
        "cover": "https://i.scdn.co/image/ab67616d00001e02365b3fb800c19f7ff72602da",
        "fixes": [
            ("remove", "atmosphere"),
        ],
    },
    {
        "job_id": "c6d213cf",
        "slug": "fast_car",
        "title": "Fast Car",
        "artist": "Tracy Chapman",
        "year": "1988",
        "genre": "Folk",
        "order": 12,
        "cover": "https://i.scdn.co/image/ab67616d00001e0258bf3f66eb79e4cd3b4449dd",
        "fixes": [
            ("remove", "other"),
        ],
    },
    {
        "job_id": "7c54d227",
        "slug": "one_more_time",
        "title": "One More Time",
        "artist": "Daft Punk",
        "year": "2001",
        "genre": "Electronic",
        "order": 14,
        "cover": "https://i.scdn.co/image/ab67616d00001e022160c02498a4a28b2c9e52a8",
        "fixes": [
            ("remove", "guitar"),
            ("remove", "piano"),
            ("merge", "synth", "atmosphere", "synth", "Synth"),
        ],
    },
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
            ("remove", "guitar"),           # energy=0.009, effectively silent
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
            ("remove", "atmosphere"),       # duplicate Pedal Steel, lower energy
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
            ("remove", "piano"),            # energy=0.003, essentially silent
        ],
    },
]


def main():
    init_db()

    for track in TRACKS:
        job_id = track["job_id"]
        slug = track["slug"]
        print(f"\n{'='*60}")
        print(f"{track['title']} — {track['artist']} (job={job_id})")
        print(f"{'='*60}")

        # Step 1: Copy stems and analysis to temp workspace
        count = copy_stems_to_work(job_id, slug)
        print(f"  Copied {count} stems to workspace")
        data = load_analysis(job_id, slug)
        print(f"  Loaded analysis ({len(data.get('stems', {}))} stems)")

        # Step 2: Apply fixes in workspace (where we have full permissions)
        for fix in track.get("fixes", []):
            if fix[0] == "remove":
                print(f"  Removing: {fix[1]}")
                remove_stem(slug, data, fix[1])
            elif fix[0] == "merge":
                _, a, b, out, label = fix
                print(f"  Merging: {a} + {b} → {out}")
                merge_stems(slug, data, a, b, out, label)
            elif fix[0] == "rename":
                _, old, new, label = fix
                print(f"  Renaming: {old} → {new}")
                rename_stem(slug, data, old, new, label)

        # Step 3: Save cleaned analysis to workspace
        save_analysis(slug, data)
        final_stems = list(data.get("stems", {}).keys())
        print(f"  Final stems: {final_stems}")

        # Step 4: Cover art (to workspace)
        cover_path = download_cover(slug, track.get("cover"))

        # Step 5: Publish workspace → static/demo/
        publish_to_demo(slug)
        print(f"  Published to {DEMO_DIR / slug}")

        # Step 6: DB
        intel = data.get("intelligence", {})
        upsert_demo_track(
            slug=slug, title=track["title"], artist=track["artist"],
            year=track["year"], genre=track["genre"],
            key_display=intel.get("key", ""), bpm=int(intel.get("bpm", 0)),
            cover_path=cover_path,
            analysis_path=str(DEMO_DIR / slug / "analysis.json"),
            stems_dir=str(DEMO_DIR / slug / "stems"),
            display_order=track["order"],
            is_visible=1,
        )
        print(f"  DB: order={track['order']}, key={intel.get('key','')}")

    print(f"\n{'='*60}")
    print(f"DONE — {len(TRACKS)} tracks ingested!")


if __name__ == "__main__":
    main()
