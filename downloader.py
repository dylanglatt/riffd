"""
downloader.py
Audio source router: YouTube → Spotify/iTunes preview → upload prompt.
Tries full-song sources first, falls back to 30-second previews, then asks user to upload.
"""

import re
import subprocess
import shutil
import requests
from pathlib import Path

UPLOAD_DIR = Path("uploads")


class AudioUnavailableError(Exception):
    """No audio source could provide audio for this track."""
    pass


def _simplify_query(query: str) -> str | None:
    """
    Strip noise from a YouTube search query for retry.
    'Eagles - Hotel California official audio' → 'Eagles Hotel California'
    Returns None if the simplified query is the same as the original.
    """
    q = query
    # Remove common suffixes
    for phrase in ["official audio", "official video", "official", "remastered", "remaster"]:
        q = re.sub(re.escape(phrase), "", q, flags=re.IGNORECASE)
    # Remove parenthesized text
    q = re.sub(r"\([^)]*\)", "", q)
    # Remove extra punctuation (keep alphanumeric, spaces, hyphens)
    q = re.sub(r"[^\w\s-]", " ", q)
    # Collapse whitespace
    q = re.sub(r"\s+", " ", q).strip()
    return q if q and q.lower() != query.strip().lower() else None


def _run_ytdlp(source: str, job_id: str) -> Path:
    """Run yt-dlp with hardened flags. Returns path to audio file or raises."""
    out_dir = UPLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--no-playlist",
        "--output", out_template,
        "--no-progress",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "--extractor-args", "youtube:player_client=web",
        "--retries", "3",
        "--socket-timeout", "30",
        source,
    ]

    print(f"[downloader] yt-dlp starting: {source[:80]}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[:500]}")

    # Find the downloaded audio file
    wav_files = list(out_dir.glob("*.wav"))
    if not wav_files:
        audio_files = [
            f for f in out_dir.iterdir()
            if f.suffix.lower() in {".wav", ".mp3", ".m4a", ".webm", ".ogg"}
        ]
        if not audio_files:
            raise RuntimeError("yt-dlp ran but no audio file was found.")
        print(f"[downloader] yt-dlp success → {audio_files[0].name}")
        return audio_files[0]

    print(f"[downloader] yt-dlp success → {wav_files[0].name}")
    return wav_files[0]


