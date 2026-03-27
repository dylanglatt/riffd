<p align="center">
  <img src="assets/logo.png" alt="Riffd" width="120" />
</p>

<p align="center">
  Analyze any song. Understand what's actually happening.
</p>

<p align="center">
  <a href="https://www.riffdlabs.com"><strong>Live Product →</strong></a>
</p>

---

## What Riffd Does

Riffd takes a song and breaks it into its core components — so you can hear, see, and understand it.

- **Stem separation** — isolate vocals, bass, drums, guitar, keys, and more using neural source separation
- **Interactive mixer** — mute, solo, loop, transpose, and explore each part in the browser
- **Harmonic analysis** — chords aligned to song sections with roman numeral notation relative to the detected key
- **Key + tempo detection** — derived directly from audio, not metadata
- **Lyrics** — full text with section structure, aligned to the analysis
- **MIDI export** — per-stem files, ready for any DAW

---

## Why This Exists

Most tools do one thing. A stem splitter. A chord chart. A lyrics site. A tuner.

Riffd connects the full pipeline — from raw audio to structured, playable musical information — in one place. Search a song, and within minutes you can hear the isolated bass line, see what key it's in, read the chord progression by section, and export the MIDI.

I built it because I wanted a single tool that could answer *"what's happening in this song"* without switching between five apps.

---

## Technical Highlights

This project required solving real production problems beyond modeling:

**Audio acquisition.**
Songs are acquired through a multi-source waterfall — YouTube, Spotify preview, iTunes preview, user upload — with automatic fallback at each stage. No single point of failure when full audio is unavailable.

**Long-running processing.**
Stem separation runs neural inference on CPU and takes real time to complete. The app handles this with background job orchestration, timeout management, polling, and failure-safe UX — no dead spinners.

**Signal processing + ML pipeline.**
Stem separation (Demucs), pitch extraction (Basic Pitch), stereo field analysis, key detection (Krumhansl-Schmuckler), and section-based harmonic analysis are combined into one end-to-end workflow with per-stage error isolation.

**Resilience.**
Every external dependency — YouTube, Spotify, Genius, chord sources — can and will fail. The app only became usable once every API call had a fallback path and every failure surfaced a clear next step instead of a broken state.

---

## Architecture

### Core Flows

```
INSTANT (default — preview-first):
  Select song → preview audio (iTunes/Spotify, ~2s)
  → key + BPM + lyrics (synchronous, ~3-5s total)
  → instant results rendered immediately

DEEP (user-triggered):
  "Run deep analysis" → full audio (YouTube, ~30-120s)
  → Demucs stem separation (~2-5min)
  → per-stem tab generation → harmonic analysis → full results
```

### Audio Acquisition

```
Preview mode (default):    Spotify preview → iTunes preview → AudioUnavailableError
Full mode (opt-in):        YouTube (yt-dlp) → Spotify preview → iTunes → AudioUnavailableError
```

### Processing Pipeline (Deep)

| Stage | Module | Time | Fatal? |
|-------|--------|------|--------|
| Stem separation | processor.py (Demucs) | 2-5 min | Yes |
| BPM detection | music_intelligence.py | ~10s | No |
| Tab generation | processor.py (Basic Pitch) | ~30s | No |
| Lyrics | external_apis.py (Genius) | ~1s | No |
| Harmonic analysis | harmonic_analysis.py | ~5s | No |
| Tags + recs | external_apis.py (Last.fm) | ~2s | No |

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, Flask |
| Stem separation | Demucs (htdemucs_6stems, subprocess or Replicate GPU) |
| Note detection | Basic Pitch (ICASSP 2022 model) |
| Key detection | Krumhansl-Schmuckler pitch-class profiling |
| Audio download | yt-dlp (full), iTunes/Spotify API (preview) |
| Frontend | Jinja2 templates, vanilla JS, inline CSS |
| APIs | Spotify (search), Genius (lyrics), Last.fm (tags) |
| Storage | SQLite (track metadata), filesystem (stems/tabs/cache) |
| Auth | Session-based password gate (SITE_PASSWORD) |
| Deployment | Render |

### Key Files

| File | Role |
|------|------|
| `app.py` | Flask server, routes, job orchestration, instant + deep processing |
| `processor.py` | Demucs stem separation, stereo refinement, Basic Pitch tab generation |
| `downloader.py` | Audio acquisition waterfall (YouTube, Spotify, iTunes) |
| `music_intelligence.py` | Key detection, BPM estimation, chord progression analysis |
| `harmonic_analysis.py` | Section-based chord alignment, roman numerals |
| `chord_source.py` | Web-based chord lookup |
| `external_apis.py` | Genius lyrics, Last.fm tags and recommendations |
| `spotify_search.py` | Spotify API search with rate limiting and local fallback |
| `db.py` | SQLite track metadata database |
| `history.py` | JSON file history + versioned result cache |
| `templates/decompose.html` | Main tool UI — search, processing, results, mixer |

---

## Local Development

### Setup

```bash
# Python 3.11 recommended (Basic Pitch has issues on 3.12+)
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file:

```
SITE_PASSWORD=your_password
FLASK_SECRET_KEY=any_random_string

# Optional — Spotify search
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...

# Optional — lyrics and tags
GENIUS_API_KEY=...
LASTFM_API_KEY=...

# Optional — hosted stem separation (GPU, ~20s instead of ~3min)
USE_HOSTED_SEPARATION=false
REPLICATE_API_TOKEN=...
```

### Run

```bash
python app.py
# → http://localhost:5001
```

---

## Debugging Guide

### Log format

All backend logs print to stdout with `[stage]` prefixes. Frontend logs use `[ui]` in the browser console.

```
[download] [job abc123] mode=preview — skipping YouTube
[job abc123] resolve_preview called — preview_url=False artist=Eagles
[job abc123] AUDIO SOURCE SELECTED: preview (itunes)
[job abc123] process start analysis_mode=instant
[job abc123] instant analysis complete in 3.2s
```

### Common failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| "No preview available" | iTunes lookup returned no match | Use deep analysis or upload audio |
| yt-dlp timeout | YouTube blocking requests | Expected on Render — preview mode avoids this |
| "Processing timed out" | Demucs took >5 min | Use Replicate hosted separation |
| Startup RSS >200MB | Heavy imports at boot | Verify lazy import chain in processor.py |

---

## Status

Riffd is in public beta. The full pipeline works end-to-end — search, acquire, separate, analyze, display.

Current focus areas include improving tab quality, refining harmonic precision, increasing processing speed, and polishing the product experience.

---

## About

Solo project by **Dylan Glatt** — New York, NY.

<a href="https://www.linkedin.com/in/dylanjglatt/">LinkedIn</a> · <a href="https://github.com/djglatt">GitHub</a> · <a href="mailto:dylanglatt@gmail.com">dylanglatt@gmail.com</a>
