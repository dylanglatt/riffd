"""
app.py — Riffd Flask web server.

Request lifecycle:
  1. User searches via /api/spotify/search → Spotify API (with local fallback)
  2. User selects a track → frontend checks /api/track/<id> for cached analysis
  3. If no cache: frontend calls /api/download with mode=full
     - Full: resolve_audio() → YouTube (yt-dlp → cobalt → piped) (~30-120s)
     - If YouTube fails: upload_required → user uploads their own file
  4. Frontend calls /api/process/<job_id> with analysis_mode=deep
     - deep: async background thread, returns via polling (~2-5min, full stems + harmonic analysis)
     - key/BPM/intelligence published progressively before stems complete
  5. Frontend polls /api/status/<job_id> for progressive results
  6. Results served via /api/audio/<job_id>/<stem> for playback

Job status lifecycle:
  downloading → ready → processing → complete|partial|error
  downloading → ready → queued → processing → complete|partial|error  (when slots full)
  downloading → upload_required      (all sources failed)
  downloading → error                (unexpected failure)

Memory management:
  - Heavy imports (numpy, pandas, basic_pitch) deferred to first job
  - Jobs pruned from memory after 10 minutes
  - Job payloads trimmed after frontend polls the result
  - MAX_CONCURRENT_JOBS (default 3) limits concurrent deep analysis; extras are queued
"""

import os
import re
import uuid
import collections
import threading
import traceback
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for, make_response, send_file
from werkzeug.utils import secure_filename

# Heavy processing modules deferred — loaded on first job, not at boot
# processor.py pulls in numpy, pandas, basic_pitch (~200MB+)
# music_intelligence.py pulls in numpy, pandas
def _lazy_processor():
    from processor import separate_stems, extract_note_events
    return separate_stems, extract_note_events

def _lazy_music_intelligence():
    from music_intelligence import analyze_song_from_notes
    return analyze_song_from_notes

from spotify_search import search_spotify, get_recommendations_for_track, RateLimitError, _DISCOVERY_TRACKS
from external_apis import get_lyrics, get_track_tags, enrich_recommendations_with_lastfm, _get_art_for_track
from downloader import download_audio_from_youtube, resolve_audio, AudioUnavailableError
from analytics import log_event
from history import add_to_history, get_recent, get_cached_result, save_cached_result, touch_history
from db import init_db, migrate_from_history_json, get_track, upsert_track, set_track_status, touch_track, get_recent_tracks, get_analysis_for_track

load_dotenv()


def _enrich_smart_recs_art(insight: dict | None) -> None:
    """Fill missing image_url on smart_recs entries using iTunes art lookup."""
    if not insight:
        return
    smart_recs = insight.get("smart_recs") or {}
    filled = 0
    for category_songs in smart_recs.values():
        if not isinstance(category_songs, list):
            continue
        for song in category_songs:
            if song.get("image_url") or not song.get("title") or not song.get("artist"):
                continue
            art = _get_art_for_track(song["artist"], song["title"])
            if art:
                song["image_url"] = art
                filled += 1
    if filled:
        print(f"[insight] filled {filled} smart_rec art entries via iTunes")


# ─── Startup checks ──────────────────────────────────────────────────────────
SITE_PASSWORD = os.getenv("SITE_PASSWORD")
FLASK_SECRET = os.getenv("FLASK_SECRET_KEY")

print(f"[auth] SITE_PASSWORD set: {bool(SITE_PASSWORD)}")
print(f"[auth] FLASK_SECRET_KEY set: {bool(FLASK_SECRET)}")
print(f"[env] USE_HOSTED_SEPARATION = {os.getenv('USE_HOSTED_SEPARATION')!r}")
print(f"[env] HAS_REPLICATE_TOKEN = {bool(os.getenv('REPLICATE_API_TOKEN'))}")
print(f"[env] CWD = {os.getcwd()}")
print(f"[env] YT_PROXY_URL set: {bool(os.getenv('YT_PROXY_URL'))}")

if not SITE_PASSWORD:
    print("[auth] SITE_PASSWORD not set — running in open-access mode")
if not FLASK_SECRET:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is required. Set it in .env or your hosting platform.")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
app.secret_key = FLASK_SECRET

# Session config: browser-session cookie (no max-age, cleared when browser closes)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # Set True if serving over HTTPS only
app.config["SESSION_COOKIE_NAME"] = "riffd_session"

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Initialize database on startup
init_db()
migrate_from_history_json()

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}

# ─── Job State ────────────────────────────────────────────────────────────────
# In-memory dict tracking all active jobs. Pruned after 10 min, trimmed after delivery.
# Keys: job_id (8-char UUID prefix)
# Values: dict with status, progress, audio_path, audio_source, audio_mode, analysis results
# WARNING: all state lost on server restart — persistent results are in filesystem cache + SQLite
jobs = {}
_stem_last_accessed: dict = {}  # job_id → timestamp of last audio request
_processing_lock = threading.Lock()
_active_processing = 0
# Stem separation runs on Replicate (cloud GPU), so local RAM is not the bottleneck.
# Allow multiple concurrent jobs; tune via MAX_CONCURRENT_JOBS env var if needed.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
_job_queue: collections.deque = collections.deque()  # jobs waiting for a processing slot

# ─── Background prefetch ─────────────────────────────────────────────────────
# In-memory dict tracking background downloads. Keyed by spotify_track_id.
# Separate from `jobs` — these are invisible to the user until they click Separate Stems.
_bg_downloads = {}
_bg_lock = threading.Lock()


def _log_memory(label=""):
    """Log current process RSS memory usage."""
    try:
        import resource
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)  # macOS returns bytes
        # Linux returns KB
        import sys
        if sys.platform == "linux":
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"[mem] {label} RSS={rss_mb:.0f}MB")
    except Exception:
        pass


def _dequeue_next():
    """Called when a processing slot opens — start the next queued job if one exists."""
    global _active_processing
    with _processing_lock:
        if _job_queue and _active_processing < MAX_CONCURRENT_JOBS:
            job_id, run_fn = _job_queue.popleft()
            if job_id in jobs:
                jobs[job_id]["status"] = "processing"
                jobs[job_id]["progress"] = "Separating stems..."
            threading.Thread(target=run_fn, daemon=True).start()


def _prune_old_jobs():
    """Remove completed/errored jobs older than 10 minutes to free memory."""
    import time
    now = time.time()
    stale = []
    for jid, job in jobs.items():
        status = job.get("status", "")
        if status in ("complete", "partial", "error", "upload_required"):
            finished_at = job.get("_finished_at", job.get("_started_at", 0))
            if finished_at and (now - finished_at) > 600:  # 10 minutes
                stale.append(jid)
    for jid in stale:
        del jobs[jid]
    if stale:
        print(f"[mem] pruned {len(stale)} stale jobs: {stale}")

    # Also prune stale prefetch entries (older than 15 minutes)
    with _bg_lock:
        stale_bg = [pid for pid, e in _bg_downloads.items()
                    if e.get("status") in ("ready", "failed") and
                    e.get("_started_at", 0) and (now - e["_started_at"]) > 900]
        for pid in stale_bg:
            del _bg_downloads[pid]
        if stale_bg:
            print(f"[mem] pruned {len(stale_bg)} stale prefetch entries")

    # Delete stem audio files for jobs idle >30 minutes
    # result_cache.json is preserved — only the stems/ subdirectory is removed
    import time as _t2, shutil as _shutil2
    _now2 = _t2.time()
    idle_stem_jobs = [jid for jid, ts in list(_stem_last_accessed.items())
                      if (_now2 - ts) > 1800]
    for jid in idle_stem_jobs:
        stems_dir = OUTPUT_DIR / jid / "stems"
        if stems_dir.exists():
            _shutil2.rmtree(stems_dir, ignore_errors=True)
            print(f"[disk] deleted idle stems for {jid}")
        del _stem_last_accessed[jid]

    # Prune old job directories from disk (outputs/ and uploads/) — 7 day retention
    _prune_old_disk_dirs()


