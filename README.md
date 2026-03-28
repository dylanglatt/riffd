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

- **Stem separation** — isolate vocals, bass, drums, guitar, keys, and more using neural source separation
- **Interactive mixer** — mute, solo, loop, transpose, and explore each part in the browser
- **Harmonic analysis** — chords aligned to song sections with roman numeral notation relative to the detected key
- **Key + tempo detection** — derived directly from audio, not metadata
- **Lyrics** — full text with section structure, aligned to the analysis
- **Smart recommendations** — song discovery based on music theory — matching progressions, keys, and voice leading — not vibes or listening history
- **Studio** — interactive theory reference with diatonic chords, common progressions, and key relationships for every key
- **MIDI export** — per-stem note detection files, ready for any DAW

---

## Why This Exists

Most tools do one thing. A stem splitter. A chord chart. A lyrics site. A tuner.

Riffd connects the full pipeline — from raw audio to structured, playable musical information — in one place. Search a song, and within minutes you can hear the isolated bass line, see what key it's in, read the chord progression by section, and discover other songs with similar harmonic DNA.

I built it because I wanted a single tool that could answer *"what's happening in this song"* without switching between five apps.

---

## Technical Highlights

**Audio acquisition.**
Songs are acquired through a hardened multi-source pipeline — YouTube (via proxied yt-dlp with dual-binary retry and bot detection bypass), Spotify preview, iTunes preview, user upload — with automatic fallback at each stage. Full-mode downloads fail loudly when YouTube is unavailable, prompting the user to upload instead of silently degrading to a 30-second preview.

**GPU-accelerated stem separation.**
Demucs runs on cloud GPU via Replicate's API, with audio uploaded directly through their file API for minimal latency. Separation completes in ~20 seconds. Stereo field analysis further refines each stem by panning position.

**Signal processing + ML pipeline.**
Stem separation (Demucs), pitch extraction (Basic Pitch), key and BPM detection (Essentia), stereo field analysis, and section-based harmonic analysis are combined into one end-to-end workflow with per-stage error isolation.

**LLM-powered insight.**
Claude generates structured musical analysis — progression names, key context, and theory-based song recommendations — from detected chords, key, tempo, and lyrics. Output is constrained to strict JSON for reliable downstream rendering. Recommendations are filtered to enforce artist separation between discovery categories.

**Performance.**
YouTube audio downloads as MP3 (not WAV) to skip transcoding and reduce file size 10x. Heavy imports are deferred to first use. Preview analysis returns in ~3 seconds while full tracks download in the background via prefetch.

**Resilience.**
Every external dependency — YouTube, Spotify, Genius, Replicate — can and will fail. The app only became usable once every API call had a fallback path and every failure surfaced a clear next step instead of a broken state.

---

## Status

Riffd is in public beta. The full pipeline works end-to-end — search, acquire, separate, analyze, recommend, display.

Current focus areas include stem export, karaoke mode, real-time chord timelines, and MusicBrainz credits integration.

---

## About

Solo project by **Dylan Glatt** — New York, NY.

<a href="https://www.linkedin.com/in/dylanjglatt/">LinkedIn</a> · <a href="https://github.com/djglatt">GitHub</a> · <a href="mailto:dylanglatt@gmail.com">dylanglatt@gmail.com</a>
