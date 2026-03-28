# Claude Code Prompt: Smart Audio Pipeline (Proxy + Background Download + Instant Playback)

## Goal

Overhaul the audio acquisition pipeline so the user **never sees a download step**. When they select a song, they immediately get a 30-second preview playing + instant analysis (key, BPM, lyrics, recommendations). Meanwhile, the full YouTube track downloads silently in the background via a residential proxy. When they click "Separate Stems," the full audio is already on disk — stem separation starts instantly.

This collapses three features into one seamless flow:
1. YouTube proxy integration (bypass CAPTCHA/bot detection)
2. Background full-track download (starts on song select, not on Separate Stems click)
3. Preview playback while downloading (user hears something immediately)

---

## Current Architecture (understand before changing)

### Flow today:
1. User searches → `/api/spotify/search` → Spotify API results
2. User clicks a song → `selectTrack()` in `decompose.html`
   - Checks DB/cache for existing analysis via `/api/track/<id>`
   - If no cache: calls `startProcessing()` → `downloadTrack("preview")` → `/api/download` with `mode=preview`
   - Backend `resolve_preview()` gets 30-sec audio from Spotify/iTunes, auto-triggers instant analysis
   - Frontend renders key, BPM, lyrics, recommendations
3. User clicks "Separate Stems" → `_startDeepAnalysis()`
   - Sets `_currentMode = "full"`, `_analysisMode = "deep"`
   - Calls `startProcessing()` → `downloadTrack("full")` → `/api/download` with `mode=full`
   - Backend `resolve_audio()` tries YouTube first (often fails due to bot detection), falls back to preview
   - If YouTube works: full stem separation via Demucs (~2-5 min)
   - If YouTube fails: user sees "upload required" error

### Key files:
- `downloader.py` — `resolve_preview()`, `resolve_audio()`, `_run_ytdlp()`, `download_audio_from_youtube()`
- `app.py` — `/api/download` route (lines 304-453), `/api/process/<job_id>` route (line 642+), job state management
- `templates/decompose.html` — `selectTrack()` (line 620), `startProcessing()` (line 1069), `downloadTrack()` (line 1177), `_startDeepAnalysis()` (line 1562)

---

## Implementation Plan

### Part 1: Proxy Support in downloader.py

**Modify `_run_ytdlp()` to use a proxy when available.**

In `downloader.py`, update the `_run_ytdlp` function:

```python
import os

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
    ]

    # Add proxy if configured
    proxy_url = os.environ.get("YT_PROXY_URL")
    if proxy_url:
        cmd.extend(["--proxy", proxy_url])
        print(f"[downloader] using proxy: {proxy_url[:30]}...")

    cmd.append(source)

    print(f"[downloader] yt-dlp starting: {source[:80]}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)  # increased timeout for proxy

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[:500]}")

    # Find the downloaded audio file (existing logic unchanged)
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
```

**Changes:**
- Read `YT_PROXY_URL` env var (e.g., `socks5://user:pass@proxy.example.com:1080` or `http://user:pass@proxy.example.com:8080`)
- Pass `--proxy` flag to yt-dlp when set
- Increase subprocess timeout from 120 → 180 seconds (proxy adds latency)
- Log proxy usage for debugging
- Do NOT change `download_audio_from_youtube()`, `resolve_preview()`, or `resolve_audio()` — they call `_run_ytdlp()` internally so they get proxy support automatically

**Add startup logging in `app.py`** near the existing env var prints (around line 67):
```python
print(f"[env] YT_PROXY_URL set: {bool(os.getenv('YT_PROXY_URL'))}")
```

---

### Part 2: Background Download API (app.py)

**Add a new endpoint that starts a YouTube download in the background and returns immediately.** This is separate from the existing `/api/download` endpoint — that one stays unchanged for the normal flow.

Add this new route in `app.py`, near the existing `/api/download` route:

```python
# ─── Background prefetch ─────────────────────────────────────────────────────
# In-memory dict tracking background downloads. Keyed by spotify_track_id.
# Separate from `jobs` — these are invisible to the user until they click Separate Stems.
_bg_downloads = {}
_bg_lock = threading.Lock()

@app.route("/api/prefetch", methods=["POST"])
def prefetch_full_track():
    """
    Start downloading the full YouTube track in the background.
    Called by the frontend immediately when a song is selected.
    Does NOT block the instant analysis flow.
    Returns immediately with {prefetch_id}.

    The frontend can later check /api/prefetch/<prefetch_id>/status
    to see if the download is done before starting deep analysis.
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

    prefetch_id = str(uuid.uuid4())[:8]
    entry = {
        "prefetch_id": prefetch_id,
        "track_id": track_id,
        "status": "downloading",  # downloading → ready | failed
        "audio_path": None,
        "error": None,
    }

    with _bg_lock:
        if track_id:
            _bg_downloads[track_id] = entry
        _bg_downloads[prefetch_id] = entry  # Also index by prefetch_id for status lookups

    print(f"[prefetch {prefetch_id}] starting background download: {yt_query[:60]}")

    def bg_download():
        try:
            track_data = {
                "query": yt_query,
                "preview_url": None,
                "artist": artist,
                "name": name,
            }
            audio_path = resolve_audio(track_data, prefetch_id)
            entry["status"] = "ready"
            entry["audio_path"] = str(audio_path)
            print(f"[prefetch {prefetch_id}] download complete → {audio_path}")
        except AudioUnavailableError as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            print(f"[prefetch {prefetch_id}] download failed (unavailable): {e}")
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            print(f"[prefetch {prefetch_id}] download failed: {e}")

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
```

**Modify the existing `/api/download` route** to check for a completed prefetch before starting a new download. Inside the `download_track()` function in `app.py`, add this check right before the `job_id = str(uuid.uuid4())[:8]` line (around line 346):

```python
    # Check if background prefetch already has the full track ready
    if mode == "full" and track_id:
        with _bg_lock:
            bg = _bg_downloads.get(track_id)
            if bg and bg["status"] == "ready" and bg["audio_path"]:
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
                return jsonify({"job_id": job_id, "mode": mode})
```

This must go AFTER the existing cache check (the `if track_id:` block that checks history) but BEFORE the new job creation. The priority order becomes:
1. Check filesystem cache (existing code)
2. Check background prefetch (new code)
3. Start fresh download (existing code)

**Add cleanup for stale prefetch entries.** Inside `_prune_old_jobs()`, add:
```python
    # Also prune stale prefetch entries (older than 15 minutes)
    with _bg_lock:
        stale_bg = [pid for pid, e in _bg_downloads.items()
                    if e.get("status") in ("ready", "failed") and
                    e.get("_started_at", 0) and (now - e["_started_at"]) > 900]
        for pid in stale_bg:
            del _bg_downloads[pid]
        if stale_bg:
            print(f"[mem] pruned {len(stale_bg)} stale prefetch entries")
```

And add `_started_at` to the prefetch entry creation:
```python
    import time
    entry = {
        "prefetch_id": prefetch_id,
        "track_id": track_id,
        "status": "downloading",
        "audio_path": None,
        "error": None,
        "_started_at": time.time(),
    }
```

---

### Part 3: Frontend Changes (decompose.html)

**Modify `selectTrack()` to kick off the background prefetch immediately when a song is selected.** This runs in parallel with the preview/instant analysis flow — the user doesn't see or wait for it.

In `selectTrack()`, right after the cache check (around line 647, before the `_currentMode = "preview"` line), add:

```javascript
  // ── Background prefetch: start full YouTube download silently ──
  // Runs in parallel with instant analysis. If the download finishes before
  // the user clicks Separate Stems, deep analysis starts with zero wait.
  if (t.yt_query) {
    _prefetchId = null;
    fetch('/api/prefetch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        track_id: t.id || null,
        yt_query: t.yt_query,
        artist: t.artist || '',
        name: t.name || '',
      }),
    })
    .then(r => r.json())
    .then(d => {
      if (d.prefetch_id) {
        _prefetchId = d.prefetch_id;
        console.log(`[prefetch] started: ${d.prefetch_id} (status=${d.status})`);
        if (d.status === 'ready') {
          _prefetchReady = true;
          console.log('[prefetch] already ready (instant cache hit)');
        }
      }
    })
    .catch(e => console.warn('[prefetch] failed to start:', e));
  }
```

**Add state variables** at the top of the script block (near the other state variables around line 1067):

```javascript
let _prefetchId = null;     // Background prefetch ID
let _prefetchReady = false; // Whether prefetch download has completed
```

**Modify `_startDeepAnalysis()`** to check if the prefetch is already done:

```javascript
function _startDeepAnalysis() {
  console.log("[ui] _startDeepAnalysis → switching to full+deep mode");
  _analysisMode = "deep";
  _currentMode = "full";

  // If background prefetch completed, use it directly
  if (_prefetchId && _prefetchReady) {
    console.log("[ui] prefetch already ready — skipping download step");
    // The /api/download endpoint will find the prefetched audio automatically
    // via the track_id lookup in _bg_downloads
  }

  currentJobId = null;  // Force new download (which will find prefetch if ready)
  startProcessing();
}
```

**Also modify `downloadTrack()`** to poll the prefetch status when in full mode, so it can show a shorter/different loading state if the download is almost done. In the `downloadTrack()` function, right before the `fetch("/api/download", ...)` call (around line 1206), add this check:

```javascript
  // If background prefetch is running, poll it briefly before starting a fresh download.
  // This avoids a duplicate download if the prefetch is about to finish.
  if (mode === "full" && _prefetchId && !_prefetchReady) {
    try {
      const pfResp = await fetch(`/api/prefetch/${_prefetchId}/status`);
      const pfData = await pfResp.json();
      console.log(`[downloadTrack] prefetch status: ${pfData.status}`);
      if (pfData.status === 'ready') {
        _prefetchReady = true;
        // Let the /api/download endpoint pick up the prefetched file
      } else if (pfData.status === 'downloading') {
        // Prefetch still running — update loading message
        if (_analysisMode === "deep") {
          _setLT("Preparing full track audio...");
        }
      }
      // If failed, fall through to normal download
    } catch (e) {
      console.warn('[downloadTrack] prefetch status check failed:', e);
    }
  }
```

**Pass `image_url` and `duration_ms` in the prefetch too** — update the `selectTrack()` prefetch `fetch` body to include them, in case the backend needs them later for the download job metadata.

**Reset prefetch state** when selecting a new track. This is already handled by the `_prefetchId = null` at the top of the prefetch block, but also add at the top of `selectTrack()`:

```javascript
_prefetchId = null;
_prefetchReady = false;
```

These should go right after the existing state resets on line 621 (`selectedTrack=t; currentJobId=null; ...`).

---

### Part 4: Analytics Logging (app.py)

**Add basic event logging** so Dylan can understand how users interact with the pipeline. Create a lightweight analytics system that writes to a JSON lines file.

Create a new file `analytics.py`:

```python
"""
analytics.py — Lightweight event logging for Riffd.
Writes JSON lines to data/analytics.jsonl. No external dependencies.
"""

import json
import time
import threading
from pathlib import Path

_LOG_PATH = Path("data/analytics.jsonl")
_lock = threading.Lock()

def log_event(event_type, properties=None):
    """
    Log an analytics event.

    event_type: string like "song_selected", "stems_requested", "prefetch_hit", etc.
    properties: optional dict of event-specific data
    """
    entry = {
        "ts": time.time(),
        "event": event_type,
    }
    if properties:
        entry["props"] = properties

    try:
        with _lock:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[analytics] write failed: {e}")
```

**Add logging calls** at key points in `app.py`:

```python
from analytics import log_event
```