_last_disk_prune = 0

def _prune_old_disk_dirs():
    """Delete job directories from outputs/ and uploads/ older than 7 days.
    Runs at most once per hour to avoid repeated filesystem scans."""
    global _last_disk_prune
    import time as _t
    now = _t.time()
    if now - _last_disk_prune < 3600:  # At most once per hour
        return
    _last_disk_prune = now

    _retention_hours = int(os.getenv("DISK_RETENTION_HOURS", "168"))  # default 7 days; set to 6 on Render
    max_age = _retention_hours * 3600
    pruned = 0
    for parent in (OUTPUT_DIR, UPLOAD_DIR):
        if not parent.exists():
            continue
        for d in parent.iterdir():
            if not d.is_dir():
                continue
            try:
                mtime = d.stat().st_mtime
                if (now - mtime) > max_age:
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)
                    pruned += 1
            except Exception:
                pass
    if pruned:
        print(f"[disk] pruned {pruned} old job directories (>7 days)")


def _trim_job_result(job_id):
    """Strip heavy payload from a completed job after the frontend has polled it.
    We keep only status + lightweight metadata. Full results are in the filesystem cache."""
    job = jobs.get(job_id)
    if not job:
        return
    status = job.get("status")
    if status not in ("complete", "partial"):
        return
    # Mark when finished for pruning
    import time
    job["_finished_at"] = time.time()
    # Keep only what the status endpoint needs
    kept = {"status", "progress", "audio_source", "audio_mode", "error", "errors",
            "_started_at", "_finished_at", "_result_delivered"}
    for key in list(job.keys()):
        if key not in kept:
            del job[key]


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


_log_memory("startup")

# Run disk cleanup immediately on startup rather than waiting for first request
_last_disk_prune = 0
threading.Thread(target=_prune_old_disk_dirs, daemon=True).start()

# ─── Authentication ───────────────────────────────────────────────────────────

# Path-based allowlist — explicit and auditable
AUTH_PUBLIC_PATHS = ("/login", "/static/", "/", "/favicon.ico", "/s/")

@app.before_request
def require_login():
    # Open-access mode: no password required
    if not SITE_PASSWORD:
        return
    path = request.path
    is_public = (path == "/login" or path == "/" or path == "/favicon.ico" or
                 path.startswith("/static/") or path.startswith("/s/") or
                 path == "/about" or path.startswith("/shared/"))
    is_authed = session.get("authenticated") is True

    if is_public:
        return  # Always allow public pages and static assets
    if is_authed:
        return  # Session is authenticated — allow

    # Block everything else
    print(f"[auth] blocked unauthenticated request: {path}")
    return redirect("/login")


def _clean_track_name(name: str) -> str:
    """Strip Spotify edition/remaster suffixes from track titles for display.
    Preserves original casing — this is for UI display, not search matching.
    Examples:
      "Song - Remastered 2009"       → "Song"
      "Song - 2009 Remaster"         → "Song"
      "Song (Remastered)"            → "Song"
      "Song - Live at Wembley"       → "Song"
      "Song - Radio Edit"            → "Song"
    """
    t = name.strip()
    # "- 2009 Remaster(ed)" and "- Remastered 2009" and "- Remaster"
    t = re.sub(r"\s*[-–]\s*\d{4}\s*remaster(ed)?.*$", "", t, flags=re.I)
    t = re.sub(r"\s*[-–]\s*remaster(ed)?(\s*\d{4})?.*$", "", t, flags=re.I)
    # Other common suffixes
    t = re.sub(r"\s*[-–]\s*(live|mono|stereo|radio edit|single version|album version|"
               r"deluxe|bonus track|anniversary edition|original mix|extended mix).*$",
               "", t, flags=re.I)
    # Parenthetical editions: (Remastered), (Live at ...), (2009), etc.
    t = re.sub(r"\s*\((remaster(ed)?|live[^)]*|\d{4}\s*remaster[^)]*)\)", "", t, flags=re.I)
    return t.strip()


@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")

@app.route("/favicon.svg")
def favicon_svg():
    return send_from_directory("static", "favicon.svg", mimetype="image/svg+xml")


@app.route("/login", methods=["GET", "POST"])
def login():
    # If already authenticated, go to home
    if session.get("authenticated") is True:
        return redirect("/")

    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == SITE_PASSWORD:
            session.clear()  # Clear any stale session data
            session["authenticated"] = True
            session.permanent = False  # Ensure browser-session only
            print(f"[auth-v2] LOGIN OK")
            return redirect("/")
        error = "Incorrect password"
        print(f"[auth-v2] LOGIN FAILED")
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    print(f"[auth-v2] LOGOUT")
    return redirect("/login")


@app.route("/")
def index():
    return render_template("home.html", active_page="home")


@app.route("/decompose")
def decompose():
    return render_template("decompose.html", active_page="decompose")


@app.route("/learn")
def learn():
    return render_template("learn.html", active_page="learn")



@app.route("/practice")
def practice():
    return render_template("practice.html", active_page="practice")


@app.route("/about")
def about():
    return render_template("about.html", active_page="about")

@app.route('/analyze')
def redirect_analyze():
    return redirect('/decompose', code=301)

@app.route('/theory')
def redirect_theory():
    return redirect('/learn', code=301)


@app.route("/api/spotify/search")
def spotify_search_route():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "No query provided"}), 400
    try:
        print(f"[search] query='{query}'")
        results = search_spotify(query)
        print(f"[search] returned {len(results)} results")
        return jsonify(results)
    except RateLimitError as e:
        print(f"[search] RATE LIMITED (retry_after={e.retry_after}s)")
        return jsonify({"error": "Too many searches. Please wait a few seconds and try again.", "retry_after": e.retry_after}), 429
    except Exception as e:
        print(f"[search] ERROR: {type(e).__name__}: {e}")
        return jsonify({"error": "Search temporarily unavailable. Please try again."}), 500


@app.route("/api/track/<track_id>")
def track_lookup(track_id):
    """
    Look up a track by Spotify ID. Returns:
    - analysis_status: available | pending | unavailable
    - track metadata (title, artist, artwork, etc.)
    - analysis payload if status is available
    """
    track = get_track(track_id)
    if not track:
        return jsonify({"analysis_status": "unavailable", "spotify_track_id": track_id})

    result = {
        "spotify_track_id": track["spotify_track_id"],
        "title": track["title"],
        "artist": track["artist"],
        "album": track.get("album", ""),
        "artwork_url": track.get("artwork_url"),
        "duration_ms": track.get("duration_ms", 0),
        "analysis_status": track["analysis_status"],
    }

    if track["analysis_status"] == "available":
        analysis = get_analysis_for_track(track_id)
        if analysis:
            # Invalidate preview-mode cache — force re-analysis with full track
            if analysis.get("audio_mode") == "preview" or analysis.get("analysis_mode") == "instant":
                print(f"[track] invalidating preview-mode cache for {track_id}")
                set_track_status(track_id, "pending")
                result["analysis_status"] = "unavailable"
            else:
                touch_track(track_id)
                result["analysis"] = analysis
                result["job_id"] = track.get("job_id")
        else:
            # Cache disappeared — downgrade status
            result["analysis_status"] = "unavailable"

    return jsonify(result)


