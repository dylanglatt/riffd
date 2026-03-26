# TODO.md — riffa

> Prioritized task list. Product quality before infrastructure.
> Keep aligned with PROJECT_CONTEXT.md.

---

## Track A: Product Quality (Do First)

### A1. Fix Basic Pitch note detection quality — DONE
- [x] Filter notes by configurable confidence score per instrument (guitar=0.35, bass=0.30, drums=0.15)
- [x] Tune predict() parameters per instrument type via INSTRUMENT_CONFIGS dict
- [x] Use detected BPM for quantization grid instead of hardcoded 120 BPM
- [x] Restructure app.py processing order: detect BPM before tab rendering
- [x] Add confidence distribution logging per stem
- [ ] **Needs validation:** run end-to-end on a real song to verify BPM detection pipeline works

### A2. Rebuild fret assignment for playable tab
- [ ] Implement position-tracking: maintain "hand position" (4-fret span)
- [ ] Prefer notes within current span, shift only when necessary
- [ ] When simultaneous notes, find single position covering them all
- [ ] Add position shift indicators in output

### A3. Replace drum transcription pipeline
- [ ] Use librosa onset detection instead of Basic Pitch for drum stems
- [ ] Classify hits by spectral band: kick (low), snare (mid+transient), hihat (high)
- [ ] Map to GM drum MIDI for tab renderer
- [ ] Current: 122 hits detected for 3-min song. Target: 800+

### A4. Improve stem separation quality
- [ ] Test `htdemucs_ft` model as primary (fine-tuned, generally better)
- [ ] Add `--shifts 2` option for higher quality (at cost of 2x processing time)
- [ ] Disable stereo splitting by default — only enable when L-R correlation is genuinely wide
- [ ] Use Basic Pitch confidence as stem quality signal — hide low-quality stems
- [ ] Fix spectral classifier: replace hardcoded thresholds with more robust approach

### A5. Full-song tabs with structure
- [ ] Remove 32-second cap in _render_string_tab
- [ ] Add measure numbers
- [ ] Smart truncation: show first verse+chorus, "..." for rest, expandable
- [ ] Only do this after A1-A2 make the full output actually good

---

## Track B: Infrastructure (Do After Track A)

### B1. Clean up codebase
- [ ] Delete `recommendations.py` (dead code, broken imports)
- [ ] Fix `pkg_resources` import error in venv
- [ ] Ensure app runs cleanly from fresh start
- [ ] Optionally: clean up existing outputs/ intermediate files (htdemucs/ and _raw_*.wav in old jobs)

### B2. Split monolithic HTML
- [ ] Extract CSS → `static/css/style.css`
- [ ] Extract JS → `static/js/app.js`
- [ ] HTML becomes clean template with includes

### B3. Fix error recovery UX
- [ ] If processing fails, show clear error with "Try Again" button
- [ ] Never get stuck on loading screen
- [ ] Handle network disconnection gracefully

### B4. Disk cleanup
- [ ] Delete intermediate files after processing (htdemucs/ dir + _raw_*.wav, ~286MB/song)
- [ ] TTL-based eviction for uploads/ and outputs/ (whole job cleanup for old songs)
- [ ] Configurable size limit (default 10GB)
- [ ] Never delete in-progress jobs

### B5. Keyboard shortcuts + polish
- [ ] Spacebar: play/pause
- [ ] Left/Right arrows: ±5s seek
- [ ] Number keys: solo stem N
- [ ] Escape: stop

---

## Later

- [ ] Remove or implement "Connect Spotify" button
- [ ] Evaluate GPU-accelerated Demucs
- [ ] Guitar Pro / MusicXML export
- [ ] Interactive tab player (scroll with audio)
- [ ] User accounts
- [ ] Deployment configuration
- [ ] Replace yt-dlp with sustainable audio source
- [ ] Consider Omnizart/MT3 for transcription if Basic Pitch ceiling is too low

---

## Completed

- [x] Full codebase analysis (2026-03-25)
- [x] Created PROJECT_CONTEXT.md (2026-03-25)
- [x] Created CHANGELOG.md (2026-03-25)
- [x] Created TODO.md (2026-03-25)
- [x] Deep audit of stem separation + tab generation quality (2026-03-25)
- [x] A1: Note detection quality — confidence filtering, per-instrument params, BPM grid (2026-03-25)
- [x] Fix: Search rate limiting — shared cooldown, 15s clamp, frontend auto-retry (2026-03-25)
- [x] Fix: Disabled background Spotify calls in recommendations (2026-03-25)
- [x] Fix: Fallback search from local data when Spotify is rate-limited (2026-03-25)
