# Progressive Analysis Overhaul — Claude Code Prompt

> **Goal**: Eliminate the blocking loading screen. Show instant results in ~2-3s, then silently run deep analysis in the background. Stems and tabs appear progressively when ready — no spinner, no timer, no "taking too long" screen.

---

## Architecture Overview

**Current flow (broken UX):**
1. User selects track → instant preview analysis (~2-3s) → results render
2. User clicks "Analyze full track" → full loading screen with timer → waits 1-5 min staring at spinner → deep results render (replaces everything)

**Target flow (progressive enhancement):**
1. User selects track → instant preview analysis (~2-3s) → results render immediately
2. Background: full YouTube track is already downloading via `/api/prefetch` (fires on track selection)
3. When prefetch completes → auto-trigger deep analysis silently (no user action needed)
4. As deep analysis completes → results upgrade **in-place**: stems appear in mixer, tabs appear in key panel, harmonic sections fill in — all with smooth animations
5. User never sees a loading screen, timer, or blocking state for deep analysis

---

## Files to Modify

### 1. `templates/decompose.html` (primary — most changes)
### 2. `app.py` (backend — moderate changes)

---

## Frontend Changes (`templates/decompose.html`)

### A. Remove / Deprecate These Functions

1. **`_showHeroLoading()`** (lines ~919-930) — Remove entirely. This is the full-screen blocking loading state with waveform animation for deep analysis. No longer needed.

2. **`buildProcessingBanner()`** (line ~932) — Remove. It's just a wrapper for `_showHeroLoading()`.

3. **`_startElapsedTimer()` / `_stopElapsedTimer()`** (lines ~1123-1146) — Remove the elapsed timer display entirely. The `proc-elapsed` element and its MM:SS counter should be deleted. Users should never see a ticking clock.

4. **`_showCancelBtnAfterDelay()`** (lines ~1151-1168) — Remove the "Taking too long? Try a different song" button. Replace with a much subtler mechanism (see below).

5. **`_showFullTrackReady()`** (lines ~565-582) — Remove. The upgrade banner ("Full track downloaded — deeper analysis available") is no longer needed because deep analysis auto-triggers silently.

6. **`_upgradeToFullTrack()`** (lines ~584-597) — Remove. No manual upgrade action needed.

7. **`_pollPrefetch()`** (lines ~540-563) — Rewrite. Instead of showing a banner when prefetch completes, it should auto-trigger deep analysis.

### B. New State Variables

Add these to the state block (~line 521):

```javascript
let _deepAnalysisTriggered = false;   // Prevents double-triggering deep analysis
let _deepAnalysisJobId = null;        // Job ID for the background deep analysis
let _deepPollTimer = null;            // Interval for polling deep analysis status
let _currentStemData = {};            // Tracks which stems have been loaded into the mixer
```

### C. Rewrite `selectTrack()` Flow

The current `selectTrack()` already fires prefetch before cache check — that's correct. Changes needed:

```javascript
async function selectTrack(t) {
  // Reset state (keep existing resets)
  selectedTrack = t;
  currentJobId = null;
  _currentMode = "preview";
  _analysisMode = "instant";
  _deepAnalysisTriggered = false;
  _deepAnalysisJobId = null;
  _prefetchId = null;
  _prefetchReady = false;
  if (_deepPollTimer) { clearInterval(_deepPollTimer); _deepPollTimer = null; }
  if (_prefetchPollTimer) { clearInterval(_prefetchPollTimer); _prefetchPollTimer = null; }

  // 1. Fire prefetch (keep existing logic — this is correct)
  if (t.yt_query) {
    fetch('/api/prefetch', { ... })
    .then(r => r.json())
    .then(d => {
      if (d.prefetch_id) {
        _prefetchId = d.prefetch_id;
        if (d.status === 'ready') {
          _prefetchReady = true;
          _autoTriggerDeepAnalysis();  // NEW: auto-trigger instead of showing banner
        } else {
          _pollPrefetch(d.prefetch_id);  // Rewritten to auto-trigger
        }
      }
    });
  }

  // 2. Check cache (keep existing logic)
  // 3. Start instant preview analysis (keep existing logic)
  _showInstantLoading();
  startProcessing();
}
```