@app.route("/api/download", methods=["POST"])
def download_track():
    """
    Audio acquisition endpoint. Always downloads full track via YouTube.
    If YouTube fails, returns upload_required so user can upload their own file.
    Downloads run in a background thread. Frontend polls /api/status/<job_id>.
    Returns immediately with {job_id}.
    """
    data = request.json
    query = data.get("query")       # YouTube search query
    url = data.get("url")           # Direct URL (rare, for YouTube links)
    track_id = data.get("track_id") # Spotify track ID for cache lookup
    mode = "full"                   # Always full — preview mode removed

    print(f"[download] triggered query={bool(query)} url={bool(url)} artist={data.get('artist','')[:20]}")
    log_event("download_start", {"mode": mode, "artist": data.get("artist", "")[:30], "track": data.get("name", "")[:40]})

    if not query and not url:
        return jsonify({"error": "Provide 'query' or 'url'"}), 400

    # Check if background prefetch has (or will soon have) the full track
    if track_id:
        with _bg_lock:
            bg = _bg_downloads.get(track_id)

        if bg:
            # If prefetch is still downloading, return immediately — blocking here would
            # exceed Gunicorn's 30s worker timeout, killing the worker and wiping in-memory state.
            # The frontend handles this by polling /api/prefetch/<id>/status and retrying.
            if bg["status"] == "downloading":
                prefetch_id = bg.get("prefetch_id")
                print(f"[download] prefetch in progress for track_id={track_id} — returning pending to frontend")
                return jsonify({"status": "prefetch_pending", "prefetch_id": prefetch_id}), 202

            # Re-check after potential wait
            if bg and bg["status"] == "ready" and bg.get("audio_path"):
                job_id = str(uuid.uuid4())[:8]
                audio_path = bg["audio_path"]
                jobs[job_id] = {
                    "status": "ready",
                    "audio_path": audio_path,
                    "audio_source": "youtube",
                    "audio_mode": "full",
                    "progress": "Download complete",
                }
                print(f"[job {job_id}] DOWNLOAD REUSED from prefetch → {audio_path}")
                log_event("prefetch_hit", {"track_id": track_id})
                return jsonify({"job_id": job_id, "mode": mode})

    # Check if we already have full-track audio from a previous job
    if track_id:
        from history import _load_history
        hist = _load_history()
        entry = hist.get(track_id)
        if entry and entry.get("job_id"):
            old_job = entry["job_id"]
            old_upload = UPLOAD_DIR / old_job
            if old_upload.exists():
                audio_files = [f for f in old_upload.iterdir()
                               if f.suffix.lower() in {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac"}
                               and f.name != "preview.mp3"]  # Skip cached previews
                if audio_files:
                    job_id = str(uuid.uuid4())[:8]
                    audio_path = str(audio_files[0])
                    jobs[job_id] = {"status": "ready", "audio_path": audio_path, "audio_source": "cache", "audio_mode": "full", "progress": "Download complete"}
                    print(f"[job {job_id}] DOWNLOAD REUSED from job {old_job} → {audio_path}")
                    return jsonify({"job_id": job_id, "mode": mode})

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "downloading", "audio_mode": "full", "progress": "Getting audio..."}
    print(f"[job {job_id}] DOWNLOAD START query='{(query or url or '')[:60]}'")

    def run():
        def _on_progress(msg):
            jobs[job_id]["progress"] = msg
            print(f"[job {job_id}] download progress: {msg}")

        try:
            track_data = {
                "query": query or url,
                "artist": data.get("artist", ""),
                "name": data.get("name", ""),
            }

            print(f"[job {job_id}] downloading full track...")
            is_direct_yt = (url and url.startswith("http") and
                            ("youtube.com" in url or "youtu.be" in url))

            if is_direct_yt:
                _on_progress("Downloading full track...")
                audio_path = download_audio_from_youtube(url, job_id)
                audio_source = "youtube"
                print(f"[job {job_id}] AUDIO SOURCE SELECTED: youtube (direct URL)")
            else:
                _on_progress("Downloading full track...")
                audio_path = resolve_audio(track_data, job_id, on_progress=_on_progress)
                audio_source = "youtube"

            print(f"[job {job_id}] download finished → {audio_path} (source={audio_source})")
            jobs[job_id].update({
                "status": "ready",
                "audio_path": str(audio_path),
                "audio_source": audio_source,
                "audio_mode": "full",
                "progress": "Download complete",
            })
            print(f"[job {job_id}] STATUS → ready")

        except AudioUnavailableError as e:
            print(f"[job {job_id}] SOURCES FAILED: {e}")
            log_event("youtube_failed", {"job_id": job_id, "error": str(e)[:100]})
            jobs[job_id].update({
                "status": "upload_required",
                "audio_source": None,
                "audio_mode": "full",
                "error": "Full track unavailable. Please upload the audio file directly.",
            })
            print(f"[job {job_id}] STATUS → upload_required")

        except Exception as e:
            print(f"[job {job_id}] DOWNLOAD ERROR: {e}")
            traceback.print_exc()
            log_event("youtube_failed", {"job_id": job_id, "error": str(e)[:100]})
            jobs[job_id].update({"status": "error", "error": str(e)})
            print(f"[job {job_id}] STATUS → error")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id, "mode": mode})


@app.route("/api/prefetch", methods=["POST"])
def prefetch_full_track():
    """
    Start downloading the full YouTube track in the background.
    Called by the frontend immediately when a song is selected.
    Does NOT block the instant analysis flow.
    Returns immediately with {prefetch_id}.
    """
    data = request.get_json(silent=True) or {}
    track_id = data.get("track_id")
    yt_query = data.get("yt_query")
    artist = data.get("artist", "")
    name = data.get("name", "")

    if not yt_query:
        return jsonify({"error": "No yt_query provided"}), 400

    # Don't start duplicate downloads for the same track
    with _bg_lock:
        if track_id and track_id in _bg_downloads:
            existing = _bg_downloads[track_id]
            if existing["status"] in ("downloading", "ready"):
                print(f"[prefetch] already running/ready for track_id={track_id}")
                return jsonify({"prefetch_id": existing["prefetch_id"], "status": existing["status"]})

    import time as _pf_time
    prefetch_id = str(uuid.uuid4())[:8]
    entry = {
        "prefetch_id": prefetch_id,
        "track_id": track_id,
        "status": "downloading",
        "audio_path": None,
        "error": None,
        "_started_at": _pf_time.time(),
    }

    with _bg_lock:
        if track_id:
            _bg_downloads[track_id] = entry
        _bg_downloads[prefetch_id] = entry

    print(f"[prefetch {prefetch_id}] starting background download: {yt_query[:60]}")
    log_event("prefetch_start", {"track": name[:40], "artist": artist[:30]})

    def bg_download():
        try:
            print(f"[prefetch {prefetch_id}] bg_download thread started")
            track_data = {
                "query": yt_query,
                "artist": artist,
                "name": name,
            }
            audio_path = resolve_audio(track_data, prefetch_id)
            entry["status"] = "ready"
            entry["audio_path"] = str(audio_path)
            entry["is_full_track"] = True
            print(f"[prefetch {prefetch_id}] COMPLETE → youtube → {audio_path}")
        except AudioUnavailableError as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            print(f"[prefetch {prefetch_id}] FAILED (unavailable): {e}")
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            import traceback
            print(f"[prefetch {prefetch_id}] FAILED: {e}")
            traceback.print_exc()

    threading.Thread(target=bg_download, daemon=True).start()
    return jsonify({"prefetch_id": prefetch_id, "status": "downloading"})


