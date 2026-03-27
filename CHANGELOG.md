# Changelog — Riffd

---

## Preview-First Architecture (2026-03-26)

- **Instant analysis mode**: select a song → key, BPM, lyrics in ~3-5 seconds (no Demucs, no stems)
- **Preview-first audio**: default path uses iTunes/Spotify preview (~2s), never touches YouTube
- **Deep analysis opt-in**: "Run deep analysis" button triggers full Demucs pipeline only when requested
- **Non-destructive upgrade**: instant results preserved when deep analysis runs or fails
- **Separate audio and analysis modes**: `audio_mode` (preview/full) independent of `analysis_mode` (instant/deep)

## Audio Acquisition Waterfall (2026-03-26)

- Multi-source audio acquisition: YouTube → Spotify preview → iTunes preview → upload prompt
- Hardened yt-dlp: user-agent spoofing, player_client=web, retries, socket timeout
- YouTube retry with simplified query on first failure
- `preview_url` field propagated through search results, discovery data, and history
- `upload_required` and `preview_unavailable` as distinct terminal states

## Memory + Stability (2026-03-26)

- Heavy imports (numpy, pandas, basic_pitch) deferred to first job — startup RSS ~40MB vs ~300MB
- `float64` → `float32` in audio processing — halves memory per song
- Explicit `del` and `gc.collect()` after Demucs and per-stem processing
- Job pruning: completed jobs removed from memory after 10 minutes
- Result trimming: heavy payloads stripped after frontend polls the result
- Concurrent job guard: MAX_CONCURRENT_JOBS=1 prevents stacking deep analysis
- Intermediate files (Demucs working dir, `_raw_*.wav`) cleaned up after processing

## Hosted Stem Separation (2026-03-26)

- Replicate integration: USE_HOSTED_SEPARATION=true runs Demucs on GPU (~20s vs ~3min)
- Feature-flagged with automatic fallback to local Demucs on failure
- Same output contract — downstream pipeline unchanged

## Timeout + Loading UX (2026-03-26)

- Backend job timeout reduced to 300s (5 min)
- Frontend poll timeout reduced to 5 min
- Elapsed timer during loading
- "Taking too long?" cancel button after 30s
- Download timeout with descriptive error message

## Section-Based Harmony (2026-03-26)

- Replaced single-progression with section-based harmonic analysis
- Chords aligned to lyric sections (Verse, Chorus, etc.)
- Roman numeral conversion relative to detected key
- Pattern detection within sections

## UI + Design System (2026-03-25)

- Multi-page Jinja2 template architecture (base.html + 8 pages)
- Dark theme with burnt orange (#D4691F) accent
- Sharp rectangles, no glassmorphism
- Landing page, Studio/Learn page, Library/Practice placeholders, About page

## Core Pipeline (2026-03-24)

- Demucs 6-stem separation with 4-stem fallback
- Basic Pitch note detection with per-instrument confidence thresholds
- BPM-aware quantization grid
- Krumhansl-Schmuckler key detection
- Web Audio API stem mixer with volume/mute/solo/seek/loop/transpose
- Spotify search with rate limiting and local fallback
- Genius lyrics with section markers
- SQLite track metadata database
- Versioned result caching
