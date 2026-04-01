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

## What It Is

Riffd is a music analysis tool that breaks any song into its component parts — stems, chords, key, tempo, lyrics, and structure — in a single interface. Search a song, get instant harmonic analysis in seconds; trigger a full analysis and you can isolate individual instruments, mute stems, export MIDI, and discover songs with similar harmonic DNA.

It's a solo-built, deployed product running on real infrastructure, processing real user requests.

---

## Why I Built It

Every music tool does one thing. A stem splitter. A chord chart. A lyrics site. A tuner. You end up with five tabs open to answer one question: *what's actually happening in this song?*

I wanted a single tool that connected the full pipeline — from raw audio to structured, playable musical information. So I built it, deployed it, and kept iterating until it worked reliably for people who aren't me.

---

## Product Decisions Worth Explaining

A few design choices that reflect how I think about building:

**Preview-first, deep analysis on demand.** Users get key, BPM, chords, and lyrics in ~3–5 seconds using a fast preview path (no GPU, no Demucs). Full stem separation is opt-in. This made the product usable instantly instead of asking users to wait 90 seconds before seeing anything.

**Silence over errors.** When a user exceeds the concurrent job limit, they're queued and silently promoted when a slot opens — no error shown, no manual retry required. The failure state is invisible. That's a UX decision, not a technical one.

**Build for failure, not the happy path.** YouTube blocks requests. Spotify previews expire. Replicate times out. Anthropic returns malformed JSON. Every external dependency fails eventually. The app only became usable once every failure had an explicit fallback and surfaced a clear next step rather than a broken state.

**Partial results over nothing.** The ML pipeline has five stages. If one fails, the others still render. Users get whatever completed rather than a blank screen.

---

## What It Does

- **Stem separation** — isolate vocals, bass, drums, guitar, piano, and other instruments via GPU-accelerated neural source separation
- **Grouped interactive mixer** — stems organized by instrument family with collapsible groups, energy-balanced faders, mute, solo, loop, and real-time transposition
- **Harmonic analysis** — chords aligned to song sections with roman numeral notation relative to the detected key
- **Key + tempo detection** — derived from audio, not metadata
- **Lyrics** — full text with section structure
- **Smart recommendations** — discovery based on music theory (matching progressions, keys, voice leading) rather than listening history
- **Studio** — interactive theory reference for every key
- **MIDI export** — per-stem note detection files, ready for any DAW
- **Multi-user job queue** — concurrent processing with graceful overflow and silent promotion

---

## Technical Highlights

**Audio acquisition.** Multi-source pipeline: YouTube (proxied yt-dlp with dual-binary retry and bot detection bypass), Spotify preview, iTunes preview, user upload — with automatic fallback at each stage. Full-mode downloads fail loudly when YouTube is unavailable, prompting upload rather than silently degrading to a 30-second clip.

**GPU stem separation.** Demucs (htdemucs_6stems) runs on cloud GPU via Replicate's API, with audio uploaded directly through their file API. Separation completes in ~20 seconds. Stereo field analysis using STFT-domain panning masks further refines each stem into sub-components by position, with RMS energy gating to suppress ghost components below threshold.

**Signal processing + ML pipeline.** Stem separation (Demucs), pitch extraction (Basic Pitch / TensorFlow), key and BPM detection (Essentia), stereo field analysis, and section-based harmonic analysis are combined into one end-to-end workflow with per-stage error isolation.

**Intelligent mixer.** Each stem's fader initializes proportional to its RMS energy — so the mix is balanced from the first play. Group-level faders and mute let users hear instrument families before individual components.

**LLM-powered insight.** Claude generates structured musical analysis — progression names, key context, theory-based recommendations — from detected chords, key, tempo, and lyrics. Output is constrained to strict JSON for reliable downstream rendering.

**Performance.** YouTube audio downloads as MP3 to skip transcoding (10x smaller files). WAV stems are converted to 192kbps MP3 post-analysis (20x reduction) before serving. Heavy imports (numpy, TensorFlow, Basic Pitch) are deferred to first job execution. Preview analysis runs synchronously in ~3–5 seconds while the full pipeline runs asynchronously in the background.

**Resilience.** Every external dependency — YouTube, Spotify, Genius, Replicate, Anthropic — can and will fail. TensorFlow memory is explicitly cleared after each job via `keras.backend.clear_session()` to prevent session compounding. Intermediate files are cleaned up post-processing.

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

Live and in public beta. The full pipeline runs end-to-end — search, acquire, separate, analyze, recommend, display.

---

## About

Solo project by **Dylan Glatt** — New York, NY.

<a href="https://www.linkedin.com/in/dylanjglatt/">LinkedIn</a> · <a href="https://github.com/djglatt">GitHub</a> · <a href="mailto:dylanglatt@gmail.com">dylanglatt@gmail.com</a>