@app.route("/api/prefetch/<prefetch_id>/status")
def prefetch_status(prefetch_id):
    """Check if a background download is done."""
    entry = _bg_downloads.get(prefetch_id)
    if not entry:
        return jsonify({"status": "unknown"}), 404
    return jsonify({
        "status": entry["status"],
        "audio_path": entry["audio_path"],
        "error": entry["error"],
    })


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename or not allowed_file(f.filename):
        return jsonify({"error": "Unsupported file type."}), 400
    job_id = str(uuid.uuid4())[:8]
    filename = secure_filename(f.filename)
    save_path = UPLOAD_DIR / job_id / filename
    save_path.parent.mkdir(parents=True, exist_ok=True)
    f.save(save_path)
    jobs[job_id] = {"status": "ready", "audio_path": str(save_path), "progress": "File uploaded"}

    # Silently attempt to match Spotify metadata — never blocks or errors
    track_meta = _match_upload_metadata(filename, str(save_path))
    resp = {"job_id": job_id, "filename": filename}
    if track_meta:
        resp["track_meta"] = track_meta
    return jsonify(resp)


def _match_upload_metadata(filename: str, filepath: str) -> dict | None:
    """
    Try to find Spotify metadata for an uploaded file using ID3 tags or filename parsing.
    Returns a track dict (same shape as search results) or None — never raises.
    Runs synchronously in the upload request; typically completes in <400ms.
    """
    try:
        import difflib
        title, artist = None, None

        # 1. Read embedded tags (mutagen handles mp3/flac/m4a/ogg)
        try:
            from mutagen import File as _MutagenFile
            audio = _MutagenFile(filepath, easy=True)
            if audio:
                title = (audio.get("title") or [None])[0]
                artist = (audio.get("artist") or [None])[0]
                # mutagen easy tags return strings directly
                if title: title = str(title).strip()
                if artist: artist = str(artist).strip()
        except Exception:
            pass

        # 2. Fall back to filename parsing ("Artist - Title.mp3" pattern)
        if not title:
            stem = Path(filename).stem.replace("_", " ").replace(".", " ")
            parts = re.split(r"\s*[-–—]\s*", stem, maxsplit=1)
            if len(parts) == 2:
                artist = artist or parts[0].strip()
                title = parts[1].strip()
            else:
                title = stem.strip()

        if not title:
            return None

        # 3. Spotify search
        query = f"{artist} {title}".strip() if artist else title
        results = search_spotify(query, limit=3)
        if not results:
            return None

        # 4. Confidence gate — reject weak matches to avoid wrong art
        top = results[0]
        ratio = difflib.SequenceMatcher(None, title.lower(), top["name"].lower()).ratio()
        if ratio < 0.65:
            print(f"[upload] metadata match rejected: '{title}' vs '{top['name']}' (ratio={ratio:.2f})")
            return None

        print(f"[upload] metadata match: '{top['name']}' by {top['artist']} (ratio={ratio:.2f})")
        return top

    except Exception as e:
        print(f"[upload] metadata match error: {e}")
        return None


