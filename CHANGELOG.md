# CHANGELOG.md — riffa

> Every code change is logged here. Read before making changes. Update after every change.

---

## Current State

### What the app currently supports
- Spotify search → YouTube download → Demucs stem separation → Basic Pitch tab generation
- Key, BPM, and chord progression detection
- Genius lyrics fetching
- Last.fm tags and similar track recommendations
- Web Audio API stem mixer with volume/mute/solo/seek/loop/transpose
- Unified results view with Mix / Tab / Lyrics / Similar tabs
- Song history with versioned result caching
- Landing page with discovery rail, capability band, product preview

### What is in progress
- A1 complete: note detection quality improvements
- Search fix complete: fallback search from local data when Spotify is rate-limited
- Awaiting approval to proceed to A2 (position-aware fret assignment)

### What is broken or incomplete
- `recommendations.py` — dead code with broken imports, not used anywhere
- "Connect Spotify" button — UI placeholder only, no functionality
- No disk cleanup — 22GB+ in outputs/, growing unbounded
- No error recovery if processing fails mid-pipeline (frontend can get stuck)
- Tab generation limited to first 32 seconds
- No mobile-optimized layout
- Fret assignment still not position-aware (A2)
- Drums still use Basic Pitch instead of onset detection (A3)
- API credentials committed to git history

---

## Pre-History

### [2026-03-24 — Initial development]

#### Change Summary
- Initial commit: full working pipeline from search to results
- Second commit (WIP): UI improvements, history system, tab fixes, artwork handling

#### Files Modified
- All files created/modified across 2 commits (initial commit + WIP end-of-day save)

#### Key Decisions Made
- Single HTML file for entire frontend (fast iteration, but scaling concern)
- Demucs 6-stem model with 4-stem fallback
- Client-credentials Spotify flow (no user auth needed)
- yt-dlp for audio acquisition
- JSON file for history instead of database
- Versioned caching system (ANALYSIS_VERSION = "v3")
- Web scraping for chord detection with audio-based fallback
- Static discovery data to avoid Spotify API calls on page load

---

### [2026-03-25 — Documentation system created]

#### Change Summary
- Created PROJECT_CONTEXT.md, CHANGELOG.md, TODO.md as production-grade documentation
- Full codebase analysis completed

#### Files Modified
- `PROJECT_CONTEXT.md`: created — full project context document
- `CHANGELOG.md`: created — this file
- `TODO.md`: created — prioritized task tracking

#### Reason
- Establish engineering discipline before making code changes
- Enable any engineer to understand the system and continue development safely

#### Impact
- Improves: onboarding, decision-making, change tracking
- Risks: none (no code changes)

#### How to Verify
- Read each file and confirm accuracy against current codebase

---

### [2026-03-25 — Deep audit of core output quality]

#### Change Summary
- Examined actual generated output files (tabs, note CSVs, stem structures) for real songs
- Identified critical quality issues in note detection, tab rendering, and drum transcription
- Revised execution plan to prioritize product quality over infrastructure

#### Files Modified
- `TODO.md`: restructured into Track A (product quality) and Track B (infrastructure)
- `PROJECT_CONTEXT.md`: updated Known Limitations and Current Priorities with specific findings

#### Key Findings
1. **Basic Pitch confidence scores are ignored.** The `extra_0` column in note CSVs contains confidence (0-1). Many notes at 0.25-0.35 confidence are rendered as definitive tab entries. Filtering below 0.4 would eliminate most ghost notes.
2. **Fret assignment is unplayable.** Algorithm picks globally lowest fret without tracking hand position. Results in fret 0 → fret 17 jumps within a single beat.
3. **Drum detection captures ~10% of actual hits.** Basic Pitch (a pitched note detector) is fundamentally wrong for percussion. 122 hits detected for a 3-minute song; should be 800+.
4. **Quantization grid hardcoded to 120 BPM.** Uses 0.25s time slots regardless of actual detected tempo.
5. **Stereo splitter creates phantom instruments.** On narrowly-panned mixes, one guitar becomes "Lead Guitar 1" + "Guitar Overdub" + "Guitar Layer".
6. **Spectral classifier misclassifies instruments.** Distorted guitar can be labeled "Banjo" based on centroid > 3000 Hz.

#### Reason
- Dylan correctly identified that core output quality is not production-ready
- Must fix foundations before investing in UI polish

#### Impact
- Improves: decision-making, prioritization
- Risks: none (no code changes, analysis only)

#### How to Verify
- Examine output files in `outputs/affcede1/tabs/` and `outputs/b8b20224/tabs/`
- Compare lead_guitar_1_tab.txt against what a real tab looks like
- Count notes in drums_notes.csv (122 lines) vs expected for a 3-min rock song

---

### [2026-03-25 — A1: Note detection quality improvements]

#### Change Summary
- Added configurable per-instrument confidence thresholds for filtering ghost notes
- Added per-instrument Basic Pitch parameters (frequency range, onset sensitivity, note length)
- Replaced hardcoded 120 BPM quantization grid with actual detected BPM
- Restructured processing order: BPM is now detected before tab rendering
- Added confidence distribution logging per stem for future threshold tuning

