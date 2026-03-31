"""
downloader.py — Audio acquisition for Riffd.

Two entry points:
  resolve_preview(track_data, job_id)  — Preview-only. Spotify → iTunes. No YouTube. ~2s.
  resolve_audio(track_data, job_id)    — Full waterfall. YouTube → Spotify → iTunes. ~30-120s.

Audio source hierarchy:
  1. Spotify preview_url (if provided by search results)  — direct MP3 download, ~1s
  2. iTunes preview (API lookup by artist+name)            — MP3 download, ~2s
  3. YouTube via yt-dlp (search or direct URL)             — WAV download, ~30-120s
  4. AudioUnavailableError → frontend prompts file upload

Preview files saved as:  uploads/<job_id>/preview.mp3
YouTube files saved as:  uploads/<job_id>/<title>.wav

Failure modes:
  - Spotify preview_url is often null (most tracks don't have one)
  - iTunes lookup can return wrong track or no results
  - YouTube frequently fails on Render (bot detection, missing JS runtime)
  - All failures are caught and logged, never crash the server

The preview path (resolve_preview) is the default for instant analysis.
The full path (resolve_audio) is only used when user explicitly requests deep analysis.
"""

import os
import re
import base64
import subprocess
import shutil
import requests
from pathlib import Path

UPLOAD_DIR = Path("uploads")

# ---------------------------------------------------------------------------
# Cookie bootstrap
# Preferred: use Render Secret Files — upload cookies.txt and set path to
#   /etc/secrets/cookies.txt in the Render dashboard (Secret Files section).
# Fallback: set YT_COOKIES_B64 env var (base64-encoded cookies.txt).
#   WARNING: large cookie files will cause "argument list too long" build errors.
#   Use Secret Files instead whenever possible.
# ---------------------------------------------------------------------------
_COOKIES_PATH = Path("cookies.txt")
_SECRET_COOKIES_PATH = Path("/etc/secrets/cookies.txt")

def _bootstrap_cookies():
    # Priority 1: Render Secret File (no env var size limits)
    if _SECRET_COOKIES_PATH.exists():
        try:
            import shutil as _shutil
            _shutil.copy(_SECRET_COOKIES_PATH, _COOKIES_PATH)
            print(f"[downloader] cookies.txt loaded from secret file ({_SECRET_COOKIES_PATH})")
            _validate_cookies_file(_COOKIES_PATH)
            return
        except Exception as e:
            print(f"[downloader] WARNING: failed to copy secret cookies file: {e}")

    # Priority 2: Base64 env var (legacy fallback — avoid for large cookie files)
    b64 = os.environ.get("YT_COOKIES_B64", "").strip()
    if b64:
        try:
            decoded = base64.b64decode(b64).decode("utf-8")
            _COOKIES_PATH.write_text(decoded)
            print(f"[downloader] cookies.txt written from YT_COOKIES_B64 ({len(decoded)} bytes)")
            _validate_cookies_file(_COOKIES_PATH)
        except Exception as e:
            print(f"[downloader] WARNING: failed to decode YT_COOKIES_B64: {e}")


def _validate_cookies_file(path: Path) -> None:
    """Log the first line of the cookies file so we can verify Netscape format in logs."""
    try:
        lines = path.read_text(errors="replace").splitlines()
        first = lines[0].strip() if lines else "(empty)"
        size = path.stat().st_size
        is_valid = first.startswith("# Netscape HTTP Cookie File") or first.startswith("# HTTP Cookie File")
        status = "✓ valid" if is_valid else "✗ INVALID — missing Netscape header"
        print(f"[downloader] cookies.txt: {size:,} bytes, {len(lines)} lines, first line: {first[:60]!r} → {status}")
    except Exception as e:
        print(f"[downloader] WARNING: could not validate cookies file: {e}")

_bootstrap_cookies()

# Log yt-dlp version at startup — critical for diagnosing YouTube extraction failures
try:
    _ytdlp_version = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
    print(f"[downloader] yt-dlp version: {_ytdlp_version.stdout.strip()}")
except Exception as _e:
    print(f"[downloader] WARNING: could not get yt-dlp version: {_e}")

# Check for JavaScript runtime — yt-dlp needs it to solve YouTube signature challenges
for _js_bin in ("node", "nodejs", "deno", "phantomjs"):
    if shutil.which(_js_bin):
        _js_ver = subprocess.run([_js_bin, "--version"], capture_output=True, text=True, timeout=5)
        print(f"[downloader] JS runtime: {_js_bin} {_js_ver.stdout.strip()}")
        break
