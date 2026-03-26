# PROJECT_CONTEXT.md — Riffd

> Source of truth for the Riffd project. Read this before making any changes.
> Last updated: 2026-03-26

---

## Project Overview

**Riffd** is a music analysis and learning tool that lets users search for any song, separate it into individual instrument stems, analyze its harmonic structure, generate tablature, and explore the results in a unified interface.

**Purpose:** Help musicians isolate parts, understand song structure, and practice along — with a premium, production-quality experience.

**Product name:** Riffd (stylized lowercase "riffd" in UI)
**Company:** Riffd Labs
**Domain:** riffdlabs.com
**Current stage:** Public beta (deployed via Vercel, local dev on localhost:5000)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, Flask 3.x |
| Stem separation | Demucs (htdemucs_6stems, fallback to htdemucs 4-stem) |
| Note detection | Basic Pitch (ICASSP 2022 model) |
| Harmonic analysis | Custom section-based chord alignment + Krumhansl-Schmuckler key detection |
| Audio download | yt-dlp (YouTube search + download → WAV) |
| Frontend | Jinja2 templates (base.html + page templates), vanilla JS, inline CSS |
| APIs | Spotify (client credentials), Genius (lyrics), Last.fm (tags + similar tracks) |
| Storage | SQLite (riffd.db) for track metadata, filesystem for stems/tabs/cache |
| Task execution | Python threading (no task queue) |
| Auth | Session-based password gate (SITE_PASSWORD env var) |
| Deployment | Vercel (api/index.py shim), gunicorn |

---

## Architecture

```
Flask app (app.py, port 5000)
│
├── Pages (templates/ — extends base.html)
│   ├── GET  /              → home.html (landing page with hero, features, preview)
│   ├── GET  /decompose     → decompose.html (main tool: search → process → results)
│   ├── GET  /learn         → learn.html (music theory explorer: chords, scales, progressions, keys)
│   ├── GET  /library       → library.html (placeholder — "Coming Soon")
│   ├── GET  /practice      → practice.html (placeholder — "Coming Soon")
│   ├── GET  /about         → about.html (tech stack, pipeline, principles)
│   ├── GET  /login         → login.html (standalone password gate)
│   └── GET  /logout        → clears session, redirects to login
│
├── API Routes
│   ├── GET  /api/spotify/search      → Spotify track search (fallback to local)
│   ├── GET  /api/track/<track_id>    → Track lookup + metadata
│   ├── POST /api/download            → yt-dlp audio download (threaded)
│   ├── POST /api/upload              → Direct file upload
│   ├── POST /api/process/<job_id>    → Full processing pipeline (threaded)
│   ├── GET  /api/status/<job_id>     → Poll job status
│   ├── GET  /api/audio/<job_id>/<stem> → Serve stem WAV files
│   ├── GET  /api/download_midi/<job_id>/<stem> → Download MIDI
│   ├── GET  /api/history             → Recent songs list
│   ├── GET  /api/cache/<track_id>    → Cached result lookup
│   ├── GET  /api/discovery           → Static curated song list
│   └── GET  /api/theory/<section>    → Music theory data (chords/scales/progressions/keys)
│
├── Processing Pipeline (processor.py)
│   ├── Demucs stem separation (6-stem → fallback 4-stem)
│   ├── STFT stereo field analysis (center/left/right panning)
│   ├── Spectral feature extraction + instrument classification
│   └── Basic Pitch note detection → MIDI + CSV + ASCII tab
│
├── Harmonic Analysis (harmonic_analysis.py)
│   ├── Lyric section parsing ([Verse], [Chorus] tags from Genius)
│   ├── Proportional chord-to-section alignment
│   ├── Roman numeral conversion relative to key
│   ├── Section pattern detection (repeating sequences)
│   └── Key inference from chord set (24-key scoring)
│
├── Music Intelligence (music_intelligence.py)
│   ├── Key detection (Krumhansl-Schmuckler pitch-class profile correlation)
│   ├── BPM estimation (inter-onset interval histogram, constrained 60-180)
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
│   ├── Track search with rate-limit handling
│   └── Fallback search from local history + discovery data
│
├── Database (db.py)
│   ├── SQLite (riffd.db) with WAL mode
│   ├── Tracks table: metadata, analysis status, job_id, view counts
│   └── Migration from legacy history.json
│
├── History + Cache (history.py)
│   ├── JSON file-based song history (legacy, migrating to SQLite)
│   └── Versioned result caching (outputs/<job_id>/result_cache.json)
│
└── Frontend (templates/ — 9 files, ~3900 lines total)
    ├── base.html (141 lines) — shared layout, nav, design system CSS
    ├── decompose.html (1323 lines) — search, processing, results UI
    ├── learn.html (457 lines) — theory explorer with filters + pagination
    ├── home.html (210 lines) — landing page
    ├── about.html (183 lines) — pipeline + tech explanation
    ├── library.html (149 lines) — placeholder preview
    ├── practice.html (126 lines) — placeholder preview
    ├── login.html (57 lines) — standalone password gate
    └── index.html (1283 lines) — LEGACY, superseded by decompose.html
```

