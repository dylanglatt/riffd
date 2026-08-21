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
  - MAX_CONCURRENT_JOBS (default 1) limits concurrent deep analysis; extras are queued
"""

import os
import re
import sys
import uuid
import collections
import threading
import traceback
from pathlib import Path
from dotenv import load_dotenv

# Ensure the venv's bin dir is on PATH for every subprocess call (yt-dlp, ffmpeg,
# etc.). launchd strips PATH down to system defaults, which excludes venv/bin —
# that's what breaks yt-dlp lookups in the download pipeline.
_venv_bin = os.path.dirname(sys.executable)
if _venv_bin and _venv_bin not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _venv_bin + os.pathsep + os.environ.get("PATH", "")
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for, make_response, send_file
from werkzeug.utils import secure_filename

# Crash reporting → Sentry (alerts land in Discord #stem-tab-app).
# Errors only — no performance tracing, no PII. Override DSN with SENTRY_DSN.
import hmac
import sentry_sdk
sentry_sdk.init(
    # No hardcoded fallback: a DSN is a write credential, and this one is in a
    # public repo. init(dsn=None) is a valid no-op. ROTATE THE OLD DSN.
    dsn=os.environ.get("SENTRY_DSN") or None,
)

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
from history import (add_to_history, get_recent, get_cached_result, get_cached_result_by_job,
                     cached_result_path, save_cached_result, touch_history)
from db import (init_db, migrate_from_history_json, get_track, upsert_track, set_track_status,
                touch_track, get_recent_tracks, get_analysis_for_track, upsert_job_checkpoint,
                get_job_checkpoint, recover_orphaned_jobs, get_visible_demo_tracks,
                get_demo_track, upsert_demo_track)

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

# Seed existing static demo tracks into DB (idempotent).
# All demo tracks are listed here so Render's ephemeral DB is always fully populated on startup.
_SEED_DEMOS = [
    {"slug": "take_it_easy", "title": "Take It Easy", "artist": "Eagles", "year": "1972",
     "genre": "Rock", "key_display": "G Major", "bpm": 0, "display_order": 1,
     "cover_path": "/static/demo/take_it_easy/cover.jpg",
     "analysis_path": "static/demo/take_it_easy/analysis.json",
     "stems_dir": "static/demo/take_it_easy/stems"},
    {"slug": "taste", "title": "Taste", "artist": "Sabrina Carpenter", "year": "2024",
     "genre": "Pop", "key_display": "D# Major", "bpm": 112, "display_order": 2,
     "cover_path": "/static/demo/taste/cover.jpg",
     "analysis_path": "static/demo/taste/analysis.json",
     "stems_dir": "static/demo/taste/stems"},
    {"slug": "bohemian_rhapsody", "title": "Bohemian Rhapsody", "artist": "Queen", "year": "1975",
     "genre": "Rock", "key_display": "Bb Major", "bpm": 0, "display_order": 3,
     "cover_path": "/static/demo/bohemian_rhapsody/cover.jpg",
     "analysis_path": "static/demo/bohemian_rhapsody/analysis.json",
     "stems_dir": "static/demo/bohemian_rhapsody/stems"},
    {"slug": "cruel_summer", "title": "Cruel Summer", "artist": "Taylor Swift", "year": "2019",
     "genre": "Pop", "key_display": "A Major", "bpm": 84, "display_order": 4,
     "cover_path": "/static/demo/cruel_summer/cover.jpg",
     "analysis_path": "static/demo/cruel_summer/analysis.json",
     "stems_dir": "static/demo/cruel_summer/stems"},
    {"slug": "seven_nation_army", "title": "Seven Nation Army", "artist": "The White Stripes", "year": "2003",
     "genre": "Rock", "key_display": "E Minor", "bpm": 123, "display_order": 5,
     "cover_path": "/static/demo/seven_nation_army/cover.jpg",
     "analysis_path": "static/demo/seven_nation_army/analysis.json",
     "stems_dir": "static/demo/seven_nation_army/stems"},
    {"slug": "gravity", "title": "Gravity", "artist": "John Mayer", "year": "2006",
     "genre": "Rock", "key_display": "C Major", "bpm": 0, "display_order": 6,
     "cover_path": "/static/demo/gravity/cover.jpg",
     "analysis_path": "static/demo/gravity/analysis.json",
     "stems_dir": "static/demo/gravity/stems"},
    {"slug": "mr_brightside", "title": "Mr. Brightside", "artist": "The Killers", "year": "2004",
     "genre": "Rock", "key_display": "C# Major", "bpm": 148, "display_order": 7,
     "cover_path": "/static/demo/mr_brightside/cover.jpg",
     "analysis_path": "static/demo/mr_brightside/analysis.json",
     "stems_dir": "static/demo/mr_brightside/stems"},
    {"slug": "circles", "title": "Circles", "artist": "Post Malone", "year": "2019",
     "genre": "Pop", "key_display": "C Major", "bpm": 120, "display_order": 8,
     "cover_path": "/static/demo/circles/cover.jpg",
     "analysis_path": "static/demo/circles/analysis.json",
     "stems_dir": "static/demo/circles/stems"},
    {"slug": "last_night", "title": "Last Night", "artist": "Morgan Wallen", "year": "2023",
     "genre": "Country", "key_display": "F# Major", "bpm": 101, "display_order": 9,
     "cover_path": "/static/demo/last_night/cover.jpg",
     "analysis_path": "static/demo/last_night/analysis.json",
     "stems_dir": "static/demo/last_night/stems"},
    {"slug": "kill_bill", "title": "Kill Bill", "artist": "SZA", "year": "2022",
     "genre": "R&B", "key_display": "D# Major", "bpm": 88, "display_order": 10,
     "cover_path": "/static/demo/kill_bill/cover.jpg",
     "analysis_path": "static/demo/kill_bill/analysis.json",
     "stems_dir": "static/demo/kill_bill/stems"},
    {"slug": "passionfruit", "title": "Passionfruit", "artist": "Drake", "year": "2017",
     "genre": "R&B", "key_display": "B Major", "bpm": 111, "display_order": 11,
     "cover_path": "/static/demo/passionfruit/cover.jpg",
     "analysis_path": "static/demo/passionfruit/analysis.json",
     "stems_dir": "static/demo/passionfruit/stems"},
    {"slug": "fast_car", "title": "Fast Car", "artist": "Tracy Chapman", "year": "1988",
     "genre": "Folk", "key_display": "A Major", "bpm": 104, "display_order": 12,
     "cover_path": "/static/demo/fast_car/cover.jpg",
     "analysis_path": "static/demo/fast_car/analysis.json",
     "stems_dir": "static/demo/fast_car/stems"},
    {"slug": "tondo", "title": "Tondo", "artist": "Disclosure, Eko Roosevelt", "year": "2020",
     "genre": "Electronic", "key_display": "C Minor", "bpm": 130, "display_order": 13,
     "cover_path": "/static/demo/tondo/cover.jpg",
     "analysis_path": "static/demo/tondo/analysis.json",
     "stems_dir": "static/demo/tondo/stems"},
    {"slug": "one_more_time", "title": "One More Time", "artist": "Daft Punk", "year": "2001",
     "genre": "Electronic", "key_display": "G Major", "bpm": 123, "display_order": 14,
     "cover_path": "/static/demo/one_more_time/cover.jpg",
     "analysis_path": "static/demo/one_more_time/analysis.json",
     "stems_dir": "static/demo/one_more_time/stems"},
    {"slug": "this_love", "title": "This Love", "artist": "Maroon 5", "year": "2004",
     "genre": "Rock", "key_display": "D# Major", "bpm": 96, "display_order": 15,
     "cover_path": "/static/demo/this_love/cover.jpg",
     "analysis_path": "static/demo/this_love/analysis.json",
     "stems_dir": "static/demo/this_love/stems"},
    {"slug": "when_it_rains", "title": "When It Rains It Pours", "artist": "Luke Combs", "year": "2017",
     "genre": "Country", "key_display": "F# Major", "bpm": 129, "display_order": 16,
     "cover_path": "/static/demo/when_it_rains/cover.jpg",
     "analysis_path": "static/demo/when_it_rains/analysis.json",
     "stems_dir": "static/demo/when_it_rains/stems"},
    {"slug": "locked_out_of_heaven", "title": "Locked out of Heaven", "artist": "Bruno Mars", "year": "2012",
     "genre": "Pop", "key_display": "C Major", "bpm": 143, "display_order": 17,
     "cover_path": "/static/demo/locked_out_of_heaven/cover.jpg",
     "analysis_path": "static/demo/locked_out_of_heaven/analysis.json",
     "stems_dir": "static/demo/locked_out_of_heaven/stems"},
]
for _demo in _SEED_DEMOS:
    _slug = _demo.pop("slug")
    _title = _demo.pop("title")
    _artist = _demo.pop("artist")
    upsert_demo_track(_slug, _title, _artist, **_demo)
print(f"[db] seeded {len(_SEED_DEMOS)} demo tracks")

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}

# ─── Job State ────────────────────────────────────────────────────────────────
# In-memory dict tracking all active jobs. Pruned after 10 min, trimmed after delivery.
# Keys: job_id (8-char UUID prefix)
# Values: dict with status, progress, audio_path, audio_source, audio_mode, analysis results
# WARNING: all state lost on server restart — persistent results are in filesystem cache + SQLite
jobs = {}

# Recover jobs orphaned by a server restart — insert stubs so /api/status returns a clean error
for _orphan in recover_orphaned_jobs():
    _oid = _orphan["job_id"]
    if _oid not in jobs:
        jobs[_oid] = {
            "status": "failed",
            "error": "Server restarted during processing — please try again.",
            "progress": "Server restarted",
        }
        upsert_job_checkpoint(_oid, "failed", error="Server restarted during processing")
        print(f"[recover] orphaned job {_oid} marked as failed")

_stem_last_accessed: dict = {}  # job_id → timestamp of last audio request
_processing_lock = threading.Lock()
_active_processing = 0
# Stem separation runs on Replicate (cloud GPU), so local RAM is not the bottleneck.
# Allow multiple concurrent jobs; tune via MAX_CONCURRENT_JOBS env var if needed.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))

# TensorFlow inference lock — only one job runs Basic Pitch at a time.
# TF loads ~1.2GB and is not safely reentrant across threads. Two concurrent
# jobs hitting inference simultaneously would OOM a 2GB instance. Jobs still
# run Demucs in parallel (cloud GPU, no local RAM); they only queue here.
_tf_inference_lock = threading.Lock()
_job_queue: collections.deque = collections.deque()  # jobs waiting for a processing slot

# ─── Background prefetch ─────────────────────────────────────────────────────
# In-memory dict tracking background downloads. Keyed by spotify_track_id.
# Separate from `jobs` — these are invisible to the user until they click Separate Stems.
_bg_downloads = {}
_bg_lock = threading.Lock()


def _log_memory(label=""):
    """Log current + peak RSS memory usage with detailed breakdown.

    Reads /proc/self/status on Linux to get *current* RSS (VmRSS) rather than
    the peak-only value from getrusage (ru_maxrss), which never decreases and
    hides whether gc.collect() actually freed anything.
    """
    try:
        import sys
        current_mb = None
        peak_mb = None
        vm_size_mb = None

        if sys.platform == "linux":
            # /proc/self/status gives us current RSS, peak RSS, and virtual size
            try:
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            current_mb = int(line.split()[1]) / 1024  # kB → MB
                        elif line.startswith("VmHWM:"):
                            peak_mb = int(line.split()[1]) / 1024
                        elif line.startswith("VmSize:"):
                            vm_size_mb = int(line.split()[1]) / 1024
            except Exception:
                pass

        # Fallback: getrusage (peak only, but better than nothing on macOS)
        if current_mb is None:
            import resource
            peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "linux":
                peak_mb /= 1024  # KB → MB
            else:
                peak_mb /= (1024 * 1024)  # bytes → MB (macOS)
            print(f"[mem] {label} peak_RSS={peak_mb:.0f}MB")
            return

        parts = [f"RSS={current_mb:.0f}MB", f"peak={peak_mb:.0f}MB"]
        if vm_size_mb is not None:
            parts.append(f"virt={vm_size_mb:.0f}MB")

        # Count active jobs + threads for context
        active = _active_processing
        thread_count = threading.active_count()
        parts.append(f"jobs={active}")
        parts.append(f"threads={thread_count}")

        print(f"[mem] {label} {' | '.join(parts)}")
    except Exception:
        pass


# ─── Job watchdog ────────────────────────────────────────────────────────────
#
# Two independent bounds, because "stuck" and "slow" are different failures:
#
#   STALL_TIMEOUT_S   no heartbeat for this long → the job is genuinely wedged.
#   JOB_MAX_SECONDS   absolute backstop for a job that heartbeats forever.
#
# The watchdog used to measure time SINCE START, which conflates the two — and
# the slow path here is legitimate and recoverable. processor.MAX_RETRIES is 3
# separation attempts, each bounded by MAX_WAIT (420s) with 10s/15s backoff, so
# one retry can reach 850s and three 1285s, all of it making steady progress.
#
# From the local logs: a retry fired on 1 of 15 separations and that one
# SUCCEEDED on attempt 2 (it had failed on a 15s socket timeout, not by
# exhausting MAX_WAIT — the common retry is cheap). Three further failures
# carried the old collapsed "no downloadable stems" message that never matched
# the retry classifier, fixed in e445898, so the retry path is about to engage
# roughly 4x more often than those logs show. A 600s since-start watchdog would
# have turned every one of those recoverable blips into a hard failure.
#
# MAX_WAIT cannot come down to compensate either: a healthy separation in the
# same logs took 376.5s of its 420s budget.
STALL_TIMEOUT_S = 300    # 5 min of silence. The longest legitimately quiet
                         # stretch is one Basic Pitch child (120s cap), and that
                         # loop heartbeats per stem.
JOB_MAX_SECONDS = 1800   # 30 min. Backstop only — the stall bound should always
                         # fire first on a real failure.


def _touch_job(job_id):
    """Heartbeat: record that this job just made progress.

    What the watchdog measures. Anything that can run for a while without
    changing the progress string should call this.
    """
    import time as _touch_time
    job = jobs.get(job_id)
    if job is not None:
        job["_last_progress_at"] = _touch_time.time()


def _dequeue_next():
    """Called when a processing slot opens — start the next queued job if one exists."""
    global _active_processing
    with _processing_lock:
        if _job_queue and _active_processing < MAX_CONCURRENT_JOBS:
            job_id, run_fn = _job_queue.popleft()
            if job_id in jobs:
                import time as _dq_time
                jobs[job_id]["status"] = "processing"
                jobs[job_id]["progress"] = "Separating stems..."
                # Restamp both clocks. _started_at was set before the job was
                # queued, so without this a job that waited 5 minutes for a slot
                # arrives with 5 minutes already charged against the watchdog.
                jobs[job_id]["_started_at"] = _dq_time.time()
                jobs[job_id]["_last_progress_at"] = _dq_time.time()
                upsert_job_checkpoint(job_id, "processing", progress="Separating stems...")
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
        _log_memory("post-prune")

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

    _retention_hours = int(os.getenv("DISK_RETENTION_HOURS", "24"))  # default 24 hours — short enough for Render, long enough for overnight users
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
        print(f"[disk] pruned {pruned} old job directories (>{_retention_hours}h)")


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
            "track_meta", "_started_at", "_finished_at", "_result_delivered", "_trimmed"}
    job["_trimmed"] = True
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
                upsert_job_checkpoint(job_id, "ready", progress="Download complete")
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
                    upsert_job_checkpoint(job_id, "ready", progress="Download complete")
                    print(f"[job {job_id}] DOWNLOAD REUSED from job {old_job} → {audio_path}")
                    return jsonify({"job_id": job_id, "mode": mode})

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "downloading", "audio_mode": "full", "progress": "Getting audio..."}
    upsert_job_checkpoint(job_id, "downloading", progress="Getting audio...")
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
            upsert_job_checkpoint(job_id, "ready", progress="Download complete")
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
            upsert_job_checkpoint(job_id, "upload_required", error="Full track unavailable")
            print(f"[job {job_id}] STATUS → upload_required")

        except Exception as e:
            print(f"[job {job_id}] DOWNLOAD ERROR: {e}")
            traceback.print_exc()
            log_event("youtube_failed", {"job_id": job_id, "error": str(e)[:100]})
            jobs[job_id].update({"status": "error", "error": str(e)})
            upsert_job_checkpoint(job_id, "error", error=str(e))
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

    # Warm the Replicate GPU in parallel with the download. Cold-booting the
    # Demucs container is the dominant cost of separation (1-4+ min vs ~20s of
    # GPU time), so booting it while the user is still deciding means the real
    # job usually lands on a warm instance. Cooldown-gated inside processor.
    def _warm_gpu():
        try:
            from processor import warm_replicate_model
            warm_replicate_model()
        except Exception as _w_e:
            print(f"[prefetch {prefetch_id}] warmup skipped: {_w_e}")
    threading.Thread(target=_warm_gpu, daemon=True).start()

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
    upsert_job_checkpoint(job_id, "ready", progress="File uploaded")

    # Warm the Replicate GPU — user will likely hit Analyze within seconds.
    def _warm_gpu_upload():
        try:
            from processor import warm_replicate_model
            warm_replicate_model()
        except Exception as _w_e:
            print(f"[upload {job_id}] warmup skipped: {_w_e}")
    threading.Thread(target=_warm_gpu_upload, daemon=True).start()

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

    # Pre-flight disk space check — reject early if disk is dangerously low.
    # A single job can need ~2GB (6 WAV stems + intermediates + analysis).
    MIN_FREE_DISK_MB = int(os.getenv("MIN_FREE_DISK_MB", "1500"))
    try:
        _disk = os.statvfs(".")
        _free_mb = (_disk.f_bavail * _disk.f_frsize) // (1024 * 1024)
        print(f"[disk] free space: {_free_mb}MB (minimum: {MIN_FREE_DISK_MB}MB)")
        if _free_mb < MIN_FREE_DISK_MB:
            print(f"[disk] REJECTING job {job_id}: only {_free_mb}MB free")
            jobs[job_id].update({"status": "error", "error": "Server disk space low — please try again later.", "progress": "Disk space insufficient"})
            return jsonify({"status": "error", "error": "Server is temporarily low on disk space. Please try again in a few minutes."}), 503
    except Exception as _disk_err:
        print(f"[disk] space check failed (non-fatal): {_disk_err}")

    spotify_track_id = req_data.get("track_id")
    spotify_artist_id = req_data.get("artist_id")
    track_meta = req_data.get("track_meta", {})

    # ── Long-track guard ──
    # Very long tracks used to fail mid-pipeline with an opaque Replicate
    # timeout or memory-guard error. Reject upfront with a clear message
    # instead. Duration from Spotify metadata; ffprobe fallback for uploads.
    MAX_TRACK_MINUTES = int(os.getenv("MAX_TRACK_MINUTES", "20"))
    _track_dur_s = (track_meta.get("duration_ms") or 0) / 1000.0
    if not _track_dur_s:
        try:
            from processor import _probe_duration_s
            _track_dur_s = _probe_duration_s(audio_path)
        except Exception:
            _track_dur_s = 0.0
    _is_long_track = _track_dur_s > 8 * 60
    if _track_dur_s > MAX_TRACK_MINUTES * 60:
        _dur_min = int(_track_dur_s // 60)
        print(f"[job {job_id}] REJECTED: track too long ({_track_dur_s:.0f}s > {MAX_TRACK_MINUTES}min cap)")
        _msg = (f"This track is about {_dur_min} minutes long — Riffd currently supports "
                f"songs up to {MAX_TRACK_MINUTES} minutes. Try a shorter track.")
        return jsonify({"status": "error", "code": "track_too_long", "error": _msg}), 400

    import time as _time_mod
    jobs[job_id]["status"] = "processing"
    jobs[job_id]["progress"] = ("Separating stems... (long track — this may take a few extra minutes)"
                                if _is_long_track else "Separating stems...")
    jobs[job_id]["_started_at"] = _time_mod.time()
    jobs[job_id]["_last_progress_at"] = _time_mod.time()
    # Publish the track's identity on the job. A tab that reconnects via ?job=
    # has no selectedTrack — it never ran the search — so without this the
    # status payload cannot say WHAT is being analyzed and the banner falls
    # back to "Result / Unknown". Small dict; name/artist/artwork only.
    if track_meta:
        jobs[job_id]["track_meta"] = {
            k: track_meta.get(k) for k in
            ("name", "artist", "image_url", "year", "id", "artist_id", "duration_ms")
            if track_meta.get(k) is not None
        }
    upsert_job_checkpoint(job_id, "processing", progress="Separating stems...")
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

        # Memory guard — reject only if RSS is dangerously high.
        # Prevents starting a job that will inevitably OOM and take down the service.
        #
        # WAS 2200. Render Standard is a 2048MB instance, so a 2200MB threshold sits
        # ABOVE the ceiling: the Linux OOM killer always fires first and this guard
        # can never trigger. It has been inoperative, not lenient.
        #
        # 1400 is a starting value, not a tuned one. The binding constraint is the
        # Basic Pitch CHILD process (~1.2GB, app.py:1505) running alongside the
        # parent, so the parent must stay under roughly 2048-1200 = ~850MB at that
        # stage. A lean parent is ~40MB (heavy imports are deferred), so anything
        # above ~1400MB at job start means something has already leaked. The known
        # offender was the in-process TensorFlow load in _melodic_split_pass, since
        # deleted; basic_pitch now loads only via _ensure_pitch_imports(), which
        # only the inference child calls. If this guard trips, look for a new path
        # pulling TF into the parent.
        # Instrument with _log_memory and tune from real data.
        MEMORY_GUARD_MB = int(os.getenv("MEMORY_GUARD_MB", "1400"))

        def _get_current_rss_mb():
            try:
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            return int(line.split()[1]) / 1024
            except Exception:
                return 0  # Non-Linux (macOS dev) — skip guard

        _current_rss = _get_current_rss_mb()
        if _current_rss > MEMORY_GUARD_MB:
            # Post-job cleanup from a prior request may still be settling.
            # Try the same TF clear + GC the post-job path uses, then wait briefly
            # and re-measure before giving up.
            print(f"[job {job_id}] memory high (RSS={_current_rss:.1f}MB > {MEMORY_GUARD_MB}MB) — attempting cleanup")
            try:
                import tensorflow as _tf
                _tf.keras.backend.clear_session()
                del _tf
            except Exception:
                pass
            for _ in range(3):
                gc.collect()
                _time.sleep(1.5)
                _current_rss = _get_current_rss_mb()
                if _current_rss <= MEMORY_GUARD_MB:
                    break
            if _current_rss > MEMORY_GUARD_MB:
                print(f"[job {job_id}] MEMORY GUARD: RSS={_current_rss:.1f}MB > {MEMORY_GUARD_MB}MB — rejecting")
                jobs[job_id].update({
                    "status": "error",
                    "error": "Server memory too high — try again in a moment.",
                    "progress": "Memory guard triggered",
                })
                upsert_job_checkpoint(job_id, "error", error="Server memory too high")
                with _processing_lock:
                    _active_processing -= 1
                _dequeue_next()
                return
            print(f"[job {job_id}] memory recovered (RSS={_current_rss:.1f}MB) — proceeding")

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
        # Stems the separator was told about but could not deliver (after its own
        # retry). Kept separate from failed_steps because it gates caching — see
        # _finalize below.
        missing_stems = []
        stem_report = {}

        def on_progress(msg):
            jobs[job_id]["progress"] = msg
            _touch_job(job_id)
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
            if missing_stems:
                result["missing_stems"] = sorted(missing_stems)
            jobs[job_id].update(result)
            import time as _t; _stem_last_accessed[job_id] = _t.time()
            cache_path = str(Path("outputs") / job_id / "result_cache.json") if spotify_track_id else None
            upsert_job_checkpoint(job_id, result["status"], progress=result.get("progress"), result_cache_path=cache_path, error=result.get("error"))
            status_label = result["status"]
            print(f"[job {job_id}] [{_elapsed()}] STATUS → {status_label}" + (f" (failed: {[s['step'] for s in failed_steps]})" if partial else ""))
            log_event("analysis_complete", {"job_id": job_id, "status": result["status"], "elapsed": round(_time.time() - _t0, 1)})

            # Save to cache + history + DB
            #
            # Two kinds of "partial" have to be treated differently here:
            #
            #   a non-stem stage failed (lyrics, recs, insight) — cache as before.
            #     The audio analysis is sound, the missing pieces degrade
            #     gracefully, and re-running wouldn't reliably fix them anyway.
            #
            #   a required stem is missing — DO NOT cache. This block also calls
            #     set_track_status(..., "available"), so caching here would pin a
            #     mix that is permanently short an instrument and serve it to
            #     everyone who opens the track later. One transient download blip
            #     would become a permanent bad result. Skipping the cache costs
            #     this user nothing (they still get the job as analyzed) and makes
            #     the track re-analyze on the next visit.
            if missing_stems:
                print(f"[job {job_id}] [{_elapsed()}] NOT caching — missing stem(s): "
                      f"{', '.join(sorted(missing_stems))}. Track will re-analyze next time.")
            if spotify_track_id and (not partial or stems) and not missing_stems:
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
                        "track_meta": jobs[job_id].get("track_meta"),
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

        # ── Transcode webm/opus/ogg → wav before any processing ─────────────────
        # Piped downloads arrive as .webm. librosa's audioread backend triggers a
        # C-level heap corruption (SIGABRT) when decoding webm, killing the entire
        # worker. Transcode early so all downstream consumers get a safe wav file.
        #
        # IMPORTANT: we use _safe_audio_path (not reassigning audio_path) because
        # assigning to audio_path inside run() would make Python treat it as a local
        # variable throughout the closure, causing UnboundLocalError on first read.
        _safe_audio_path = audio_path
        _audio_ext = Path(audio_path).suffix.lower()
        if _audio_ext in (".webm", ".opus", ".ogg"):
            _wav_target = Path(audio_path).with_suffix(".wav")
            try:
                import subprocess as _tc_sp
                _tc = _tc_sp.run(
                    ["ffmpeg", "-y", "-i", audio_path, "-ac", "2", "-ar", "44100", str(_wav_target)],
                    capture_output=True, timeout=120,
                )
                if _tc.returncode == 0 and _wav_target.exists() and _wav_target.stat().st_size > 0:
                    print(f"[job {job_id}] transcoded {_audio_ext} → .wav ({_wav_target.stat().st_size:,} bytes)")
                    _safe_audio_path = str(_wav_target)
                else:
                    print(f"[job {job_id}] webm transcode failed (rc={_tc.returncode}) — proceeding with original")
            except Exception as _tc_err:
                print(f"[job {job_id}] webm transcode error: {_tc_err} — proceeding with original")

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
                # Wait for instrument hints (fast — <2s) so energy overrides
                # can be applied during stem refinement. Hints resolve well
                # before Demucs finishes its 20s+ separation.
                hints = None
                try:
                    hints = fut_hints.result(timeout=10)
                except Exception:
                    pass  # Hints are optional — proceed without them
                return separate_stems(_safe_audio_path, job_id, progress_callback=on_progress,
                                      instrument_hints=hints, report=stem_report)

            def _run_early_key():
                """Key + BPM on the original audio — runs concurrently with Demucs.
                Publishes intelligence within ~10s so Key tab populates early."""
                try:
                    from music_intelligence import detect_key_from_audio, detect_bpm_from_audio, format_key
                    key_num, mode_num, key_conf = detect_key_from_audio(_safe_audio_path)
                    bpm, bpm_conf = detect_bpm_from_audio(_safe_audio_path)
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

            def _run_instrument_hints():
                try:
                    from insight import predict_instruments
                    return predict_instruments(track_name, artist_name, tags=None)
                except Exception as e:
                    _fail("instrument_hints", e)
                    return None

            with _TPE(max_workers=5) as _pool:
                fut_hints     = _pool.submit(_run_instrument_hints)
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

                # Unload essentia from process memory — it's never used again.
                # Frees ~100MB that would otherwise stay resident for the rest of the job.
                try:
                    import sys as _sys_ess
                    _ess_mods = [m for m in _sys_ess.modules if m.startswith("essentia")]
                    for _em in _ess_mods:
                        del _sys_ess.modules[_em]
                    if _ess_mods:
                        gc.collect()
                        print(f"[job {job_id}] [{_elapsed()}] unloaded {len(_ess_mods)} essentia modules")
                except Exception:
                    pass

                # Collect instrument hints (fast — should complete in <2s)
                instrument_hints = fut_hints.result()
                if instrument_hints:
                    jobs[job_id]["instrument_hints"] = instrument_hints
                    print(f"[job {job_id}] [{_elapsed()}] instrument hints collected")
                else:
                    print(f"[job {job_id}] [{_elapsed()}] instrument hints: none")

                # Wait for Demucs (the slow one)
                _demucs_exc = None  # bound here so the `if not stems` branch below
                                    # can't NameError when Demucs returns empty
                                    # without raising
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
                upsert_job_checkpoint(job_id, "error", progress="Stem separation failed", error=str(_demucs_exc))
                return

            gc.collect()
            print(f"[job {job_id}] [{_elapsed()}] DEMUCS finished → {len(stems)} stems: {list(stems.keys())}")
            _log_memory(f"[job {job_id}] post-demucs")

            # Stems the separator was given a URL for but could not deliver, even
            # after its own retry. The job continues — the user gets the stems that
            # did arrive — but it must not report "complete", and _finalize must not
            # cache it. A stem the model simply didn't return ("omitted") is not a
            # failure and doesn't land here.
            _stem_failures = stem_report.get("stem_failures") or {}
            if _stem_failures:
                missing_stems.extend(_stem_failures)
                _names = ", ".join(sorted(_stem_failures))
                print(f"[job {job_id}] [{_elapsed()}] STEMS INCOMPLETE — could not download: {_names}")
                # Appended directly rather than via _fail(): there is no live
                # exception here, so _fail's traceback.print_exc() would print a
                # misleading "NoneType: None".
                failed_steps.append({
                    "step": "stem_download",
                    "message": f"could not download stem(s) after retry: {_names}",
                })

            # Apply instrument hints to improve stem classification
            if stems and instrument_hints:
                from processor import apply_instrument_hints
                stems = apply_instrument_hints(stems, instrument_hints)

            # Free any large in-memory objects from demucs before Basic Pitch
            gc.collect()
            _log_memory(f"[job {job_id}] pre-basic-pitch")
            jobs[job_id]["stems"] = {k: {"label": v.get("label", k), "energy": v.get("energy", 0), "active": v.get("active", True)} for k, v in stems.items()}
            jobs[job_id]["stems_ready"] = True
            jobs[job_id]["progress"] = "Stems ready — analyzing..."
            _touch_job(job_id)

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

            # ── Stage 2.5: Early WAV→MP3 for non-inference stems ──
            # Stems that won't go through Basic Pitch (drums, low-priority pitched)
            # don't need their WAV files anymore. Converting now frees ~190MB per
            # stem BEFORE TensorFlow loads, cutting peak memory significantly.
            _DRUM_KEYS = {"drums", "drum", "kick", "snare", "percussion"}
            _SKIP_INFERENCE_KEYS = {
                "harmony_vocal", "backing_vocal", "synth", "other",
                "keys", "guitar_layer", "strumming_guitar",
            }

            def _is_inference_stem(k):
                """True if this stem will go through Basic Pitch."""
                kl = k.lower()
                if kl in _DRUM_KEYS or kl.startswith("drum"):
                    return False
                if kl in _SKIP_INFERENCE_KEYS:
                    return False
                return True

            import subprocess as _sp_early
            _early_converted = 0
            for _sk, _sv in active_stems.items():
                if _is_inference_stem(_sk):
                    continue  # Keep WAV — Basic Pitch needs it
                _wav = Path(_sv["path"])
                if not _wav.exists() or _wav.suffix.lower() != ".wav":
                    continue
                _mp3 = _wav.with_suffix(".mp3")
                try:
                    _rc = _sp_early.run(
                        ["ffmpeg", "-y", "-i", str(_wav), "-b:a", "192k", "-ac", "2", str(_mp3)],
                        capture_output=True, timeout=60,
                    )
                    if _rc.returncode == 0 and _mp3.exists() and _mp3.stat().st_size > 1000:
                        _wav.unlink()
                        _sv["path"] = str(_mp3)  # Update stem path for downstream
                        _early_converted += 1
                except Exception as _ec_e:
                    print(f"[job {job_id}] early MP3 convert warning ({_sk}): {_ec_e}")
            if _early_converted:
                print(f"[job {job_id}] [{_elapsed()}] early WAV→MP3: freed {_early_converted} non-inference stems")
                _log_memory(f"[job {job_id}] post-early-mp3")
            del _sp_early

            # Adjust note detection configs based on instrument hints (per-job copy)
            _adjusted_configs = None
            if instrument_hints:
                from processor import get_adjusted_configs
                _adjusted_configs = get_adjusted_configs(instrument_hints)

            # ── Stage 3: Note extraction (Basic Pitch) — sequential across priority stems ──
            # Drums produce no useful pitch data — skip entirely.
            # Secondary stems (harmony/backing vocals, synth, keys, other) are skipped for
            # Basic Pitch — they add ~3-4 min processing time with marginal harmonic value.
            # Their MP3 files are preserved for playback; only inference is skipped.
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
                # Wait for exclusive access to TF before loading the model.
                # Logged so we can see queue time in Render logs.
                if not _tf_inference_lock.acquire(blocking=False):
                    jobs[job_id]["progress"] = "Waiting for inference slot..."
                    _touch_job(job_id)
                    print(f"[job {job_id}] [{_elapsed()}] TF lock busy — waiting...")
                    _tf_inference_lock.acquire()
                print(f"[job {job_id}] [{_elapsed()}] TF lock acquired")

                def _extract_one_stem(stem_key, stem_info):
                    import tempfile as _tempfile, subprocess as _sp, os as _os
                    label = stem_info.get("label", stem_key)
                    full_path = stem_info["path"]
                    print(f"[job {job_id}] [{_elapsed()}] Basic Pitch → {stem_key}...")
                    _touch_job(job_id)   # per-stem: the progress string doesn't change here

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

                    # Run Basic Pitch in a child process so TF memory (~1.2GB) is
                    # fully reclaimed by the OS when the child exits.
                    # The parent stays lean between stems.
                    import pickle as _pickle
                    _result_path = None
                    try:
                        _res_fd, _result_path = _tempfile.mkstemp(suffix=f"_{stem_key}_notes.pkl")
                        _os.close(_res_fd)

                        # Serialize configs to pass to child process
                        _configs_path = None
                        if _adjusted_configs:
                            _cfg_fd, _configs_path = _tempfile.mkstemp(suffix="_configs.pkl")
                            _os.close(_cfg_fd)
                            with open(_configs_path, "wb") as _cf:
                                _pickle.dump(_adjusted_configs, _cf)

                        _script = f'''
import pickle, sys
sys.path.insert(0, ".")
from processor import extract_note_events
configs = None
configs_path = {_configs_path!r}
if configs_path:
    with open(configs_path, "rb") as f:
        configs = pickle.load(f)
result = extract_note_events({infer_path!r}, {stem_key!r}, label={label!r}, bpm={detected_bpm!r}, configs=configs)
with open({_result_path!r}, "wb") as f:
    pickle.dump(result, f)
'''
                        import sys as _sys_exec
                        _proc = _sp.run(
                            [_sys_exec.executable, "-c", _script],
                            capture_output=True, text=True, timeout=120,
                            cwd=str(Path(__file__).parent),
                        )
                        if _proc.returncode != 0:
                            raise RuntimeError(f"Basic Pitch subprocess failed: {_proc.stderr[-500:]}")

                        with open(_result_path, "rb") as _rf:
                            ne = _pickle.load(_rf)
                    finally:
                        # Always clean up temp files
                        for _tmp in (tmp_path, _result_path, _configs_path):
                            if _tmp and _os.path.exists(_tmp):
                                try:
                                    _os.remove(_tmp)
                                except Exception:
                                    pass

                    # Convert this stem's WAV→MP3 immediately after inference.
                    # Frees ~190MB per stem instead of holding all WAVs until the batch step.
                    _wav_path = Path(full_path)
                    if _wav_path.exists() and _wav_path.suffix.lower() == ".wav":
                        _mp3_out = _wav_path.with_suffix(".mp3")
                        try:
                            _conv = _sp.run(
                                ["ffmpeg", "-y", "-i", str(_wav_path), "-b:a", "192k", "-ac", "2", str(_mp3_out)],
                                capture_output=True, timeout=60,
                            )
                            if _conv.returncode == 0 and _mp3_out.exists() and _mp3_out.stat().st_size > 1000:
                                _wav_path.unlink()
                                stem_info["path"] = str(_mp3_out)
                                print(f"[job {job_id}] [{_elapsed()}] {stem_key} WAV→MP3 done (freed ~{_wav_path.name})")
                        except Exception as _mp3_e:
                            print(f"[job {job_id}] post-inference MP3 convert warning ({stem_key}): {_mp3_e}")

                    gc.collect()
                    _log_memory(f"[job {job_id}] post-stem-inference ({stem_key})")
                    _touch_job(job_id)
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
                # No TF clear_session() here. This block used to `import
                # tensorflow` and call clear_session() on the parent — a parent
                # that never ran inference. Inference happens in the child
                # spawned above, and the child's ~1.2GB is already gone the
                # moment it exits. All the import achieved was pulling TF into
                # the one process that must stay lean.
                print(f"[job {job_id}] [{_elapsed()}] NOTE EXTRACTION finished → {len(note_events_all)} stems with notes")
            except Exception as e:
                _fail("note_extraction", e)
            finally:
                # Always release the lock — even if inference crashed — so the next job isn't stuck.
                try:
                    _tf_inference_lock.release()
                    print(f"[job {job_id}] [{_elapsed()}] TF lock released")
                except RuntimeError:
                    pass  # Already released or never acquired (e.g. no pitched stems)

            # ── WAV → MP3 cleanup (safety net) ──
            # Non-inference stems were converted before Basic Pitch loaded.
            # Inference stems were converted individually after each extraction.
            # This pass catches any stragglers (e.g. if a per-stem convert failed).
            try:
                import subprocess as _sp_mp3
                stems_dir_mp3 = OUTPUT_DIR / job_id / "stems"
                _straggler = 0
                for wav_file in list(stems_dir_mp3.glob("*.wav")):
                    mp3_file = wav_file.with_suffix(".mp3")
                    result = _sp_mp3.run(
                        ["ffmpeg", "-y", "-i", str(wav_file), "-b:a", "192k", "-ac", "2", str(mp3_file)],
                        capture_output=True, timeout=60,
                    )
                    if result.returncode == 0 and mp3_file.exists() and mp3_file.stat().st_size > 1000:
                        wav_file.unlink()
                        _straggler += 1
                    else:
                        print(f"[job {job_id}] MP3 convert failed for {wav_file.name}, keeping WAV")
                if _straggler:
                    print(f"[job {job_id}] [{_elapsed()}] WAV→MP3 cleanup: {_straggler} straggler(s)")
            except Exception as _mp3_e:
                print(f"[job {job_id}] WAV→MP3 cleanup warning: {_mp3_e}")

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
            # Aggressive disk cleanup — remove leftover intermediates regardless of outcome.
            # processor.py has its own try/finally, but this catches cases where the crash
            # happens outside processor (e.g. during MP3 conversion or between stages).
            try:
                _stems_dir = OUTPUT_DIR / job_id / "stems"
                if _stems_dir.exists():
                    _cleaned = 0
                    for _raw in list(_stems_dir.glob("_raw_*.wav")):
                        try:
                            _raw.unlink()
                            _cleaned += 1
                        except Exception:
                            pass
                    for _htd in list(_stems_dir.glob("htdemucs_*")):
                        if _htd.is_dir():
                            import shutil as _shutil_clean
                            _shutil_clean.rmtree(_htd, ignore_errors=True)
                            _cleaned += 1
                    if _cleaned:
                        print(f"[job {job_id}] post-job cleanup: removed {_cleaned} intermediate file(s)")
            except Exception as _clean_err:
                print(f"[job {job_id}] post-job cleanup warning: {_clean_err}")

            # Release processing slot, kick off any queued jobs, then GC
            with _processing_lock:
                _active_processing -= 1
            _dequeue_next()
            gc.collect()
            _log_memory(f"[job {job_id}] PROCESS END")
            print(f"[mem] active processing jobs: {_active_processing}")

    # Either start immediately or enqueue if at capacity
    with _processing_lock:
        if _active_processing >= MAX_CONCURRENT_JOBS:
            # ── Queue instead of reject ──
            # Cap the queue to prevent unbounded growth
            MAX_QUEUE_SIZE = 10
            if len(_job_queue) >= MAX_QUEUE_SIZE:
                print(f"[job {job_id}] REJECTED: queue full ({len(_job_queue)} waiting)")
                jobs.pop(job_id, None)
                return jsonify({
                    "status": "busy",
                    "error": "Riffd is at capacity right now. Try again in a moment.",
                }), 503

            jobs[job_id]["status"] = "queued"
            jobs[job_id]["progress"] = "Waiting for a processing slot..."
            queue_pos = len(_job_queue) + 1
            _job_queue.append((job_id, run))
            upsert_job_checkpoint(job_id, "queued", progress="Waiting for a processing slot...")
            print(f"[job {job_id}] QUEUED: {_active_processing} active, {queue_pos} in queue")
            return jsonify({"status": "queued", "job_id": job_id, "queue_position": queue_pos}), 202

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "processing", "job_id": job_id})


def _stems_on_disk(job_id) -> list:
    """Stem keys still present in outputs/<job_id>/stems, newest format first."""
    stems_dir = OUTPUT_DIR / job_id / "stems"
    if not stems_dir.exists():
        return []
    names = set()
    for f in stems_dir.iterdir():
        # _raw_* are separation intermediates, not deliverable stems
        if f.suffix in (".mp3", ".wav") and not f.name.startswith("_raw_"):
            names.add(f.stem)
    return sorted(names)


def _rehydrate_job(job_id):
    """Rebuild a job from persisted state after it left the in-memory dict.

    A miss in `jobs` is usually not a restart: _prune_old_jobs() evicts finished
    jobs after 600s while result_cache.json and the stems are still on disk, and
    a shared ?job= link goes stale the same way. Three durable sources already
    exist, so consult them before giving up.

    Returns a job dict (also inserted into `jobs`), or None to let the 404 stand.
    """
    import time as _rt
    checkpoint = get_job_checkpoint(job_id)
    if not checkpoint:
        return None                      # we genuinely never had this job

    status = (checkpoint.get("status") or "").strip()

    # Still marked processing means the worker died mid-job — nothing resumed it,
    # because job state is per-process. This is the ONLY case where "the server
    # restarted" is an honest thing to tell the user.
    if status in ("processing", "queued", "downloading", "ready"):
        print(f"[job {job_id}] REHYDRATE → orphaned (checkpoint status={status!r})")
        return {
            "status": "error",
            "error": "Processing was interrupted before it finished.",
            "error_step": "orphaned",
            "progress": checkpoint.get("progress") or "Interrupted",
        }

    if status not in ("complete", "partial"):
        return None

    cached = get_cached_result_by_job(job_id)   # None on a version mismatch too
    stem_keys = _stems_on_disk(job_id)

    if cached:
        # The stems dict describes what the analysis produced; only serve it when
        # the audio is actually still there, or every channel 404s on load.
        job = {
            "status": status,
            "progress": "Done!" if status == "complete" else "Completed with errors",
            "stems": cached.get("stems", {}) if stem_keys else {},
            "stems_available": bool(stem_keys),
            "stems_ready": bool(stem_keys),
            "intelligence": cached.get("intelligence"),
            "lyrics": cached.get("lyrics"),
            "tags": cached.get("tags", []),
            "insight": cached.get("insight"),
            "recommendations": cached.get("recommendations"),
            "audio_source": cached.get("audio_source"),
            "audio_mode": cached.get("audio_mode", "full"),
            "track_meta": cached.get("track_meta"),
            "_rehydrated": True,
        }
    elif cached_result_path(job_id):
        # A cache file exists but get_cached_result_by_job rejected it — stale
        # ANALYSIS_VERSION. Do not fall through to the stems on disk: after the
        # v5->v6 bump those may be stem types this pipeline no longer produces
        # (lead_guitar, rhythm_guitar). Expire it and let the user re-analyze.
        print(f"[job {job_id}] REHYDRATE → refused, cached analysis is a stale version")
        return None
    elif stem_keys:
        # Uploads never write result_cache.json — save_cached_result is gated on
        # spotify_track_id — so there is no analysis to restore. The stems are
        # still on disk though, so hand back the multitrack rather than nothing.
        # energy is None so _addStemChannel's <0.015 skip doesn't drop them.
        print(f"[job {job_id}] REHYDRATE → stems only ({len(stem_keys)}), no cached analysis")
        job = {
            "status": status,
            "progress": "Done!",
            "stems": {k: {"label": "", "energy": None, "active": True} for k in stem_keys},
            "stems_available": True,
            "stems_ready": True,
            "_rehydrated": True,
            "_stems_only": True,
        }
    else:
        return None                      # checkpoint survived, the data didn't

    if checkpoint.get("error"):
        job["error"] = checkpoint["error"]

    now = _rt.time()
    # _finished_at so _prune_old_jobs can evict this again on its normal cycle.
    job["_finished_at"] = now
    jobs[job_id] = job
    if stem_keys:
        # Without this the 1800s idle sweep could delete the stems moments after
        # we handed the user a mixer built on them.
        _stem_last_accessed[job_id] = now
    print(f"[job {job_id}] REHYDRATE → {status} "
          f"(stems={'yes' if stem_keys else 'expired'}, cache={'hit' if cached else 'miss'})")
    return job


@app.route("/api/status/<job_id>")
def job_status(job_id):
    import time as _t
    # A trimmed job is as empty as a missing one: _trim_job_result strips stems
    # and intelligence once the original client has collected them, so a LATER
    # poller — someone opening a shared ?job= link — would otherwise get a
    # "complete" payload with nothing in it. Rehydrating restores the full result
    # from disk; it gets trimmed again on the same cycle.
    if job_id not in jobs or jobs[job_id].get("_trimmed"):
        rehydrated = _rehydrate_job(job_id)
        if rehydrated is None:
            print(f"[job {job_id}] STATUS POLL → unknown job")
            return jsonify({"error": "Unknown job ID"}), 404
        if rehydrated.get("error_step") == "orphaned":
            return jsonify(rehydrated)   # not stored in `jobs` — nothing to poll
    job = jobs[job_id]

    # Watchdog: force-expire stuck jobs.
    # Stall first, absolute ceiling second — see STALL_TIMEOUT_S above for why
    # this measures silence rather than total elapsed time.
    if job.get("status") == "processing" and job.get("_started_at"):
        _now = _t.time()
        stalled = _now - (job.get("_last_progress_at") or job["_started_at"])
        elapsed = _now - job["_started_at"]
        _expire = None
        if stalled > STALL_TIMEOUT_S:
            _expire = f"no progress for {int(stalled)}s (last: {job.get('progress', 'unknown')!r})"
        elif elapsed > JOB_MAX_SECONDS:
            _expire = f"exceeded the {JOB_MAX_SECONDS}s ceiling at {int(elapsed)}s"
        if _expire:
            print(f"[job {job_id}] WATCHDOG: {_expire}, forcing error")
            _msg = f"Processing timed out after {int(elapsed)}s"
            job.update({"status": "error", "error": _msg, "error_step": "timeout"})
            upsert_job_checkpoint(job_id, "error", error=_msg)

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

    # Determine which file to serve — MP3 preferred when available (5MB vs 100MB)
    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        serve_path = mp3_path
        mime = "audio/mpeg"
    elif wav_path.exists() and wav_path.stat().st_size > 0:
        serve_path = wav_path
        mime = "audio/wav"
    else:
        wav_size = wav_path.stat().st_size if wav_path.exists() else -1
        print(f"[audio] MISSING {job_id}/{stem_name} — wav={wav_size}b cwd={Path('.').resolve()} path={wav_path}")
        return jsonify({"error": "stem not ready"}), 404

    import time as _t; _stem_last_accessed[job_id] = _t.time()

    # Stream from disk — avoids loading 100MB+ WAV files into process memory per request.
    # send_file with conditional=True also enables proper Range request support for seeking.
    # Cache headers — stems are immutable per job_id+stem_name
    cache_control = "public, max-age=86400, immutable" if mime == "audio/mpeg" else "public, max-age=3600"

    try:
        file_size = serve_path.stat().st_size
        print(f"[audio] SERVING {job_id}/{stem_name} — {file_size:,}b from {serve_path}")
        resp = make_response(send_file(
            str(serve_path),
            mimetype=mime,
            conditional=True,  # Enables range requests (audio seek without re-downloading)
        ))
        resp.headers["Cache-Control"] = cache_control
        return resp
    except Exception as e:
        print(f"[audio] READ ERROR {job_id}/{stem_name}: {e}")
        return jsonify({"error": "read failed"}), 500



# ── Admin routes ──────────────────────────────────────────────────────────────

def _check_admin_auth():
    """Shared admin auth gate. Returns None if OK, or a Flask response to short-circuit."""
    admin_secret = os.environ.get("ADMIN_SECRET", "").strip()
    if not admin_secret:
        # Fail CLOSED. This used to `return None` (= everyone is admin) when the
        # env var was missing, which left /api/admin/refresh-cookies (spawns
        # Playwright), /cookie-status (dumps cookie names) and /diag-audio
        # (arbitrary yt-dlp fetch, errors echoed back) open to the internet.
        return jsonify({"error": "admin endpoints disabled (ADMIN_SECRET unset)"}), 403
    provided = (
        request.args.get("secret", "")
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    if not hmac.compare_digest(provided, admin_secret):
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.route("/api/admin/refresh-cookies", methods=["POST"])
def admin_refresh_cookies():
    """
    Trigger a Playwright-based YouTube cookie refresh.
    Protected by ADMIN_SECRET env var (pass as ?secret= or Authorization header).
    """
    auth_err = _check_admin_auth()
    if auth_err:
        return auth_err

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


@app.route("/api/admin/cookie-status", methods=["GET"])
def admin_cookie_status():
    """
    Inspect the current cookies.txt: size, count, whether it has login cookies,
    and whether it was self-managed by cookie_refresher (anonymous-only) or
    externally provided (likely authenticated).

    Use this to tell at a glance why label-restricted tracks ("Sign in to confirm")
    might be failing — anonymous-only cookies don't pass that bot wall.
    """
    auth_err = _check_admin_auth()
    if auth_err:
        return auth_err

    cookies_path = Path("cookies.txt")
    if not cookies_path.exists():
        return jsonify({"exists": False, "message": "cookies.txt not found"})

    try:
        text = cookies_path.read_text(errors="replace")
        lines = text.splitlines()
        cookie_lines = [ln for ln in lines if ln and not ln.startswith("#")]
        cookie_names = []
        for ln in cookie_lines:
            parts = ln.split("\t")
            if len(parts) >= 6:
                cookie_names.append(parts[5])

        login_cookie_names = {"LOGIN_INFO", "SAPISID", "SID", "HSID", "SSID", "APISID",
                              "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PSIDTS"}
        has_login_cookies = any(n in login_cookie_names for n in cookie_names)
        self_managed = "# cookie_refresher.py managed: True" in text[:512]

        if self_managed:
            managed_by = "cookie_refresher (anonymous-only — won't pass 'Sign in to confirm' wall)"
        elif has_login_cookies:
            managed_by = "external + authenticated (good — should pass bot detection)"
        else:
            managed_by = "external (anonymous — likely insufficient for label-restricted content)"

        stat = cookies_path.stat()
        from datetime import datetime, timezone
        return jsonify({
            "exists": True,
            "size_bytes": stat.st_size,
            "num_cookies": len(cookie_lines),
            "cookie_names": cookie_names,
            "has_login_cookies": has_login_cookies,
            "self_managed": self_managed,
            "managed_by": managed_by,
            "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/diag-audio", methods=["GET", "POST"])
def admin_diag_audio():
    """
    Run the audio waterfall (yt-dlp → Cobalt → Piped) against a query and
    return per-step success/error/timing as JSON. Lets you see exactly which
    leg failed for any given track without digging through Render logs.

    Usage: GET /api/admin/diag-audio?secret=...&q=Black+Magic+Woman+Santana
    """
    auth_err = _check_admin_auth()
    if auth_err:
        return auth_err

    query = (request.args.get("q") or
             (request.get_json(silent=True) or {}).get("q") or "").strip()
    if not query:
        return jsonify({"error": "missing 'q' query parameter"}), 400

    import time
    import shutil as _shutil
    from downloader import (
        download_audio_from_youtube,
        _download_via_cobalt,
        _download_via_piped,
        UPLOAD_DIR,
    )

    diag_job_id = f"diag-{uuid.uuid4().hex[:8]}"
    out_dir = UPLOAD_DIR / diag_job_id

    def _run_step(name, fn):
        t0 = time.time()
        try:
            path = fn()
            return {
                "step": name,
                "success": True,
                "duration_s": round(time.time() - t0, 2),
                "audio_file": Path(path).name if path else None,
                "size_bytes": Path(path).stat().st_size if path and Path(path).exists() else 0,
            }
        except Exception as e:
            return {
                "step": name,
                "success": False,
                "duration_s": round(time.time() - t0, 2),
                "error": str(e)[:500],
                "error_type": type(e).__name__,
            }

    results = []
    try:
        results.append(_run_step("yt-dlp", lambda: download_audio_from_youtube(query, diag_job_id)))
        if not results[-1]["success"]:
            results.append(_run_step("cobalt", lambda: _download_via_cobalt(query, diag_job_id)))
        if not results[-1]["success"]:
            results.append(_run_step("piped", lambda: _download_via_piped(query, diag_job_id)))
    finally:
        # Clean up the diag directory — we don't keep diagnostic downloads
        if out_dir.exists():
            try:
                _shutil.rmtree(out_dir)
            except Exception:
                pass

    first_success = next((r for r in results if r["success"]), None)
    return jsonify({
        "query": query,
        "first_success": first_success["step"] if first_success else None,
        "would_show_upload_required": first_success is None,
        "steps": results,
    })


# ── Demo routes ────────────────────────────────────────────────────────────────

@app.route("/demo")
def demo():
    rows = get_visible_demo_tracks()
    demo_tracks = [
        {
            "id": r["slug"],
            "name": r["title"],
            "artist": r["artist"],
            "year": r["year"] or "",
            "cover": r["cover_path"] or "",
            "key": r["key_display"] or "",
            "bpm": r["bpm"] or 0,
            "genre": r["genre"] or "",
        }
        for r in rows
    ]
    return render_template("demo.html", active_page="demo", demo_tracks=demo_tracks)


@app.route("/api/demo/<demo_id>")
def get_demo_analysis(demo_id):
    """Serve pre-baked analysis for a demo track. Identical shape to /api/status/<job_id>."""
    safe_id = demo_id.replace("/", "").replace("..", "")
    row = get_demo_track(safe_id)
    if row and row.get("analysis_path"):
        analysis_path = row["analysis_path"]
    else:
        # Fallback to convention-based path for backwards compat
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
    row = get_demo_track(safe_id)
    if row and row.get("stems_dir"):
        stems_dir = row["stems_dir"]
    else:
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