### D. Rewrite `_pollPrefetch()` — Auto-Trigger Deep Analysis

```javascript
function _pollPrefetch(pfId) {
  _prefetchPollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/prefetch/${pfId}/status`);
      const d = await r.json();

      if (d.status === 'ready') {
        clearInterval(_prefetchPollTimer);
        _prefetchPollTimer = null;
        _prefetchReady = true;
        console.log('[prefetch] ready — auto-triggering deep analysis');
        _autoTriggerDeepAnalysis();
      } else if (d.status === 'failed') {
        clearInterval(_prefetchPollTimer);
        _prefetchPollTimer = null;
        console.warn('[prefetch] failed:', d.error);
        // Silently fail — user still has instant results, no degradation
      }
    } catch (e) {
      console.warn('[prefetch] poll error:', e);
    }
  }, 5000);
}
```

### E. New Function: `_autoTriggerDeepAnalysis()`

This is the core of the progressive enhancement. It fires ONLY after:
- Instant results have rendered (`_instantAlreadyRendered === true`)
- Prefetch is ready (`_prefetchReady === true`)
- Deep analysis hasn't already been triggered (`_deepAnalysisTriggered === false`)

```javascript
async function _autoTriggerDeepAnalysis() {
  // Guard: only trigger once, only after instant results are showing
  if (_deepAnalysisTriggered) return;
  if (!_instantAlreadyRendered) {
    // Instant results haven't rendered yet — will be called again after they render
    console.log('[deep] waiting for instant results to render first');
    return;
  }
  if (!_prefetchReady || !_prefetchId) return;

  _deepAnalysisTriggered = true;
  console.log('[deep] auto-triggering deep analysis with prefetch:', _prefetchId);

  // Show subtle "enhancing" indicator (NOT a loading screen)
  _showDeepAnalysisIndicator();

  try {
    // Step 1: Start download using prefetched audio (mode=full)
    const dlResp = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: 'full',
        query: selectedTrack.yt_query,
        track_id: selectedTrack.id || null,
        preview_url: null,  // Force full track, not preview
        artist: selectedTrack.artist,
        name: selectedTrack.name,
        image_url: selectedTrack.image_url || '',
        duration_ms: selectedTrack.duration_ms || 0,
        year: selectedTrack.year || '',
        artist_id: selectedTrack.artist_id || '',
      }),
    });
    const dlData = await dlResp.json();
    if (!dlData.job_id) throw new Error('No job_id returned');

    _deepAnalysisJobId = dlData.job_id;

    // Step 2: Wait for download to be ready, then trigger deep processing
    // Poll the download status, then call /api/process with analysis_mode=deep
    _pollDeepDownload(dlData.job_id);

  } catch (e) {
    console.warn('[deep] auto-trigger failed:', e);
    _hideDeepAnalysisIndicator();
    // Silent failure — user keeps instant results
  }
}
```

### F. New Function: `_pollDeepDownload(jobId)`

Polls the download job until audio is ready, then triggers deep processing:

```javascript
function _pollDeepDownload(jobId) {
  const poll = setInterval(async () => {
    try {
      const r = await fetch(`/api/status/${jobId}`);
      const d = await r.json();

      if (d.status === 'ready') {
        clearInterval(poll);
        // Audio is ready — now trigger deep analysis
        _startDeepProcessing(jobId);
      } else if (d.status === 'error' || d.status === 'upload_required' || d.status === 'preview_unavailable') {
        clearInterval(poll);
        console.warn('[deep] download failed:', d.error || d.status);
        _hideDeepAnalysisIndicator();
      }
      // If 'downloading' or 'processing', keep polling
    } catch (e) {
      console.warn('[deep] poll error:', e);
    }
  }, 2000);

  // Hard timeout: give up after 3 minutes
  setTimeout(() => {
    clearInterval(poll);
    if (!_deepAnalysisJobId) return;
    _hideDeepAnalysisIndicator();
    console.warn('[deep] download timed out');
  }, 180000);
}
```

### G. New Function: `_startDeepProcessing(jobId)`

Triggers the deep analysis pipeline and starts polling for progressive results:

```javascript
async function _startDeepProcessing(jobId) {
  try {
    const resp = await fetch(`/api/process/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        analysis_mode: 'deep',
        artist: selectedTrack.artist,
        name: selectedTrack.name,
        image_url: selectedTrack.image_url || '',
        duration_ms: selectedTrack.duration_ms || 0,
        year: selectedTrack.year || '',
        artist_id: selectedTrack.artist_id || '',
      }),
    });

    if (!resp.ok) throw new Error(`Process failed: ${resp.status}`);

    // Start polling for progressive results
    _pollDeepResults(jobId);

  } catch (e) {
    console.warn('[deep] processing trigger failed:', e);
    _hideDeepAnalysisIndicator();
  }
}
```

### H. New Function: `_pollDeepResults(jobId)` — Progressive UI Updates

This is where the magic happens. Instead of waiting for 100% completion and replacing the entire view, poll and progressively enhance the existing results view:

```javascript
function _pollDeepResults(jobId) {
  let prevStemCount = 0;

  _deepPollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/status/${jobId}`);
      const d = await r.json();

      // Progressive stem loading: check if new stems are available
      if (d.stems && Object.keys(d.stems).length > prevStemCount) {
        const newStems = Object.keys(d.stems).filter(k => !_currentStemData[k]);
        for (const stemName of newStems) {
          _addStemToMixer(stemName, d.stems[stemName]);
          _currentStemData[stemName] = true;
        }
        prevStemCount = Object.keys(d.stems).length;
      }

      // Update intelligence if we got better data from full track
      if (d.intelligence && d.intelligence.key) {
        _updateIntelligence(d.intelligence);
      }

      // Update harmonic sections if available
      if (d.intelligence && d.intelligence.sections) {
        _updateHarmonicSections(d.intelligence.sections);
      }

      // Add tabs when available
      if (d.tabs && Object.keys(d.tabs).length > 0) {
        _updateTabs(d.tabs);
      }

      // Update lyrics if we got better ones
      if (d.lyrics && d.lyrics !== jobData?.lyrics) {
        _updateLyrics(d.lyrics);
      }

      // Check completion
      if (d.status === 'complete' || d.status === 'partial') {
        clearInterval(_deepPollTimer);
        _deepPollTimer = null;
        _hideDeepAnalysisIndicator();
        _showDeepAnalysisComplete();

        // Update the stored jobData with full results
        jobData = d;
        currentJobId = jobId;

        console.log(`[deep] analysis ${d.status} — all progressive updates applied`);
      } else if (d.status === 'error') {
        clearInterval(_deepPollTimer);
        _deepPollTimer = null;
        _hideDeepAnalysisIndicator();
        console.warn('[deep] analysis failed:', d.error);
        // User keeps instant results — no degradation
      }

      // Update progress text in the subtle indicator
      if (d.progress) {
        _updateDeepProgressText(d.progress);
      }

    } catch (e) {
      console.warn('[deep] result poll error:', e);
    }
  }, 2500);

  // Hard timeout: 5 minutes
  setTimeout(() => {
    if (_deepPollTimer) {
      clearInterval(_deepPollTimer);
      _deepPollTimer = null;
      _hideDeepAnalysisIndicator();
      console.warn('[deep] analysis timed out');
    }
  }, 300000);
}
```

### I. Progressive UI Update Functions

These functions modify the **already-rendered** instant results view in-place:

#### `_addStemToMixer(stemName, stemData)`
```javascript
function _addStemToMixer(stemName, stemData) {
  const mixerEl = document.getElementById('mixer-channels');
  if (!mixerEl) return;

  // If this is the first stem, we need to upgrade from preview-only player
  // to full multi-channel mixer
  if (Object.keys(_currentStemData).length === 0) {
    _upgradeToStemMixer();
  }

  // Create channel strip for this stem (reuse existing renderResults channel HTML)
  const channel = _createChannelStrip(stemName, stemData);
  channel.style.opacity = '0';
  channel.style.transform = 'translateY(8px)';
  mixerEl.appendChild(channel);

  // Animate in
  requestAnimationFrame(() => {
    channel.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
    channel.style.opacity = '1';
    channel.style.transform = 'translateY(0)';
  });

  // Load audio buffer for this stem
  _loadStemAudio(stemName, stemData.url);
}
```

#### `_upgradeToStemMixer()`
Transitions the simple preview player to a full multi-stem mixer:
- Keep the transport bar (play/pause, seek, time)
- Remove the single "Preview" channel
- Prepare the mixer-channels container for individual stem channels
- Animate the transition smoothly

#### `_updateIntelligence(newIntel)`
Update the meta chips (Key, BPM, Genre) if the full-track analysis produced more accurate values:
```javascript
function _updateIntelligence(newIntel) {
  // Only update if values actually changed
  const metaEl = document.getElementById('results-meta');
  if (!metaEl) return;

  // Animate chip updates with a subtle flash
  // Update key chip, BPM chip, etc.
}
```

#### `_updateHarmonicSections(sections)`
Populate the `#harmonic-sections` container with chord progression data. Animate sections appearing.

