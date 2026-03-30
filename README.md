<p align="center">
  <img src="assets/logo.png" alt="Riffd" width="120" />
</p>

<p align="center">
  Break down real songs. See how they actually work.
</p>

<p align="center">
  <a href="https://www.riffdlabs.com"><strong>Live Product →</strong></a>
</p>

---

## What Riffd Does

Riffd takes a song and breaks it into its core components — so you can hear, see, and understand it.

- **Stem separation** — isolate vocals, bass, drums, guitar, piano, and other instruments using neural source separation on cloud GPU
- **Grouped interactive mixer** — stems organized by instrument family with collapsible groups, energy-based initial fader levels, mute, solo, loop, and real-time transposition
- **Harmonic analysis** — chords aligned to song sections with roman numeral notation relative to the detected key
- **Key + tempo detection** — derived directly from audio, not metadata
- **Lyrics** — full text with section structure
- **Smart recommendations** — song discovery based on music theory — matching progressions, keys, and voice leading — not vibes or listening history
- **Studio** — interactive theory reference with diatonic chords, common progressions, and key relationships for every key
- **MIDI export** — per-stem note detection files, ready for any DAW
- **Multi-user processing** — concurrent job queue supports multiple simultaneous analyses with graceful overflow handling

---

## Why This Exists

Most tools do one thing. A stem splitter. A chord chart. A lyrics site. A tuner.

Riffd connects the full pipeline — from raw audio to structured, playable musical information — in one place. Search a song, and within seconds you can see what key it's in, read the chord progression by section, and discover other songs with similar harmonic DNA. Trigger a full analysis and you can hear the isolated bass line, mute the vocals, or export individual stems to a DAW.

I built it because I wanted a single tool that could answer *"what's happening in this song"* without switching between five apps.

---

## Technical Highlights

**Audio acquisition.**
Songs are acquired through a hardened multi-source pipeline — YouTube (via proxied yt-dlp with dual-binary retry and bot detection bypass), Spotify preview, iTunes preview, user upload — with automatic fallback at each stage. Full-mode downloads fail loudly when YouTube is unavailable, prompting the user to upload instead of silently degrading to a 30-second preview.

**GPU-accelerated stem separation.**
Demucs (htdemucs_6stems) runs on cloud GPU via Replicate's REST API, with audio uploaded directly through their file API for minimal latency. Separation completes in ~20 seconds. Stereo field analysis using STFT-domain panning masks further refines each stem into sub-components by position — center, left-panned, right-panned — with RMS energy gating to filter ghost components below threshold.

**Signal processing + ML pipeline.**
Stem separation (Demucs), pitch extraction (Basic Pitch / TensorFlow), key and BPM detection (Essentia), stereo field analysis, and section-based harmonic analysis are combined into one end-to-end workflow with per-stage error isolation. Partial results are returned and rendered even when individual pipeline stages fail, so users always get something useful.

**Intelligent mixer.**
The browser mixer initializes each stem's fader level proportional to its RMS energy — so the mix is balanced from the first play. Stems are grouped by instrument family (Guitar, Vocal, etc.) with collapsible group rows that include a group-level fader and mute, letting users hear instrument families cleanly before diving into sub-components. A Full Mix button resets all state to energy-balanced defaults in one click.

**LLM-powered insight.**
Claude generates structured musical analysis — progression names, key context, and theory-based song recommendations — from detected chords, key, tempo, and lyrics. Output is constrained to strict JSON for reliable downstream rendering. Recommendations are filtered to enforce artist separation between discovery categories.

**Multi-user concurrency.**
A FIFO job queue allows multiple users to run full deep analyses simultaneously (up to 3 concurrent by default, configurable via `MAX_CONCURRENT_JOBS`). Users beyond the concurrent limit receive a `queued` status and are automatically promoted when a slot opens — no manual retry, no error shown. Since stem separation runs on Replicate, local memory pressure is bounded primarily by Basic Pitch's TensorFlow session rather than Demucs, making 3 concurrent jobs well within a 2GB instance.

**Performance.**
YouTube audio downloads as MP3 (not WAV) to skip transcoding and reduce file size 10x. WAV stems are converted to 192kbps MP3 post-analysis for a 20x size reduction before serving. Heavy imports (numpy, TensorFlow, Basic Pitch) are deferred to first job execution. Preview analysis runs synchronously in ~3–5 seconds while the full pipeline runs asynchronously in the background, with a silent prefetch fired on track selection.

**Resilience.**
Every external dependency — YouTube, Spotify, Genius, Replicate, Anthropic — can and will fail. The app only became usable once every API call had a fallback path and every failure surfaced a clear next step instead of a broken state. TensorFlow memory is explicitly cleared after each job via `keras.backend.clear_session()` to prevent session compounding across sequential runs.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python / Flask / Gunicorn |
| Stem separation | Demucs (htdemucs_6stems) via Replicate API |
| Pitch detection | Basic Pitch (Spotify) / TensorFlow |
| Audio analysis | Essentia (key, BPM) |
| Harmonic analysis | Custom diatonic template matching |
| LLM | Claude API (Anthropic) |
| Audio acquisition | yt-dlp, Spotify API, iTunes API |
| Frontend | Vanilla JS / Web Audio API |
| Database | SQLite (track cache) |
| Deployment | Render (Standard, 2GB) |

---

## Status

Riffd is in public beta. The full pipeline works end-to-end — search, acquire, separate, analyze, recommend, display.

---

## About

Solo project by **Dylan Glatt** — New York, NY.

<a href="https://www.linkedin.com/in/dylanjglatt/">LinkedIn</a> · <a href="https://github.com/djglatt">GitHub</a> · <a href="mailto:dylanglatt@gmail.com">dylanglatt@gmail.com</a>
