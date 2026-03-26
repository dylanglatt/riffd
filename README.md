# Riffd

**Search any song. Separate the stems. See the harmony.**

Riffd is a music analysis tool that takes a song and breaks it apart — isolating instruments into individual stems, detecting key and tempo, mapping chords to song sections, and generating tablature. Built for musicians who want to hear what's actually happening inside a track.

🔗 **[riffdlabs.com](https://riffdlabs.com)** — live beta

---

## What It Does

Pick a song from Spotify search (or upload your own audio). Riffd runs it through a multi-stage analysis pipeline and gives you back:

- **Isolated stems** — vocals, bass, drums, guitar, keys, and more via neural source separation
- **Interactive mixer** — per-stem volume, mute, solo, seek, loop, and transpose controls in the browser
- **Section-based harmony** — chords aligned to song sections (Verse, Chorus, Bridge) with roman numeral analysis relative to the detected key
- **Key and BPM detection** — from actual audio content, not metadata
- **Lyrics with structure** — full lyrics with section markers pulled from Genius
- **MIDI export** — download per-stem MIDI files for use in any DAW

---

## Why I Built This

I wanted a single tool that could answer "what's happening in this song" without switching between five different apps. Most stem separators don't analyze. Most theory tools don't work with real audio. Riffd connects the full pipeline — from raw audio to playable, structured musical information.

This is also the project where I learned the most about building production software: real API integrations with failure handling, CPU-intensive background processing, audio DSP, and shipping something that actual users interact with.

---

## Technical Highlights

**Audio pipeline.** Songs are acquired through a multi-source waterfall (YouTube → Spotify preview → iTunes preview → user upload) with automatic fallback at each stage. Audio is then separated into up to 6 stems using Meta's Demucs neural network, with stereo field analysis to further isolate instruments by panning position.

**Note detection and tab generation.** Each stem is run through Spotify's Basic Pitch model with per-instrument confidence thresholds and frequency ranges. Notes are quantized to a BPM-aware grid (tempo detected from inter-onset interval histograms, not hardcoded). Output is MIDI, CSV, and ASCII tablature.

**Harmonic analysis.** Chords are sourced from the web with an audio-based fallback, then aligned proportionally to lyric sections from Genius. Key is detected using Krumhansl-Schmuckler pitch-class profile correlation across 24 major/minor keys. Roman numerals are computed relative to the detected key, with handling for non-diatonic chords.

**Rate limiting and resilience.** Spotify search uses a shared cooldown with automatic fallback to local data — users always get results, even when the API is throttled. Background recommendation calls are disabled entirely to preserve search quota. All external API calls have timeouts, retries, and graceful degradation.

**Frontend.** Multi-page Flask app with Jinja2 templates. Web Audio API powers the stem mixer. Dark theme with a custom design system — flat surfaces, burnt orange accent, sharp geometry. No frontend framework; vanilla JS throughout.

---

## Stack

Python · Flask · Demucs · Basic Pitch · SQLite · Web Audio API · Spotify API · Genius API · Last.fm API · iTunes Search API · yt-dlp

---

## Status

Riffd is in public beta. Core pipeline works end-to-end: search → download → separate → analyze → display. Active areas of improvement include fret assignment for more playable tabs, drum transcription accuracy, and full-song tab rendering beyond the current 32-second window.

---

## What I Learned

Building Riffd pushed me into problems I hadn't solved before — CPU-bound background jobs in a web context without a task queue, graceful degradation across four external APIs with different failure modes, audio signal processing (FFT-based stereo analysis, spectral classification, onset detection), and shipping a product that needs to feel responsive while running 3-minute neural network inference on CPU.

The biggest lesson was about resilience. Every external dependency — YouTube, Spotify, Genius, chord sites — can and will fail. The app only became usable once every API call had a fallback path and every failure surfaced a clear next step to the user instead of a dead spinner.

---

## About

Built by **Dylan Glatt** as a solo project.

📧 dylanglatt@gmail.com
🔗 [github.com/djglatt](https://github.com/djglatt)
