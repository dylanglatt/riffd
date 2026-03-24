"""
downloader.py
Audio downloader using yt-dlp.
Given a search query or YouTube URL, downloads the best audio and converts to WAV.

Note: yt-dlp is for personal use only. Respect YouTube's ToS.
"""

import subprocess
import shutil
from pathlib import Path

UPLOAD_DIR = Path("uploads")


def download_audio_from_youtube(query_or_url: str, job_id: str) -> Path:
    """
    Download audio from YouTube using yt-dlp.

    Args:
        query_or_url: Either a search query (e.g. "The Beatles - Let It Be")
                      or a direct YouTube URL.
        job_id: Unique job ID for organizing output files.

    Returns:
        Path to the downloaded WAV file.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp not found. Install it with: pip install yt-dlp"
        )

    out_dir = UPLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "%(title)s.%(ext)s")

    # If it looks like a URL, use it directly; otherwise prepend ytsearch1:
    if query_or_url.startswith("http"):
        source = query_or_url
    else:
        source = f"ytsearch1:{query_or_url}"

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",          # best quality
        "--no-playlist",
        "--output", out_template,
        "--no-progress",
        source,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr}")

    # Find the downloaded WAV file
    wav_files = list(out_dir.glob("*.wav"))
    if not wav_files:
        # Sometimes yt-dlp produces a different extension — find any audio file
        audio_files = [
            f for f in out_dir.iterdir()
            if f.suffix.lower() in {".wav", ".mp3", ".m4a", ".webm", ".ogg"}
        ]
        if not audio_files:
            raise RuntimeError("yt-dlp ran but no audio file was found.")
        return audio_files[0]

    return wav_files[0]