else:
    print(f"[downloader] ⚠️  NO JS RUNTIME FOUND — yt-dlp signature solving WILL FAIL")
    print(f"[downloader] Install Node.js: add 'nodejs' to Render build or use Docker")


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


def _is_proxy_error(err: Exception) -> bool:
    """Return True if the error is clearly a proxy connectivity failure."""
    msg = str(err).lower()
    return any(k in msg for k in (
        "tunnel connection failed",
        "unable to connect to proxy",
        "proxy error",
        "502 bad gateway",
        "proxyerror",
    ))


def _is_stale_cookie_error(err: Exception) -> bool:
    """Return True if the error is about expired/invalid YouTube cookies."""
    from cookie_refresher import is_stale_cookie_error
    return is_stale_cookie_error(err)


def _run_ytdlp(source: str, job_id: str, use_proxy: bool = True, _cookie_retried: bool = False) -> Path:
    """Run yt-dlp with hardened flags. Tries yt-dlp first, falls back to yt-dlp-ejs.
    If use_proxy=True and every attempt fails with a proxy error, retries once without proxy.
    If cookies are stale, refreshes them via Playwright and retries once."""
    out_dir = UPLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "%(title)s.%(ext)s")

    binaries = ["yt-dlp"]
    if shutil.which("yt-dlp-ejs"):
        binaries.append("yt-dlp-ejs")

    last_error = None
    all_proxy_errors = True
    any_stale_cookie = False
    for binary in binaries:
        try:
            result = _run_ytdlp_with_binary(binary, source, out_template, out_dir, job_id, use_proxy=use_proxy)
            return result
        except Exception as e:
            last_error = e
            if not _is_proxy_error(e):
                all_proxy_errors = False
            if _is_stale_cookie_error(e):
                any_stale_cookie = True
            print(f"[downloader] {binary} failed: {e}")
            for f in out_dir.glob("*.part"):
                f.unlink(missing_ok=True)
            continue

    # If every failure was a proxy error and we haven't already tried without proxy, retry direct
    if use_proxy and all_proxy_errors and os.environ.get("YT_PROXY_URL"):
        print(f"[downloader] proxy failed with 502 — retrying WITHOUT proxy")
        return _run_ytdlp(source, job_id, use_proxy=False, _cookie_retried=_cookie_retried)

    # If any failure was a stale cookie error, refresh cookies and retry once
    if any_stale_cookie and not _cookie_retried:
        print(f"[downloader] 🍪 stale cookies detected — attempting Playwright refresh...")
        try:
            from cookie_refresher import refresh_cookies
            success = refresh_cookies()
            if success:
                print(f"[downloader] 🍪 cookies refreshed — retrying yt-dlp")
                return _run_ytdlp(source, job_id, use_proxy=use_proxy, _cookie_retried=True)
            else:
                print(f"[downloader] 🍪 cookie refresh failed — continuing with fallbacks")
        except Exception as refresh_err:
            print(f"[downloader] 🍪 cookie refresh error: {refresh_err}")

    raise last_error or RuntimeError("All yt-dlp binaries failed")


