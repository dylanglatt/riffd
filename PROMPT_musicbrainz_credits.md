# Claude Code Prompt: MusicBrainz Credits Tab

## Add a "Credits" tab showing producer, engineer, studio, label, and release info from MusicBrainz.

---

## Part 1: New Backend Module — `musicbrainz.py`

### Create a new file `musicbrainz.py` in the project root (same directory as `app.py`).

This module handles all MusicBrainz API interaction. It should be self-contained — no imports from other project modules.

```python
"""
musicbrainz.py — MusicBrainz recording credits lookup for Riffd.

Entry point:
  get_credits(artist: str, track_name: str) -> dict | None

Returns structured credits dict or None if lookup fails.
No API key needed — just a User-Agent header.
Rate limited to 1 request/second (MusicBrainz requirement).
"""

import time
import requests

_BASE_URL = "https://musicbrainz.org/ws/2"
_HEADERS = {
    "User-Agent": "Riffd/1.0 ( dylanglatt@gmail.com )",
    "Accept": "application/json",
}
_last_request_time = 0


def _rate_limit():
    """Enforce 1 request/second to comply with MusicBrainz rate limits."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_request_time = time.time()


def _search_recording(artist: str, title: str) -> str | None:
    """
    Search MusicBrainz for a recording matching artist + title.
    Returns the recording MBID (ID) of the best match, or None.
    """
    query = f'artist:"{artist}" AND recording:"{title}"'
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/recording",
            params={"query": query, "fmt": "json", "limit": 5},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            print("[musicbrainz] rate limited — skipping")
            return None
        resp.raise_for_status()
        recordings = resp.json().get("recordings", [])
        if not recordings:
            print(f"[musicbrainz] no results for: {artist} - {title}")
            return None

        # Try to find exact match first
        artist_lower = artist.lower()
        title_lower = title.lower()
        for rec in recordings:
            rec_title = (rec.get("title") or "").lower()
            rec_artists = " ".join(
                (ac.get("name") or ac.get("artist", {}).get("name", "")).lower()
                for ac in rec.get("artist-credit", [])
            )
            if title_lower in rec_title and artist_lower in rec_artists:
                print(f"[musicbrainz] matched: {rec.get('title')} (id={rec['id'][:8]})")
                return rec["id"]

        # Fallback to first result
        first = recordings[0]
        print(f"[musicbrainz] fallback match: {first.get('title')} (id={first['id'][:8]})")
        return first["id"]

    except Exception as e:
        print(f"[musicbrainz] search failed: {e}")
        return None


def _get_recording_relationships(recording_id: str) -> dict | None:
    """Fetch recording with artist relationships, work relationships, and releases."""
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/recording/{recording_id}",
            params={"inc": "artist-credits+artist-rels+work-rels+releases", "fmt": "json"},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[musicbrainz] recording lookup failed: {e}")
        return None


def _get_release_details(release_id: str) -> dict | None:
    """Fetch release with label info."""
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/release/{release_id}",
            params={"inc": "labels+release-groups", "fmt": "json"},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[musicbrainz] release lookup failed: {e}")
        return None


def get_credits(artist: str, track_name: str) -> dict | None:
    """
    Main entry point. Search for a recording, fetch its relationships, and return
    structured credits data.

    Returns dict with keys:
      - producers: [{"name": str, "type": str}]   (type: "producer", "executive producer", etc.)
      - engineers: [{"name": str, "role": str}]    (role: "recording", "mix", "mastering", etc.)
      - studios: [str]                              (recording location names)
      - writers: [str]                              (songwriter/composer names from work rels)
      - performers: [{"name": str, "instrument": str}]  (session musicians)
      - label: str | None                           (record label name)
      - release_date: str | None                    (earliest release date, YYYY or YYYY-MM-DD)
      - release_country: str | None                 (ISO country code)
      - album: str | None                           (release/album title)

    Returns None if lookup fails entirely.
    """
    if not artist or not track_name:
        return None

    print(f"[musicbrainz] looking up: {artist} - {track_name}")

    # Step 1: Find the recording
    recording_id = _search_recording(artist, track_name)
    if not recording_id:
        return None

    # Step 2: Get relationships
    data = _get_recording_relationships(recording_id)
    if not data:
        return None

    credits = {
        "producers": [],
        "engineers": [],
        "studios": [],
        "writers": [],
        "performers": [],
        "label": None,
        "release_date": None,
        "release_country": None,
        "album": None,
    }

    # Parse artist relationships
    for rel in data.get("relations", []):
        rel_type = (rel.get("type") or "").lower()
        target_artist = rel.get("artist", {}).get("name") or rel.get("target", {}).get("name", "")
        attributes = [a.lower() for a in rel.get("attributes", [])]

        if not target_artist and rel.get("place"):
            # Studio / recording location
            place_name = rel["place"].get("name", "")
            if place_name and rel_type in ("recorded at", "recording location", "recorded in"):
                if place_name not in credits["studios"]:
                    credits["studios"].append(place_name)
            continue

        if not target_artist:
            continue

        # Producers
        if "producer" in rel_type:
            prod_type = rel_type.replace("_", " ").title()
            if not any(p["name"] == target_artist for p in credits["producers"]):
                credits["producers"].append({"name": target_artist, "type": prod_type})

        # Engineers
        elif rel_type in ("engineer", "audio", "sound", "editor"):
            role = ", ".join(attributes) if attributes else rel_type
            if not any(e["name"] == target_artist for e in credits["engineers"]):
                credits["engineers"].append({"name": target_artist, "role": role})

        # Mix / mastering (often tagged as engineer with attributes)
        elif rel_type == "mix" or "mix" in attributes:
            if not any(e["name"] == target_artist for e in credits["engineers"]):
                credits["engineers"].append({"name": target_artist, "role": "mix"})
        elif rel_type == "mastering" or "mastering" in attributes:
            if not any(e["name"] == target_artist for e in credits["engineers"]):
                credits["engineers"].append({"name": target_artist, "role": "mastering"})

        # Performers / session musicians
        elif rel_type in ("performer", "instrument", "vocal", "programming"):
            instrument = ", ".join(attributes) if attributes else rel_type
            if not any(p["name"] == target_artist for p in credits["performers"]):
                credits["performers"].append({"name": target_artist, "instrument": instrument})

        # Writers/composers (from work relationships)
        elif rel_type in ("writer", "composer", "lyricist", "songwriter"):
            if target_artist not in credits["writers"]:
                credits["writers"].append(target_artist)

    # Also check work-level relationships for writers
    for rel in data.get("relations", []):
        if rel.get("type") == "performance" and rel.get("work"):
            work = rel["work"]
            # Work relations contain writer info — but we'd need another API call
            # For now, we note the work title for reference
            pass

    # Step 3: Get release info (use earliest release)
    releases = data.get("releases", [])
    if releases:
        # Sort by date to find earliest
        dated = [r for r in releases if r.get("date")]
        if dated:
            dated.sort(key=lambda r: r["date"])
            earliest = dated[0]
        else:
            earliest = releases[0]

        credits["album"] = earliest.get("title")
        credits["release_date"] = earliest.get("date")
        credits["release_country"] = earliest.get("country")

        # Fetch label from release details
        release_id = earliest.get("id")
        if release_id:
            release_data = _get_release_details(release_id)
            if release_data:
                label_info = release_data.get("label-info", [])
                if label_info:
                    label_name = label_info[0].get("label", {}).get("name")
                    if label_name:
                        credits["label"] = label_name

    # Check if we actually got anything useful
    has_data = any([
        credits["producers"],
        credits["engineers"],
        credits["studios"],
        credits["writers"],
        credits["performers"],
        credits["label"],
        credits["release_date"],
    ])

    if not has_data:
        print(f"[musicbrainz] no credits data found for: {artist} - {track_name}")
        return None

    print(f"[musicbrainz] credits found: {sum(len(v) for v in credits.values() if isinstance(v, list))} entries")
    return credits
```

