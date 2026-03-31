# Demo Tab — Full Implementation Prompt

## Context
You are working inside the Riffd Flask web app (`app.py`, `templates/`, `static/`). The app does real-time stem separation via Demucs/Replicate and music analysis. You are adding a **Demo tab** that shows 3 pre-loaded, always-available songs with the identical mixer interface as a real analyzed song — zero load time, no API calls at runtime.

## Final Song List
1. **Take It Easy** — Eagles (1972)
2. **Gravity** — John Mayer (2006)
3. **Bohemian Rhapsody** — Queen

## Target Stem Labels (from verified reference — do not deviate)

### Take It Easy
`vocal`, `backing_vocal`, `acoustic_guitar`, `lead_guitar_1`, `lead_guitar_2`, `banjo`, `bass_guitar`, `drums`

Display labels: Vocal, Backing Vocal, Acoustic Guitar, Lead Guitar 1, Lead Guitar 2, Banjo, Bass Guitar, Drums

### Gravity
`vocal`, `choir`, `lead_guitar`, `solo_guitar`, `additional_guitar_1`, `additional_guitar_2`, `organ`, `bass_guitar`, `drums`

Display labels: Vocal, Choir, Lead Guitar, Solo Guitar, Additional Guitar 1, Additional Guitar 2, Organ, Bass Guitar, Drums

### Bohemian Rhapsody
`vocal`, `backing_vocal_1`, `backing_vocal_2`, `backing_vocal_3`, `backing_vocal_4`, `lead_guitar`, `rhythm_guitar_1`, `rhythm_guitar_2`, `additional_guitar`, `piano`, `bass_guitar`, `gong`, `drums`

Display labels: Vocal, Backing Vocal 1, Backing Vocal 2, Backing Vocal 3, Backing Vocal 4, Lead Guitar, Rhythm Guitar 1, Rhythm Guitar 2, Additional Guitar, Piano, Bass Guitar, Gong, Drums

**Note on Bohemian Rhapsody:** Demucs will not produce 13 separate stems. Map whatever stems Demucs produces to the closest matching labels from the list above. Do your best. You will not get all 13 — that is acceptable. Prioritize: Vocal, Lead Guitar, Piano, Bass Guitar, Drums. Collapse multiple backing vocals into Backing Vocal 1 / Backing Vocal 2 if needed.

---

## Phase 1: Re-process All 3 Songs Through the App

All 3 songs need fresh processing. Bohemian Rhapsody has no cached stems at all. Gravity's existing stems are v3 with poor labels (instrument, texture, keys — too generic). Take It Easy has good stems but needs re-running to get the most accurate separation.

### How to trigger reprocessing

The app must be running. Start it if needed: `python app.py` or however it's configured.

For each song, use the app's own API to trigger analysis:

**Step 1 — Download**
```
POST /api/download
Content-Type: application/json
{
  "track_id": "<spotify_id>",
  "track_name": "<name>",
  "artist_name": "<artist>",
  "yt_query": "<yt_query>",
  "mode": "full"
}
```

**Step 2 — Process** (once status = "ready")
```
POST /api/process/<job_id>
Content-Type: application/json
{
  "analysis_mode": "deep",
  "track_id": "<spotify_id>",
  "track_name": "<name>",
  "artist_name": "<artist>"
}
```

**Step 3 — Poll** until `status = "complete"` or `"partial"`
```
GET /api/status/<job_id>
```

### Song credentials for API calls

**Take It Easy**
- spotify_track_id: `4yugZvBYaoREkJKtbG08Qr`
- track_name: `Take It Easy - 2013 Remaster`
- artist_name: `Eagles`
- yt_query: `Eagles - Take It Easy - 2013 Remaster official audio`

**Gravity**
- spotify_track_id: `3SktMqZmo3M9zbB7oKMIF7`
- track_name: `Gravity`
- artist_name: `John Mayer`
- yt_query: `John Mayer - Gravity official audio`

**Bohemian Rhapsody**
- Look up in `riffd.db`: `SELECT spotify_track_id, yt_query FROM tracks WHERE title='Bohemian Rhapsody' AND artist='Queen' LIMIT 1`
- If not in DB, use:
  - spotify_track_id: search Spotify API via `/api/spotify/search?q=Bohemian+Rhapsody+Queen` and grab the top result's id
  - yt_query: `Queen - Bohemian Rhapsody official video`

**Poll with a 10-second interval. Timeout after 10 minutes per song. If status = "partial", proceed anyway — partial is acceptable for demo.**

---

## Phase 2: Build Static Demo File Structure

After each song completes, copy its stems and analysis into the demo static directory.

### Target structure
```
static/demo/
  take_it_easy/
    cover.jpg          ← download from Spotify CDN
    stems/
      vocal.mp3
      backing_vocal.mp3
      acoustic_guitar.mp3
      lead_guitar_1.mp3
      lead_guitar_2.mp3
      banjo.mp3
      bass_guitar.mp3
      drums.mp3
    analysis.json
  gravity/
    cover.jpg
    stems/
      vocal.mp3
      choir.mp3
      lead_guitar.mp3
      solo_guitar.mp3
      additional_guitar_1.mp3
      additional_guitar_2.mp3
      organ.mp3
      bass_guitar.mp3
      drums.mp3
    analysis.json
  bohemian_rhapsody/
    cover.jpg
    stems/
      vocal.mp3
      backing_vocal_1.mp3
      ... (whatever Demucs produced, mapped to closest label)
      bass_guitar.mp3
      drums.mp3
    analysis.json
```