#### `_updateTabs(tabs)`
Add tab data to the Key panel. If tabs weren't previously shown, add a visual indicator on the "Key" nav button (e.g., a dot or subtle glow).

#### `_updateLyrics(lyrics)`
Replace lyrics content if the deep analysis found better/more complete lyrics.

### J. Subtle Deep Analysis Indicator

Instead of a full loading screen, show a **minimal, non-blocking indicator** that deep analysis is running. Two options:

**Option A: Status pill in results header**
```javascript
function _showDeepAnalysisIndicator() {
  const banner = document.getElementById('results-banner');
  if (!banner) return;

  const pill = document.createElement('div');
  pill.id = 'deep-analysis-pill';
  pill.innerHTML = `
    <div class="deep-pill">
      <div class="deep-pill-dot"></div>
      <span class="deep-pill-text">Enhancing analysis...</span>
    </div>
  `;
  banner.parentElement.insertBefore(pill, banner.nextSibling);
}

function _hideDeepAnalysisIndicator() {
  const pill = document.getElementById('deep-analysis-pill');
  if (pill) {
    pill.style.opacity = '0';
    setTimeout(() => pill.remove(), 300);
  }
}

function _showDeepAnalysisComplete() {
  // Brief flash or toast: "Full analysis complete" — auto-dismiss after 3s
}

function _updateDeepProgressText(text) {
  const el = document.querySelector('.deep-pill-text');
  if (el) el.textContent = text;
}
```