@app.route("/api/process/<job_id>", methods=["POST"])
def process_audio(job_id):
    """
    Processing endpoint. Runs deep analysis pipeline in background thread.
    Key/BPM, lyrics, and tags are published progressively before stems complete.
    Frontend polls /api/status/<job_id> for progressive results.
    """
    global _active_processing
    if job_id not in jobs:
        return jsonify({"error": "Unknown job ID"}), 404
    job = jobs[job_id]
    if job["status"] != "ready":
        return jsonify({"error": f"Job not ready (status: {job['status']})"}), 400
    audio_path = job.get("audio_path")
    if not audio_path:
        return jsonify({"error": "No audio file"}), 400

    req_data = request.json or {}

    # Guard against stacking heavy jobs — queue instead of reject

    # Prune stale jobs before starting a new one
    _prune_old_jobs()

    spotify_track_id = req_data.get("track_id")
    spotify_artist_id = req_data.get("artist_id")
    track_meta = req_data.get("track_meta", {})

    import time as _time_mod
    jobs[job_id]["status"] = "processing"
    jobs[job_id]["progress"] = "Separating stems..."
    jobs[job_id]["_started_at"] = _time_mod.time()
    print(f"[job {job_id}] process start analysis_mode=deep audio={audio_path}")
    log_event("deep_analysis_start", {"job_id": job_id, "audio_source": job.get("audio_source")})

    # Mark track as pending in DB
    if spotify_track_id:
        upsert_track(
            spotify_track_id,
            track_meta.get("name", ""),
            track_meta.get("artist", ""),
            artwork_url=track_meta.get("image_url"),
            duration_ms=track_meta.get("duration_ms", 0),
            year=track_meta.get("year", ""),
            artist_id=track_meta.get("artist_id"),
            yt_query=track_meta.get("yt_query", ""),
        )
        set_track_status(spotify_track_id, "pending")

    def run():
        global _active_processing
        with _processing_lock:
            _active_processing += 1
        print(f"[mem] active processing jobs: {_active_processing}")

        import time as _time
        import gc
        _t0 = _time.time()
        def _elapsed():
            return f"{_time.time()-_t0:.1f}s"

        # Load heavy processing modules on first job
        separate_stems, extract_note_events = _lazy_processor()
        analyze_song_from_notes = _lazy_music_intelligence()
        _log_memory(f"[job {job_id}] PROCESS START (after imports)")

        # Extract track metadata for use throughout the pipeline
        artist_name = track_meta.get("artist", "")
        track_name  = track_meta.get("name", "")

        # Partial results accumulate — returned even if a later stage fails
        stems = {}
        intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 0, "bpm_confidence": 0, "progression": None}
        lyrics = None
        tags = []
        insight_text = None
        recs = {"more_like_this": [], "same_style": [], "around_this_time": []}
        failed_steps = []

        def on_progress(msg):
            jobs[job_id]["progress"] = msg
            print(f"[job {job_id}] [{_elapsed()}] progress: {msg}")

        def _fail(step, e):
            """Record a failed step without aborting the pipeline."""
            print(f"[job {job_id}] [{_elapsed()}] {step} FAILED: {e}")
            traceback.print_exc()
            failed_steps.append({"step": step, "message": str(e)})

        def _finalize():
            """Write whatever we have to the job dict. Called on success AND failure."""
            partial = bool(failed_steps)
            result = {
                "status": "complete" if not partial else "partial",
                "stems": {k: {"label": v.get("label", k), "energy": v.get("energy", 0), "active": v.get("active", True)} for k, v in stems.items()},
                "intelligence": intelligence,
                "lyrics": lyrics,
                "tags": tags,
                "insight": insight_text,
                "recommendations": recs,
                "audio_source": jobs[job_id].get("audio_source"),
                "audio_mode": jobs[job_id].get("audio_mode", "full"),
                "progress": "Done!" if not partial else "Completed with errors",
            }
            if partial:
                result["errors"] = failed_steps
                result["error"] = f"{len(failed_steps)} step(s) failed: {', '.join(s['step'] for s in failed_steps)}"
            jobs[job_id].update(result)
            import time as _t; _stem_last_accessed[job_id] = _t.time()
            status_label = result["status"]
            print(f"[job {job_id}] [{_elapsed()}] STATUS → {status_label}" + (f" (failed: {[s['step'] for s in failed_steps]})" if partial else ""))
            log_event("analysis_complete", {"job_id": job_id, "status": result["status"], "elapsed": round(_time.time() - _t0, 1)})

            # Save to cache + history + DB
            if spotify_track_id and (not partial or stems):
                try:
                    save_cached_result(job_id, {
                        "stems": result["stems"],
                        "intelligence": intelligence,
                        "lyrics": lyrics,
                        "tags": tags,
                        "insight": insight_text,
                        "recommendations": recs,
                        "audio_source": jobs[job_id].get("audio_source"),
                        "audio_mode": jobs[job_id].get("audio_mode", "full"),
                        "job_id": job_id,
                        "track_id": spotify_track_id,
                    })
                    add_to_history(spotify_track_id, track_meta, job_id)

                    # Update DB: mark as available
                    from db import upsert_track, set_track_status, ANALYSIS_VERSION
                    upsert_track(
                        spotify_track_id,
                        track_meta.get("name", ""),
                        track_meta.get("artist", ""),
                        album=track_meta.get("album", ""),
                        artwork_url=track_meta.get("image_url"),
                        duration_ms=track_meta.get("duration_ms", 0),
                        year=track_meta.get("year", ""),
                        artist_id=track_meta.get("artist_id"),
                        yt_query=track_meta.get("yt_query", ""),
                    )
                    set_track_status(spotify_track_id, "available",
                                     job_id=job_id, analysis_version=ANALYSIS_VERSION)

                    print(f"[job {job_id}] [{_elapsed()}] cache+history+db saved")
                except Exception as e:
                    print(f"[job {job_id}] [{_elapsed()}] save error: {e}")

        try:
            from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _as_completed

            # ── Stages 1 + 4 + 6 fired in parallel ──
            # Lyrics and tags only need artist/track name — no audio required.
            # Fire them concurrently with Demucs so they're ready by the time stems land.
            print(f"[job {job_id}] [{_elapsed()}] DEMUCS + metadata fetch starting in parallel...")

            def _fetch_lyrics():
                try:
                    if not (artist_name and track_name): return None
                    return get_lyrics(artist_name, track_name)
                except Exception as e:
                    _fail("lyrics", e)
                    return None

            def _fetch_tags():
                try:
                    if not (artist_name and track_name): return []
                    return get_track_tags(artist_name, track_name)
                except Exception as e:
                    _fail("tags", e)
                    return []

            def _run_demucs():
                return separate_stems(audio_path, job_id, progress_callback=on_progress)

            def _run_early_key():
                """Key + BPM on the original audio — runs concurrently with Demucs.
                Publishes intelligence within ~10s so Key tab populates early."""
                try:
                    from music_intelligence import detect_key_from_audio, detect_bpm_from_audio, format_key
                    key_num, mode_num, key_conf = detect_key_from_audio(audio_path)
                    bpm, bpm_conf = detect_bpm_from_audio(audio_path)
                    return key_num, mode_num, key_conf, bpm, bpm_conf
                except Exception as e:
                    print(f"[job {job_id}] early key/BPM failed: {e}")
                    return -1, -1, 0.0, 0, 0.0

            # Pre-warm numpy in the main thread before the pool starts.
            # Two threads racing to initialize numpy's C extension simultaneously
            # causes a circular import crash on some local Python environments.
            try:
                import numpy as _np_preload  # noqa: F401
            except Exception:
                pass

            with _TPE(max_workers=4) as _pool:
                fut_demucs    = _pool.submit(_run_demucs)
                fut_lyrics    = _pool.submit(_fetch_lyrics)
                fut_tags      = _pool.submit(_fetch_tags)
                fut_early_key = _pool.submit(_run_early_key)

                # Collect metadata results (fast — done well before Demucs)
                lyrics       = fut_lyrics.result()
                tags         = fut_tags.result()
                print(f"[job {job_id}] [{_elapsed()}] metadata fetched: lyrics={'yes' if lyrics else 'no'} tags={len(tags)}")
                # Publish lyrics/tags progressively so frontend can show them before stems
                if lyrics: jobs[job_id]["lyrics"] = lyrics
                if tags: jobs[job_id]["tags"] = tags

                # Early key/BPM — should finish well before Demucs (runs on original audio)
                try:
                    from music_intelligence import format_key as _fmt_key
                    _ek_num, _ek_mode, _ek_conf, _ebpm, _ebpm_conf = fut_early_key.result()
                    if _ek_num >= 0:
                        intelligence["key"]            = _fmt_key(_ek_num, _ek_mode)
                        intelligence["key_num"]        = _ek_num
                        intelligence["mode_num"]       = _ek_mode
                        intelligence["key_confidence"] = round(_ek_conf, 3)
                    if _ebpm > 0 and _ebpm_conf >= 0.1:
                        intelligence["bpm"]            = round(_ebpm, 1)
                        intelligence["bpm_confidence"] = round(_ebpm_conf, 3)
                    print(f"[job {job_id}] [{_elapsed()}] early key={intelligence['key']} bpm={intelligence.get('bpm', 0)}")
                    # Publish now — Key tab populates before Demucs finishes
                    jobs[job_id]["intelligence"] = dict(intelligence)
                except Exception as _ek_e:
                    print(f"[job {job_id}] [{_elapsed()}] early key publish error: {_ek_e}")

                # Wait for Demucs (the slow one)
                try:
                    stems = fut_demucs.result()
                except Exception as _demucs_err:
                    stems = None
                    _demucs_exc = _demucs_err

            if not stems:
                # Demucs failure is fatal — nothing to work with
                print(f"[job {job_id}] [{_elapsed()}] DEMUCS FATAL: {_demucs_exc}")
                traceback.print_exc()
                jobs[job_id].update({
                    "status": "error",
                    "error": str(_demucs_exc),
                    "error_step": "stem_separation",
                    "progress": "Stem separation failed",
                })
                return

            gc.collect()
            print(f"[job {job_id}] [{_elapsed()}] DEMUCS finished → {len(stems)} stems: {list(stems.keys())}")
            _log_memory(f"[job {job_id}] post-demucs")
            # Free any large in-memory objects from demucs before Basic Pitch
            gc.collect()
            _log_memory(f"[job {job_id}] pre-basic-pitch")
            jobs[job_id]["stems"] = {k: {"label": v.get("label", k), "energy": v.get("energy", 0), "active": v.get("active", True)} for k, v in stems.items()}
            jobs[job_id]["progress"] = "Stems ready — analyzing..."

            active_stems = {k: v for k, v in stems.items() if v.get("active", True)}
            detected_bpm = 120.0

            # ── Stage 2: Key + BPM — already detected early (concurrent with Demucs) ──
            # Reuse the result from _run_early_key; no need to re-run on the same audio.
            essentia_key_num  = intelligence.get("key_num",  -1)
            essentia_mode_num = intelligence.get("mode_num", -1)
            essentia_key_conf = intelligence.get("key_confidence", 0.0)
            if intelligence.get("bpm", 0) > 0:
                detected_bpm = intelligence["bpm"]
            print(f"[job {job_id}] [{_elapsed()}] key/BPM (from early detect): key={intelligence['key']} bpm={detected_bpm}")

            # ── Stage 3: Note extraction (Basic Pitch) — sequential across priority stems ──
            # Drums produce no useful pitch data — skip entirely.
            # Secondary stems (harmony/backing vocals, synth, keys, other) are skipped for
            # Basic Pitch — they add ~3-4 min processing time with marginal harmonic value.
            # Their WAV files are preserved for playback; only inference is skipped.
            _DRUM_KEYS = {"drums", "drum", "kick", "snare", "percussion"}
            # Stems where Basic Pitch adds little harmonic value vs. processing cost
            _SKIP_INFERENCE_KEYS = {
                "harmony_vocal", "backing_vocal", "synth", "other",
                "keys", "guitar_layer", "strumming_guitar",
            }
            all_pitched = {k: v for k, v in active_stems.items()
                          if k.lower() not in _DRUM_KEYS and not k.lower().startswith("drum")}
            pitched_stems = {k: v for k, v in all_pitched.items()
                             if k.lower() not in _SKIP_INFERENCE_KEYS}
            skipped = set(all_pitched.keys()) - set(pitched_stems.keys())
            print(f"[job {job_id}] [{_elapsed()}] NOTE EXTRACTION starting ({len(pitched_stems)} stems, "
                  f"skipping drums + {len(skipped)} low-priority: {sorted(skipped)})...")
            note_events_all = {}
            try:
                from compat import patch_lzma
                patch_lzma()

                def _extract_one_stem(stem_key, stem_info):
                    import tempfile as _tempfile, subprocess as _sp, os as _os
                    label = stem_info.get("label", stem_key)
                    full_path = stem_info["path"]
                    print(f"[job {job_id}] [{_elapsed()}] Basic Pitch → {stem_key}...")

                    # Truncate to first 90s for inference only — full WAV stays for playback.
                    # Basic Pitch scales linearly with duration; 90s captures the full
                    # harmonic content of any song structure while cutting inference ~70%.
                    INFER_SECS = 90
                    tmp_path = None
                    infer_path = full_path
                    try:
                        tmp_fd, tmp_path = _tempfile.mkstemp(suffix=f"_{stem_key}_90s.wav")
                        _os.close(tmp_fd)
                        _sp.run(
                            ["ffmpeg", "-y", "-i", full_path, "-t", str(INFER_SECS),
                             "-c", "copy", tmp_path],
                            capture_output=True, timeout=30,
                        )
                        if _os.path.getsize(tmp_path) > 1000:
                            infer_path = tmp_path
                            print(f"[job {job_id}] truncated {stem_key} to {INFER_SECS}s for inference")
                    except Exception as _trunc_e:
                        print(f"[job {job_id}] truncation warning ({stem_key}): {_trunc_e} — using full file")

                    try:
                        ne = extract_note_events(infer_path, stem_key, label=label, bpm=detected_bpm)
                    finally:
                        # Always clean up temp file — full WAV untouched for playback
                        if tmp_path and _os.path.exists(tmp_path):
                            try:
                                _os.remove(tmp_path)
                            except Exception:
                                pass

                    gc.collect()
                    print(f"[job {job_id}] [{_elapsed()}] Basic Pitch → {stem_key} done ({len(ne) if ne is not None else 0} notes)")
                    return stem_key, ne

                TAB_WORKERS = 1  # Sequential to avoid OOM — TF retains memory across parallel workers
                with _TPE(max_workers=TAB_WORKERS) as _tab_pool:
                    tab_futures = {
                        _tab_pool.submit(_extract_one_stem, k, v): k
                        for k, v in pitched_stems.items()
                    }
                    for fut in _as_completed(tab_futures):
                        stem_key, note_df = fut.result()
                        if note_df is not None and len(note_df) > 0:
                            note_events_all[stem_key] = note_df

                gc.collect()
                _log_memory(f"[job {job_id}] post-basic-pitch")
                # Release TF model weights from memory so next job starts clean.
                # gc.collect() alone doesn't free TF memory — it stays resident in
                # the process and compounds across sequential jobs, causing OOM.
                try:
                    import tensorflow as _tf
                    _tf.keras.backend.clear_session()
                    del _tf
                    gc.collect()
                    _log_memory(f"[job {job_id}] post-tf-clear")
                except Exception as _tf_e:
                    print(f"[job {job_id}] TF clear_session warning: {_tf_e}")
                print(f"[job {job_id}] [{_elapsed()}] NOTE EXTRACTION finished → {len(note_events_all)} stems with notes")
            except Exception as e:
                _fail("note_extraction", e)

            # ── WAV → MP3 conversion (post-analysis) ──
            # Analysis is done; WAV files are no longer needed for processing.
            # Convert to 192kbps MP3 to free disk space and reduce memory when serving.
            # WAV stems are ~107MB each; MP3 reduces this to ~5MB — a 20x improvement.
            # The audio endpoint already prefers MP3 when present, so no other changes needed.
            try:
                import subprocess as _sp_mp3
                stems_dir_mp3 = OUTPUT_DIR / job_id / "stems"
                converted = 0
                for wav_file in list(stems_dir_mp3.glob("*.wav")):
                    mp3_file = wav_file.with_suffix(".mp3")
                    result = _sp_mp3.run(
                        ["ffmpeg", "-y", "-i", str(wav_file), "-b:a", "192k", "-ac", "2", str(mp3_file)],
                        capture_output=True, timeout=120,
                    )
                    if result.returncode == 0 and mp3_file.exists() and mp3_file.stat().st_size > 1000:
                        wav_file.unlink()
                        converted += 1
                    else:
                        print(f"[job {job_id}] MP3 convert failed for {wav_file.name}, keeping WAV")
                _log_memory(f"[job {job_id}] post-mp3-convert")
                print(f"[job {job_id}] [{_elapsed()}] WAV→MP3: converted {converted} stems")
            except Exception as _mp3_e:
                print(f"[job {job_id}] WAV→MP3 warning: {_mp3_e}")

            # ── Stage 5: Song intelligence (key, BPM, progression) ──
            on_progress("Analyzing key and structure...")
            print(f"[job {job_id}] [{_elapsed()}] INTELLIGENCE starting...")
            try:
                if note_events_all:
                    essentia_override = None
                    if essentia_key_num >= 0:
                        essentia_override = {
                            "key_num": essentia_key_num,
                            "mode_num": essentia_mode_num,
                            "key_conf": essentia_key_conf,
                            "bpm": detected_bpm,
                            "bpm_conf": intelligence.get("bpm_confidence", 0),
                        }
                    intelligence = analyze_song_from_notes(
                        note_events_all, song_name=track_name, artist=artist_name,
                        lyrics_text=lyrics, audio_key_override=essentia_override,
                    )
                    print(f"[job {job_id}] [{_elapsed()}] INTELLIGENCE finished → key={intelligence['key']}, bpm={intelligence['bpm']}, sections={len(intelligence.get('harmonic_sections',[]))}")
                    jobs[job_id]["intelligence"] = intelligence
                else:
                    print(f"[job {job_id}] [{_elapsed()}] INTELLIGENCE skipped (no note events)")
            except Exception as e:
                _fail("intelligence", e)

            # ── Stages 6.5 + 7: LLM Insight and Recommendations in parallel ──
            # Both only need intelligence output — they don't depend on each other.
            on_progress("Generating insight...")
            print(f"[job {job_id}] [{_elapsed()}] INSIGHT + RECS starting in parallel...")

            def _run_insight():
                if not (artist_name and track_name): return None
                try:
                    from insight import generate_insight
                    result = generate_insight(
                        song_name=track_name,
                        artist=artist_name,
                        intelligence=intelligence,
                        lyrics=lyrics,
                        tags=tags,
                    )
                    _enrich_smart_recs_art(result)
                    return result
                except Exception as e:
                    _fail("insight", e)
                    return None

            def _run_recs():
                if not spotify_track_id: return {}
                _recs = {"more_like_this": [], "same_style": [], "around_this_time": []}
                try:
                    _recs = get_recommendations_for_track(
                        track_id=spotify_track_id,
                        artist_id=spotify_artist_id or track_meta.get("artist_id"),
                        track_name=track_name, artist_name=artist_name,
                        year=track_meta.get("year", ""),
                        detected_key=intelligence.get("key", ""),
                        detected_bpm=intelligence.get("bpm", 120),
                    )
                except Exception as e:
                    _fail("recommendations_spotify", e)
                if artist_name and track_name:
                    try:
                        _recs = enrich_recommendations_with_lastfm(_recs, artist_name, track_name)
                    except Exception as e:
                        _fail("recommendations_lastfm", e)
                return _recs

            with _TPE(max_workers=2) as _final_pool:
                fut_insight = _final_pool.submit(_run_insight)
                fut_recs    = _final_pool.submit(_run_recs)
                insight_text = fut_insight.result()
                recs         = fut_recs.result() or recs

            print(f"[job {job_id}] [{_elapsed()}] INSIGHT + RECS finished")

            # ── Finalize ──
            _finalize()

        except Exception as e:
            # Unexpected crash — still save partial results
            print(f"[job {job_id}] [{_elapsed()}] UNEXPECTED FATAL: {e}")
            traceback.print_exc()
            failed_steps.append({"step": "unknown", "message": str(e)})
            _finalize()
        finally:
            # Release processing slot, kick off any queued jobs, then GC
            with _processing_lock:
                _active_processing -= 1
            _dequeue_next()
            gc.collect()
            _log_memory(f"[job {job_id}] PROCESS END")
            print(f"[mem] active processing jobs: {_active_processing}")

    # Either start immediately or reject gracefully if at capacity
    with _processing_lock:
        if _active_processing >= MAX_CONCURRENT_JOBS:
            print(f"[job {job_id}] REJECTED: {_active_processing} jobs active (max {MAX_CONCURRENT_JOBS})")
            # Clean up the job entry so it doesn't linger
            jobs.pop(job_id, None)
            return jsonify({
                "status": "busy",
                "error": "Riffd is at capacity right now. Try again in a moment.",
            }), 503

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "processing", "job_id": job_id})


