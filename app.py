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
     - deep: async background thread, returns via polling (~2-5min, full stems + tabs)
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
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
from werkzeug.utils import secure_filename

# Heavy processing modules deferred — loaded on first job, not at boot
# processor.py pulls in numpy, pandas, basic_pitch (~200MB+)
# music_intelligence.py pulls in numpy, pandas
def _lazy_processor():
    from processor import separate_stems, generate_tabs
    return separate_stems, generate_tabs

def _lazy_music_intelligence():
    from music_intelligence import analyze_song_from_notes
    return analyze_song_from_notes

from spotify_search import search_spotify, get_recommendations_for_track, RateLimitError
from external_apis import get_lyrics, get_track_tags, enrich_recommendations_with_lastfm
from downloader import download_audio_from_youtube, resolve_audio, resolve_preview, AudioUnavailableError
from history import add_to_history, get_recent, get_cached_result, save_cached_result, touch_history
from db import init_db, migrate_from_history_json, get_track, upsert_track, set_track_status, touch_track, get_recent_tracks, get_analysis_for_track

load_dotenv()

# ─── Startup checks ──────────────────────────────────────────────────────────
SITE_PASSWORD = os.getenv("SITE_PASSWORD")
FLASK_SECRET = os.getenv("FLASK_SECRET_KEY")

print(f"[auth] SITE_PASSWORD set: {bool(SITE_PASSWORD)}")
print(f"[auth] FLASK_SECRET_KEY set: {bool(FLASK_SECRET)}")
print(f"[env] USE_HOSTED_SEPARATION = {os.getenv('USE_HOSTED_SEPARATION')!r}")
print(f"[env] HAS_REPLICATE_TOKEN = {bool(os.getenv('REPLICATE_API_TOKEN'))}")
print(f"[env] CWD = {os.getcwd()}")

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
AUTH_PUBLIC_PATHS = ("/login", "/static/")