---

## Part 2: Integrate into the Processing Pipeline

### File: `app.py`

**Add import at the top** (near the other external API imports, around line 54):

```python
from musicbrainz import get_credits
```

**Add MusicBrainz lookup to `_process_instant()`** (around line 710, after the tags fetch):

Find this block:
```python
    # Tags (fast HTTP call)
    if artist and track_name:
        try:
            tags = get_track_tags(artist, track_name)
            print(f"[job {job_id}] instant: tags={tags}")
        except Exception:
            pass
```

Add right after it:
```python
    # MusicBrainz credits (2-3 API calls, ~3-4s due to rate limiting)
    credits = None
    if artist and track_name:
        try:
            credits = get_credits(artist, track_name)
            print(f"[job {job_id}] instant: credits={'found' if credits else 'none'}")
        except Exception as e:
            print(f"[job {job_id}] instant: credits failed: {e}")
```

**Add `credits` to the instant result dict** (around line 731). Find the `result = {` block and add `"credits": credits,` alongside the other fields:

```python
    result = {
        "status": "complete",
        "analysis_mode": "instant",
        ...
        "tags": tags,
        "credits": credits,    # <-- ADD THIS
        "insight": insight_text,
        ...
    }
```

**Also add `credits` to the cache save** (around line 752). In the `save_cached_result()` call, add `"credits": credits,` to the dict.