---

## Key Files and Roles

| File | Lines | Role |
|------|-------|------|
| `app.py` | 633 | Flask server, route definitions, 7-stage processing pipeline, job orchestration |
| `processor.py` | 985 | Demucs stem separation, stereo refinement, spectral classification, tab generation |
| `harmonic_analysis.py` | 474 | Section-based harmonic analysis, chord-to-lyric alignment, roman numerals |
| `music_intelligence.py` | 656 | Key/BPM/chord detection from audio note data |
| `chord_source.py` | 474 | Web-based chord lookup + functional harmony analysis |
| `spotify_search.py` | 288 | Spotify API wrapper + fallback local search |
| `external_apis.py` | 431 | Genius lyrics + Last.fm integration |
| `db.py` | 233 | SQLite track metadata database |
| `history.py` | 182 | Song history + versioned result cache persistence |
| `downloader.py` | 71 | yt-dlp wrapper for YouTube audio download |
| `recommendations.py` | 248 | **Unused** — scoring framework, never called. Candidate for deletion. |
| `templates/index.html` | 1283 | **Legacy** — old single-page frontend, superseded by decompose.html |

---

## Processing Pipeline (7 Stages)

When a user processes a song, `app.py` runs these stages in a daemon thread. Each stage can fail independently — partial results are saved.

| Stage | Module | Fatal? | Description |
|-------|--------|--------|-------------|
| 1. Stem Separation | processor.py | Yes | Demucs 6-stem → 4-stem fallback, stereo refinement |
| 2. BPM Detection | music_intelligence.py | No | Basic Pitch on first harmonic stem, IOI histogram |
| 3. Tab Generation | processor.py | No | Per-stem Basic Pitch → MIDI + CSV + ASCII tab |
| 4. Lyrics | external_apis.py | No | Genius API search + HTML scrape |
| 5. Song Intelligence | harmonic_analysis.py + chord_source.py | No | Section-based harmony, key, chords, roman numerals |
| 6. Track Tags | external_apis.py | No | Last.fm top tags |
| 7. Recommendations | external_apis.py | No | Last.fm similar tracks only (Spotify recs disabled) |

Job timeout watchdog: 600 seconds. Results cached to `outputs/<job_id>/result_cache.json`.

---

## Harmony System (Section-Based)

The current harmony system replaced an earlier single-progression approach. It now works as follows:

1. **Chord sourcing** — Web scrape for chords (chord_source.py), with audio-based fallback
2. **Lyric fetching** — Genius lyrics with `[Section]` tags (external_apis.py)
3. **Section alignment** — Distributes chord sequence proportionally across lyric sections, weighted by line count (harmonic_analysis.py)
4. **Pattern detection** — Finds repeating chord patterns within each section
5. **Roman numeral conversion** — Converts chords relative to detected key, handles non-diatonic chords

**UI rendering** (decompose.html): Harmony panel in results header shows per-section cards with:
- Section label (Verse 1, Chorus, etc.)
- Chords in monospace (dash-separated: G – C – D – C)
- Roman numerals in accent color (I – IV – V – IV)
- Repeating pattern summary when detected
- Optional confidence badge

---

## Current Features

