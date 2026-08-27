"""Fetch the eval set through riffd's own downloader.

Deliberately reuses `downloader.download_audio_from_youtube` rather than any
separate acquisition path: the point of the A/B is that both separators see the
generation production actually sees (yt-dlp mp3, 44.1 kHz stereo, ~210-250 kbps).
Sourcing cleaner audio here would flatter both systems and answer a question
riffd does not have.

The alternative — reconstructing mixes by summing the stems already in
static/demo — was rejected: those stems are htdemucs_6s's own output, so feeding
them back would let the incumbent re-separate its own artifacts and bias the
comparison toward it.

    python modal_worker/eval/fetch_audio.py
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from downloader import download_audio_from_youtube  # noqa: E402

OUT = Path(__file__).parent / "audio"

TRACKS = {
    # slug: (search query, why it is in the set)
    "layla": ("Derek and the Dominos Layla official audio",
              "guitar + piano heavy; layered guitars and a two-minute piano coda"),
    "livin_thing": ("Electric Light Orchestra Livin Thing official audio",
                    "strings + guitar + piano; the track behind riffd's Horns/Strings bug"),
    "take_it_easy": ("Eagles Take It Easy official audio",
                     "acoustic + electric guitars, close harmony, no piano"),
    "one_more_time": ("Daft Punk One More Time official audio",
                      "electronic negative control: should contain no guitar or piano"),
}


def main(slugs):
    OUT.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        query, why = TRACKS[slug]
        if list(OUT.glob(f"{slug}.*")):
            print(f"[fetch] {slug}: already present")
            continue
        t0 = time.time()
        src = download_audio_from_youtube(query, f"eval_{slug}")
        dest = OUT / f"{slug}{Path(src).suffix}"
        shutil.copy2(src, dest)
        print(f"[fetch] {slug}: {time.time() - t0:.0f}s -> {dest} "
              f"({dest.stat().st_size / 1e6:.1f} MB)  [{why}]")


if __name__ == "__main__":
    main(sys.argv[1:] or list(TRACKS))