#### Files Modified
- `processor.py`:
  - Added `INSTRUMENT_CONFIGS` dict (line ~38): per-instrument Basic Pitch params + confidence thresholds
  - Added `_get_instrument_config()`: lookup function for instrument configs
  - Modified `generate_tabs()`: accepts `bpm` param, selects per-instrument Basic Pitch settings, logs confidence stats
  - Added `_count_confident_notes()`: counts notes above threshold for quality gate
  - Added `_log_confidence_stats()`: logs confidence distribution per stem (histogram buckets)
  - Modified `_normalize_note_events()`: renames `extra_0` to `confidence`, ensures column always exists
  - Modified `render_ascii_tab()`: accepts `bpm` and `confidence_threshold`, filters before rendering
  - Added `_filter_by_confidence()`: filters DataFrame by confidence column
  - Modified `_render_string_tab()`: accepts `bpm`, computes grid as `60/bpm/2` instead of hardcoded 0.25s
  - Modified `_render_drum_tab()`: accepts `bpm`, computes beat_duration from BPM
- `app.py`:
  - Restructured processing order: added early BPM detection step (runs Basic Pitch on first harmonic stem, estimates BPM before tab generation)
  - `generate_tabs()` calls now pass `bpm=detected_bpm`

#### Reason
- Tab output was rendering low-confidence ghost notes as definitive fret numbers
- Hardcoded 120 BPM grid caused notes to land on wrong beats for songs at other tempos
- Same Basic Pitch parameters for all instruments caused bleed detection (e.g., guitar frequencies in bass stem)

#### Impact
- Improves: tab accuracy, note density (fewer false positives), timing alignment
- Guitar: 421 ghost notes removed (25.3% of total) on test song "Take It Easy"
- Bass: 32 ghost notes removed (4.9%), frequency range now limited to 27-350 Hz
- Average confidence of kept guitar notes: 0.42 → 0.45 (median 0.40 → 0.43)
- Grid slots with collisions (>1 note): 68 → 49 (28% reduction in note overlap)
- Risks: some real quiet notes may be filtered (false negatives). Thresholds are configurable per instrument in INSTRUMENT_CONFIGS.

#### Implementation Details
- Confidence thresholds: guitar=0.35, bass=0.30, drums=0.15 (drums intentionally low — Basic Pitch is wrong for drums, A3 will replace it)
- BPM detection runs on the first non-drum stem's Basic Pitch output, before tab rendering
- If early BPM detection fails, falls back to 120 BPM (same as before)
- The `INSTRUMENT_CONFIGS` dict is designed for easy tuning — change thresholds without touching logic
- Confidence stats are logged to stdout per stem: total, kept, dropped, min/median/max, histogram

#### How to Verify
1. Process any song through the app
2. Check server logs for `[notes]` lines showing confidence distribution per stem
3. Compare guitar tab output: should have fewer isolated high-fret notes, more empty space where guitar isn't playing
4. For a song with known BPM (e.g., "Take It Easy" at ~138 BPM), verify the grid aligns with beats
5. Re-render existing note data: `python3 -c "from processor import _render_string_tab, _filter_by_confidence; ..."` (see test scripts in session)

#### What Is Still Wrong After A1
- Fret assignment still picks globally lowest fret — no position awareness (A2)
- Drums still use Basic Pitch instead of onset detection — nearly empty output (A3)
- Tab still capped at 32 seconds (A5)
- Strummed chords still rendered as individual notes, not chord symbols
- No validation on a full end-to-end run yet (BPM detection in app.py pipeline untested)

---

### [2026-03-25 — Fix: Search rate limiting]

#### Change Summary
- Fixed persistent "Too many searches" by sharing cooldown state between all Spotify callers
- Added frontend cooldown: blocks search during backoff, shows countdown, auto-retries
- All cooldowns clamped to max 15 seconds, always replaced (never accumulated)

#### Files Modified
- `spotify_search.py`:
  - Added `_set_cooldown(retry_after)`: centralized cooldown setter. Clamps to 15s max, always replaces.
  - Modified `search_spotify()`: checks shared `_rate_limit_until` before calling Spotify API. Sets cooldown via `_set_cooldown()` on 429. Clears cooldown on successful call. Logs: when blocked, when cooldown set, when cleared.
  - Modified `_safe_search()`: removed duplicate cooldown-setting logic — `search_spotify()` now handles it. Still checks cooldown before calling.
- `templates/index.html`:
  - Added `_searchBackoffUntil` (client-side cooldown timestamp) and `_MAX_SEARCH_COOLDOWN=15`
  - Modified `doSearch()`: early-exits with countdown message during cooldown. On 429: clamps `retry_after` to 15s, sets cooldown (always replaces), schedules auto-retry that explicitly clears cooldown and re-invokes search.