def _run_ytdlp_with_binary(binary: str, source: str, out_template: str, out_dir: Path, job_id: str, use_proxy: bool = True) -> Path:
    """Execute a specific yt-dlp binary with full hardened flags."""
    cmd = [
        binary,
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "--output", out_template,
        "--no-progress",
        "--retries", "3",
        "--socket-timeout", "30",
        "--no-check-certificates",
        "--prefer-free-formats",
        "--force-ipv4",
        "--user-agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]

    # Build extractor args — player_client selection + optional PO token
    # As of 2026.03: 'default' uses the web client, 'android' often works without PO tokens,
    # 'web' requires PO tokens on many videos. Use 'default,android' as the safest combo.
    # The 'formats=missing_pot' flag requests formats even when PO token is missing.
    po_token = os.environ.get("YT_PO_TOKEN", "").strip()
    extractor_args = "youtube:player_client=default,android;formats=missing_pot;lang=en"
    if po_token:
        extractor_args += f";po_token={po_token}"
        print(f"[downloader] using PO token ({len(po_token)} chars)")
    cmd.extend(["--extractor-args", extractor_args])

    # Add cookies FIRST — yt-dlp uses them for auth + bot bypass
    if _COOKIES_PATH.exists():
        cmd[1:1] = ["--cookies", str(_COOKIES_PATH)]
        print(f"[downloader] using cookies.txt ({_COOKIES_PATH.stat().st_size:,} bytes)")

    # Add proxy if configured and not bypassed
    proxy_url = os.environ.get("YT_PROXY_URL")
    if proxy_url and use_proxy:
        cmd.extend(["--proxy", proxy_url])
        print(f"[downloader] using proxy: {proxy_url[:30]}...")
    elif proxy_url and not use_proxy:
        print(f"[downloader] proxy bypassed — trying direct connection")

    cmd.append(source)

    print(f"[downloader] {binary} starting: {source[:80]}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        stderr_tail = result.stderr[-1200:] if len(result.stderr) > 1200 else result.stderr
        raise RuntimeError(f"{binary} failed:\n{stderr_tail}")

    # Find the downloaded audio file — look for MP3 first (default), then WAV (fallback)
    audio_files = list(out_dir.glob("*.mp3"))
    if not audio_files:
        audio_files = list(out_dir.glob("*.wav"))
    if audio_files:
        audio_files = [f for f in audio_files if not f.name.startswith("preview")]
    if audio_files:
        print(f"[downloader] {binary} success → {audio_files[0].name}")
        return audio_files[0]

    # Broader fallback — any audio file
    other_audio = [
        f for f in out_dir.iterdir()
        if f.suffix.lower() in {".wav", ".mp3", ".m4a", ".webm", ".ogg"}
        and not f.name.startswith("preview")
    ]
    if other_audio:
        print(f"[downloader] {binary} success → {other_audio[0].name}")
        return other_audio[0]

    raise RuntimeError(f"{binary} ran but no audio file was found.")


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


def _get_cobalt_instances() -> list[str]:
    """
    Fetch live Cobalt API instances from the community registry.
    Falls back to a hardcoded list if the registry is unreachable.

    Note: api.cobalt.tools (the official instance) requires JWT auth as of v10 —
    it is intentionally excluded. Community self-hosted instances do not require auth.
    """
    FALLBACK = [
        "https://cobalt-api.kwiatekmiki.com",
        "https://cobalt.api.lisek.world",
        "https://cobalt-api.hyperna.me",
        "https://cobalt.drgns.space",
    ]
    try:
        r = requests.get(
            "https://instances.cobalt.best/api/instances.json",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.ok:
            instances = r.json()
            # Filter to instances that have an API URL and don't require auth
            api_urls = [
                inst["api"].rstrip("/")
                for inst in instances
                if inst.get("api") and not inst.get("auth_required", False)
                and "cobalt.tools" not in inst.get("api", "")  # exclude official (JWT-gated)
            ]
            merged = list(dict.fromkeys(api_urls[:8] + FALLBACK))
            print(f"[cobalt] fetched {len(api_urls)} instances from registry, using {len(merged)} total")
            return merged
    except Exception as e:
        print(f"[cobalt] instance registry failed: {e} — using fallback list")
    return FALLBACK


def _download_via_cobalt(query: str, job_id: str) -> Path:
    """
    Fallback YouTube download via Cobalt API (community self-hosted instances).
    Cobalt is an open-source media downloader that extracts audio from YouTube.
    The official api.cobalt.tools now requires JWT auth — we use community instances instead.
    """
    out_dir = UPLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Cobalt needs a direct YouTube URL, not a search query.
    # First, resolve the search query to a video URL using yt-dlp --get-url (fast, no download)
    video_url = None
    if query.startswith("http"):
        video_url = query
    else:
        search_source = f"ytsearch1:{query}" if not query.startswith("ytsearch") else query
        try:
            result = subprocess.run(
                ["yt-dlp", "--get-url", "--no-playlist", search_source],
                capture_output=True, text=True, timeout=30,
            )
            # yt-dlp --get-url returns the direct media URL, but we need the watch URL
            # Use --get-id instead to get the video ID
            id_result = subprocess.run(
                ["yt-dlp", "--get-id", "--no-playlist", search_source],
                capture_output=True, text=True, timeout=30,
            )
            vid_id = id_result.stdout.strip()
            if vid_id:
                video_url = f"https://www.youtube.com/watch?v={vid_id}"
        except Exception as e:
            print(f"[cobalt] could not resolve search to URL: {e}")

    if not video_url:
        raise RuntimeError("Cobalt: could not resolve search query to YouTube URL")

    # Dynamically fetch working Cobalt instances
    COBALT_APIS = _get_cobalt_instances()

    for api_base in COBALT_APIS:
        try:
            print(f"[cobalt] trying {api_base} — url: {video_url[:60]}")
            resp = requests.post(
                f"{api_base}/",
                json={
                    "url": video_url,
                    "audioFormat": "mp3",
                    "audioBitrate": "320",
                    "downloadMode": "audio",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )

            if not resp.ok:
                print(f"[cobalt] {api_base} returned {resp.status_code}: {resp.text[:200]}")
                continue

            data = resp.json()
            status = data.get("status")
            dl_url = data.get("url")

            if status == "error":
                print(f"[cobalt] {api_base} error: {data.get('error', {}).get('code', 'unknown')}")
                continue

            if not dl_url:
                print(f"[cobalt] {api_base}: no download URL in response")
                continue

            # Download the audio file
            print(f"[cobalt] downloading audio from cobalt...")
            dl_resp = requests.get(dl_url, stream=True, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
            dl_resp.raise_for_status()

            safe_name = re.sub(r'[^\w\s-]', '', query)[:80].strip() or "audio"
            out_path = out_dir / f"{safe_name}.mp3"
            with open(out_path, "wb") as f:
                for chunk in dl_resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            size = out_path.stat().st_size
            if size < 100_000:
                print(f"[cobalt] file too small ({size}b) — skipping")
                out_path.unlink(missing_ok=True)
                continue

            print(f"[cobalt] ✅ SUCCESS → {out_path.name} ({size:,} bytes)")
            return out_path

        except Exception as e:
            print(f"[cobalt] {api_base} error: {e}")
            continue

    raise RuntimeError("All Cobalt API endpoints failed")


def _get_piped_instances() -> list[str]:
    """
    Fetch live Piped API instances dynamically, merged with a hardcoded fallback list.
    Always returns the full fallback list even if the registry returns results — this
    ensures we have plenty of candidates even when the registry only returns a few entries.
    """
    FALLBACK = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.leptons.xyz",
        "https://pipedapi-libre.kavin.rocks",
        "https://pipedapi.adminforge.de",
        "https://api.piped.yt",
        "https://pipedapi.drgns.space",
        "https://pipedapi.nosebs.ru",
        "https://piped-api.osphost.fi",
        "https://pipedapi.tokhmi.xyz",
        "https://api.piped.private.coffee",
    ]
    try:
        r = requests.get("https://piped-instances.kavin.rocks/", timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.ok:
            instances = r.json()
            # Each entry has "api_url" — collect all with a valid API URL
            api_urls = [inst["api_url"].rstrip("/") for inst in instances
                        if inst.get("api_url")]
            # Merge registry results with fallback — registry entries go first,
            # dict.fromkeys preserves order and deduplicates
            merged = list(dict.fromkeys(api_urls + FALLBACK))[:15]
            print(f"[piped] fetched {len(api_urls)} instances from registry, using {len(merged)} total")
            return merged
    except Exception as e:
        print(f"[piped] instance registry failed: {e} — using fallback list")
    return FALLBACK


def _download_via_piped(query: str, job_id: str) -> Path:
    """
    Fallback YouTube download via Piped API.
    Piped proxies YouTube through its own servers, bypassing datacenter IP blocks.
    Tries multiple public instances for reliability.
    """
    # Dynamically fetch working Piped API instances, fall back to hardcoded list
    PIPED_INSTANCES = _get_piped_instances()

    out_dir = UPLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for base_url in PIPED_INSTANCES:
        try:
            # 1. Search for the video
            print(f"[piped] trying {base_url} — query: {query[:60]}")
            search_resp = requests.get(
                f"{base_url}/search",
                params={"q": query, "filter": "music_songs"},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if not search_resp.ok:
                print(f"[piped] {base_url} search failed: {search_resp.status_code}")
                continue

            items = search_resp.json().get("items", [])
            if not items:
                # Try without music filter
                search_resp = requests.get(
                    f"{base_url}/search",
                    params={"q": query, "filter": "videos"},
                    timeout=15,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                items = search_resp.json().get("items", []) if search_resp.ok else []

            if not items:
                print(f"[piped] {base_url}: no results")
                continue

            # Extract video ID from /watch?v=xxxxx URL
            video_url = items[0].get("url", "")
            video_id = video_url.replace("/watch?v=", "") if "/watch?v=" in video_url else ""
            if not video_id:
                print(f"[piped] {base_url}: could not extract video ID from {video_url}")
                continue

            title = items[0].get("title", "audio")
            duration = items[0].get("duration", 0)
            print(f"[piped] found: {title} ({duration}s) — id={video_id}")

            # Skip results shorter than 60s (likely not a full song)
            if 0 < duration < 60:
                print(f"[piped] skipping short result ({duration}s)")
                continue

            # 2. Get audio streams
            streams_resp = requests.get(
                f"{base_url}/streams/{video_id}",
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if not streams_resp.ok:
                print(f"[piped] {base_url} streams failed: {streams_resp.status_code}")
                continue

            data = streams_resp.json()
            audio_streams = data.get("audioStreams", [])
            if not audio_streams:
                print(f"[piped] {base_url}: no audio streams for {video_id}")
                continue

            # Pick best quality audio stream (prefer m4a/mp4 for compatibility)
            # Filter to audio-only streams, sort by bitrate descending
            audio_streams.sort(key=lambda s: s.get("bitrate", 0), reverse=True)
            best = audio_streams[0]
            audio_url = best.get("url")
            if not audio_url:
                continue

            mime = best.get("mimeType", "audio/mp4")
            bitrate = best.get("bitrate", 0)
            print(f"[piped] downloading stream: {bitrate}bps {mime}")

            # 3. Download the audio file
            ext = "m4a" if "mp4" in mime or "m4a" in mime else "webm" if "webm" in mime else "mp3"
            safe_title = re.sub(r'[^\w\s-]', '', title)[:80].strip()
            out_path = out_dir / f"{safe_title}.{ext}"

            dl_resp = requests.get(audio_url, stream=True, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
            dl_resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in dl_resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            size = out_path.stat().st_size
            if size < 100_000:  # Less than 100KB is probably an error page
                print(f"[piped] file too small ({size}b) — skipping")
                out_path.unlink(missing_ok=True)
                continue

            print(f"[piped] ✅ SUCCESS → {out_path.name} ({size:,} bytes, ~{size // 1024 // 128}s)")
            return out_path

        except Exception as e:
            print(f"[piped] {base_url} error: {e}")
            continue

    raise RuntimeError("All Piped instances failed — YouTube audio unavailable")


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


def resolve_audio(track_data: dict, job_id: str, on_progress=None, allow_preview_fallback: bool = True) -> Path:
    """
    Main entry point. Tries audio sources in waterfall order:
    1. YouTube via yt-dlp (full song)
    2. YouTube via Piped API (full song — bypasses IP blocks)
    3. Spotify preview URL (only if allow_preview_fallback=True)
    4. iTunes preview URL (only if allow_preview_fallback=True)
    5. Raises AudioUnavailableError

    track_data keys: query, preview_url, artist, name
    """
    query = track_data.get("query")
    preview_url = track_data.get("preview_url")
    artist = track_data.get("artist", "")
    name = track_data.get("name", "")

    # 1. YouTube via yt-dlp (full audio)
    if query:
        if on_progress:
            on_progress("Downloading from YouTube...")
        try:
            path = download_audio_from_youtube(query, job_id)
            print(f"[job {job_id}] AUDIO SOURCE SELECTED: youtube (yt-dlp)")
            return path
        except Exception as e:
            print(f"[job {job_id}] ⚠️  yt-dlp FAILED: {str(e)[:300]}")

    # 2. YouTube via Cobalt API (third-party extraction service)
    if query:
        if on_progress:
            on_progress("Trying alternative download...")
        try:
            path = _download_via_cobalt(query, job_id)
            print(f"[job {job_id}] AUDIO SOURCE SELECTED: youtube (cobalt)")
            return path
        except Exception as e:
            print(f"[job {job_id}] ⚠️  Cobalt FAILED: {str(e)[:300]}")

    # 3. YouTube via Piped API (bypasses datacenter IP blocks)
    if query:
        if on_progress:
            on_progress("Trying another alternative...")
        try:
            path = _download_via_piped(query, job_id)
            print(f"[job {job_id}] AUDIO SOURCE SELECTED: youtube (piped)")
            return path
        except Exception as e:
            print(f"[job {job_id}] ⚠️  Piped FAILED: {str(e)[:300]}")

    # All YouTube methods failed (yt-dlp, cobalt, piped)
    if query and not allow_preview_fallback:
        print(f"[job {job_id}] all full-track sources failed — raising upload_required")
        raise AudioUnavailableError(
            f"Full-track download unavailable. Upload your own audio file to get full-length stems."
        )

    if query:
        print(f"[job {job_id}] ⚠️  FALLING BACK TO PREVIEW — stems will be ~30s, not full song")
        if on_progress:
            on_progress("Full track unavailable, trying preview...")

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
