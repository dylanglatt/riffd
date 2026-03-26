# Riffd

**Search any song. Separate the stems. See the harmony.**

Riffd is a music analysis tool that breaks a song into its parts — isolating instruments, detecting key and tempo, mapping chords to song sections, and generating tablature. Built for musicians who want to understand what's actually happening inside a track.

🔗 **[riffdlabs.com](https://riffdlabs.com)**

---

## What It Does

Pick a song from Spotify search or drop in your own audio. Riffd runs a multi-stage pipeline and returns isolated stems, harmonic analysis, lyrics, and downloadable MIDI — all from the browser.

- **Stem separation** — vocals, bass, drums, guitar, keys, and more via Meta's Demucs neural network
- **Stem mixer** — per-instrument volume, mute, solo, seek, loop, and transpose using the Web Audio API
- **Harmonic analysis** — chords aligned to song sections (Verse, Chorus, Bridge) with roman numeral notation relative to the detected key
- **Key and BPM** — detected from audio content using Krumhansl-Schmuckler pitch-class profiling and inter-onset interval analysis
- **Lyrics** — full text with section structure via Genius
- **MIDI export** — per-stem MIDI files for use in any DAW

---

## How It Works

**Audio acquisition** follows a multi-source waterfall — YouTube, Spotify preview, iTunes preview, user upload — with automatic fallback at each stage.

**Stem separation** uses Demucs (6-stem model, 4-stem fallback) plus stereo field analysis to further isolate instruments by panning position.

**Tab generation** runs each stem through Spotify's Basic Pitch model with per-instrument confidence thresholds. Notes are quantized to a BPM-aware grid. Output is MIDI, CSV, and ASCII tablature.

**Harmonic analysis** sources chords from the web (with audio-based fallback), then aligns them proportionally to lyric sections. Key detection scores across all 24 major/minor keys. Roman numerals handle non-diatonic chords.

**Resilience** is built into every external call. Spotify search falls back to local data when throttled. All API integrations have timeouts, retries, and graceful degradation — no dead spinners.

---

## Stack

Python · Flask · Demucs · Basic Pitch · SQLite · Web Audio API · Spotify · Genius · Last.fm · iTunes Search API · yt-dlp

---

## Status

Public beta. The core pipeline works end-to-end: search → acquire → separate → analyze → display. Active work includes improved fret assignment for more playable tabs, better drum transcription, and full-length tab rendering.

---

## About

Solo project by **Dylan Glatt**.

[LinkedIn](https://www.linkedin.com/in/dylanjglatt/) · [GitHub](https://github.com/djglatt) · [dylanglatt@gmail.com](mailto:dylanglatt@gmail.com)
