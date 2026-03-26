# CHANGELOG.md — Riffd

> Every code change is logged here. Read before making changes. Update after every change.

---

## Current State (as of 2026-03-26)

### What the app does now
- Multi-page Flask app with Jinja2 templates (base.html + 8 page templates)
- Password-gated access (session-based, SITE_PASSWORD env var)
- Spotify search → YouTube download → Demucs stem separation → Basic Pitch tab generation
- Section-based harmonic analysis: chords aligned to lyric sections, roman numerals, pattern detection
- Genius lyrics fetching with section markers
- Last.fm tags and similar track recommendations
- Web Audio API stem mixer with volume/mute/solo/seek/loop/transpose
- Decompose results with Mix / Tab / Lyrics panels (Tab is placeholder)
- Harmony panel in results header showing per-section chords + roman numerals
- Studio/Learn page with searchable music theory content
- Landing page, About page, Library placeholder, Practice placeholder
- SQLite database for track metadata + filesystem cache for results
- Per-instrument Basic Pitch configuration with confidence filtering
- BPM-aware quantization grid
- Dark theme with burnt orange (#D4691F) accent, sharp rectangles, no glassmorphism
- Vercel deployment config

### What is placeholder / coming soon
- Tab panel in decompose results (blurred preview, "Tabs coming soon")
- Library page (blurred preview grid, non-functional)
- Practice page (blurred preview modules, non-functional)

### What is broken or incomplete
- **Audio acquisition broken in production** — yt-dlp fails on Render (missing JS runtime + YouTube bot detection). Needs hardening + preview fallback waterfall. See TODO A0.
- `recommendations.py` — unused scoring framework, never called (dead code)
- `templates/index.html` — legacy single-page frontend, superseded by decompose.html (dead code)
- Fret assignment not position-aware (unplayable jumps)
- Drum transcription weak (~10% hit detection)
- Stereo splitter creates phantom instruments on narrow mixes
- Web chord scraping fragile (no caching, no rate limiting)
- No disk cleanup — outputs/ grows unbounded
- No error recovery if processing fails mid-pipeline
- Tab generation capped at 32 seconds
- SESSION_COOKIE_SECURE = False (should be True in production)
- Debug auth logging left in app.py

---

## [2026-03-26 — Section-based harmony, UI panel, removed single progression]

Git: `02c11bd`

### Change Summary
- Replaced the single-progression concept with section-based harmonic analysis
- Created `harmonic_analysis.py` (474 lines): lyric section parsing, proportional chord-to-section alignment, roman numeral conversion, repeating pattern detection, key inference from chords
- Added harmony UI panel to decompose results: per-section cards showing chords (dash-separated), roman numerals (accent-colored), pattern summaries, and confidence badges
- Integrated section-based harmony into the processing pipeline (stage 5)
- Removed the old single "progression" string output

### Files Modified
- `harmonic_analysis.py`: created — full section-based harmonic analysis module
- `app.py`: integrated harmonic analysis into processing pipeline
- `templates/decompose.html`: added harmony panel rendering in results header

### Impact
- Harmony output is now structurally rich: per-section chords, roman numerals, patterns
- Chords are displayed at section level only (no forced chord-to-lyric-line alignment)
- Roman numerals show relative function (I, IV, V, vi) with accidentals for non-diatonic chords
- Old single progression string no longer returned

---

## [2026-03-25 → 2026-03-26 — UI unification and Figma redesign]

Git: `9ec2d3b`, `ec8e2a7`

### Change Summary
- Rebuilt entire frontend from single `index.html` to multi-page Jinja2 template architecture
- Created `base.html` layout with shared nav, design system CSS, page inheritance
- Created dedicated templates: home, decompose, learn, library, practice, about, login
- Restyled all pages to match Figma design reference:
  - Flat `#0B0B0B` background (removed ambient gradient washes)
  - Burnt orange `#D4691F` accent (replaced purple/teal scheme)
  - Sharp rectangles everywhere (removed all border-radius)
  - Removed glassmorphism, colored glows, gradient text effects
  - Inter font, 500 weight headlines, generous spacing
- Built Studio/Learn page: searchable theory explorer with sidebar nav, filters, pagination, cards for chords/scales/progressions/keys
- Built Library placeholder: blurred preview grid with sample songs
- Built Practice placeholder: blurred preview of training modules
- Built About page: pipeline visualization, tech cards, principles
- Built landing page: hero section, feature cards, product preview mockup

### Files Modified
- `templates/base.html`: created — shared layout and design system
- `templates/home.html`: created — landing page
- `templates/decompose.html`: created — replaces index.html as main tool
- `templates/learn.html`: created — music theory explorer
- `templates/library.html`: created — coming soon placeholder
- `templates/practice.html`: created — coming soon placeholder
- `templates/about.html`: created — tech and principles
- `templates/login.html`: created — standalone password gate
- `app.py`: added routes for all new pages, theory API endpoint

### Impact
- Multi-page app with consistent visual language
- Decompose results reduced to 3 tabs (Mix | Tab | Lyrics) — removed Similar tab
- "Connect Spotify" button removed from current UI (only in legacy index.html)
- All CSS inline in templates (no external stylesheets)

---

## [2026-03-25 — Password gate and auth hardening]

Git: `0e125e8`, `e7a3049`, `1265f7c`

### Change Summary
- Added site-wide password gate using SITE_PASSWORD env var
- Session-based auth: `@app.before_request` guard on all routes except `/login` and `/static/`
- Login page is standalone HTML (does not extend base.html)

### Files Modified
- `app.py`: added auth middleware, login/logout routes, session config
- `templates/login.html`: created — password entry form

---

## [2026-03-25 — SQLite database]

Git: part of UI unification commits

### Change Summary
- Created `db.py` with SQLite (riffd.db) for track metadata storage
- WAL mode, indexed on status and last_viewed
- Migration path from legacy history.json
- Tracks table: spotify_track_id, title, artist, album, artwork, duration, analysis status, job_id, view counts

### Files Modified
- `db.py`: created — full database module

---

## [2026-03-25 — Library coming soon + Decompose loading UX]

Git: `ed82b5a`

### Change Summary
- Added Library page with "Coming Soon" blurred preview
- Added estimated wait time display during processing

---

## [2026-03-25 — Fix: Search rate limiting + fallback]

Git: `0b85f01`

### Change Summary
- Fixed persistent "Too many searches" by sharing cooldown state
- Added frontend cooldown with countdown + auto-retry
- All cooldowns clamped to max 15 seconds
- Disabled background Spotify API calls in recommendations (quota reserved for user search)
- Added fallback search from local history + discovery data when Spotify is rate-limited
- Search always returns results (live or fallback) — never shows rate limit errors

### Files Modified
- `spotify_search.py`: shared cooldown, `_fallback_search()`, disabled recommendation Spotify calls
- `templates/index.html`: frontend cooldown UI (note: this was pre-template-split)

---

## [2026-03-25 — A1: Note detection quality improvements]

Git: part of processing improvements

### Change Summary
- Per-instrument confidence thresholds (guitar=0.35, bass=0.30, drums=0.15)
- Per-instrument Basic Pitch parameters (frequency ranges, onset sensitivity)
- BPM-aware quantization grid (detected BPM replaces hardcoded 120)
- Restructured processing: BPM detected before tab rendering
- Confidence distribution logging per stem

### Files Modified
- `processor.py`: `INSTRUMENT_CONFIGS`, confidence filtering, BPM-aware grid
- `app.py`: restructured processing order

### Impact
- Guitar: 25% ghost notes removed on test songs
- Grid aligns with actual song tempo
- Configurable thresholds per instrument

---

## [2026-03-25 — Stability and performance fixes]

Git: `6bed933`, `51c6adb`, `54ef21b`, `d0d4c43`

### Change Summary
- Fixed Demucs crash handling
- Added job timeout watchdog (600s)
- Prevented infinite polling on lost jobs
- Instant results from cache, parallel audio decode
- Fixed history validation, removed placeholder entries
- Fixed lzma import issue
- Added partial result recovery on pipeline failure

---

## [2026-03-24 — Initial development]

Git: `3d93a6e`, `2fde0ed`

### Change Summary
- Initial commit: full working pipeline from search to results
- Single `index.html` frontend with inline CSS/JS
- Demucs 6-stem model with 4-stem fallback
- Client-credentials Spotify flow
- yt-dlp for audio acquisition
- JSON file for history
- Versioned caching system (ANALYSIS_VERSION = "v3")
- Web scraping for chord detection with audio fallback
- Static discovery data

---

## Pre-History Notes

Completed design documents archived to `design/archive/`:

- `FIGMA_UI_SPEC.md` — original Figma spec with purple/teal color scheme (outdated — app uses orange/dark theme)
- `REDESIGN_IMPLEMENTATION.md` — step-by-step restyling guide (redesign is complete)
- `figma-reference/` — React/Tailwind reference codebase from Figma export (design source, not runtime code)
