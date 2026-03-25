# PROJECT_CONTEXT.md — riffa

> Source of truth for the riffa project. Read this before making any changes.
> Last updated: 2026-03-25

---

## Project Overview

**riffa** is a music analysis and learning tool that lets users search for any song, separate it into individual instrument stems, analyze its musical properties, generate tablature, and explore the results in a unified interface.

**Purpose:** Help musicians isolate parts, understand song structure, and practice along — with a premium, production-quality experience.

**Product name:** riffa (lowercase)
**Current stage:** Public beta (local development)
**URL:** http://localhost:5001

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, Flask 3.x |
| Stem separation | Demucs (htdemucs_6stems, fallback to htdemucs 4-stem) |
| Note detection | Basic Pitch (ICASSP 2022 model) |
| Audio download | yt-dlp (YouTube search + download → WAV) |
| Frontend | Single HTML file (vanilla JS, inline CSS, no framework) |
| APIs | Spotify (client credentials), Genius (lyrics), Last.fm (tags + similar tracks) |
| Storage | Local filesystem (uploads/, outputs/, history.json) |
| Task execution | Python threading (no task queue) |

---

## Architecture

```
Flask app (app.py, port 5001)
│
├── Routes
│   ├── GET  /                        → index.html (entire frontend)
│   ├── GET  /api/spotify/search      → Spotify track search
│   ├── POST /api/download            → yt-dlp audio download (async)
│   ├── POST /api/upload              → Direct file upload
│   ├── POST /api/process/<job_id>    → Full processing pipeline (async)
│   ├── GET  /api/status/<job_id>     → Poll job status
│   ├── GET  /api/audio/<job_id>/<stem> → Serve stem WAV files
│   ├── GET  /api/download_midi/<job_id>/<stem> → Download MIDI
│   ├── GET  /api/history             → Recent songs list
│   ├── GET  /api/cache/<track_id>    → Cached result lookup
│   └── GET  /api/discovery           → Static curated song list
│
├── Processing Pipeline (processor.py)
│   ├── Demucs stem separation (6-stem → fallback 4-stem)
│   ├── STFT stereo field analysis (center/left/right panning)
│   ├── Spectral feature extraction + instrument classification
│   └── Basic Pitch note detection → MIDI + CSV + ASCII tab
│
├── Music Intelligence (music_intelligence.py)
│   ├── Key detection (Krumhansl-Schmuckler pitch-class profile correlation)
│   ├── BPM estimation (inter-onset interval histogram)
│   └── Chord progression (template matching against diatonic chords)
│
├── Chord Source (chord_source.py)
│   ├── Web scraping (DuckDuckGo → Ultimate Guitar/chord sites)
│   ├── Chord simplification + parsing
│   ├── Key estimation from chord set
│   └── Fuzzy template matching against canonical progressions
│
├── External APIs (external_apis.py)
│   ├── Genius lyrics (search + scrape with confidence scoring)
│   └── Last.fm similar tracks + tags
│
├── Spotify (spotify_search.py)
│   ├── Client-credentials token management
│   ├── Track search
│   └── Recommendation pools (same artist, year-based, style via Last.fm)
│
├── History + Cache (history.py)
│   ├── JSON file-based song history
│   └── Versioned result caching (outputs/<job_id>/result_cache.json)
│
└── Frontend (templates/index.html — 1257 lines)
    ├── CSS (~400 lines inline)
    ├── HTML views: Search → Confirm → Processing → Results
    ├── Results tabs: Mix | Tab | Lyrics | Similar
    └── JS (~650 lines): search, audio engine, mixer, tab display
```

---

## Key Files and Roles

| File | Lines | Role |
|------|-------|------|
| `app.py` | 323 | Flask server, route definitions, job orchestration, static discovery data |
| `processor.py` | 855 | Core audio pipeline: Demucs, stereo separation, classification, tab generation |
| `music_intelligence.py` | 625 | Key/BPM/chord detection from note data |
| `chord_source.py` | 475 | Web-based chord lookup + functional harmony analysis |
| `spotify_search.py` | 197 | Spotify API wrapper + search-based recommendations |
| `external_apis.py` | 387 | Genius lyrics + Last.fm integration |
| `downloader.py` | 72 | yt-dlp wrapper for YouTube audio download |
| `history.py` | 156 | Song history + result cache persistence |
| `recommendations.py` | 249 | **DEAD CODE** — not imported, has broken imports. Should be deleted. |
| `templates/index.html` | 1257 | Entire frontend: CSS + HTML + JS in one file |

