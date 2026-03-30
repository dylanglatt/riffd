"""
app.py — Riffd Flask web server.

Request lifecycle:
  1. User searches via /api/spotify/search → Spotify API (with local fallback)
  2. User selects a track → frontend checks /api/track/<id> for cached analysis
  3. If no cache: frontend calls /api/download with mode=preview|full
     - preview: resolve_preview() → iTunes/Spotify preview (~2s, 30s audio)
     - full: resolve_audio() → YouTube first, then preview fallback (~30-120s)
  4. Frontend calls /api/process/<job_id> with analysis_mode=instant|deep
     - instant: synchronous, returns in ~3-5s (key, BPM, lyrics, no stems)
     - deep: async background thread, returns via polling (~2-5min, full stems + harmonic analysis)
  5. Frontend polls /api/status/<job_id> for deep analysis progress
  6. Results served via /api/audio/<job_id>/<stem> for playback

Job status lifecycle:
  downloading → ready → processing → complete|partial|error
  downloading → preview_unavailable  (preview mode, no source)
  downloading → upload_required      (full mode, all sources failed)
  downloading → error                (unexpected failure)

Two analysis modes:
  - instant: _process_instant() — synchronous, no Demucs, no threading
  - deep:    process_audio() run() thread — full 7-stage pipeline

Memory management:
  - Heavy imports (numpy, pandas, basic_pitch) deferred to first job
  - Jobs pruned from memory after 10 minutes
  - Job payloads trimmed after frontend polls the result
  - MAX_CONCURRENT_JOBS=1 prevents stacking deep analysis
"""

import os
import uuid
import threading
import traceback
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for, make_response
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
from downloader import download_audio_from_youtube, resolve_audio, resolve_preview, AudioUnavailableError
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
    raise RuntimeError("SITE_PASSWORD environment variable is required. Set it in .env or your hosting platform.")
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
_processing_lock = threading.Lock()
_active_processing = 0
MAX_CONCURRENT_JOBS = 1  # Only one deep analysis at a time (Demucs uses ~500MB RAM)

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

    max_age = 7 * 86400  # 7 days
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

# ─── Authentication ───────────────────────────────────────────────────────────

# Path-based allowlist — explicit and auditable
AUTH_PUBLIC_PATHS = ("/login", "/static/", "/", "/favicon.ico", "/s/")

@app.before_request
def require_login():
    path = request.path
    is_public = (path == "/login" or path == "/" or path == "/favicon.ico" or
                 path.startswith("/static/") or path.startswith("/s/"))
    is_authed = session.get("authenticated") is True

    if is_public:
        return  # Always allow login page and static assets
    if is_authed:
        return  # Session is authenticated — allow

    # Block everything else
    print(f"[auth] blocked unauthenticated request: {path}")
    return redirect("/login")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")


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


@app.route("/library")
def library():
    return render_template("library.html", active_page="library")


@app.route("/practice")
def practice():
    return render_template("practice.html", active_page="practice")


