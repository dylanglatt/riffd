# TODO.md — Riffd

> Prioritized task list. Product quality before infrastructure.
> Keep aligned with PROJECT_CONTEXT.md.

---

## Next Priorities

### A0. Audio acquisition — replace yt-dlp as default source
- [ ] Add `preview_url` to Spotify track format (`_format_track()`)
- [ ] Add iTunes preview URL lookup (extend existing iTunes function in `external_apis.py`)
- [ ] Create `resolve_audio()` waterfall: cached → yt-dlp (hardened) → Spotify preview → iTunes preview → upload prompt
- [ ] Harden yt-dlp: user-agent, player_client=web, retries, socket timeout
- [ ] Add Node.js to Render build for yt-dlp JS runtime
- [ ] Frontend: handle `upload_required` status gracefully (not as error)
- [ ] Frontend: pass `preview_url`, `artist`, `name` in download POST body
- [ ] Isolate yt-dlp to direct-URL-only path (not default for Spotify tracks)

### A2. Rebuild fret assignment for playable tab
- [ ] Implement position-tracking: maintain "hand position" (4-fret span)
- [ ] Prefer notes within current span, shift only when necessary
- [ ] When simultaneous notes, find single position covering them all
- [ ] Add position shift indicators in output

### A3. Replace drum transcription pipeline
- [ ] Use librosa onset detection instead of Basic Pitch for drum stems
- [ ] Classify hits by spectral band: kick (low), snare (mid+transient), hihat (high)
- [ ] Map to GM drum MIDI for tab renderer
- [ ] Current: ~122 hits detected for 3-min song. Target: 800+

### A4. Improve stem separation quality
- [ ] Test `htdemucs_ft` model as primary (fine-tuned, generally better)
- [ ] Add `--shifts 2` option for higher quality (2x processing time)
- [ ] Disable stereo splitting by default — only enable on genuinely wide L-R mixes
- [ ] Use Basic Pitch confidence as stem quality signal — hide low-quality stems
- [ ] Fix spectral classifier: replace hardcoded thresholds with robust approach

### A5. Full-song tabs with structure
- [ ] Remove 32-second cap in _render_string_tab
- [ ] Add measure numbers
- [ ] Smart truncation: show first verse+chorus, "..." for rest, expandable
- [ ] Only do this after A2 makes the full output playable

---

## Infrastructure

### B1. Clean up dead code
- [ ] Delete `recommendations.py` (unused, broken imports)
- [ ] Delete or archive `templates/index.html` (legacy, superseded by decompose.html)
- [ ] Fix `pkg_resources` import warning in venv
- [ ] Remove debug auth logging from app.py (lines 74-75)

### B2. Disk cleanup
- [ ] Delete intermediate files after processing (htdemucs/ dir + _raw_*.wav)
- [ ] TTL-based eviction for uploads/ and outputs/
- [ ] history.json cleanup or full migration to SQLite
- [ ] Configurable size limit

### B3. Fix error recovery UX
- [ ] If processing fails, show clear error with "Try Again" button
- [ ] Never get stuck on loading screen
- [ ] Handle network disconnection gracefully

### B4. Production hardening
- [ ] Set SESSION_COOKIE_SECURE = True for production
- [ ] Pin dependency versions in requirements.txt
- [ ] Add explicit scipy/librosa to requirements
- [ ] Vercel env var documentation

### B5. Keyboard shortcuts
- [ ] Spacebar: play/pause
- [ ] Left/Right arrows: ±5s seek
- [ ] Number keys: solo stem N
- [ ] Escape: stop

---

## Later / Nice-to-Haves

- [ ] Evaluate GPU-accelerated Demucs
- [ ] Guitar Pro / MusicXML export
- [ ] Interactive tab player (scroll with audio)
- [ ] User accounts
- [ ] Library page: save + organize analyses
- [ ] Practice page: jam tracks, scale trainer, chord trainer, progression looper
- [ ] Replace yt-dlp entirely with licensed audio source (long-term)
- [ ] Consider Omnizart/MT3 for transcription if Basic Pitch ceiling is too low
- [ ] Extract CSS/JS from inline templates to static files

---

## Completed

- [x] Full codebase analysis and documentation system (2026-03-25)
- [x] Deep audit of stem separation + tab generation quality (2026-03-25)
- [x] A1: Note detection quality — confidence filtering, per-instrument params, BPM grid (2026-03-25)
- [x] Fix: Search rate limiting — shared cooldown, 15s clamp, frontend auto-retry (2026-03-25)
- [x] Fix: Disabled background Spotify calls in recommendations (2026-03-25)
- [x] Fix: Fallback search from local data when Spotify is rate-limited (2026-03-25)
- [x] Fix: Demucs crash handling, job timeouts, infinite polling prevention (2026-03-25)
- [x] Perf: Instant results from cache, parallel audio decode (2026-03-25)
- [x] Fix: History validation, placeholder entry removal (2026-03-25)
- [x] Fix: lzma issue, stable pipeline, partial result recovery (2026-03-25)
- [x] Password gate with session-based auth (2026-03-25)
- [x] SQLite database for track metadata (2026-03-25)
- [x] UI unification: multi-page template architecture (base.html + 8 pages) (2026-03-25)
- [x] Figma-based redesign: dark theme, burnt orange accent, sharp rectangles (2026-03-25)
- [x] Landing page with hero, features, product preview (2026-03-25)
- [x] Studio/Learn page: theory explorer with filters + pagination (2026-03-25)
- [x] Library + Practice placeholder pages (2026-03-25)
- [x] About page: pipeline visualization, tech cards, principles (2026-03-25)
- [x] Section-based harmonic analysis replacing single progression (2026-03-26)
- [x] Harmony UI panel with per-section chords, roman numerals, patterns (2026-03-26)
- [x] Vercel deployment config (2026-03-25)
- [x] Estimated wait time in processing view (2026-03-25)