JOB_TIMEOUT = 720  # 12 minutes — must fire before frontend poll timeout (11 min) to ensure clean error

@app.route("/api/status/<job_id>")
def job_status(job_id):
    import time as _t
    if job_id not in jobs:
        print(f"[job {job_id}] STATUS POLL → unknown job")
        return jsonify({"error": "Unknown job ID"}), 404
    job = jobs[job_id]

    # Watchdog: force-expire stuck jobs
    if job.get("status") == "processing" and job.get("_started_at"):
        elapsed = _t.time() - job["_started_at"]
        if elapsed > JOB_TIMEOUT:
            print(f"[job {job_id}] WATCHDOG: job stuck for {elapsed:.0f}s, forcing error")
            job.update({"status": "error", "error": f"Processing timed out after {int(elapsed)}s", "error_step": "timeout"})

    # Only log status polls for terminal states (avoid noise from repeated polling)
    status = job.get("status", "")
    if status not in ("processing", "downloading", "queued"):
        print(f"[job {job_id}] STATUS POLL → {status} | {job.get('progress', '')}")
    resp = jsonify(job)

    # After delivering a completed result, trim the heavy payload from memory
    # The full result is persisted in filesystem cache — the job dict only needs status
    if job.get("status") in ("complete", "partial"):
        if job.get("_result_delivered"):
            # Second poll after completion — safe to trim
            _trim_job_result(job_id)
        else:
            # First delivery — mark it, trim on next poll
            job["_result_delivered"] = True

    return resp