Add these calls at strategic points (do NOT restructure existing code, just add log_event calls):

1. **Song selected** — in `/api/download` route, at the start:
   ```python
   log_event("download_start", {"mode": mode, "artist": data.get("artist", "")[:30], "track": data.get("name", "")[:40]})
   ```

2. **Prefetch started** — in `/api/prefetch`, after creating the entry:
   ```python
   log_event("prefetch_start", {"track": name[:40], "artist": artist[:30]})
   ```

3. **Prefetch hit** — in `/api/download`, when reusing prefetch audio:
   ```python
   log_event("prefetch_hit", {"track_id": track_id})
   ```

4. **Deep analysis requested** — in `/api/process/<job_id>`, in the deep branch:
   ```python
   log_event("deep_analysis_start", {"job_id": job_id, "audio_source": job.get("audio_source")})
   ```

5. **Analysis complete** — in `_finalize()` inside the deep processing thread:
   ```python
   log_event("analysis_complete", {"job_id": job_id, "status": result["status"], "elapsed": round(_time.time() - _t0, 1)})
   ```

6. **YouTube download failed** — in the `except` blocks in the download thread:
   ```python
   log_event("youtube_failed", {"job_id": job_id, "error": str(e)[:100]})
   ```

---

### Part 5: Shareable Analysis Links

**Add a shareable URL for completed analyses** so users can send a link that shows key, BPM, chord progression, and recommendations without the recipient needing to re-analyze.

**Add a new route in `app.py`:**

```python
@app.route("/s/<track_id>")
def shared_analysis(track_id):
    """
    Shareable analysis page. Shows cached analysis results for a track.
    Accessible without re-running analysis.
    """
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
```

**This route must be placed AFTER the `require_login` before_request handler** — but shared links should be publicly accessible. Add `/s/` to the public paths:

```python
AUTH_PUBLIC_PATHS = ("/login", "/static/", "/", "/favicon.ico", "/s/")
```

And update the `require_login` function's `is_public` check:
```python
is_public = (path == "/login" or path == "/" or path == "/favicon.ico" or
             path.startswith("/static/") or path.startswith("/s/"))
```

**Create `templates/shared.html`:**

This is a read-only view. No audio playback, no stems, no mixer. Just the analysis summary and a CTA to try riffd themselves.