#### Reason
**Root cause:** `search_spotify()` (called by the user's search route) did not check `_rate_limit_until`. Background recommendation threads call `_safe_search()` which did check it. When recommendations got a 429, the cooldown was set — but the user's next search call bypassed it, hit Spotify again, got 429 again. The error persisted because each path kept re-triggering the limit independently.

**Why it felt permanent:** The frontend showed the error message but didn't prevent the next keystroke from firing another request. Each debounced character triggered another failed API call, each extending the cooldown window.

#### Impact
- Improves: search reliability — cooldown is now shared, clamped, and self-clearing
- User-facing: during cooldown, shows "Search available in Xs..." then auto-retries
- Risks: none — this is strictly a bug fix. No behavioral changes to happy path.

#### Implementation Details
- Cooldown is always replaced, never accumulated: `_set_cooldown()` sets `_rate_limit_until = now + clamped`
- Max cooldown is 15s (both backend `_MAX_COOLDOWN` and frontend `_MAX_SEARCH_COOLDOWN`)
- Successful API call clears residual cooldown (`_rate_limit_until = 0`)
- Frontend `setTimeout` explicitly clears `_searchBackoffUntil = 0` before auto-retry
- `_safe_search()` no longer sets cooldown separately — it delegates to `search_spotify()` which handles it

#### How to Verify
1. Restart the app — search should work immediately (no residual cooldown)
2. Type rapidly — debounce (450ms) prevents most API calls; should not trigger rate limit
3. If rate limited: search shows "Search available in Xs...", then resumes automatically
4. After processing a song (which fires 3 recommendation searches), user search should still work
5. Server logs show `[spotify] cooldown set: Ns`, `[spotify] cooldown cleared` at appropriate times
6. No cooldown should ever exceed 15 seconds

---

### [2026-03-25 — Fix: Eliminate background Spotify API calls]

#### Change Summary
- Disabled all Spotify search calls in `get_recommendations_for_track()`
- Spotify API is now used ONLY for user-initiated search
- Recommendations still work via Last.fm (same_style pool)

#### Files Modified
- `spotify_search.py`:
  - `get_recommendations_for_track()`: removed 3 `_safe_search()` calls that previously fetched "more_like_this" (same artist) and "around_this_time" (year range) from Spotify. Function now returns empty pools immediately. Last.fm enrichment in app.py fills same_style independently.

#### Reason
The previous rate limit fix (shared cooldown, 15s clamp) improved the timer behavior but didn't solve the underlying problem: Spotify's rate limit is account-level and server-side. Each song processing fired 3 Spotify searches for recommendations, consuming quota that starved user search. Even after server restart, Spotify remembered recent calls and returned 429 on the user's first search.

#### Impact
- Spotify API calls per song: 4-5 → 1 (user search only)
- "More Like This" and "Around This Time" recommendation pools will be empty
- "Similar Style" still populated by Last.fm (no Spotify dependency)
- User search should no longer enter cooldown during normal use

#### How to Verify
1. Restart server, refresh browser — first search should return results, no cooldown
2. Process a song — server logs should show `[recs] skipping Spotify searches`, zero `[spotify]` lines during processing
3. Search immediately after processing starts — should work without cooldown
4. Results page "Similar" tab should still show "Similar Style" tracks from Last.fm

---

### [2026-03-25 — Fix: Fallback search when Spotify is rate-limited]

#### Change Summary
- `search_spotify()` no longer raises errors on rate limit — falls back to local search
- Local search matches query against history.json entries + static discovery tracks
- Returns results in identical format to Spotify results — frontend needs no changes
- Spotify live results resume automatically when rate limit clears

#### Files Modified
- `spotify_search.py`:
  - Modified `search_spotify()`: on 429 or network error, calls `_fallback_search()` instead of raising `RateLimitError`. Function now always returns a list, never raises.
  - Added `_DISCOVERY_TRACKS`: static list of 11 discovery tracks (same data as `app.py:_STATIC_DISCOVERY`, duplicated to avoid circular import)
  - Added `_load_history_tracks()`: reads `history.json` and converts entries to search result format
  - Added `_fallback_search(query, limit)`: substring matches query against name/artist in history + discovery pool, scores by match quality, returns top results

#### Reason
Spotify rate-limited these client credentials at the account level (`Retry-After: 1781` — 29 minutes). The first API call from a fresh process gets 429. No code change to cooldown logic can fix this — Spotify's servers reject the request before our code runs. Users need to be able to search regardless.

#### Impact
- Search always returns results (live or fallback) — never shows rate limit errors
- Fallback results limited to previously processed songs + 11 discovery tracks
- Songs not in local data return 0 results (user sees "No results found")
- When Spotify rate limit clears, live results resume automatically (no restart needed)

#### How to Verify
1. Restart server, search for "Eagles" — should return results immediately from fallback
2. Search for "Hotel California" — should return 1 result
3. Server logs show `[spotify] 429 from Spotify — using fallback search` then `[fallback] query='...' → N results`
4. Search for something not in history/discovery (e.g., "Taylor Swift") — returns 0 results (honest)
5. Frontend shows results normally — no error messages, no cooldown timer
