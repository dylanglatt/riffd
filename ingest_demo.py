#!/usr/bin/env python3
"""
ingest_demo.py — Convert a processed job into a permanent demo track.

Usage:
  python ingest_demo.py --job-id abc12345 --slug circles --title "Circles" \
    --artist "Post Malone" --year 2019 --genre "Pop / Rock" --order 6

What it does:
  1. Reads result_cache.json from outputs/<job_id>/
  2. Converts stem WAVs → MP3 via ffmpeg (or copies existing MP3s)
  3. Downloads cover art from Spotify (or copies local file)
  4. Copies everything into static/demo/<slug>/
  5. Renames result_cache.json → analysis.json (demo format)
  6. Inserts/updates the demo_tracks DB row

After running, the track appears on /demo immediately.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Add project root to path so we can import db
sys.path.insert(0, str(Path(__file__).parent))
from db import init_db, upsert_demo_track, get_demo_track


OUTPUT_DIR = Path("outputs")
DEMO_DIR = Path("static/demo")


def find_job_dir(job_id: str) -> Path:
    """Locate the job output directory."""
    job_dir = OUTPUT_DIR / job_id
    if job_dir.exists():
        return job_dir
    # Try prefix match (job IDs are 8-char prefixes)
    matches = [d for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name.startswith(job_id)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Error: multiple directories match '{job_id}': {[m.name for m in matches]}")
        sys.exit(1)
    print(f"Error: no output directory found for job '{job_id}'")
    print(f"  Looked in: {OUTPUT_DIR.resolve()}")
    print(f"  Available: {[d.name for d in OUTPUT_DIR.iterdir() if d.is_dir()]}")
    sys.exit(1)


def convert_stems(job_dir: Path, dest_stems: Path) -> list[str]:
    """Convert WAV stems to MP3. Returns list of stem names."""
    stems_dir = job_dir / "stems"
    if not stems_dir.exists():
        print(f"Error: no stems/ directory in {job_dir}")
        sys.exit(1)

    dest_stems.mkdir(parents=True, exist_ok=True)
    stem_names = []

    # Build a per-stem source map: prefer WAV over MP3 for each stem individually.
    # This handles mixed directories where some stems landed as WAV (e.g. from a
    # melodic split that was in-flight when the server restarted) and others as MP3.
    stem_sources: dict[str, Path] = {}
    for stem_file in sorted(stems_dir.iterdir()):
        if stem_file.suffix not in (".wav", ".mp3"):
            continue
        name = stem_file.stem
        # WAV wins over MP3 for the same stem name (higher quality source)
        if name not in stem_sources or stem_file.suffix == ".wav":
            stem_sources[name] = stem_file

    if not stem_sources:
        print(f"Error: no stem files found in {stems_dir}")
        sys.exit(1)

    for stem_file in stem_sources.values():
        stem_name = stem_file.stem
        dest_mp3 = dest_stems / f"{stem_name}.mp3"

        if stem_file.suffix == ".mp3":
            # Already MP3 — copy directly
            shutil.copy2(stem_file, dest_mp3)
            print(f"  copied: {stem_name}.mp3")
            stem_names.append(stem_name)
            continue
        else:
            # WAV → MP3 conversion (192kbps, good quality for demos)
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(stem_file), "-codec:a", "libmp3lame",
                 "-b:a", "192k", "-ar", "44100", str(dest_mp3)],
                capture_output=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"  WARNING: ffmpeg failed for {stem_name}: {result.stderr[-200:]}")
                continue
            print(f"  converted: {stem_name}.wav → .mp3")

        stem_names.append(stem_name)

    return stem_names


def prepare_analysis(job_dir: Path, dest_dir: Path) -> dict:
    """Load result_cache.json and write as analysis.json in demo format."""
    cache_file = job_dir / "result_cache.json"
    if not cache_file.exists():
        print(f"Error: no result_cache.json in {job_dir}")
        sys.exit(1)

    data = json.loads(cache_file.read_text())

    # Strip internal fields not needed for demo
    for key in ("_analysis_version", "_cached_at", "job_id", "track_id",
                "audio_source", "audio_mode"):
        data.pop(key, None)

    analysis_path = dest_dir / "analysis.json"
    analysis_path.write_text(json.dumps(data, indent=2))
    print(f"  wrote: analysis.json")

    return data


def fetch_cover(dest_dir: Path, cover_source: str | None, slug: str) -> str:
    """Get cover art. Returns the web-accessible path."""
    dest_cover = dest_dir / "cover.jpg"

    if cover_source and os.path.exists(cover_source):
        # Local file
        shutil.copy2(cover_source, dest_cover)
        print(f"  copied cover from: {cover_source}")
    elif cover_source and cover_source.startswith("http"):
        # Download from URL
        try:
            import requests
            resp = requests.get(cover_source, timeout=15)
            resp.raise_for_status()
            dest_cover.write_bytes(resp.content)
            print(f"  downloaded cover from URL")
        except Exception as e:
            print(f"  WARNING: could not download cover: {e}")
            return ""
    else:
        if not dest_cover.exists():
            print(f"  WARNING: no cover art provided and none exists at {dest_cover}")
            return ""
        print(f"  using existing cover.jpg")

    return f"/static/demo/{slug}/cover.jpg"


def detect_key(analysis: dict) -> str:
    """Extract key display from analysis data."""
    intel = analysis.get("intelligence", {})
    key = intel.get("key", "")
    if key and key != "Unknown":
        return key
    return ""


def detect_bpm(analysis: dict) -> int:
    """Extract BPM from analysis data."""
    intel = analysis.get("intelligence", {})
    bpm = intel.get("bpm", 0)
    return int(bpm) if bpm else 0


def main():
    parser = argparse.ArgumentParser(description="Ingest a processed track as a demo")
    parser.add_argument("--job-id", required=True, help="Job ID from outputs/ directory")
    parser.add_argument("--slug", required=True, help="URL-safe identifier (e.g. 'circles')")
    parser.add_argument("--title", required=True, help="Track title")
    parser.add_argument("--artist", required=True, help="Artist name")
    parser.add_argument("--year", default="", help="Release year")
    parser.add_argument("--genre", default="", help="Genre label (e.g. 'Pop / Rock')")
    parser.add_argument("--order", type=int, default=99, help="Display order (lower = first)")
    parser.add_argument("--cover", default=None, help="Cover art: local path or URL")
    parser.add_argument("--description", default="", help="Short description for the UI")
    parser.add_argument("--spotify-id", default=None, help="Spotify track ID (optional)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing demo track")
    args = parser.parse_args()

    # Validate slug
    if "/" in args.slug or ".." in args.slug or " " in args.slug:
        print("Error: slug must be URL-safe (no slashes, spaces, or '..')")
        sys.exit(1)

    # Check for existing demo
    init_db()
    existing = get_demo_track(args.slug)
    dest_dir = DEMO_DIR / args.slug
    if existing and not args.force:
        print(f"Demo track '{args.slug}' already exists. Use --force to overwrite.")
        sys.exit(1)

    # Find job directory
    job_dir = find_job_dir(args.job_id)
    print(f"\nIngesting demo: {args.title} — {args.artist}")
    print(f"  Source: {job_dir}")
    print(f"  Destination: {dest_dir}")
    print()

    # Create destination
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_stems = dest_dir / "stems"

    # Step 1: Convert stems
    print("Converting stems...")
    stem_names = convert_stems(job_dir, dest_stems)
    print(f"  {len(stem_names)} stems ready\n")

    # Step 2: Prepare analysis.json
    print("Preparing analysis...")
    analysis = prepare_analysis(job_dir, dest_dir)

    # Step 3: Cover art
    print("Cover art...")
    cover_path = fetch_cover(dest_dir, args.cover, args.slug)

    # Auto-detect key and BPM from analysis if not overridden
    key_display = detect_key(analysis)
    bpm = detect_bpm(analysis)

    # Step 4: Insert/update DB
    print("\nUpdating database...")
    upsert_demo_track(
        slug=args.slug,
        title=args.title,
        artist=args.artist,
        year=args.year,
        genre=args.genre,
        key_display=key_display,
        bpm=bpm,
        cover_path=cover_path,
        analysis_path=str(dest_dir / "analysis.json"),
        stems_dir=str(dest_stems),
        display_order=args.order,
        description=args.description,
        spotify_track_id=args.spotify_id,
        is_visible=1,
    )
    print(f"  demo_tracks row upserted for '{args.slug}'")

    # Summary
    print(f"\n{'='*50}")
    print(f"Done! '{args.title}' by {args.artist} is now a demo track.")
    print(f"  Slug:   {args.slug}")
    print(f"  Key:    {key_display or '(not detected)'}")
    print(f"  BPM:    {bpm or '(not detected)'}")
    print(f"  Stems:  {len(stem_names)} ({', '.join(stem_names)})")
    print(f"  Order:  {args.order}")
    print(f"  Path:   {dest_dir}")
    print(f"\nRestart Flask and visit /demo to see it.")


if __name__ == "__main__":
    main()