@app.before_request
def require_login():
    path = request.path
    is_public = path == "/login" or path.startswith("/static/")
    is_authed = session.get("authenticated") is True

    # DEBUG: remove after confirming production auth works
    print(f"[auth-v2] path={path} public={is_public} authed={is_authed} endpoint={request.endpoint}")

    if is_public:
        return  # Always allow login page and static assets
    if is_authed:
        return  # Session is authenticated — allow

    # Block everything else
    print(f"[auth-v2] BLOCKED → redirecting to /login")
    return redirect("/login")


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

    if not query and not url and mode != "preview":
        return jsonify({"error": "Provide 'query' or 'url'"}), 400

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
                    job_id = str(uuid.uuid4())[:8]
                    audio_path = str(audio_files[0])
                    cached_source = "cache"
                    # Detect if cached file is preview or full
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
                    audio_path = resolve_audio(track_data, job_id, on_progress=_on_progress)
                    audio_source = "youtube"
                    if str(audio_path).endswith("preview.mp3"):
                        audio_source = "preview"

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
            jobs[job_id].update({"status": "error", "error": str(e)})
            print(f"[job {job_id}] STATUS → error")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id, "mode": mode})


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

    intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 120, "progression": None}
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

    # Lightweight BPM + key estimation using Basic Pitch on the raw audio
    # This runs on the preview file directly (30s) — much faster than stem-separated analysis
    try:
        jobs[job_id]["progress"] = "Analyzing key and tempo..."
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import predict as bp_predict
        print(f"[job {job_id}] instant: running Basic Pitch on raw audio...")
        _, _, note_events = bp_predict(
            str(audio_path),
            ICASSP_2022_MODEL_PATH,
            minimum_note_length=80, minimum_frequency=40, maximum_frequency=2000,
        )
        if note_events and len(note_events) > 10:
            import pandas as pd
            df = pd.DataFrame(note_events, columns=["start_time_s", "end_time_s", "pitch_midi", "confidence", "pitch_bends"][:len(note_events[0])])
            from music_intelligence import detect_key_from_notes, estimate_bpm, format_key
            key_num, mode_num, key_conf = detect_key_from_notes(df)
            if key_num >= 0:
                intelligence["key"] = format_key(key_num, mode_num)
                intelligence["key_num"] = key_num
                intelligence["mode_num"] = mode_num
                intelligence["key_confidence"] = round(key_conf, 3)
            bpm, bpm_conf = estimate_bpm(df)
            if bpm > 0 and bpm_conf >= 0.15:
                intelligence["bpm"] = round(bpm, 1)
                intelligence["bpm_confidence"] = round(bpm_conf, 3)
            del df, note_events
        print(f"[job {job_id}] instant: key={intelligence['key']} bpm={intelligence['bpm']}")
    except Exception as e:
        print(f"[job {job_id}] instant: key/bpm detection failed: {e}")

    # Lyrics (fast HTTP call, no ML)
    if artist and track_name:
        try:
            jobs[job_id]["progress"] = "Fetching lyrics..."
            lyrics = get_lyrics(artist, track_name)
            print(f"[job {job_id}] instant: lyrics={'found' if lyrics else 'none'}")
        except Exception as e:
            print(f"[job {job_id}] instant: lyrics failed: {e}")

    # Tags (fast HTTP call)
    if artist and track_name:
        try:
            tags = get_track_tags(artist, track_name)
        except Exception:
            pass

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
        "audio_duration": round(audio_duration, 1),
        "stems": {},
        "tabs": {},
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
                "audio_source": jobs[job_id].get("audio_source"),
                "audio_mode": jobs[job_id].get("audio_mode"),
                "analysis_mode": "instant",
                "stems": {},
                "tabs": {},
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
        separate_stems, generate_tabs = _lazy_processor()
        analyze_song_from_notes = _lazy_music_intelligence()
        _log_memory(f"[job {job_id}] PROCESS START (after imports)")

        # Partial results accumulate — returned even if a later stage fails
        stems = {}
        tabs = {}
        intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 120, "progression": None}
        lyrics = None
        tags = []
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
                "tabs": tabs,
                "stems": {k: {"label": v.get("label", k), "energy": v.get("energy", 0), "active": v.get("active", True)} for k, v in stems.items()},
                "intelligence": intelligence,
                "lyrics": lyrics,
                "tags": tags,
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

            # Save to cache + history + DB
            if spotify_track_id and (not partial or stems):
                try:
                    save_cached_result(job_id, {
                        "tabs": tabs,
                        "stems": result["stems"],
                        "intelligence": intelligence,
                        "lyrics": lyrics,
                        "tags": tags,
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
            # ── Stage 1: Stem separation (Demucs) ──
            print(f"[job {job_id}] [{_elapsed()}] DEMUCS starting...")
            try:
                stems = separate_stems(audio_path, job_id, progress_callback=on_progress)
                gc.collect()  # Release Demucs intermediates
                print(f"[job {job_id}] [{_elapsed()}] DEMUCS finished → {len(stems)} stems: {list(stems.keys())}")
                _log_memory(f"[job {job_id}] post-demucs")
                jobs[job_id]["stems"] = stems
            except Exception as e:
                # Demucs failure is fatal — nothing to work with
                print(f"[job {job_id}] [{_elapsed()}] DEMUCS FATAL: {e}")
                traceback.print_exc()
                jobs[job_id].update({
                    "status": "error",
                    "error": str(e),
                    "error_step": "stem_separation",
                    "progress": "Stem separation failed",
                })
                return

            active_stems = {k: v for k, v in stems.items() if v.get("active", True)}
            detected_bpm = 120.0
            artist_name = track_meta.get("artist", "")
            track_name = track_meta.get("name", "")

            # ── Stage 2: BPM detection ──
            on_progress("Detecting tempo...")
            bpm_stem_key = None
            for priority_list in [["guitar", "piano", "bass", "other"], list(active_stems.keys())]:
                for k in priority_list:
                    if k in active_stems and k not in ("drums",):
                        bpm_stem_key = k
                        break
                if bpm_stem_key:
                    break

            if bpm_stem_key:
                try:
                    print(f"[job {job_id}] [{_elapsed()}] BPM detection starting (stem={bpm_stem_key})...")
                    from basic_pitch import ICASSP_2022_MODEL_PATH
                    from basic_pitch.inference import predict as bp_predict
                    _, _, bpm_notes = bp_predict(
                        active_stems[bpm_stem_key]["path"],
                        ICASSP_2022_MODEL_PATH,
                        minimum_note_length=80, minimum_frequency=40, maximum_frequency=2000,
                    )
                    if bpm_notes and len(bpm_notes) > 10:
                        import pandas as pd
                        bpm_df = pd.DataFrame(bpm_notes, columns=["start_time_s", "end_time_s", "pitch_midi", "confidence", "pitch_bends"][:len(bpm_notes[0])])
                        from music_intelligence import estimate_bpm
                        est_bpm, bpm_conf = estimate_bpm(bpm_df)
                        if est_bpm > 0 and bpm_conf >= 0.15:
                            detected_bpm = est_bpm
                    print(f"[job {job_id}] [{_elapsed()}] BPM detection finished → {detected_bpm}")
                except Exception as e:
                    _fail("bpm_detection", e)

            # ── Stage 3: Tab generation (Basic Pitch) ──
            print(f"[job {job_id}] [{_elapsed()}] TAB GENERATION starting ({len(active_stems)} stems)...")
            note_events_all = {}
            try:
                for stem_key, stem_info in active_stems.items():
                    label = stem_info.get("label", stem_key)
                    on_progress(f"Tabbing {label}...")
                    print(f"[job {job_id}] [{_elapsed()}] Basic Pitch → {stem_key}...")
                    tab_result = generate_tabs(stem_info["path"], job_id, stem_key, label=label, bpm=detected_bpm)
                    tabs[stem_key] = tab_result
                    csv_path = tab_result.get("notes_csv")
                    if csv_path:
                        try:
                            import pandas as pd
                            df = pd.read_csv(csv_path)
                            if len(df) > 0:
                                note_events_all[stem_key] = df
                        except Exception:
                            pass
                    print(f"[job {job_id}] [{_elapsed()}] Basic Pitch → {stem_key} done")
                print(f"[job {job_id}] [{_elapsed()}] TAB GENERATION finished")
            except Exception as e:
                _fail("tab_generation", e)

            # ── Stage 4: Lyrics (Genius) — moved before intelligence for section analysis ──
            if artist_name and track_name:
                on_progress("Fetching lyrics...")
                print(f"[job {job_id}] [{_elapsed()}] LYRICS starting...")
                try:
                    lyrics = get_lyrics(artist_name, track_name)
                    print(f"[job {job_id}] [{_elapsed()}] LYRICS finished → {'found' if lyrics else 'not found'} ({len(lyrics) if lyrics else 0} chars)")
                except Exception as e:
                    _fail("lyrics", e)

            # ── Stage 5: Song intelligence + harmonic analysis ──
            on_progress("Analyzing harmony and structure...")
            print(f"[job {job_id}] [{_elapsed()}] INTELLIGENCE starting...")
            try:
                if note_events_all:
                    intelligence = analyze_song_from_notes(
                        note_events_all, song_name=track_name, artist=artist_name,
                        lyrics_text=lyrics,
                    )
                    print(f"[job {job_id}] [{_elapsed()}] INTELLIGENCE finished → key={intelligence['key']}, bpm={intelligence['bpm']}, sections={len(intelligence.get('harmonic_sections',[]))}")
                else:
                    print(f"[job {job_id}] [{_elapsed()}] INTELLIGENCE skipped (no note events)")
            except Exception as e:
                _fail("intelligence", e)

            # ── Stage 6: Tags (Last.fm) ──
            if artist_name and track_name:
                try:
                    tags = get_track_tags(artist_name, track_name)
                    print(f"[job {job_id}] [{_elapsed()}] TAGS → {tags}")
                except Exception as e:
                    _fail("tags", e)

            # ── Stage 7: Recommendations ──
            if spotify_track_id:
                on_progress("Finding recommendations...")
                print(f"[job {job_id}] [{_elapsed()}] RECS starting...")
                try:
                    recs = get_recommendations_for_track(
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
                        recs = enrich_recommendations_with_lastfm(recs, artist_name, track_name)
                    except Exception as e:
                        _fail("recommendations_lastfm", e)
                for k, v in recs.items():
                    print(f"[job {job_id}] [{_elapsed()}] recs {k}: {len(v)} tracks")

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


JOB_TIMEOUT = 300  # 5 minutes — if a job is still "processing" after this, force error

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

    print(f"[job {job_id}] STATUS POLL → {job.get('status')} | {job.get('progress', '')} | error={job.get('error', 'none')}")
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
    stems_dir = OUTPUT_DIR / job_id / "stems"
    # Try WAV first, then MP3 (preview files may be MP3)
    wav_path = stems_dir / f"{stem_name}.wav"
    mp3_path = stems_dir / f"{stem_name}.mp3"
    if wav_path.exists():
        return send_from_directory(str(stems_dir), f"{stem_name}.wav")
    elif mp3_path.exists():
        return send_from_directory(str(stems_dir), f"{stem_name}.mp3")
    return send_from_directory(str(stems_dir), f"{stem_name}.wav")  # 404 fallback


@app.route("/api/download_midi/<job_id>/<stem_name>")
def download_midi(job_id, stem_name):
    return send_from_directory(str(OUTPUT_DIR / job_id / "tabs"), f"{stem_name}.mid", as_attachment=True)


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

_discovery_cache = {"tracks": None, "fetched": False}

# Pre-built discovery data — no Spotify API calls needed
# These are static entries with enough data to select and process
_STATIC_DISCOVERY = [
    {"id":"40riOy7x9W7GXjyGp4pjAv","name":"Hotel California","artist":"Eagles","artist_id":"0ECwFtbIWEVNwjlrfc6xoL","year":"1977","image_url":"https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e024637341b9f507521afa9a778","yt_query":"Eagles - Hotel California official audio","album":"Hotel California","preview_url":None,"duration_ms":391376},
    {"id":"4u7EnebtmKWzUH433cf5Qv","name":"Bohemian Rhapsody","artist":"Queen","artist_id":"1dfeR4HaWDbWqFHLkxsg1d","year":"1975","image_url":"https://i.scdn.co/image/ab67616d00001e02ce4f1737bc8a646c8c4bd25a","yt_query":"Queen - Bohemian Rhapsody official audio","album":"A Night at the Opera","preview_url":None,"duration_ms":354947},
    {"id":"5CQ30WqJwcep0pYcV4AMNc","name":"Stairway to Heaven","artist":"Led Zeppelin","artist_id":"36QJpDe2go2KgaRleHCDTp","year":"1971","image_url":"https://i.scdn.co/image/ab67616d00001e02c8a11e48c91a982d086afc69","yt_query":"Led Zeppelin - Stairway to Heaven official audio","album":"Led Zeppelin IV","preview_url":None,"duration_ms":482830},
    {"id":"7J1uxwnxfQLu4APicE5Rnj","name":"Billie Jean","artist":"Michael Jackson","artist_id":"3fMbdgg4jU18AjLCKBhRSm","year":"1982","image_url":"https://i.scdn.co/image/ab67616d00001e024121faee8df82c526cbab2be","yt_query":"Michael Jackson - Billie Jean official audio","album":"Thriller","preview_url":None,"duration_ms":293827},
    {"id":"1h2xVEoJORqrg71HocgqXd","name":"Superstition","artist":"Stevie Wonder","artist_id":"7guDJrEfX3qb6FEbdPA5qi","year":"1972","image_url":"https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e029e447b59bd3e2cbefaa31d91","yt_query":"Stevie Wonder - Superstition official audio","album":"Talking Book","preview_url":None,"duration_ms":245493},
    {"id":"3EYOJ1ST5O5ZQNBKuYh9VQ","name":"Wonderwall","artist":"Oasis","artist_id":"2DaxqgrOhkeH0fpeiQq2f4","year":"1995","image_url":"https://i.scdn.co/image/ab67616d00001e02ff5429125128b43572dbdccd","yt_query":"Oasis - Wonderwall official audio","album":"(What's the Story) Morning Glory?","preview_url":None,"duration_ms":258773},
    {"id":"2RnPATK05MTl8pVGObsqm4","name":"Come As You Are","artist":"Nirvana","artist_id":"6olE6TJLqED3rqDCT0FyPh","year":"1991","image_url":"https://i.scdn.co/image/ab67616d00001e02e175a19e530c898d167d39bf","yt_query":"Nirvana - Come As You Are official audio","album":"Nevermind","preview_url":None,"duration_ms":219219},
    {"id":"4yugZvBYaoREkJKtbG08Qr","name":"Take It Easy","artist":"Eagles","artist_id":"0ECwFtbIWEVNwjlrfc6xoL","year":"1972","image_url":"https://i.scdn.co/image/ab67616d00001e0284243a01af3c77b56fe01ab1","yt_query":"Eagles - Take It Easy official audio","album":"Eagles","preview_url":None,"duration_ms":211760},
    {"id":"7snQQk1zcKl8gGSbzO08Cs","name":"Purple Rain","artist":"Prince","artist_id":"5a2EaR3hamoenG9rDuVn8j","year":"1984","image_url":"https://i.scdn.co/image/ab67616d00001e02d4daf28d55fe4197ede848be","yt_query":"Prince - Purple Rain official audio","album":"Purple Rain","preview_url":None,"duration_ms":520000},
    {"id":"3AhXZa8sUQht0UEdBJgpGc","name":"Let It Be","artist":"The Beatles","artist_id":"3WrFJ7ztbogyGnTHbHJFl2","year":"1970","image_url":"https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e020cb0884829c5503b2e242541","yt_query":"The Beatles - Let It Be official audio","album":"Let It Be","preview_url":None,"duration_ms":243027},
    {"id":"0vFOzaXqZHahrZp6enQwQb","name":"Blinding Lights","artist":"The Weeknd","artist_id":"1Xyo4u8uXC1ZmMpatF05PJ","year":"2020","image_url":"https://i.scdn.co/image/ab67616d00001e028863bc11d2aa12b54f5aeb36","yt_query":"The Weeknd - Blinding Lights official audio","album":"After Hours","preview_url":None,"duration_ms":200040},
]


@app.route("/api/discovery")
def discovery():
    """
    Return curated songs for the landing page.
    Uses static pre-built data — NO Spotify API calls. Zero rate-limit risk.
    Returns stable order to prevent browser image cache mismatches.
    """
    return jsonify(_STATIC_DISCOVERY[:8])


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


@app.route("/api/theory/<section>")
def theory_data(section):
    if section not in ("chords", "scales", "progressions", "keys"):
        return jsonify({"error": "Unknown section"}), 404
    return jsonify(_load_theory(section))


if __name__ == "__main__":
    print("\n  Riffd running at http://localhost:5001\n")
    app.run(debug=True, port=5001, threaded=True)