**CSS for the pill:**
```css
.deep-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  background: rgba(255,255,255,0.06);
  font-size: 12px;
  color: rgba(255,255,255,0.5);
  margin-top: 8px;
}
.deep-pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4ade80;
  animation: deepPulse 1.5s ease-in-out infinite;
}
@keyframes deepPulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}
```

### K. Modify `renderInstantResults()` — Add Hook for Auto-Trigger

At the END of `renderInstantResults()`, after all instant content has rendered, add:

```javascript
// After rendering instant results, check if prefetch is ready to auto-trigger deep
_instantAlreadyRendered = true;
if (_prefetchReady && !_deepAnalysisTriggered) {
  console.log('[instant] results rendered, prefetch ready — triggering deep analysis');
  _autoTriggerDeepAnalysis();
}
```

This handles the race condition where prefetch finishes BEFORE instant results render.

### L. Modify `startProcessing()` — Instant Mode Only

The `startProcessing()` function currently handles both instant and deep modes. After this overhaul:
- **Instant path**: Keep as-is — it handles preview download + instant analysis
- **Deep path**: Remove the deep analysis branch from `startProcessing()`. Deep analysis is now triggered automatically by `_autoTriggerDeepAnalysis()`, not by user action

Remove from `startProcessing()`:
- The `else if (_analysisMode === 'deep')` branch
- Calls to `buildProcessingBanner()`
- Calls to `_showHeroLoading()`
- Loading message rotation for stems
- The `pollStatus()` call for deep analysis

### M. Keep `pollStatus()` but Only for Edge Cases

`pollStatus()` can remain as a fallback for:
- Jobs that were already in-progress when the page loaded
- Cache miss scenarios
- But it should NOT be the primary path for deep analysis anymore

### N. Remove Loading UI Elements