### Stem file handling

1. Demucs output stems are in `outputs/<job_id>/stems/` as `.wav` or `.mp3`
2. **If WAV**: convert to MP3 at 160kbps using ffmpeg: `ffmpeg -i input.wav -codec:a libmp3lame -b:a 160k output.mp3`
3. **Mapping**: Demucs will output stems with its own names. Map them to the target labels using your best judgment based on:
   - `lead_vocal` / `vocals` → `vocal`
   - `harmony_vocal` / `backing_vocal` / `choir` → `backing_vocal` or `choir`
   - `acoustic_guitar` → `acoustic_guitar`
   - `lead_guitar` → `lead_guitar_1` (for Take It Easy), `lead_guitar` (for Gravity)
   - `lead_guitar_2` / second guitar stem → `lead_guitar_2` or `banjo` (Take It Easy's second guitar is a banjo)
   - `rhythm_guitar` → `banjo` for Take It Easy (it IS a banjo — relabel it)
   - `bass` → `bass_guitar`
   - `drums` → `drums`
   - `keys` / `piano` → `organ` for Gravity, `piano` for Bohemian Rhapsody
   - `instrument` / `other` / `texture` → use context of the song to assign best label
   - If a stem has no good match in the target label list, use the closest one or omit it

4. Copy/rename each stem file to `static/demo/<song_id>/stems/<target_label>.mp3`

### Cover images
Download cover art from the Spotify CDN URLs and save as `cover.jpg`:
- Take It Easy: `https://i.scdn.co/image/ab67616d0000b273c13acd642ba9f6f5f127aa1b`
- Gravity: `https://i.scdn.co/image/ab67616d0000b273ac9fea717d5b78e73cbd89f6`
- Bohemian Rhapsody: grab `artwork_url` from DB after processing

Use: `curl -L "<url>" -o static/demo/<song_id>/cover.jpg`

### analysis.json format

Each `analysis.json` must be shaped **exactly** like a `/api/status/<job_id>` response with `status: "complete"`. Build it from the job's `result_cache.json` but:

1. Replace `stems` dict keys and labels with the corrected target labels (see above)
2. Remove `path` field from each stem entry (not needed by frontend)
3. Set `status: "complete"`
4. Replace any audio path references with `/api/demo/<song_id>/audio/<stem_name>` format
5. Keep all other fields intact: `intelligence`, `lyrics`, `tags`, `insight`, `recommendations`, `audio_source`, `audio_mode`

Example stem entry in analysis.json:
```json
"vocal": {
  "label": "Vocal",
  "energy": 0.088,
  "active": true
}
```

Song IDs to use:
- `take_it_easy`
- `gravity`
- `bohemian_rhapsody`

---

## Phase 3: Backend — New Routes in app.py

Add these three routes to `app.py`. Place them near the other API routes, after the existing `/api/audio/` route.

```python
import json as _json  # only add if not already imported

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
            "bpm": 136,
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
            "key": "",   # fill in after processing
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
        data = _json.load(f)
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
```

**Important**: Check if `json` is already imported in app.py. If it is (likely imported as `json`), use that directly instead of `_json`.

---

## Phase 4: Navigation — base.html

In `templates/base.html`, find this block (around line 160–163):
```html
<a href="/decompose" class="nav-link {% if active_page == 'decompose' %}active{% endif %}">Analyze</a>
<a href="/learn" class="nav-link {% if active_page == 'learn' %}active{% endif %}">Theory</a>
<a href="/about" class="nav-link {% if active_page == 'about' %}active{% endif %}">How It's Built</a>
```

Replace with:
```html
<a href="/decompose" class="nav-link {% if active_page == 'decompose' %}active{% endif %}">Analyze</a>
<a href="/demo" class="nav-link {% if active_page == 'demo' %}active{% endif %}">Demo</a>
<a href="/learn" class="nav-link {% if active_page == 'learn' %}active{% endif %}">Theory</a>
<a href="/about" class="nav-link {% if active_page == 'about' %}active{% endif %}">How It's Built</a>
```

---

## Phase 5: demo.html Template

Create `templates/demo.html`. This page must:

1. Extend `base.html`
2. Show a **song picker** at the top (3 cards)
3. Show the **identical mixer UI** as `decompose.html` below
4. Auto-load the first song (Take It Easy) on page load
5. Preload all 3 songs' stems in the background immediately on page load

### Key requirements for demo.html

**Song picker cards** — each card shows:
- Album art (cover.jpg)
- Song name + artist
- Key chip (e.g. "G Major") and BPM chip if available
- Active state (highlighted border/background) when selected
- Clicking calls `loadDemoTrack(demoId)`

**Mixer UI** — copy the relevant HTML from `decompose.html`:
- `#results-banner` (album art, song name, artist, key/bpm chips)
- `#mixer-channels` (stem channel list)
- `.mixer-transport` (play/pause, full mix, time display)
- All associated CSS

**JS — loadDemoTrack(demoId)**

```javascript
async function loadDemoTrack(demoId) {
    // 1. Mark active card
    document.querySelectorAll('.demo-card').forEach(c => c.classList.remove('active'));
    document.querySelector(`[data-demo-id="${demoId}"]`).classList.add('active');

    // 2. Stop current playback, clear stems
    stopAudio();
    clearStems();
    resetState();

    // 3. Show a brief "loading" visual (150ms) so it doesn't feel broken-instant
    showDemoLoading();
    await new Promise(r => setTimeout(r, 150));

    // 4. Fetch pre-baked analysis (instant — just reading a JSON file)
    const data = await fetch(`/api/demo/${demoId}`).then(r => r.json());

    // 5. Set globals exactly as selectTrack() + renderResults() would
    currentJobId = demoId;
    selectedTrack = demoMeta[demoId];   // populated from Jinja template data

    // 6. Render the full results UI — identical to a real completed analysis
    renderResults(data);   // reuse the exact same function from decompose.html

    hideDemoLoading();
}
```

**JS — Preloading**

On `DOMContentLoaded`, start loading Take It Easy immediately. After a 3-second delay, preload Gravity. After 6 seconds, preload Bohemian Rhapsody. This staggers the network requests so the first song is ready as fast as possible.

The preloading calls `_preloadDemoStems(demoId)` which fetches each stem's audio and decodes it into the Web Audio context buffer, storing it keyed by `demoId + "_" + stemName`. When the user switches songs, buffers are already decoded.

**Important implementation note**: The JS in `demo.html` should reuse as much of `decompose.html`'s JS as possible by extracting it into shared functions. If that's a large refactor, it's acceptable to copy the relevant JS functions (audio playback, stem channel rendering, transport controls) into `demo.html` directly and adapt them for demo mode. Demo mode differences:
- No Spotify search bar
- No "Dive In" / download flow
- No polling — analysis is loaded once, instantly
- `currentJobId` is a demo ID like `"take_it_easy"`, so audio requests go to `/api/demo/<id>/audio/<stem>` instead of `/api/audio/<job_id>/<stem>`

To route audio correctly, add a flag or check: if `currentJobId` starts with a known demo ID (or a `isDemoMode` boolean), use `/api/demo/` URLs for audio fetching. Otherwise use the normal `/api/audio/` URLs.

**demoMeta object** — populate from Jinja-rendered template data:
```javascript
const demoMeta = {
    "take_it_easy": {
        id: "take_it_easy",
        name: "Take It Easy",
        artist: "Eagles",
        year: "1972",
        image_url: "/static/demo/take_it_easy/cover.jpg"
    },
    "gravity": { ... },
    "bohemian_rhapsody": { ... }
};
```

### Styling

The demo page should look polished and premium:

- Song picker cards: dark surface, rounded corners, 1px border, album art ~80×80px, hover state with subtle glow
- Active card: accent-colored border (`var(--accent)`)
- Key/BPM chips on cards: small pill badges, muted text color
- "Demo Mode" subtle label somewhere (e.g. small badge in the results banner or near the top of the mixer)
- The mixer below the cards is visually identical to the real analyze page — no dumbed-down UI

---

## Phase 6: Verify Everything Works

After implementation, verify:

1. `GET /demo` returns 200 with the demo page
2. `GET /api/demo/take_it_easy` returns valid JSON with stems, intelligence, insight
3. `GET /api/demo/take_it_easy/audio/vocal` returns a valid MP3 (200 or 206 for range)
4. `GET /api/demo/gravity/audio/organ` returns a valid MP3
5. Demo tab appears in nav on all pages
6. Clicking a demo card loads the full mixer interface
7. Play button works — audio plays for all 3 songs
8. Mute/Solo buttons work per stem
9. Volume sliders work
10. No console errors

---

## Notes & Edge Cases

- **json import**: `json` is almost certainly already imported in app.py. Use it directly, don't add a duplicate import.
- **send_file conditional**: The `conditional=True` parameter enables HTTP range request support (needed for audio seeking). Double check this is correct for the Flask version in use.
- **Stem count mismatch**: Gravity will likely produce 6-7 stems, not 9. Do not fabricate missing stems. Only include stems that actually exist. Update the analysis.json to only list stems that have audio files.
- **Bohemian Rhapsody BPM**: Leave as 0 or omit if not detected — it's a medley with variable tempo.
- **Cover images**: If the CDN URLs don't serve directly, try downloading via the Python requests library: `requests.get(url, headers={"User-Agent": "Mozilla/5.0"})`.
- **File permissions**: The `static/demo/` directory and all files should be readable by the Flask process.
- **Do not modify the existing processing pipeline** — this feature is purely additive.
- **Do not break the existing `/decompose` page** — the audio URL routing change (demo vs real) must be backward-compatible.