def download_audio_from_youtube(query_or_url: str, job_id: str) -> Path:
    """
    Download audio from YouTube using yt-dlp.
    If the first attempt fails on a search query, retries once with a simplified query.
    Returns path to the downloaded audio file.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp not found. Install it with: pip install yt-dlp")

    is_url = query_or_url.startswith("http")
    source = query_or_url if is_url else f"ytsearch1:{query_or_url}"

    # First attempt
    try:
        return _run_ytdlp(source, job_id)
    except Exception as first_err:
        print(f"[downloader] YouTube failed (attempt 1): {first_err}")

        # Only retry with simplified query for search queries, not direct URLs
        if is_url:
            raise

        simplified = _simplify_query(query_or_url)
        if not simplified:
            raise

        retry_source = f"ytsearch1:{simplified}"
        print(f"[downloader] YT RETRY with simplified query: {simplified}")
        try:
            return _run_ytdlp(retry_source, job_id)
        except Exception as retry_err:
            print(f"[downloader] YouTube failed (attempt 2): {retry_err}")
            # Raise the original error — it's more informative
            raise first_err


def download_preview(url: str, job_id: str) -> Path:
    """
    Download an audio preview (Spotify or iTunes MP3 URL).
    Returns path to the saved file.
    """
    out_dir = UPLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    save_path = out_dir / "preview.mp3"

    print(f"[downloader] downloading preview: {url[:80]}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    save_path.write_bytes(resp.content)
    print(f"[downloader] preview saved → {save_path.name} ({len(resp.content)} bytes)")
    return save_path


def get_itunes_preview_url(artist: str, track_name: str) -> str | None:
    """
    Look up a track on iTunes and return its preview URL if found.
    """
    try:
        term = f"{artist} {track_name}"
        print(f"[downloader] iTunes lookup: {term[:60]}")
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": term, "media": "music", "entity": "song", "limit": 3},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            print("[downloader] iTunes: no results")
            return None

        # Basic string matching to find best result
        artist_lower = artist.lower()
        track_lower = track_name.lower()
        for r in results:
            r_artist = (r.get("artistName") or "").lower()
            r_track = (r.get("trackName") or "").lower()
            if artist_lower in r_artist or r_artist in artist_lower:
                if track_lower in r_track or r_track in track_lower:
                    url = r.get("previewUrl")
                    if url:
                        print(f"[downloader] iTunes match: {r.get('trackName')} by {r.get('artistName')}")
                        return url

        # If no exact match, return first result's preview if available
        url = results[0].get("previewUrl")
        if url:
            print(f"[downloader] iTunes fallback: {results[0].get('trackName')} by {results[0].get('artistName')}")
        return url

    except Exception as e:
        print(f"[downloader] iTunes lookup failed: {e}")
        return None


def resolve_preview(track_data: dict, job_id: str, on_progress=None) -> Path:
    """
    Preview-only path. Tries preview sources only (no YouTube).
    Fast, reliable, returns 30-second audio clip.

    track_data keys: preview_url, artist, name
    Raises AudioUnavailableError if no preview source works.
    """
    preview_url = track_data.get("preview_url")
    artist = track_data.get("artist", "")
    name = track_data.get("name", "")
    print(f"[job {job_id}] resolve_preview called — preview_url={bool(preview_url)} artist={artist[:20]} name={name[:30]}")

    # 1. Spotify preview URL
    if preview_url:
        if on_progress:
            on_progress("Getting preview audio...")
        try:
            path = download_preview(preview_url, job_id)
            print(f"[job {job_id}] AUDIO SOURCE SELECTED: preview (spotify)")
            return path
        except Exception as e:
            print(f"[job {job_id}] Spotify preview failed: {e}")

    # 2. iTunes preview
    if artist and name:
        if on_progress:
            on_progress("Getting preview audio...")
        try:
            itunes_url = get_itunes_preview_url(artist, name)
            if itunes_url:
                path = download_preview(itunes_url, job_id)
                print(f"[job {job_id}] AUDIO SOURCE SELECTED: preview (itunes)")
                return path
            else:
                print(f"[job {job_id}] iTunes: no preview URL found")
        except Exception as e:
            print(f"[job {job_id}] iTunes preview failed: {e}")

    # No preview available
    print(f"[job {job_id}] no preview source available")
    raise AudioUnavailableError("No preview audio available for this track.")


def resolve_audio(track_data: dict, job_id: str, on_progress=None) -> Path:
    """
    Main entry point. Tries audio sources in waterfall order:
    1. YouTube (full song)
    2. Spotify preview URL
    3. iTunes preview URL
    4. Raises AudioUnavailableError

    track_data keys: query, preview_url, artist, name
    """
    query = track_data.get("query")
    preview_url = track_data.get("preview_url")
    artist = track_data.get("artist", "")
    name = track_data.get("name", "")

    # 1. YouTube first (full audio)
    if query:
        if on_progress:
            on_progress("Downloading from YouTube...")
        try:
            path = download_audio_from_youtube(query, job_id)
            print(f"[job {job_id}] AUDIO SOURCE SELECTED: youtube")
            return path
        except Exception as e:
            print(f"[job {job_id}] YouTube failed: {e}")
            if on_progress:
                on_progress("YouTube unavailable, trying preview...")

    # 2. Spotify preview URL
    if preview_url:
        try:
            path = download_preview(preview_url, job_id)
            print(f"[job {job_id}] AUDIO SOURCE SELECTED: preview (spotify)")
            return path
        except Exception as e:
            print(f"[job {job_id}] Preview failed: {e}")
            if on_progress:
                on_progress("Trying iTunes preview...")

    # 3. iTunes preview
    if artist and name:
        if on_progress and not preview_url:
            on_progress("Trying iTunes preview...")
        try:
            itunes_url = get_itunes_preview_url(artist, name)
            if itunes_url:
                path = download_preview(itunes_url, job_id)
                print(f"[job {job_id}] AUDIO SOURCE SELECTED: preview (itunes)")
                return path
            else:
                print(f"[job {job_id}] iTunes: no preview URL found")
        except Exception as e:
            print(f"[job {job_id}] Preview failed: {e}")

    # 4. All sources exhausted
    print(f"[job {job_id}] all audio sources failed — upload required")
    raise AudioUnavailableError("No audio source available. Please upload your own file.")