From the HTML, remove or hide:
- The elapsed timer element (`proc-elapsed`)
- The "Taking too long? Try a different song" button
- The full-screen waveform loading overlay (the `hero-loading` div can stay hidden as fallback but should never show for the progressive flow)

### O. Cancel / Back Button

Replace the aggressive "Taking too long?" pattern with:
- A subtle "Back to search" link in the results header (always visible)
- If deep analysis has been running for >60 seconds with no stems yet, show a **small, muted** text: "Deep analysis in progress..." — no alarm, no timer
- If deep analysis fails entirely, silently hide the indicator. User keeps full instant results.

---

## Backend Changes (`app.py`)

### A. Expose Progressive Status in `/api/status/<job_id>`

Currently the status endpoint returns the full job dict only on completion. For progressive enhancement, stems should be available as they complete.

**Modify the deep analysis `run()` thread** (lines ~857-1132):

After Stage 1 (stem separation), immediately update `jobs[job_id]["stems"]` with the stem URLs so the frontend can start loading them while tabs/harmonic analysis continues:

```python
# Stage 1: Stem separation
stems = separate_stems(audio_path, job_id, progress_callback=on_progress)
# Make stems available IMMEDIATELY for progressive loading
jobs[job_id]["stems"] = stems
jobs[job_id]["progress"] = "Stems ready — analyzing harmony..."
```

After Stage 3 (tab generation), update tabs immediately:
```python
# Stage 3: Tab generation
tabs = generate_tabs(...)
jobs[job_id]["tabs"] = tabs
jobs[job_id]["progress"] = "Analyzing harmony..."
```

After Stage 5 (harmonic analysis), update intelligence:
```python
# Stage 5: Harmonic analysis
intelligence = analyze_song_from_notes(...)
jobs[job_id]["intelligence"] = intelligence
jobs[job_id]["progress"] = "Finishing up..."
```

**Key principle**: Each stage writes its results to `jobs[job_id]` immediately, not just at the end in `_finalize()`. The frontend polls and picks up new data as it appears.

### B. New Endpoint: `/api/deep-analyze` (Optional Optimization)

Instead of making the frontend orchestrate download → wait → process → poll, create a single endpoint that does it all:

```python
@app.route("/api/deep-analyze", methods=["POST"])
def deep_analyze():
    """
    Combined endpoint: uses prefetched audio to run deep analysis.
    Returns job_id immediately; frontend polls /api/status for progressive results.
    """
    data = request.json
    track_id = data.get("track_id")
    prefetch_id = data.get("prefetch_id")

    # Find prefetched audio
    with _bg_lock:
        bg = _bg_downloads.get(track_id) or _bg_downloads.get(prefetch_id)

    if not bg or bg["status"] != "ready":
        return jsonify({"error": "No prefetched audio available"}), 400

    audio_path = bg["audio_path"]
    job_id = uuid.uuid4().hex[:8]

    # Create job and start deep analysis immediately
    jobs[job_id] = {
        "status": "processing",
        "progress": "Starting deep analysis...",
        "audio_path": audio_path,
        "audio_source": "youtube" if bg.get("is_full_track") else "preview",
        "audio_mode": "full" if bg.get("is_full_track") else "preview",
    }

    # Run deep analysis in background thread
    threading.Thread(target=_run_deep_pipeline, args=(job_id, audio_path, data), daemon=True).start()

    return jsonify({"job_id": job_id, "status": "processing"})
```

This simplifies the frontend from 3 steps (download → wait → process) to 1 step (deep-analyze → poll).

---

## CSS Additions

```css
/* Progressive stem appearance */
.stem-channel-enter {
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 0.4s ease, transform 0.4s ease;
}
.stem-channel-enter.visible {
  opacity: 1;
  transform: translateY(0);
}

/* Deep analysis complete toast */
.deep-complete-toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%) translateY(20px);
  background: rgba(30, 30, 30, 0.95);
  border: 1px solid rgba(74, 222, 128, 0.3);
  color: #fff;
  padding: 10px 20px;
  border-radius: 12px;
  font-size: 13px;
  opacity: 0;
  transition: all 0.4s ease;
  z-index: 100;
}
.deep-complete-toast.show {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}

/* Nav button indicator dot (new content available) */
.results-nav-btn .new-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4ade80;
  display: inline-block;
  margin-left: 6px;
  animation: deepPulse 1.5s ease-in-out infinite;
}
```