@app.route("/about")
def about():
    return render_template("about.html", active_page="about")


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
    Audio acquisition endpoint. Accepts mode="preview" or mode="full".
    - preview: resolve_preview() — Spotify/iTunes only, no YouTube. ~2s.
    - full: resolve_audio() — YouTube first, then preview fallback. ~30-120s.
    Downloads run in a background thread. Frontend polls /api/status/<job_id>.
    Returns immediately with {job_id, mode}.
    """
    data = request.json
    query = data.get("query")       # YouTube search query (null in preview mode)
    url = data.get("url")           # Direct URL (rare, for YouTube links)
    track_id = data.get("track_id") # Spotify track ID for cache lookup
    mode = data.get("mode", "preview")  # "preview" (default) or "full"

    print(f"[download] triggered mode={mode} query={bool(query)} url={bool(url)} preview_url={bool(data.get('preview_url'))} artist={data.get('artist','')[:20]}")
    log_event("download_start", {"mode": mode, "artist": data.get("artist", "")[:30], "track": data.get("name", "")[:40]})

    if not query and not url and mode != "preview":
        return jsonify({"error": "Provide 'query' or 'url'"}), 400

    # Check if background prefetch has (or will soon have) the full track
    if mode == "full" and track_id:
        with _bg_lock:
            bg = _bg_downloads.get(track_id)

        if bg:
            # If prefetch is still downloading, wait for it (up to 60s) instead of starting a duplicate
            if bg["status"] == "downloading":
                import time as _wait_time
                print(f"[download] prefetch in progress for track_id={track_id} — waiting...")
                deadline = _wait_time.time() + 60
                while _wait_time.time() < deadline:
                    _wait_time.sleep(2)
                    with _bg_lock:
                        bg = _bg_downloads.get(track_id)
                    if not bg or bg["status"] != "downloading":
                        break
                    print(f"[download] still waiting for prefetch... ({bg['status']})")

            # Re-check after potential wait
            if bg and bg["status"] == "ready" and bg.get("audio_path"):
                job_id = str(uuid.uuid4())[:8]
                audio_path = bg["audio_path"]
                jobs[job_id] = {
                    "status": "ready",
                    "audio_path": audio_path,
                    "audio_source": "youtube",
                    "audio_mode": mode,
                    "progress": "Download complete",
                }
                print(f"[job {job_id}] DOWNLOAD REUSED from prefetch → {audio_path}")
                log_event("prefetch_hit", {"track_id": track_id})
                return jsonify({"job_id": job_id, "mode": mode})

    # Check if we already have audio from a previous job for this track
    if track_id:
        from history import _load_history
        hist = _load_history()
        entry = hist.get(track_id)
        if entry and entry.get("job_id"):
            old_job = entry["job_id"]
            old_upload = UPLOAD_DIR / old_job
            if old_upload.exists():
                audio_files = [f for f in old_upload.iterdir()
                               if f.suffix.lower() in {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac"}]
                if audio_files:
                    # In full mode, skip cached previews — we want the full track
                    if mode == "full" and audio_files[0].name == "preview.mp3":
                        print(f"[download] skipping cached preview for mode=full (track_id={track_id})")
                    else:
                        job_id = str(uuid.uuid4())[:8]
                        audio_path = str(audio_files[0])
                        cached_source = "cache"
                        if audio_files[0].name == "preview.mp3":
                            cached_source = "preview"
                        jobs[job_id] = {"status": "ready", "audio_path": audio_path, "audio_source": cached_source, "audio_mode": mode, "progress": "Download complete"}
                        print(f"[job {job_id}] DOWNLOAD REUSED from job {old_job} → {audio_path} (mode={mode})")
                        return jsonify({"job_id": job_id})

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "downloading", "audio_mode": mode, "progress": "Getting audio..."}
    print(f"[job {job_id}] DOWNLOAD START mode={mode} query='{(query or url or '')[:60]}'")

    def run():
        def _on_progress(msg):
            jobs[job_id]["progress"] = msg
            print(f"[job {job_id}] download progress: {msg}")

        try:
            track_data = {
                "query": query or url,
                "preview_url": data.get("preview_url"),
                "artist": data.get("artist", ""),
                "name": data.get("name", ""),
            }

            if mode == "preview":
                # Preview-first: only try preview sources, no YouTube
                print(f"[job {job_id}] mode=preview — skipping YouTube")
                _on_progress("Getting preview audio...")
                audio_path = resolve_preview(track_data, job_id, on_progress=_on_progress)
                audio_source = "preview"

                # Auto-trigger instant analysis immediately after preview download
                # This avoids a second round-trip from the frontend.
                # Must run inside app context since _process_instant uses jsonify().
                print(f"[process] [job {job_id}] auto-trigger instant after preview")
                jobs[job_id].update({
                    "status": "ready",
                    "audio_path": str(audio_path),
                    "audio_source": audio_source,
                    "audio_mode": mode,
                })
                req_data = {
                    "track_id": data.get("track_id"),
                    "track_meta": {
                        "artist": data.get("artist", ""),
                        "name": data.get("name", ""),
                        "album": data.get("album", ""),
                        "image_url": data.get("image_url"),
                        "duration_ms": data.get("duration_ms", 0),
                        "year": data.get("year", ""),
                        "artist_id": data.get("artist_id"),
                        "yt_query": data.get("query", ""),
                    },
                    "analysis_mode": "instant",
                }
                with app.app_context():
                    _process_instant(job_id, str(audio_path), req_data)
                print(f"[process] [job {job_id}] instant analysis auto-completed")
                return  # Skip the normal "ready" status — job is already "complete"

            else:
                # Full-track: try YouTube first, then previews as fallback
                print(f"[job {job_id}] mode=full — trying YouTube first")
                is_direct_yt = (url and url.startswith("http") and
                                ("youtube.com" in url or "youtu.be" in url))

                if is_direct_yt:
                    _on_progress("Downloading full track...")
                    audio_path = download_audio_from_youtube(url, job_id)
                    audio_source = "youtube"
                    print(f"[job {job_id}] AUDIO SOURCE SELECTED: youtube (direct URL)")
                else:
                    _on_progress("Downloading full track...")
                    audio_path = resolve_audio(track_data, job_id, on_progress=_on_progress, allow_preview_fallback=False)
                    audio_source = "youtube"

            print(f"[job {job_id}] download finished → {audio_path} (source={audio_source}, mode={mode})")
            jobs[job_id].update({
                "status": "ready",
                "audio_path": str(audio_path),
                "audio_source": audio_source,
                "audio_mode": mode,
                "progress": "Download complete",
            })
            print(f"[job {job_id}] STATUS → ready")

        except AudioUnavailableError as e:
            print(f"[job {job_id}] SOURCES FAILED (mode={mode}): {e}")
            log_event("youtube_failed", {"job_id": job_id, "error": str(e)[:100]})
            if mode == "preview":
                # Preview unavailable — tell frontend, don't try YouTube automatically
                jobs[job_id].update({
                    "status": "preview_unavailable",
                    "audio_source": None,
                    "audio_mode": mode,
                    "error": "No preview available for this track.",
                })
                print(f"[job {job_id}] STATUS → preview_unavailable")
            else:
                jobs[job_id].update({
                    "status": "upload_required",
                    "audio_source": None,
                    "audio_mode": mode,
                    "error": str(e),
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
                "preview_url": None,
                "artist": artist,
                "name": name,
            }
            audio_path = resolve_audio(track_data, prefetch_id, allow_preview_fallback=False)
            # Verify we actually got a full track, not a preview
            if str(audio_path).endswith("preview.mp3"):
                raise AudioUnavailableError("Only preview audio available — full track download failed")
            is_full = True
            entry["status"] = "ready"
            entry["audio_path"] = str(audio_path)
            entry["is_full_track"] = is_full
            source_type = "youtube (full)" if is_full else "preview (fallback)"
            print(f"[prefetch {prefetch_id}] COMPLETE → {source_type} → {audio_path}")
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
    return jsonify({"job_id": job_id, "filename": filename})


def _process_instant(job_id, audio_path, req_data):
    """
    Instant analysis: lightweight, synchronous, no Demucs.
    Runs in the request thread — no background processing, no polling needed.

    Steps (~3-5s total):
      1. Copy preview audio to stems dir for playback       ~0.1s
      2. Detect audio duration from file header              ~0.1s
      3. Run Basic Pitch on raw audio for key + BPM          ~1-3s
      4. Fetch lyrics from Genius API                        ~1s
      5. Fetch tags from Last.fm API                         ~0.5s
      6. Save to cache + history + DB                        ~0.1s

    Returns JSON result directly (no job polling).
    Frontend calls renderInstantResults() with the response.
    """
    import time as _t
    _t0 = _t.time()
    track_meta = req_data.get("track_meta", {})
    artist = track_meta.get("artist", "")
    track_name = track_meta.get("name", "")

    print(f"[job {job_id}] process start analysis_mode=instant audio={audio_path}")

    intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 0, "bpm_confidence": 0, "progression": None}
    lyrics = None
    tags = []
    audio_duration = 0

    # Copy preview audio to stems dir so /api/audio/<job_id>/preview serves it
    import shutil as _shutil
    stems_dir = OUTPUT_DIR / job_id / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    audio_ext = Path(audio_path).suffix  # .mp3 or .wav
    preview_dest = stems_dir / f"preview{audio_ext}"
    if not preview_dest.exists():
        _shutil.copy2(audio_path, preview_dest)
        print(f"[job {job_id}] instant: preview copied to {preview_dest}")

    # Get audio duration from file
    try:
        import soundfile as sf
        info = sf.info(str(audio_path))
        audio_duration = info.duration
        print(f"[job {job_id}] instant: duration={audio_duration:.1f}s (via soundfile)")
    except Exception:
        try:
            fsize = Path(audio_path).stat().st_size
            audio_duration = fsize / 16000  # rough MP3 estimate
            print(f"[job {job_id}] instant: duration={audio_duration:.1f}s (estimated)")
        except Exception as e:
            print(f"[job {job_id}] instant: duration detection failed: {e}")

    # Key + BPM detection using Essentia (fast, lightweight, no TensorFlow)
    # Runs directly on the audio file — no intermediate note detection needed
    try:
        jobs[job_id]["progress"] = "Analyzing key and tempo..."
        from music_intelligence import detect_key_from_audio, detect_bpm_from_audio, format_key
        print(f"[job {job_id}] instant: running Essentia on raw audio...")

        key_num, mode_num, key_conf = detect_key_from_audio(audio_path)
        if key_num >= 0:
            intelligence["key"] = format_key(key_num, mode_num)
            intelligence["key_num"] = key_num
            intelligence["mode_num"] = mode_num
            intelligence["key_confidence"] = round(key_conf, 3)

        bpm, bpm_conf = detect_bpm_from_audio(audio_path)
        if bpm > 0 and bpm_conf >= 0.1:
            intelligence["bpm"] = round(bpm, 1)
            intelligence["bpm_confidence"] = round(bpm_conf, 3)

        print(f"[job {job_id}] instant: key={intelligence['key']} bpm={intelligence['bpm']}")
    except Exception as e:
        print(f"[job {job_id}] instant: key/bpm detection failed: {e}")

    # Lyrics + Tags — independent HTTP calls, run in parallel
    if artist and track_name:
        from concurrent.futures import ThreadPoolExecutor
        jobs[job_id]["progress"] = "Fetching lyrics..."

        def _fetch_lyrics():
            try:
                return get_lyrics(artist, track_name)
            except Exception as e:
                print(f"[job {job_id}] instant: lyrics failed: {e}")
                return None

        def _fetch_tags():
            try:
                return get_track_tags(artist, track_name)
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_lyrics = pool.submit(_fetch_lyrics)
            f_tags = pool.submit(_fetch_tags)

        lyrics = f_lyrics.result()
        tags = f_tags.result()
        print(f"[job {job_id}] instant: lyrics={'found' if lyrics else 'none'} tags={tags}")

    # LLM Insight (non-blocking — failure is fine)
    insight_text = None
    if artist and track_name:
        try:
            from insight import generate_insight
            jobs[job_id]["progress"] = "Generating insight..."
            insight_text = generate_insight(
                song_name=track_name,
                artist=artist,
                intelligence=intelligence,
                lyrics=lyrics,
                tags=tags,
            )
            _enrich_smart_recs_art(insight_text)
        except Exception as e:
            print(f"[job {job_id}] insight failed: {e}")

    elapsed = _t.time() - _t0
    print(f"[job {job_id}] instant analysis complete in {elapsed:.1f}s")

    result = {
        "status": "complete",
        "analysis_mode": "instant",
        "audio_source": jobs[job_id].get("audio_source"),
        "audio_mode": jobs[job_id].get("audio_mode", "preview"),
        "intelligence": intelligence,
        "lyrics": lyrics,
        "tags": tags,
        "insight": insight_text,
        "audio_duration": round(audio_duration, 1),
        "stems": {},
        "recommendations": {"more_like_this": [], "same_style": [], "around_this_time": []},
        "progress": "Done!",
    }
    jobs[job_id].update(result)

    # Save to cache if we have a track ID
    spotify_track_id = req_data.get("track_id")
    if spotify_track_id:
        try:
            save_cached_result(job_id, {
                "intelligence": intelligence,
                "lyrics": lyrics,
                "tags": tags,
                "insight": insight_text,
                "audio_source": jobs[job_id].get("audio_source"),
                "audio_mode": jobs[job_id].get("audio_mode"),
                "analysis_mode": "instant",
                "stems": {},
                "recommendations": result["recommendations"],
                "job_id": job_id,
                "track_id": spotify_track_id,
            })
            add_to_history(spotify_track_id, track_meta, job_id)

            # Update DB status so re-selecting this track loads from cache
            from db import ANALYSIS_VERSION
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
            set_track_status(spotify_track_id, "available",
                             job_id=job_id, analysis_version=ANALYSIS_VERSION)
            print(f"[job {job_id}] instant: cache+history+db saved")
        except Exception as e:
            print(f"[job {job_id}] instant: cache save failed: {e}")

    return jsonify(result)


@app.route("/api/process/<job_id>", methods=["POST"])
def process_audio(job_id):
    """
    Processing endpoint. Routes to instant or deep analysis based on analysis_mode.

    analysis_mode="instant" → _process_instant() — synchronous, ~3-5s
    analysis_mode="deep"   → background thread with 7-stage pipeline, ~2-5min

    Deep analysis stages (all non-fatal except stage 1):
      1. Stem separation (Demucs)       — FATAL if fails, ~2-5min
      2. BPM detection (Basic Pitch)    — ~10s
      3. Tab generation (Basic Pitch)   — ~30s per stem
      4. Lyrics (Genius API)            — ~1s
      5. Harmonic analysis              — ~5s
      6. Tags (Last.fm API)             — ~1s
      7. Recommendations (Last.fm)      — ~1s
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
    analysis_mode = req_data.get("analysis_mode", "deep")  # "instant" or "deep"

    # ── Instant analysis: lightweight, synchronous, no heavy models ──
    if analysis_mode == "instant":
        return _process_instant(job_id, audio_path, req_data)

    # Guard against stacking heavy jobs (deep only)
    with _processing_lock:
        if _active_processing >= MAX_CONCURRENT_JOBS:
            print(f"[job {job_id}] REJECTED: {_active_processing} jobs already processing")
            return jsonify({"error": "Server is busy processing another song. Please try again in a moment."}), 503

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
                "audio_mode": jobs[job_id].get("audio_mode", "preview"),
                "progress": "Done!" if not partial else "Completed with errors",
            }
            if partial:
                result["errors"] = failed_steps
                result["error"] = f"{len(failed_steps)} step(s) failed: {', '.join(s['step'] for s in failed_steps)}"
            jobs[job_id].update(result)
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
                        "audio_mode": jobs[job_id].get("audio_mode", "preview"),
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

            with _TPE(max_workers=3) as _pool:
                fut_demucs   = _pool.submit(_run_demucs)
                fut_lyrics   = _pool.submit(_fetch_lyrics)
                fut_tags     = _pool.submit(_fetch_tags)

                # Collect metadata results (fast — done well before Demucs)
                lyrics       = fut_lyrics.result()
                tags         = fut_tags.result()
                print(f"[job {job_id}] [{_elapsed()}] metadata fetched: lyrics={'yes' if lyrics else 'no'} tags={len(tags)}")

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

            # ── Stage 2: Key + BPM detection (Essentia) ──
            on_progress("Detecting key and tempo...")
            essentia_key_num, essentia_mode_num, essentia_key_conf = -1, -1, 0.0
            try:
                from music_intelligence import detect_key_from_audio, detect_bpm_from_audio, format_key
                print(f"[job {job_id}] [{_elapsed()}] Essentia key/BPM starting...")

                essentia_key_num, essentia_mode_num, essentia_key_conf = detect_key_from_audio(audio_path)
                if essentia_key_num >= 0:
                    intelligence["key"] = format_key(essentia_key_num, essentia_mode_num)
                    intelligence["key_num"] = essentia_key_num
                    intelligence["mode_num"] = essentia_mode_num
                    intelligence["key_confidence"] = round(essentia_key_conf, 3)

                ess_bpm, ess_bpm_conf = detect_bpm_from_audio(audio_path)
                if ess_bpm > 0 and ess_bpm_conf >= 0.1:
                    detected_bpm = ess_bpm
                    intelligence["bpm"] = round(ess_bpm, 1)
                    intelligence["bpm_confidence"] = round(ess_bpm_conf, 3)

                print(f"[job {job_id}] [{_elapsed()}] Essentia → key={intelligence['key']} bpm={detected_bpm}")
            except Exception as e:
                _fail("essentia_detection", e)

            # ── Stage 3: Note extraction (Basic Pitch) — parallelized across pitched stems ──
            # Drums produce no useful pitch data for harmonic analysis — skip them entirely.
            # No MIDI/CSV/ASCII files written — inference output only.
            _DRUM_KEYS = {"drums", "drum", "kick", "snare", "percussion"}
            pitched_stems = {k: v for k, v in active_stems.items()
                             if k.lower() not in _DRUM_KEYS and not k.lower().startswith("drum")}
            print(f"[job {job_id}] [{_elapsed()}] NOTE EXTRACTION starting ({len(pitched_stems)} pitched stems, skipping drums)...")
            note_events_all = {}
            try:
                from compat import patch_lzma
                patch_lzma()

                def _extract_one_stem(stem_key, stem_info):
                    label = stem_info.get("label", stem_key)
                    print(f"[job {job_id}] [{_elapsed()}] Basic Pitch → {stem_key}...")
                    ne = extract_note_events(stem_info["path"], stem_key, label=label, bpm=detected_bpm)
                    gc.collect()  # release numpy arrays before next stem
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
                print(f"[job {job_id}] [{_elapsed()}] NOTE EXTRACTION finished → {len(note_events_all)} stems with notes")
            except Exception as e:
                _fail("note_extraction", e)

            # ── Stage 5: Song intelligence + harmonic analysis ──
            on_progress("Analyzing harmony and structure...")
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
            # Release processing slot and force garbage collection
            with _processing_lock:
                _active_processing -= 1
            gc.collect()
            _log_memory(f"[job {job_id}] PROCESS END")
            print(f"[mem] active processing jobs: {_active_processing}")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "processing", "job_id": job_id})


JOB_TIMEOUT = 900  # 15 minutes — sequential Basic Pitch on full-length stems takes 5-10 min

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
    if status not in ("processing", "downloading"):
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

    # Read and return directly — bypasses send_from_directory quirks on Render
    try:
        data = serve_path.read_bytes()
        print(f"[audio] SERVING {job_id}/{stem_name} — {len(data):,}b from {serve_path}")
        resp = make_response(data)
        resp.headers["Content-Type"] = mime
        resp.headers["Content-Length"] = len(data)
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    except Exception as e:
        print(f"[audio] READ ERROR {job_id}/{stem_name}: {e}")
        return jsonify({"error": "read failed"}), 500



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