---

## Data Flow (End-to-End)

### 1. Song Selection
- User types in search bar → frontend debounces (450ms) → `GET /api/spotify/search?q=...`
- Spotify client-credentials API returns track metadata (name, artist, album art, year, Spotify ID)
- OR user clicks a discovery card (static data, no API call)
- OR user clicks a history card (checks cache first)
- OR user uploads a file directly

### 2. Audio Acquisition
- Frontend sends `POST /api/download` with `yt_query` (e.g., "Eagles - Hotel California official audio")
- Backend spawns thread: yt-dlp searches YouTube, downloads best audio → converts to WAV
- Frontend polls `GET /api/status/<job_id>` until status = "ready"

### 3. Processing Pipeline
- Frontend sends `POST /api/process/<job_id>` with track metadata
- Backend spawns thread running this sequence:
  1. **Demucs separation** — splits WAV into 6 stems (vocals, drums, bass, guitar, piano, other). Falls back to 4-stem if 6-stem model fails.
  2. **Stereo refinement** — for non-drum/bass stems, STFT analysis splits by panning (center/left/right). Classifies each component (Lead Vocal, Acoustic Guitar, etc.).
  3. **Tab generation** — Basic Pitch runs on each active stem → MIDI + note CSV + ASCII tab text. Renderer chosen by instrument type (guitar tab, bass tab, drum tab, note list).
  4. **Music intelligence** — key detection from all note events, BPM from onset intervals, chord progression from external web source (fallback: audio-based template matching).
  5. **Lyrics** — Genius API search with fuzzy artist+title matching → HTML scrape.
  6. **Tags** — Last.fm top tags for the track.
  7. **Recommendations** — Spotify search for same-artist tracks + year-range tracks, Last.fm for similar-style tracks.
  8. **Cache** — results saved to `outputs/<job_id>/result_cache.json`, history entry added to `history.json`.

### 4. Results Display
- Frontend polls until status = "complete"
- Renders unified results view with tabs:
  - **Mix** — stem audio player with per-channel volume/mute/solo, seek, loop, transpose
  - **Tab** — ASCII tablature for each instrument, MIDI download links
  - **Lyrics** — full lyrics display
  - **Similar** — recommendation cards (More Like This, Similar Style, Around This Time)

### 5. Caching
- On return visit, `pickHistory()` checks `GET /api/cache/<track_id>`
- If cache hit with matching analysis version → loads instantly (no reprocessing)
- If cache miss or version mismatch → full reprocessing

---

## Current Features

### Working
- Spotify search with debounce, caching, rate limit handling
- YouTube audio download via yt-dlp
- Direct file upload (drag & drop or browse)
- 6-stem Demucs separation with 4-stem fallback
- Stereo field analysis splitting stems by panning position
- Spectral instrument classification (Lead Vocal, Acoustic Guitar, Banjo, etc.)
- Basic Pitch note detection → MIDI + CSV + ASCII tab
- Key detection (Krumhansl-Schmuckler)
- BPM estimation with confidence scoring
- Chord progression detection (web scraping + audio fallback)
- Genius lyrics with fuzzy matching
- Last.fm tags and similar tracks
- Spotify-based recommendations (same artist, same era)
- Web Audio API stem mixer with volume/mute/solo
- Seek, loop, transpose controls
- Song history with versioned caching
- Discovery rail with static curated songs
- Landing page with sheet music background, capability band, product preview

### Partially Working
- Chord detection — web scraping is brittle, audio fallback is moderate quality
- Tab quality — limited to first 32 seconds, no rhythm notation
- BPM — works but confidence varies significantly by genre

### Not Working / Missing
- "Connect Spotify" button — placeholder only (shows "Coming soon")
- `recommendations.py` — dead code with broken imports
- No file cleanup — 22GB+ accumulated in outputs/
- No error recovery in frontend if processing fails mid-pipeline
- No mobile-optimized layout

---

## Known Issues / Bugs