```html
{% extends "base.html" %}
{% block title %}{{ track.title }} by {{ track.artist }} — Riffd{% endblock %}

{% block extra_css %}
  .shared-wrap { max-width:640px; margin:0 auto; padding:80px 24px 60px; }
  .shared-art { width:180px; height:180px; object-fit:cover; margin-bottom:24px; }
  .shared-title { font-size:2rem; font-weight:500; color:#F5F5F5; letter-spacing:-0.03em; margin-bottom:4px; }
  .shared-artist { font-size:1.125rem; color:rgba(245,245,245,0.5); margin-bottom:32px; }
  .shared-meta { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:40px; }
  .shared-chip { padding:6px 14px; font-size:0.875rem; border:1px solid rgba(255,255,255,0.1); color:rgba(245,245,245,0.7); }
  .shared-section { margin-bottom:32px; }
  .shared-section-label { font-size:0.6875rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; color:#D4691F; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid rgba(212,105,31,0.15); }
  .shared-recs { display:flex; flex-direction:column; gap:8px; }
  .shared-rec { padding:10px 16px; border:1px solid rgba(255,255,255,0.06); background:rgba(245,245,245,0.02); }
  .shared-rec-title { font-size:0.875rem; font-weight:500; color:rgba(245,245,245,0.8); }
  .shared-rec-artist { font-size:0.8125rem; color:rgba(245,245,245,0.35); }
  .shared-rec-reason { font-size:0.75rem; color:rgba(212,105,31,0.6); font-style:italic; margin-top:4px; }
  .shared-cta { text-align:center; margin-top:48px; padding-top:32px; border-top:1px solid rgba(255,255,255,0.06); }
  .shared-cta-btn { display:inline-block; padding:12px 32px; font-size:0.9375rem; color:#F5F5F5; border:1px solid rgba(255,255,255,0.2); text-decoration:none; transition:all .2s; }
  .shared-cta-btn:hover { border-color:#D4691F; color:#D4691F; }
  .shared-insight { font-size:0.9375rem; color:rgba(245,245,245,0.6); line-height:1.7; margin-bottom:32px; padding:16px 20px; border-left:2px solid rgba(212,105,31,0.3); }
  @media(max-width:480px) {
    .shared-wrap { padding:48px 16px 40px; }
    .shared-title { font-size:1.5rem; }
    .shared-art { width:140px; height:140px; }
  }
{% endblock %}

{% block body %}
<div class="shared-wrap">
  {% if track.artwork_url %}
    <img class="shared-art" src="{{ track.artwork_url }}" alt="" />
  {% endif %}
  <div class="shared-title">{{ track.title }}</div>
  <div class="shared-artist">{{ track.artist }}{% if track.year %} · {{ track.year }}{% endif %}</div>

  <div class="shared-meta">
    {% if analysis.intelligence %}
      {% if analysis.intelligence.key and analysis.intelligence.key != 'Unknown' %}
        <span class="shared-chip">Key: {{ analysis.intelligence.key }}</span>
      {% endif %}
      {% if analysis.intelligence.bpm and analysis.intelligence.bpm > 0 %}
        <span class="shared-chip">{{ analysis.intelligence.bpm }} BPM</span>
      {% endif %}
    {% endif %}
    {% for tag in (analysis.tags or [])[:4] %}
      <span class="shared-chip">{{ tag }}</span>
    {% endfor %}
  </div>

  {% if analysis.insight %}
    <div class="shared-insight">{{ analysis.insight }}</div>
  {% endif %}

  {% set recs = analysis.get('recommendations', {}) %}
  {% set smart_recs = recs.get('smart_recs', []) %}
  {% if smart_recs %}
    <div class="shared-section">
      <div class="shared-section-label">Songs with similar musical DNA</div>
      <div class="shared-recs">
        {% for rec in smart_recs[:8] %}
          <div class="shared-rec">
            <span class="shared-rec-title">{{ rec.title }}</span>
            <span class="shared-rec-artist">{{ rec.artist }}</span>
            {% if rec.reason %}<div class="shared-rec-reason">{{ rec.reason }}</div>{% endif %}
          </div>
        {% endfor %}
      </div>
    </div>
  {% endif %}

  <div class="shared-cta">
    <div style="font-size:0.875rem;color:rgba(245,245,245,0.4);margin-bottom:12px">Analyzed with Riffd</div>
    <a class="shared-cta-btn" href="/decompose">Try it yourself →</a>
  </div>
</div>
{% endblock %}
```

**Create `templates/shared_404.html`:**

```html
{% extends "base.html" %}
{% block title %}Not Found — Riffd{% endblock %}
{% block body %}
<div style="max-width:480px;margin:0 auto;padding:120px 24px;text-align:center">
  <div style="font-size:1.5rem;font-weight:500;color:#F5F5F5;margin-bottom:12px">Analysis not found</div>
  <div style="font-size:0.9375rem;color:rgba(245,245,245,0.5);margin-bottom:32px">This song hasn't been analyzed yet, or the analysis has expired.</div>
  <a href="/decompose" style="color:#D4691F;text-decoration:none;font-size:0.9375rem">Go to Decompose →</a>
</div>
{% endblock %}
```

**Add a "Share" button to the instant results view** in `decompose.html`. In the `renderInstantResults()` function, find where the "Separate Stems" button is rendered, and add a share button next to it:

Find the spot where the full-track button is created (inside `renderInstantResults`, look for where `fullBtn` or `Separate Stems` is set up). After that button, add a share button:

```javascript
// Share button — only show if we have a track ID
if (selectedTrack && selectedTrack.id) {
  const shareUrl = `${window.location.origin}/s/${selectedTrack.id}`;
  const shareBtn = document.createElement('button');
  shareBtn.className = 'new-song-btn';
  shareBtn.style.cssText = 'font-size:0.8125rem; padding:8px 20px;';
  shareBtn.textContent = 'Share Analysis';
  shareBtn.onclick = () => {
    navigator.clipboard.writeText(shareUrl).then(() => {
      shareBtn.textContent = 'Link Copied!';
      setTimeout(() => { shareBtn.textContent = 'Share Analysis'; }, 2000);
    }).catch(() => {
      // Fallback for mobile
      prompt('Copy this link:', shareUrl);
    });
  };
  // Append shareBtn to the same container as the Separate Stems button
  const newSongRow = document.querySelector('.new-song-row');
  if (newSongRow) newSongRow.appendChild(shareBtn);
}
```

Also add share functionality to the deep analysis results in `renderResults()` using the same pattern.

---

## Environment Variable

Add `YT_PROXY_URL` to your `.env` file and Render environment:

```
YT_PROXY_URL=socks5://user:pass@proxy.example.com:1080
```

Format depends on provider:
- Smartproxy: `http://user:pass@gate.smartproxy.com:10001`
- Oxylabs: `http://user:pass@pr.oxylabs.io:7777`
- BrightData: `http://user:pass@brd.superproxy.io:22225`

If `YT_PROXY_URL` is not set, yt-dlp runs without a proxy (existing behavior, YouTube may fail on Render).

---

## Files to Create

| File | Description |
|------|-------------|
| `analytics.py` | Lightweight JSON lines event logger |
| `templates/shared.html` | Public shareable analysis page |
| `templates/shared_404.html` | 404 for expired/missing shared analyses |

## Files to Modify

| File | What changes |
|------|-------------|
| `downloader.py` | Add `--proxy` flag to `_run_ytdlp()`, increase timeout |
| `app.py` | Add `/api/prefetch` endpoint, prefetch check in `/api/download`, `/s/<track_id>` route, analytics imports, public path for `/s/`, startup logging for proxy |
| `templates/decompose.html` | Add prefetch call in `selectTrack()`, prefetch state vars, prefetch status check in `downloadTrack()`, share button in results |

## Do NOT Change

- `resolve_preview()` — preview path stays fast and unchanged
- `resolve_audio()` waterfall logic — it already handles YouTube → preview fallback
- Existing cache/history/DB logic
- Instant analysis auto-trigger in preview download
- Any templates other than `decompose.html` (and the new shared templates)
- The `insight.py` or `theory_search.py` files
- Any CSS on desktop — mobile changes only where needed for share button

---

## Testing Checklist

1. **No proxy set** → yt-dlp should work exactly as before (no `--proxy` flag passed)
2. **Proxy set** → yt-dlp should pass `--proxy` flag, log it, and download succeeds
3. **Select a song** → instant analysis renders immediately, console shows `[prefetch] started: <id>`
4. **Wait 30-60s then click Separate Stems** → console shows `DOWNLOAD REUSED from prefetch`, deep analysis starts instantly (no download wait)
5. **Click Separate Stems before prefetch finishes** → normal download flow kicks in (prefetch status is "downloading", falls through to fresh download)
6. **Prefetch fails** → no user-visible error, normal download flow on Separate Stems click
7. **Select a different song while prefetch is running** → old prefetch is abandoned (stale), new one starts
8. **Shared link `/s/<track_id>`** → shows analysis page without login
9. **Shared link for non-existent track** → shows 404 page
10. **Share button** → copies link to clipboard, shows "Link Copied!" feedback
11. **Analytics** → `data/analytics.jsonl` grows with events, doesn't crash if data/ dir missing
12. **Memory** → stale prefetch entries cleaned up after 15 minutes by `_prune_old_jobs()`
13. **Duplicate prefetch** → selecting same song twice doesn't start two downloads
14. **Mobile** → share button and shared page render properly on phones