### Fully Working
- Password-gated access (session-based auth)
- Spotify search with debounce, rate limiting, and local fallback
- YouTube audio download via yt-dlp
- Direct file upload (drag & drop or browse)
- 6-stem Demucs separation with 4-stem fallback
- Stereo field analysis splitting stems by panning position
- Spectral instrument classification
- Per-instrument Basic Pitch note detection with confidence filtering
- BPM-aware quantization grid (detected BPM, not hardcoded)
- MIDI + CSV + ASCII tab generation
- Section-based harmonic analysis with chord-to-lyric alignment
- Roman numeral display relative to detected key
- Key detection (Krumhansl-Schmuckler)
- BPM estimation (constrained 60-180 range)
- Genius lyrics with section markers
- Last.fm tags and similar tracks
- Web Audio API stem mixer with volume/mute/solo/seek/loop/transpose
- Song history with versioned caching
- SQLite track metadata database
- Discovery rail with static curated songs
- Landing page with hero, feature cards, product preview
- Studio/Learn page with searchable theory content (chords, scales, progressions, keys)
- About page with pipeline visualization and tech cards
- Consistent dark theme with burnt orange (#D4691F) accent

### Placeholder / Coming Soon
- **Library page** — blurred preview grid of sample songs, non-functional
- **Practice page** — blurred preview of training modules (jam tracks, scales, chords, loops)
- **Tab panel** in decompose results — shows "Tabs coming soon" with blurred preview

### Not Working / Unused
- `recommendations.py` — scoring framework, never called from app.py
- `templates/index.html` — legacy single-page frontend, superseded by decompose.html
- Spotify-based recommendation pools (intentionally disabled to preserve API quota)

---

## Known Issues

1. **Tab quality is limited.** Fret assignment picks globally lowest fret with no position tracking — causes unplayable jumps. Tabs capped at 32 seconds. No rhythm notation.
2. **Drum transcription is weak.** Basic Pitch detects ~10% of actual hits. Needs onset detection, not pitched note detection.
3. **Stereo splitter creates phantom instruments.** On narrowly-panned mixes, one guitar becomes multiple stems. Spectral classifier can mislabel instruments (distorted guitar → "Banjo").
4. **Web chord scraping is fragile.** Depends on DuckDuckGo + chord site HTML structure. No caching, no rate limiting.
5. **No disk cleanup.** outputs/ grows unbounded (hundreds of MB per song).
6. **In-memory job dict (`jobs = {}`)** — all state lost on server restart.
7. **No error recovery in frontend** — processing failure can leave UI on loading screen.
8. **SESSION_COOKIE_SECURE = False** — should be True in production.
9. **Debug logging left in auth** — marked for removal in app.py.
10. **history.json grows unbounded** — no TTL or cleanup.
11. **No mobile-optimized layout** — responsive basics only.

---

## Design System

The app was restyled to match a Figma design reference (see `design/figma-reference/`).

| Element | Value |
|---------|-------|
| Background | `#0B0B0B` (flat near-black) |
| Surfaces | `#0D0D0D` to `#222222` |
| Accent | `#D4691F` (burnt orange) |
| Text primary | `#F5F5F5` |
| Text secondary | `rgba(245,245,245, 0.55)` |
| Borders | `rgba(255,255,255, 0.08)` standard, `0.15` interactive |
| Font | Inter, weight 500 for headlines |
| Shape | Sharp rectangles everywhere (no border-radius) |
| Style | No glassmorphism, no gradients, no ambient backgrounds |

---

## Deployment

| Setting | Value |
|---------|-------|
| Platform | Vercel (serverless) |
| Entry point | `api/index.py` → imports Flask `app` |
| Config | `vercel.json` — catch-all route to `api/index.py` |
| Auth | `SITE_PASSWORD` env var required |
| Local | `python app.py` → localhost:5000 |

---

## Critical Assumptions

1. **yt-dlp will continue to work** — YouTube frequently patches against downloaders. Single biggest fragility.
2. **Demucs runs on CPU** — no GPU acceleration. Processing takes 2-5 minutes per song.
3. **Single user at a time** — no concurrency handling, no queue.
4. **Web scraping for chords** — can break when sites change HTML.
5. **Client-credentials Spotify** — no user auth, limited API access, rate-limited at account level.

---

## Open Questions

1. What's the deployment target long-term — hosted web app, desktop app, or stay local?
2. Should tab generation target a standard format (MusicXML, Guitar Pro) instead of ASCII?
3. Is the yt-dlp dependency acceptable long-term?
4. Should processing move to a cloud GPU service?
5. What's the priority for Library and Practice pages?