**Add MusicBrainz lookup to the deep analysis pipeline** (in the `run()` function, around line 1070-1080 — after the tags fetch in the deep path):

Find where tags are fetched in the deep pipeline (should be a similar pattern). Add after it:
```python
            # ── Stage: MusicBrainz credits ──
            on_progress("Fetching credits...")
            try:
                from musicbrainz import get_credits
                credits_data = get_credits(artist_name, track_name)
                if credits_data:
                    print(f"[job {job_id}] [{_elapsed()}] credits found")
            except Exception as e:
                _fail("credits", e)
                credits_data = None
```

**Add `credits_data` to the deep analysis `_finalize()` result dict.** Find where the result dict is built in `_finalize()` (around line 897). Add `"credits": credits_data,` alongside `"tags"`, `"lyrics"`, etc. Also initialize `credits_data = None` at the top of `run()` near the other initializations (around line 880).

**Add credits to the deep cache save** as well (in `_finalize()`'s `save_cached_result()` call, around line 921). Add `"credits": credits_data,`.

---

## Part 3: New API Endpoint for Credits

### File: `app.py`

Add a dedicated endpoint after the existing `/api/track/<track_id>` route (or anywhere convenient). This lets the frontend fetch credits separately if needed:

```python
@app.route("/api/credits/<job_id>")
def get_job_credits(job_id):
    """Return MusicBrainz credits for a job."""
    job = jobs.get(job_id)
    if job and job.get("credits"):
        return jsonify(job["credits"])
    return jsonify(None), 404
```

---

## Part 4: Frontend — Credits Tab

### File: `templates/decompose.html`

**Add the Credits tab button** in the results nav (around line 558-562). Add after the Recommended button:

```html
<button class="results-nav-btn" data-panel="credits" onclick="switchPanel('credits',this)">Credits</button>
```

**Add the Credits panel div** after the recommended panel div (around line 600):

```html
<div class="results-panel" id="panel-credits">
  <div id="credits-content"></div>
</div>
```

**Add CSS for the Credits panel.** Add this in the `<style>` block alongside the other panel styles (near the `.smart-recs` styles around line 271). These classes follow the exact same design language as the Recommended and Lyrics panels — same font sizes, colors, spacing, uppercase section labels, and border treatments:

```css
/* ═══ Credits panel ═══ */
.credits-wrap { padding:32px 36px; max-width:700px; }
.credits-release { margin-bottom:28px; padding-bottom:20px; border-bottom:1px solid rgba(212,105,31,0.15); }
.credits-release-label { font-size:0.8125rem; color:rgba(245,245,245,0.4); margin-bottom:4px; }
.credits-release-title { font-size:1.125rem; font-weight:500; color:#F5F5F5; letter-spacing:-0.02em; }
.credits-release-meta { font-size:0.8125rem; color:rgba(245,245,245,0.4); margin-top:6px; }
.credits-section { margin-bottom:24px; }
.credits-section:last-child { margin-bottom:0; }
.credits-section-label { font-size:0.6875rem; font-weight:600; color:rgba(245,245,245,0.3); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:10px; }
.credits-entry { display:flex; justify-content:space-between; align-items:baseline; padding:6px 16px; border:1px solid rgba(255,255,255,0.04); background:rgba(245,245,245,0.015); margin-bottom:4px; transition:border-color 0.15s; }
.credits-entry:hover { border-color:rgba(255,255,255,0.1); }
.credits-name { font-size:0.875rem; font-weight:500; color:rgba(245,245,245,0.75); }
.credits-role { font-size:0.75rem; color:rgba(245,245,245,0.3); margin-left:12px; white-space:nowrap; }
.credits-simple { font-size:0.875rem; color:rgba(245,245,245,0.75); padding:6px 16px; border:1px solid rgba(255,255,255,0.04); background:rgba(245,245,245,0.015); margin-bottom:4px; transition:border-color 0.15s; }
.credits-simple:hover { border-color:rgba(255,255,255,0.1); }
.credits-empty { padding:48px 20px; text-align:center; color:rgba(245,245,245,0.3); font-size:0.875rem; }
@media (max-width:768px) {
  .credits-wrap { padding:24px 16px; }
  .credits-entry { flex-direction:column; gap:2px; }
  .credits-role { margin-left:0; }
}
@media (max-width:480px) {
  .credits-wrap { padding:20px 12px; }
}
```

**Add the render function.** Place it near the other render functions (e.g., near `_renderSmartRecs`):

```javascript
function _renderCredits(credits) {
  const el = document.getElementById('credits-content');
  if (!el) return;

  if (!credits) {
    el.innerHTML = '<div class="credits-empty">Credits not available for this track.</div>';
    return;
  }

  let html = '<div class="credits-wrap">';

  // Release info header
  if (credits.album || credits.release_date || credits.label) {
    html += '<div class="credits-release">';
    if (credits.album) {
      html += `<div class="credits-release-label">From the album</div>`;
      html += `<div class="credits-release-title">${esc(credits.album)}</div>`;
    }
    const metaParts = [];
    if (credits.release_date) metaParts.push(credits.release_date.substring(0, 4));
    if (credits.label) metaParts.push(credits.label);
    if (credits.release_country) metaParts.push(credits.release_country);
    if (metaParts.length) {
      html += `<div class="credits-release-meta">${esc(metaParts.join(' \u00b7 '))}</div>`;
    }
    html += '</div>';
  }

  // Helper to render a credits section
  function section(title, items) {
    if (!items || !items.length) return '';
    let s = '<div class="credits-section">';
    s += `<div class="credits-section-label">${title}</div>`;
    for (const item of items) {
      if (typeof item === 'string') {
        s += `<div class="credits-simple">${esc(item)}</div>`;
      } else if (item.name) {
        const detail = item.type || item.role || item.instrument || '';
        s += '<div class="credits-entry">';
        s += `<span class="credits-name">${esc(item.name)}</span>`;
        if (detail) s += `<span class="credits-role">${esc(detail)}</span>`;
        s += '</div>';
      }
    }
    s += '</div>';
    return s;
  }

  html += section('Produced by', credits.producers);
  html += section('Engineering', credits.engineers);
  html += section('Recorded at', credits.studios);
  html += section('Written by', credits.writers);
  html += section('Musicians', credits.performers);

  html += '</div>';
  el.innerHTML = html;
}
```

**Call `_renderCredits()` in `renderInstantResults()`** (around line 1850, near where `_renderSmartRecs` is called):

```javascript
_renderCredits(data.credits || null);
```

**Call `_renderCredits()` in `renderResults()` (for cached deep analysis)** — in the same area where other panels are populated:

```javascript
_renderCredits(data.credits || null);
```

**Call `_renderCredits()` in the stem poll completion handler** (around line 823, where `d.insight` is processed). Add:

```javascript
if (d.credits) _renderCredits(d.credits);
```

**Clear credits in `resetApp()`** — add to the cleanup chain:

```javascript
document.getElementById("credits-content").innerHTML = "";
```

**Add mobile CSS for the credits panel** (in the responsive media queries). The credits panel uses inline styles so it should be fine, but make sure the max-width works on mobile. Inside the `@media (max-width: 600px)` block, add:

```css
#panel-credits > div { padding:20px 12px !important; }
```

---

## Part 5: Include credits in the track cache lookup

### File: `app.py`

In the `/api/track/<track_id>` route (around line 288), the `get_analysis_for_track()` already returns whatever was cached. Since we're adding `credits` to the cache save in both instant and deep paths, it should flow through automatically. No changes needed here — just verify `credits` is included in the cache.

---

## Verification Checklist

1. **New file exists**: `musicbrainz.py` should be in the project root alongside `app.py`.
2. **Import works**: `python -c "from musicbrainz import get_credits"` — no errors.
3. **Credits in instant results**: Grep for `"credits"` in `app.py` — should appear in `_process_instant()` result dict and cache save.
4. **Credits in deep results**: Grep for `credits_data` in `app.py` — should appear in `run()` and `_finalize()`.
5. **Frontend tab**: Grep for `panel-credits` in `decompose.html` — should appear in HTML and be reachable via `switchPanel('credits')`.
6. **Render function**: Grep for `_renderCredits` in `decompose.html` — should be called in `renderInstantResults()`, `renderResults()`, and the stem poll completion handler.
7. **No regressions**: The existing tabs (Mix, Key, Lyrics, Recommended) must still work. `switchPanel()` is generic and doesn't need changes.

---

## Files Modified (summary)

| File | What Changed | Why |
|------|-------------|-----|
| `musicbrainz.py` (NEW) | MusicBrainz API module: search, relationships, release lookup | Self-contained credits data source |
| `app.py` | Import + credits lookup in instant + deep paths, new `/api/credits` endpoint | Fetch and serve credits data |
| `templates/decompose.html` | New "Credits" tab button + panel + `_renderCredits()` function | Display producer/engineer/studio/label info |