1. **`recommendations.py` imports non-existent functions** — `get_compatible_keys` and `COMMON_PROGRESSIONS` do not exist in `music_intelligence.py`. File is not used; should be deleted.
2. **22GB in outputs/ with no cleanup** — grows unbounded.
3. **In-memory job dict (`jobs = {}`)** — all state lost on server restart.
4. **Web chord scraping is fragile** — depends on DuckDuckGo + Ultimate Guitar HTML structure.
5. **No server-side rate limiting** — anyone can spam processing.
6. **API credentials were committed to git** — `.env` is in `.gitignore` but was committed before.

---

## Current Priorities

1. **Fix core output quality** — note detection, tab rendering, drum transcription, stem classification
2. Clean up dead code and environment issues
3. Frontend architecture (split monolithic HTML)
4. Processing UX (error recovery, progress)
5. Infrastructure (disk cleanup, keyboard shortcuts)

Product quality before infrastructure. A polished UI on top of bad output is worse than rough UI on good output.

---

## Product Direction

- **Unified results experience** — one view with tabs, not separate pages
- **Premium feel** — dark theme, glassmorphism, smooth animations
- **Intent-based routing** — clicking "Play" on the landing page opens results on the Tab panel
- **Core loop:** Search → Process → Explore (Mix / Tab / Lyrics / Similar)
- **Target user:** Musicians who want to learn songs by ear, practice along with isolated parts
- **Not a prototype** — every visible element should feel polished and intentional

---

## Critical Assumptions

1. **yt-dlp will continue to work** — YouTube frequently patches against downloaders. This is the single biggest fragility in the pipeline.
2. **Demucs runs on CPU** — no GPU acceleration configured. Processing a 4-minute song takes 2-5 minutes.
3. **Single user at a time** — no concurrency handling, no queue. Multiple simultaneous users would compete for CPU.
4. **Local development only** — no deployment target, no production config, no HTTPS.
5. **Web scraping for chords** — can break any time DuckDuckGo or chord sites change their HTML.

---

## Known Limitations

1. **Tab generation is fundamentally weak.** Basic Pitch is a polyphonic note detector, not a music transcription system. Current output has: ghost notes from low-confidence detections (many notes at 0.25-0.35 confidence rendered as certain), unplayable fret assignments (jumps from fret 0 to fret 17 in one beat), hardcoded 120 BPM quantization regardless of actual tempo, and a 32-second rendering cap.
2. **Drum transcription is nearly broken.** Basic Pitch detects ~122 hits for a 3-minute song (should be 800+). It's fundamentally wrong for percussion — needs onset detection, not pitched note detection.
3. **Stem separation creates phantom instruments.** The stereo panning splitter (STFT Gaussian masks) creates 2-3 stems from a single instrument on narrowly-panned mixes. Spectral classifier uses hardcoded thresholds that misclassify instruments (distorted guitar → "Banjo", etc.).
4. **No real-time processing** — everything is batch. User waits 2-5 minutes per song.
5. **No user accounts** — no way to save preferences, playlists, or share results.
6. **ASCII tab only** — no standard notation, no Guitar Pro format, no interactive tab.
7. **Basic Pitch predict() parameters are not tuned per instrument.** Same settings used for bass (30Hz) and vocals (4000Hz). The `extra_0` column in note CSVs contains confidence scores that are completely ignored.

---

## Where the System is Fragile

| Area | Fragility | Blast Radius |
|------|-----------|-------------|
| yt-dlp | YouTube blocks or changes API | **Entire product breaks** — no audio = nothing works |
| DuckDuckGo scraping | Rate limits, HTML changes | Chord detection falls back to audio (lower quality) |
| Genius scraping | HTML structure changes | Lyrics unavailable (graceful degradation) |
| Demucs model loading | First run downloads ~1GB model | Fails silently if no internet on first run |
| outputs/ disk usage | No cleanup, grows forever | Disk full → all processing fails |
| jobs dict | Server restart | All in-progress jobs lost, no recovery |
| Monolithic HTML | Any CSS/JS change touches one file | High merge conflict risk, hard to test in isolation |

---

## Open Questions

1. Should "Connect Spotify" become a real feature (OAuth user auth, personal playlists) or be removed?
2. What's the deployment target — hosted web app, Electron desktop app, or stay local?
3. Should tab generation target a standard format (MusicXML, Guitar Pro) instead of ASCII?
4. Is the yt-dlp dependency acceptable long-term, or should there be an alternative audio source?
5. Should processing be moved to a cloud service (GPU-accelerated Demucs)?