---

## Edge Cases to Handle

1. **User switches track while deep analysis is running**: Clear `_deepPollTimer`, reset `_deepAnalysisTriggered`, abandon the in-flight deep job. The `selectTrack()` reset block already handles most of this.

2. **Prefetch fails**: Silent failure. User keeps instant results with no indication anything went wrong. No degradation.

3. **Deep analysis fails after stems are loaded**: Keep the stems that loaded. Show "partial" state. Hide the progress indicator.

4. **Prefetch completes before instant results render**: The guard in `_autoTriggerDeepAnalysis()` (`if (!_instantAlreadyRendered) return`) handles this. The hook at the end of `renderInstantResults()` re-checks and triggers.

5. **Cache hit with existing deep analysis**: If `selectTrack()` finds cached results with stems, render them directly (existing behavior). Skip prefetch and deep analysis entirely.

6. **Cache hit with only instant results (no stems)**: Render instant results from cache, but still fire prefetch and auto-trigger deep analysis to enhance.

7. **No yt_query available**: Some tracks may not have a YouTube query. In this case, prefetch never fires, deep analysis never triggers. User gets instant results only. This is fine.

8. **User is on the Key/Lyrics/Recommended tab when stems arrive**: The Mix tab should get a subtle indicator (green dot) showing new content is available. Don't force-switch tabs.

---

## Testing Checklist

- [ ] Select a track → instant results appear in <3s
- [ ] No loading screen, timer, or "taking too long" button visible at any point
- [ ] Deep analysis pill appears shortly after instant results
- [ ] Stems progressively appear in the mixer (animate in one by one)
- [ ] Tab navigation works while deep analysis is running
- [ ] Switching tracks cancels in-flight deep analysis
- [ ] Prefetch failure → no visible error, instant results remain
- [ ] Deep analysis failure → no visible error, instant results remain
- [ ] Cached results with stems load directly (no re-analysis)
- [ ] Full flow works on Render deployment (not just localhost)
- [ ] Audio playback works for both preview-only and stem modes

---

## Summary of What Gets Removed

| Element | Why |
|---|---|
| `_showHeroLoading()` | Replaced by non-blocking progressive flow |
| `buildProcessingBanner()` | Wrapper for above |
| `_startElapsedTimer()` / `_stopElapsedTimer()` | No timer — ever |
| `proc-elapsed` element | Timer display gone |
| `_showCancelBtnAfterDelay()` | No "taking too long" panic button |
| `_showFullTrackReady()` | No manual upgrade banner |
| `_upgradeToFullTrack()` | Deep analysis auto-triggers |
| Deep branch in `startProcessing()` | Deep analysis triggered by `_autoTriggerDeepAnalysis()` |
| `processBtn` click handler for deep mode | No manual deep trigger |

## Summary of What Gets Added

| Element | Purpose |
|---|---|
| `_autoTriggerDeepAnalysis()` | Fires deep analysis when prefetch ready + instant rendered |
| `_pollDeepDownload()` | Waits for full audio to be ready |
| `_startDeepProcessing()` | Triggers `/api/process` with deep mode |
| `_pollDeepResults()` | Polls and progressively updates UI |
| `_addStemToMixer()` | Animates individual stems into mixer |
| `_upgradeToStemMixer()` | Transitions preview player → multi-stem mixer |
| `_updateIntelligence()` | Updates meta chips with full-track data |
| `_updateHarmonicSections()` | Adds chord sections progressively |
| `_updateTabs()` | Adds tab data to Key panel |
| `_showDeepAnalysisIndicator()` | Subtle non-blocking progress pill |
| `_hideDeepAnalysisIndicator()` | Removes pill on complete/fail |
| `_showDeepAnalysisComplete()` | Brief toast notification |
| `/api/deep-analyze` endpoint | Combined prefetch→deep in one call (optional) |
| Progressive `jobs[job_id]` updates | Backend writes stems/tabs/intel as each stage completes |