@app.route("/api/audio/<job_id>/<stem_name>")
def serve_stem_audio(job_id, stem_name):
    stems_dir = (OUTPUT_DIR / job_id / "stems").resolve()
    wav_path = stems_dir / f"{stem_name}.wav"
    mp3_path = stems_dir / f"{stem_name}.mp3"

    # Determine which file to serve
    if wav_path.exists() and wav_path.stat().st_size > 0:
        serve_path = wav_path
        mime = "audio/wav"
    elif mp3_path.exists() and mp3_path.stat().st_size > 0:
        serve_path = mp3_path
        mime = "audio/mpeg"
    else:
        wav_size = wav_path.stat().st_size if wav_path.exists() else -1
        print(f"[audio] MISSING {job_id}/{stem_name} — wav={wav_size}b cwd={Path('.').resolve()} path={wav_path}")
        return jsonify({"error": "stem not ready"}), 404

    import time as _t; _stem_last_accessed[job_id] = _t.time()

    # Stream from disk — avoids loading 100MB+ WAV files into process memory per request.
    # send_file with conditional=True also enables proper Range request support for seeking.
    try:
        file_size = serve_path.stat().st_size
        print(f"[audio] SERVING {job_id}/{stem_name} — {file_size:,}b from {serve_path}")
        return send_file(
            str(serve_path),
            mimetype=mime,
            conditional=True,  # Enables range requests (audio seek without re-downloading)
        )
    except Exception as e:
        print(f"[audio] READ ERROR {job_id}/{stem_name}: {e}")
        return jsonify({"error": "read failed"}), 500



# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route("/api/admin/refresh-cookies", methods=["POST"])
def admin_refresh_cookies():
    """
    Trigger a Playwright-based YouTube cookie refresh.
    Protected by ADMIN_SECRET env var (pass as ?secret= or Authorization header).
    """
    admin_secret = os.environ.get("ADMIN_SECRET", "").strip()
    if admin_secret:
        provided = (
            request.args.get("secret", "")
            or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        )
        if provided != admin_secret:
            return jsonify({"error": "unauthorized"}), 401

    try:
        from cookie_refresher import refresh_cookies
        success = refresh_cookies(timeout=45)
        if success:
            size = Path("cookies.txt").stat().st_size if Path("cookies.txt").exists() else 0
            return jsonify({"status": "ok", "message": "Cookies refreshed", "size_bytes": size})
        else:
            return jsonify({"status": "failed", "message": "Cookie refresh failed — check logs"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Demo routes ────────────────────────────────────────────────────────────────

@app.route("/demo")
def demo():
    demo_tracks = [
        {
            "id": "take_it_easy",
            "name": "Take It Easy",
            "artist": "Eagles",
            "year": "1972",
            "cover": "/static/demo/take_it_easy/cover.jpg",
            "key": "G Major",
            "bpm": 0,
        },
        {
            "id": "gravity",
            "name": "Gravity",
            "artist": "John Mayer",
            "year": "2006",
            "cover": "/static/demo/gravity/cover.jpg",
            "key": "C Major",
            "bpm": 0,
        },
        {
            "id": "bohemian_rhapsody",
            "name": "Bohemian Rhapsody",
            "artist": "Queen",
            "year": "1975",
            "cover": "/static/demo/bohemian_rhapsody/cover.jpg",
            "key": "Bb Major",
            "bpm": 0,
        },
    ]
    return render_template("demo.html", active_page="demo", demo_tracks=demo_tracks)


@app.route("/api/demo/<demo_id>")
def get_demo_analysis(demo_id):
    """Serve pre-baked analysis for a demo track. Identical shape to /api/status/<job_id>."""
    safe_id = demo_id.replace("/", "").replace("..", "")
    analysis_path = os.path.join("static", "demo", safe_id, "analysis.json")
    if not os.path.exists(analysis_path):
        return jsonify({"error": "Demo track not found"}), 404
    with open(analysis_path) as f:
        import json as _json_local
        data = _json_local.load(f)
    return jsonify(data)


@app.route("/api/demo/<demo_id>/audio/<stem_name>")
def serve_demo_stem(demo_id, stem_name):
    """Serve pre-baked stem audio for a demo track. Supports range requests for seeking."""
    safe_id = demo_id.replace("/", "").replace("..", "")
    safe_stem = stem_name.replace("/", "").replace("..", "").replace(".mp3", "")
    stems_dir = os.path.join("static", "demo", safe_id, "stems")
    mp3_path = os.path.join(stems_dir, f"{safe_stem}.mp3")
    if not os.path.exists(mp3_path):
        return jsonify({"error": "Stem not found"}), 404
    return send_file(mp3_path, mimetype="audio/mpeg", conditional=True)


@app.route("/api/download_stem/<job_id>/<stem_name>")
def download_stem_audio(job_id, stem_name):
    """Download a separated stem as an audio file (with Content-Disposition: attachment)."""
    stems_dir = OUTPUT_DIR / job_id / "stems"
    wav_path = stems_dir / f"{stem_name}.wav"
    mp3_path = stems_dir / f"{stem_name}.mp3"
    if wav_path.exists():
        return send_from_directory(str(stems_dir), f"{stem_name}.wav", as_attachment=True)
    elif mp3_path.exists():
        return send_from_directory(str(stems_dir), f"{stem_name}.mp3", as_attachment=True)
    return jsonify({"error": "Stem file not found"}), 404



@app.route("/api/refresh-recs/<job_id>", methods=["POST"])
def refresh_recs(job_id):
    """Re-generate LLM recommendations without re-running analysis."""
    from insight import generate_insight

    # Try in-memory job first, then filesystem cache
    job = jobs.get(job_id)
    intelligence = None
    lyrics = None
    tags = []
    artist = ""
    track_name = ""

    if job:
        intelligence = job.get("intelligence")
        lyrics = job.get("lyrics")
        tags = job.get("tags", [])
        # track meta may be on the job or we fall back to selectedTrack from frontend
    else:
        # Try filesystem cache
        cached = get_cached_result(job_id)
        if cached:
            intelligence = cached.get("intelligence")
            lyrics = cached.get("lyrics")
            tags = cached.get("tags", [])

    if not intelligence:
        return jsonify({"error": "No analysis data found for this job"}), 404

    req_data = request.json or {}
    exclude = req_data.get("exclude", [])
    artist = req_data.get("artist", "")
    track_name = req_data.get("track_name", "")

    try:
        result = generate_insight(
            song_name=track_name,
            artist=artist,
            intelligence=intelligence,
            lyrics=lyrics,
            tags=tags,
            exclude_songs=exclude,
        )
        if not result:
            return jsonify({"error": "Failed to generate recommendations"}), 500
        return jsonify(result)
    except Exception as e:
        print(f"[refresh-recs] error: {e}")
        return jsonify({"error": "Failed to generate recommendations"}), 500


# ─── History endpoints ────────────────────────────────────────────────────────

@app.route("/api/history")
def history_list():
    """Get recent songs."""
    return jsonify(get_recent(8))


@app.route("/api/cache/<track_id>")
def cache_check(track_id):
    """Check if a cached result exists for a track. Returns the result or 404."""
    result = get_cached_result(track_id)
    if result:
        touch_history(track_id)  # update last_viewed on reopen
        return jsonify(result)
    return jsonify({"error": "No valid cache"}), 404


# ─── Discovery endpoint ───────────────────────────────────────────────────────

@app.route("/api/discovery")
def discovery():
    """
    Return curated songs for the landing page.
    Uses static pre-built data from spotify_search._DISCOVERY_TRACKS (single source of truth).
    NO Spotify API calls. Zero rate-limit risk.
    Returns stable order to prevent browser image cache mismatches.
    """
    return jsonify(_DISCOVERY_TRACKS[:8])


# ─── Theory data endpoints ────────────────────────────────────────────────────

import json as _json

_THEORY_DATA = {}
_DATA_DIR = Path("data")

def _load_theory(name):
    if name not in _THEORY_DATA:
        p = _DATA_DIR / f"{name}.json"
        if p.exists():
            _THEORY_DATA[name] = _json.loads(p.read_text())
        else:
            _THEORY_DATA[name] = []
    return _THEORY_DATA[name]


@app.route("/api/theory/ask", methods=["POST"])
def theory_ask():
    """LLM-powered natural language search over theory data."""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    section = data.get("section")

    if not question:
        return jsonify({"error": "No question provided"}), 400
    if len(question) > 500:
        return jsonify({"error": "Question too long"}), 400

    # Load all theory data
    theory_data = {}
    for sec in ("chords", "scales", "progressions", "keys"):
        theory_data[sec] = _load_theory(sec)

    from theory_search import ask_theory
    result = ask_theory(question, section=section, theory_data=theory_data)

    if result is None:
        return jsonify({"error": "Could not process question"}), 500

    return jsonify(result)


@app.route("/api/theory/<section>")
def theory_data(section):
    if section not in ("chords", "scales", "progressions", "keys"):
        return jsonify({"error": "Unknown section"}), 404
    return jsonify(_load_theory(section))


@app.route("/s/<track_id>")
def shared_analysis(track_id):
    """Shareable analysis page. Shows cached analysis results for a track."""
    track = get_track(track_id)
    if not track or track["analysis_status"] != "available":
        return render_template("shared_404.html"), 404

    analysis = get_analysis_for_track(track_id)
    if not analysis:
        return render_template("shared_404.html"), 404

    touch_track(track_id)
    log_event("shared_view", {"track_id": track_id})

    return render_template("shared.html",
        track=track,
        analysis=analysis,
        active_page="decompose",
    )


if __name__ == "__main__":
    print("\n  Riffd running at http://localhost:5001\n")
    app.run(debug=True, port=5001, threaded=True)
