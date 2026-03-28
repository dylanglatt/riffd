# Progressive Analysis Overhaul v2 — Claude Code Implementation Prompt

> **Read this entire document before writing any code.**

---

## The Problem

The current app shows a blocking loading screen (waveform animation, "Preparing audio...", elapsed timer, "Taking too long?" button) while both preview and deep analysis run. This feels slow, anxious, and unpolished. We're eliminating it entirely.

---

## The Vision

### User Flow (exact spec — implement this precisely):

1. **User selects a track** → Results view renders in ~2-3 seconds with instant analysis (key, BPM, lyrics, insight, recommendations). **No loading screen. No spinner. No timer. No waveform animation.** The results just appear.

2. **The Mix tab** shows a single preview audio player at the top. Below it, a **call-to-action card** centered in the mixer area:
   ```
   Isolate Instruments
   Separate vocals, drums, bass, and more into individual tracks
   [ Separate Stems ]
   ```
   The button is always clickable. Behind the scenes, the full YouTube track is already downloading via `/api/prefetch` (fires on track selection). The user doesn't know this.

3. **The "Separate Stems" button has two visual states** (both clickable):
   - **Prefetch in progress**: Button has a subtle circular progress indicator on its icon (like an iOS download ring). Clicking it works — it just waits for prefetch to finish, then starts stem separation.
   - **Prefetch complete**: The progress ring fills and disappears. Button gets a subtle brightness lift. The icon shifts from a download arrow to a waveform/separation icon. User feels "it's ready" without understanding why.

4. **User clicks the button** → The CTA card transforms into a **segmented progress stepper**:
   ```
   [ Downloading ✓ ] → [ Separating ● ·· ] → [ Analyzing ]
   ```
   Each segment is a labeled stage. Active one has smooth fill animation. Completed ones get a check. Below the stepper: a single context line like "Isolating vocals and drums..." that updates.

5. **User can still interact with everything** while this runs — browse Lyrics, check Key panel, explore Recommendations. Nothing is blocked. The progress stepper lives only in the Mix panel.

6. **As each stem completes**, it slides into the mixer below the progress stepper with a smooth fade-up animation. User can start playing the first stem immediately — they don't wait for all four.

7. **If the user is on another tab when stems arrive**, a small green dot appears on the "Mix" tab label (like an unread indicator). Don't force-switch tabs.

8. **When everything finishes**, the progress stepper collapses away smoothly. The full mixer is populated. A brief, subtle shimmer animation plays across all channel strips when the user first interacts with the completed mixer (hits play or drags a fader). Then never again for that track.

9. **No "Preview" / "Full Track" labels anywhere.** The instant results are the results. Stems are an upgrade, not a correction. If deep analysis refines key/BPM, just animate the chip updating silently.

---

## Files to Modify

- `templates/decompose.html` — Primary. Most changes.
- `app.py` — Backend. Moderate changes for progressive status publishing.

---

## PART 1: Frontend (`templates/decompose.html`)

### A. REMOVE These Functions Entirely

Delete the following functions. They represent the old blocking loading UX:

1. **`_showHeroLoading()`** (lines ~1143-1156) — Full-screen blocking loading with waveform, timer, cancel button. Gone.

2. **`buildProcessingBanner()`** (line ~1157) — Wrapper for `_showHeroLoading()`. Gone.

3. **`_startElapsedTimer()`** (lines ~1356-1373) — The MM:SS counter. Gone. Delete the entire function.

4. **`_stopElapsedTimer()`** (lines ~1375-1379) — Timer cleanup. Gone.

5. **`_showCancelBtnAfterDelay()`** (lines ~1384-1407) — "Taking too long? Try a different song". Gone.

6. **`_autoTriggerDeepAnalysis()`** (lines ~576-613) — Auto-triggering is removed. User explicitly clicks the button. Gone.

7. **`_pollDeepDownload()`** (lines ~615-633) — Part of auto-trigger chain. Gone.

8. **`_startDeepProcessing()`** (lines ~635-661) — Part of auto-trigger chain. Will be rewritten as part of the new `_startStemSeparation()`.

9. **`_pollDeepResults()`** (lines ~663-721) — Will be completely rewritten as `_pollStemProgress()` with progressive stem loading.

10. **`_upgradeToStemMixer()`** (lines ~723-785) — Will be replaced by progressive stem insertion.

11. **`_showDeepAnalysisIndicator()` / `_hideDeepAnalysisIndicator()` / `_showDeepAnalysisComplete()`** (lines ~787-820) — The "Enhancing analysis..." pill. Gone. Replaced by the in-panel progress stepper.

12. **`_updateDeepProgressText()`** — Part of pill system. Gone.

Also remove from `renderInstantResults()` (at the very end, lines ~1929-1933):
```javascript
// REMOVE THIS BLOCK:
_instantAlreadyRendered = true;
if (_prefetchReady && !_deepAnalysisTriggered) {
  console.log('[instant] results rendered, prefetch ready — triggering deep analysis');
  _autoTriggerDeepAnalysis();
}
```
Replace with just:
```javascript
_instantAlreadyRendered = true;
```

### B. REMOVE These CSS Rules

Delete all CSS for:
- `.deep-pill`, `.deep-pill-dot`, `.deep-pill-text`
- `@keyframes deepPulse`
- `.deep-complete-toast`, `.deep-complete-toast.show`
- `.results-nav-btn .new-dot` (will be rewritten)

### C. REMOVE These State Variables

Remove from the state block:
- `_deepAnalysisTriggered` — no auto-trigger
- `_deepAnalysisJobId` — replaced by `_stemJobId`
- `_deepPollTimer` — replaced by `_stemPollTimer`
- `_currentStemData` — replaced by `_loadedStems`

### D. NEW State Variables

Add to the state block (~line 529):

```javascript
let _stemJobId = null;           // Job ID for stem separation
let _stemPollTimer = null;       // Interval for polling stem progress
let _loadedStems = {};           // Tracks which stems have been loaded into mixer
let _stemSeparationActive = false; // Whether stem separation is running
let _stemMixerReady = false;     // Whether the full mixer has been shown
let _firstMixerInteraction = false; // For the shimmer animation on first play
```

### E. REWRITE `_showInstantLoading()`

The current version still shows a loading state with a waveform-wrap and "Analyzing..." text on the confirm view. Replace it with a **zero-UI transition**: the confirm view should not show any loading state at all. Instead, when data is ready, jump straight to the results view.

The goal: user clicks a track, and results just *appear*. If there's a 1-2s wait while the preview downloads, show **only** the album art, title, and artist (the proc-hero section) with no loading indicators. The moment instant analysis completes, transition to view-results.

New implementation:
```javascript
function _showInstantLoading() {
  // Show ONLY the hero (album art + title) — no spinners, no text, no waveform
  _populateHero();
  document.getElementById("hero-actions").classList.add("hidden");
  const heroLoading = document.getElementById("hero-loading");
  heroLoading.classList.remove("hidden");
  // Hide everything in hero-loading — we just want the hero section visible
  const wvWrap = heroLoading.querySelector(".waveform-wrap");
  if (wvWrap) wvWrap.style.display = "none";
  const est = document.getElementById("proc-estimate");
  if (est) est.style.display = "none";
  const progLabel = document.getElementById("progress-label");
  if (progLabel) progLabel.style.display = "none";
  showView("confirm");
}
```

### F. REWRITE `selectTrack()`

Remove the auto-trigger hook from the prefetch callback. Keep prefetch firing (it should still download silently), but when it completes, just set `_prefetchReady = true` — don't trigger anything.

```javascript
async function selectTrack(t) {
  selectedTrack = t;
  currentJobId = null;
  setError("");
  _currentMode = "preview";
  _analysisMode = "instant";
  _previewJobData = null;
  _fullTrackInProgress = false;
  _instantAlreadyRendered = false;
  _prefetchId = null;
  _prefetchReady = false;
  _stemJobId = null;
  _stemSeparationActive = false;
  _stemMixerReady = false;
  _loadedStems = {};
  _firstMixerInteraction = false;
  if (_stemPollTimer) { clearInterval(_stemPollTimer); _stemPollTimer = null; }
  if (_prefetchPollTimer) { clearInterval(_prefetchPollTimer); _prefetchPollTimer = null; }

  console.log(`[ui] selectTrack → ${t.name} by ${t.artist} (id=${t.id||'none'})`);

  // ── Background prefetch: download full YouTube track silently ──
  if (t.yt_query) {
    console.log(`[prefetch] firing for: ${t.yt_query}`);
    fetch('/api/prefetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_id: t.id || null,
        yt_query: t.yt_query,
        artist: t.artist || '',
        name: t.name || '',
      }),
    })
    .then(r => r.json())
    .then(d => {
      if (d.prefetch_id) {
        _prefetchId = d.prefetch_id;
        console.log(`[prefetch] started: ${d.prefetch_id} (status=${d.status})`);
        if (d.status === 'ready') {
          _prefetchReady = true;
          _updateStemButton();  // Update button visual state
        } else {
          _pollPrefetch(d.prefetch_id);
        }
      }
    })
    .catch(e => console.warn('[prefetch] failed to start:', e));
  }

  // ── Check cache ──
  if (t.id) {
    try {
      const r = await fetch(`/api/track/${encodeURIComponent(t.id)}`);
      const d = await r.json();
      if (d.analysis_status === 'available' && d.analysis) {
        currentJobId = d.job_id;
        jobData = { status: "complete", tabs: d.analysis.tabs || {}, stems: d.analysis.stems || {}, intelligence: d.analysis.intelligence || {}, lyrics: d.analysis.lyrics || null, tags: d.analysis.tags || [], insight: d.analysis.insight || null, audio_source: d.analysis.audio_source || null, audio_mode: d.analysis.audio_mode || null, analysis_mode: d.analysis.analysis_mode || null };
        const hasStems = Object.keys(jobData.stems || {}).length > 0;
        if (hasStems) {
          renderResults(jobData);
        } else {
          renderInstantResults(jobData);
        }
        return;
      }
    } catch (e) { console.warn("[track] lookup failed:", e); }
  }

  // ── Start instant preview analysis ──
  _currentMode = "preview";
  _analysisMode = "instant";
  _showInstantLoading();
  startProcessing();
}
```

### G. REWRITE `_pollPrefetch()`

No more auto-triggering. Just track readiness and update the button state:

```javascript
function _pollPrefetch(pfId) {
  if (_prefetchPollTimer) clearInterval(_prefetchPollTimer);
  _prefetchPollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/prefetch/${pfId}/status`);
      const d = await r.json();
      if (d.status === 'ready') {
        _prefetchReady = true;
        clearInterval(_prefetchPollTimer);
        _prefetchPollTimer = null;
        console.log('[prefetch] ready');
        _updateStemButton();  // Visual update only — no auto-trigger
      } else if (d.status === 'failed') {
        clearInterval(_prefetchPollTimer);
        _prefetchPollTimer = null;
        console.warn('[prefetch] failed:', d.error);
      }
    } catch (e) {
      console.warn('[prefetch] poll error:', e);
    }
  }, 5000);
}
```

### H. REWRITE `renderInstantResults()`

The function is mostly correct. Key changes:

1. **Remove the auto-trigger block at the end** (the `_autoTriggerDeepAnalysis()` call).

2. **Replace the Mix panel content.** Instead of just a bare "Preview" channel, render the preview player + the CTA card:

Replace the mixer-channels innerHTML section with:
```javascript
// Mix panel: preview player + stem separation CTA
const mixerChannels = document.getElementById("mixer-channels");
mixerChannels.innerHTML = `
  <div class="preview-channel">
    <div class="channel" id="ch-preview">
      <div class="channel-name">Preview</div>
      <div class="channel-volume">
        <input type="range" class="vol-slider" min="0" max="100" value="100"
               oninput="setVolume('preview',this.value)" id="vol-preview"/>
        <span class="vol-label" id="vol-label-preview">100</span>
      </div>
      <button class="ch-btn" id="mute-preview" onclick="toggleMute('preview')">M</button>
      <button class="ch-btn" id="solo-preview" onclick="toggleSolo('preview')">S</button>
    </div>
  </div>
  <div class="stem-cta-card" id="stem-cta-card">
    <div class="stem-cta-inner">
      <div class="stem-cta-title">Isolate Instruments</div>
      <div class="stem-cta-desc">Separate vocals, drums, bass, and more into individual tracks</div>
      <button class="stem-cta-btn" id="stem-cta-btn" onclick="_startStemSeparation()">
        <span class="stem-btn-icon" id="stem-btn-icon">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <path d="M12 8v8M8 12h8"/>
          </svg>
        </span>
        <span class="stem-btn-text">Separate Stems</span>
        <span class="stem-btn-ring" id="stem-btn-ring"></span>
      </button>
    </div>
  </div>
`;
// Update button state based on prefetch
_updateStemButton();
```

3. **Remove the "Separate Stems" button from the results footer** — it now lives in the CTA card:
```javascript
const fullBtn = document.getElementById("full-track-btn");
if (fullBtn) fullBtn.style.display = "none";
```

### I. NEW FUNCTION: `_updateStemButton()`

Updates the "Separate Stems" button visual state based on prefetch status:

```javascript
function _updateStemButton() {
  const ring = document.getElementById('stem-btn-ring');
  const icon = document.getElementById('stem-btn-icon');
  const btn = document.getElementById('stem-cta-btn');
  if (!btn) return;

  if (_stemSeparationActive) {
    // Separation is running — button is replaced by progress stepper
    return;
  }

  if (_prefetchReady) {
    // Full track ready — button glows, icon changes to waveform
    btn.classList.add('stem-btn-ready');
    btn.classList.remove('stem-btn-loading');
    if (ring) ring.style.display = 'none';
    if (icon) icon.innerHTML = `
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M2 12h4l3-9 4 18 3-9h4"/>
      </svg>
    `;
  } else {
    // Still downloading — show subtle progress ring
    btn.classList.add('stem-btn-loading');
    btn.classList.remove('stem-btn-ready');
    if (ring) ring.style.display = '';
  }
}
```

### J. NEW FUNCTION: `_startStemSeparation()`

Called when user clicks "Separate Stems". This is the main entry point:

```javascript
async function _startStemSeparation() {
  if (_stemSeparationActive) return;
  _stemSeparationActive = true;

  console.log('[stems] user triggered stem separation');

  // Replace the CTA card with the progress stepper
  _showStemProgress();

  try {
    // Step 1: Get full audio (from prefetch or start fresh download)
    _updateStemStage('download', 'active');

    if (_prefetchReady && _prefetchId) {
      // Prefetch already done — reuse it via /api/download with mode=full
      const dlResp = await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: 'full',
          query: selectedTrack.yt_query,
          track_id: selectedTrack.id || null,
          preview_url: null,
          artist: selectedTrack.artist || '',
          name: selectedTrack.name || '',
          image_url: selectedTrack.image_url || '',
          duration_ms: selectedTrack.duration_ms || 0,
          year: selectedTrack.year || '',
          artist_id: selectedTrack.artist_id || '',
        }),
      });
      const dlData = await dlResp.json();
      if (!dlData.job_id) throw new Error('Download failed');
      _stemJobId = dlData.job_id;
      _updateStemStage('download', 'complete');
    } else {
      // Prefetch not ready — start download, poll until ready
      const dlResp = await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: 'full',
          query: selectedTrack.yt_query,
          track_id: selectedTrack.id || null,
          preview_url: null,
          artist: selectedTrack.artist || '',
          name: selectedTrack.name || '',
          image_url: selectedTrack.image_url || '',
          duration_ms: selectedTrack.duration_ms || 0,
          year: selectedTrack.year || '',
          artist_id: selectedTrack.artist_id || '',
        }),
      });
      const dlData = await dlResp.json();
      if (!dlData.job_id) throw new Error('Download failed');
      _stemJobId = dlData.job_id;

      // Poll download until ready
      await _waitForDownload(_stemJobId);
      _updateStemStage('download', 'complete');
    }

    // Step 2: Trigger deep processing
    _updateStemStage('separate', 'active');
    _updateStemContextText('Pulling apart the layers...');

    const procResp = await fetch(`/api/process/${_stemJobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_id: selectedTrack?.id || null,
        artist_id: selectedTrack?.artist_id || null,
        track_meta: selectedTrack || {},
        analysis_mode: 'deep',
      }),
    });
    const procData = await procResp.json();
    if (procData.error) throw new Error(procData.error);

    // Step 3: Poll for progressive results
    _pollStemProgress(_stemJobId);

  } catch (e) {
    console.warn('[stems] separation failed:', e);
    _stemSeparationActive = false;
    _showStemError(e.message);
  }
}
```

### K. NEW FUNCTION: `_waitForDownload(jobId)` — Returns a Promise

```javascript
function _waitForDownload(jobId) {
  return new Promise((resolve, reject) => {
    const poll = setInterval(async () => {
      try {
        const r = await fetch(`/api/status/${jobId}`);
        const d = await r.json();
        if (d.status === 'ready') {
          clearInterval(poll);
          resolve();
        } else if (d.status === 'error' || d.status === 'upload_required' || d.status === 'preview_unavailable') {
          clearInterval(poll);
          reject(new Error(d.error || 'Download failed'));
        }
        // Still downloading — keep polling
      } catch (e) {
        // Network error — keep trying
      }
    }, 2000);

    // Hard timeout: 3 minutes
    setTimeout(() => {
      clearInterval(poll);
      reject(new Error('Download timed out'));
    }, 180000);
  });
}
```

### L. NEW FUNCTION: `_showStemProgress()`

Replaces the CTA card with the segmented progress stepper:

```javascript
function _showStemProgress() {
  const card = document.getElementById('stem-cta-card');
  if (!card) return;

  card.innerHTML = `
    <div class="stem-progress" id="stem-progress">
      <div class="stem-stages">
        <div class="stem-stage" id="stage-download" data-stage="download">
          <div class="stage-indicator">
            <div class="stage-fill"></div>
            <svg class="stage-check" viewBox="0 0 16 16" width="12" height="12">
              <polyline points="3,8 7,12 13,4" fill="none" stroke="currentColor" stroke-width="2"/>
            </svg>
          </div>
          <span class="stage-label">Downloading</span>
        </div>
        <div class="stage-connector"></div>
        <div class="stem-stage" id="stage-separate" data-stage="separate">
          <div class="stage-indicator">
            <div class="stage-fill"></div>
            <svg class="stage-check" viewBox="0 0 16 16" width="12" height="12">
              <polyline points="3,8 7,12 13,4" fill="none" stroke="currentColor" stroke-width="2"/>
            </svg>
          </div>
          <span class="stage-label">Separating</span>
        </div>
        <div class="stage-connector"></div>
        <div class="stem-stage" id="stage-analyze" data-stage="analyze">
          <div class="stage-indicator">
            <div class="stage-fill"></div>
            <svg class="stage-check" viewBox="0 0 16 16" width="12" height="12">
              <polyline points="3,8 7,12 13,4" fill="none" stroke="currentColor" stroke-width="2"/>
            </svg>
          </div>
          <span class="stage-label">Analyzing</span>
        </div>
      </div>
      <div class="stem-context-text" id="stem-context-text">Downloading full track...</div>
    </div>
  `;
}
```

### M. NEW FUNCTION: `_updateStemStage(stage, state)`

Updates the visual state of a progress stage:

```javascript
function _updateStemStage(stage, state) {
  // stage: 'download' | 'separate' | 'analyze'
  // state: 'pending' | 'active' | 'complete'
  const el = document.getElementById(`stage-${stage}`);
  if (!el) return;

  el.classList.remove('stage-pending', 'stage-active', 'stage-complete');
  el.classList.add(`stage-${state}`);
}
```

### N. NEW FUNCTION: `_updateStemContextText(text)`

```javascript
function _updateStemContextText(text) {
  const el = document.getElementById('stem-context-text');
  if (el) {
    el.style.opacity = '0';
    setTimeout(() => {
      el.textContent = text;
      el.style.opacity = '1';
    }, 200);
  }
}
```

### O. NEW FUNCTION: `_pollStemProgress(jobId)` — The Core Progressive Loader

This replaces the old `_pollDeepResults()`. It progressively adds stems to the mixer as they become available:

```javascript
function _pollStemProgress(jobId) {
  _stemPollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/status/${jobId}`);
      const d = await r.json();

      // Progressive stem loading
      if (d.stems) {
        const newStems = Object.keys(d.stems).filter(k => !_loadedStems[k]);
        if (newStems.length > 0) {
          for (const stemName of newStems) {
            _addStemChannel(stemName, d.stems[stemName], jobId);
            _loadedStems[stemName] = true;
          }
          // Show green dot on Mix tab if user is on another tab
          _notifyMixTab();
          // Update context text
          const count = Object.keys(_loadedStems).length;
          _updateStemContextText(`${count} instrument${count > 1 ? 's' : ''} isolated...`);
        }
      }

      // Detect stage transitions from progress text
      if (d.progress) {
        if (d.progress.includes('Separating') || d.progress.includes('stems') || d.progress.includes('Demucs')) {
          _updateStemStage('separate', 'active');
          _updateStemContextText('Pulling apart the layers...');
        }
        if (d.progress.includes('tab') || d.progress.includes('Tab') || d.progress.includes('pitch')) {
          _updateStemStage('separate', 'complete');
          _updateStemStage('analyze', 'active');
          _updateStemContextText('Analyzing harmony and rhythm...');
        }
        if (d.progress.includes('harmonic') || d.progress.includes('Harmonic') || d.progress.includes('intel')) {
          _updateStemStage('analyze', 'active');
          _updateStemContextText('Detecting chord progressions...');
        }
      }

      // Publish tabs progressively
      if (d.tabs && Object.keys(d.tabs).length > 0) {
        // Tabs are available — could update Key panel here
      }

      // Update intelligence (key/BPM) if refined by full-track analysis
      if (d.intelligence && d.intelligence.key && d.intelligence.key !== 'Unknown') {
        _silentlyUpdateMetaChips(d.intelligence);
      }

      // Check completion
      if (d.status === 'complete' || d.status === 'partial') {
        clearInterval(_stemPollTimer);
        _stemPollTimer = null;
        _stemSeparationActive = false;
        _stemMixerReady = true;

        // Final updates
        _updateStemStage('download', 'complete');
        _updateStemStage('separate', 'complete');
        _updateStemStage('analyze', 'complete');
        _updateStemContextText('Complete');

        // Load any remaining stems
        if (d.stems) {
          Object.keys(d.stems).filter(k => !_loadedStems[k]).forEach(k => {
            _addStemChannel(k, d.stems[k], jobId);
            _loadedStems[k] = true;
          });
        }

        // Update harmonic sections
        if (d.intelligence && d.intelligence.harmonic_sections) {
          _renderHarmonicSections(d.intelligence.harmonic_sections);
        }

        // Update lyrics if better ones found
        if (d.lyrics) {
          const lyricsEl = document.getElementById('lyrics-content');
          if (lyricsEl) {
            lyricsEl.innerHTML = `<div class="lyrics-container">${_formatLyrics(d.lyrics)}</div>`;
          }
        }

        // Update insight/recommendations
        if (d.insight) {
          const llm = d.insight;
          _progressionNames = llm.progression_names || {};
          _smartRecs = llm.smart_recs || {};
          _keyContext = llm.key_context || "";
          _renderSmartRecs(_smartRecs);
          const keyCtxEl = document.getElementById("key-context");
          if (keyCtxEl) { keyCtxEl.textContent = _keyContext; keyCtxEl.style.display = _keyContext ? "block" : "none"; }
        }

        // Update global state
        jobData = d;
        currentJobId = jobId;

        // Collapse progress stepper after a brief delay
        setTimeout(() => {
          const progress = document.getElementById('stem-progress');
          if (progress) {
            progress.style.transition = 'opacity 0.4s ease, max-height 0.4s ease';
            progress.style.opacity = '0';
            progress.style.maxHeight = '0';
            progress.style.overflow = 'hidden';
            setTimeout(() => progress.remove(), 400);
          }
        }, 1500);

        // Remove the preview-only channel if stems are loaded
        const previewCh = document.querySelector('.preview-channel');
        if (previewCh && Object.keys(_loadedStems).length > 0) {
          previewCh.style.transition = 'opacity 0.3s ease';
          previewCh.style.opacity = '0';
          setTimeout(() => previewCh.remove(), 300);
        }

        // Notify mix tab
        _notifyMixTab();

        console.log(`[stems] separation complete — ${Object.keys(_loadedStems).length} stems loaded`);

      } else if (d.status === 'error') {
        clearInterval(_stemPollTimer);
        _stemPollTimer = null;
        _stemSeparationActive = false;
        _showStemError(d.error || 'Stem separation failed');
      }

    } catch (e) {
      console.warn('[stems] poll error:', e);
    }
  }, 2500);

  // Hard timeout: 5 minutes
  setTimeout(() => {
    if (_stemPollTimer) {
      clearInterval(_stemPollTimer);
      _stemPollTimer = null;
      _stemSeparationActive = false;
      _showStemError('Stem separation timed out. Try again.');
    }
  }, 300000);
}
```

### P. NEW FUNCTION: `_addStemChannel(stemName, stemData, jobId)`

Progressively adds a single stem to the mixer with animation:

```javascript
function _addStemChannel(stemName, stemData, jobId) {
  const mixerEl = document.getElementById('mixer-channels');
  if (!mixerEl) return;

  const label = formatStemLabel(stemName, (stemData && stemData.label) || "");
  const channel = document.createElement('div');
  channel.className = 'channel stem-channel-enter';
  channel.id = `ch-${stemName}`;
  channel.innerHTML = `
    <div class="channel-name">${esc(label)}</div>
    <div class="channel-volume">
      <input type="range" class="vol-slider" min="0" max="100" value="100"
             oninput="setVolume('${stemName}',this.value)" id="vol-${stemName}"/>
      <span class="vol-label" id="vol-label-${stemName}">100</span>
    </div>
    <button class="ch-btn" id="mute-${stemName}" onclick="toggleMute('${stemName}')">M</button>
    <button class="ch-btn" id="solo-${stemName}" onclick="toggleSolo('${stemName}')">S</button>
  `;

  // Insert before the progress stepper (if it exists), otherwise append
  const progress = document.getElementById('stem-progress');
  if (progress) {
    mixerEl.insertBefore(channel, progress.parentElement);
  } else {
    mixerEl.appendChild(channel);
  }

  // Animate in
  requestAnimationFrame(() => {
    channel.classList.add('visible');
  });

  // Initialize audio state for this stem
  stemVolumes[stemName] = 1;
  stemMuted[stemName] = false;
  stemSoloed[stemName] = false;

  // Load audio for this stem
  _loadSingleStemAudio(stemName, jobId);
}
```

### Q. NEW FUNCTION: `_loadSingleStemAudio(stemName, jobId)`

```javascript
async function _loadSingleStemAudio(stemName, jobId) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const r = await fetch(`/api/audio/${jobId}/${stemName}`);
    if (!r.ok) return;
    const ab = await r.arrayBuffer();
    const buf = await audioCtx.decodeAudioData(ab);
    stemBuffers[stemName] = buf;
    const g = audioCtx.createGain();
    g.connect(audioCtx.destination);
    stemGains[stemName] = g;

    // Update duration to match full track
    if (buf.duration > duration) {
      duration = buf.duration;
      document.getElementById("seek-bar").max = duration;
      updateTimeDisplay();
      _buildPlayerWaveform();
    }

    console.log(`[audio] stem loaded: ${stemName} (${buf.duration.toFixed(1)}s)`);
  } catch (e) {
    console.warn(`[audio] failed to load stem ${stemName}:`, e);
  }
}
```

### R. NEW FUNCTION: `_notifyMixTab()`

Shows green dot on Mix tab when user is on another panel:

```javascript
function _notifyMixTab() {
  const activePanel = document.querySelector('.results-nav-btn.active');
  if (activePanel && activePanel.dataset.panel !== 'mix') {
    const mixBtn = document.querySelector('.results-nav-btn[data-panel="mix"]');
    if (mixBtn && !mixBtn.querySelector('.stem-dot')) {
      const dot = document.createElement('span');
      dot.className = 'stem-dot';
      mixBtn.appendChild(dot);
    }
  }
}
```

Remove the dot when user switches to Mix tab. In the `switchPanel()` function, add:
```javascript
// Remove stem notification dot when switching to Mix
if (panel === 'mix') {
  const dot = document.querySelector('.results-nav-btn[data-panel="mix"] .stem-dot');
  if (dot) dot.remove();
}
```

### S. NEW FUNCTION: `_silentlyUpdateMetaChips(intel)`

If deep analysis produces different key/BPM, animate the update:

```javascript
function _silentlyUpdateMetaChips(intel) {
  const metaEl = document.getElementById('results-meta');
  if (!metaEl) return;

  // Check if values actually changed
  const currentKey = metaEl.querySelector('.mc-val:last-child')?.textContent;
  if (intel.key && intel.key !== currentKey) {
    // Rebuild chips with subtle flash animation
    let chips = '';
    const tags = jobData?.tags || [];
    if (tags.length) {
      const genre = tags[0].charAt(0).toUpperCase() + tags[0].slice(1);
      chips += `<span class="meta-chip"><span class="mc-label">Genre</span><span class="mc-val">${esc(genre)}</span></span>`;
    }
    const bpmVal = Math.round(intel.bpm || 0);
    const bpmConf = intel.bpm_confidence || 0;
    if (bpmVal > 0 && bpmConf >= 0.15) {
      const bpmLabel = bpmConf >= 0.4 ? "BPM" : "Est. BPM";
      chips += `<span class="meta-chip chip-updated"><span class="mc-label">${bpmLabel}</span><span class="mc-val">${bpmVal}</span></span>`;
    }
    if (intel.key && intel.key !== 'Unknown') {
      chips += `<span class="meta-chip chip-updated"><span class="mc-label">Key</span><span class="mc-val">${esc(intel.key)}</span></span>`;
    }
    metaEl.innerHTML = chips;
    songKey = (intel.key_num >= 0) ? { num: intel.key_num, mode: intel.mode_num, name: intel.key } : songKey;
    _initDecomposeKeyTab(intel.key);
  }
}
```

### T. NEW FUNCTION: `_showStemError(message)`

Shows error in the CTA area without blocking the rest of the UI:

```javascript
function _showStemError(message) {
  const card = document.getElementById('stem-cta-card');
  if (!card) return;

  card.innerHTML = `
    <div class="stem-cta-inner">
      <div class="stem-cta-desc" style="color: rgba(245,245,245,0.4);">${esc(message)}</div>
      <button class="stem-cta-btn" onclick="_startStemSeparation()" style="margin-top: 12px;">
        <span class="stem-btn-text">Try Again</span>
      </button>
    </div>
  `;
  _stemSeparationActive = false;
}
```

### U. MODIFY `resetApp()`

Update to clear new state variables:

Replace the old deep analysis cleanup:
```javascript
// OLD — remove these:
_deepAnalysisTriggered=false;_deepAnalysisJobId=null;_currentStemData={};
if(_deepPollTimer){clearInterval(_deepPollTimer);_deepPollTimer=null;}
_hideDeepAnalysisIndicator();

// NEW — add these:
_stemJobId=null;_stemSeparationActive=false;_stemMixerReady=false;_loadedStems={};_firstMixerInteraction=false;
if(_stemPollTimer){clearInterval(_stemPollTimer);_stemPollTimer=null;}
```

Also remove `_stopElapsedTimer()` and `_hideCancelBtn()` calls from resetApp.

### V. MODIFY `startProcessing()` Deep Branch

The deep branch in `startProcessing()` (lines ~1488-1516) should be simplified or removed. Deep analysis is now triggered only by `_startStemSeparation()`, not by `startProcessing()`. The deep branch can be replaced with:

```javascript
} else {
  // Deep analysis is handled by _startStemSeparation() — not here
  console.warn("[startProcessing] deep mode called directly — delegating to stem separation");
  _startStemSeparation();
}
```

---

## PART 2: New CSS

Add these styles. Remove the old `.deep-pill`, `.deep-complete-toast`, `.results-nav-btn .new-dot` CSS.

```css
/* ── Stem CTA Card ── */
.stem-cta-card {
  padding: 40px 24px;
  text-align: center;
}
.stem-cta-inner {
  max-width: 320px;
  margin: 0 auto;
}
.stem-cta-title {
  font-size: 1.125rem;
  font-weight: 600;
  color: rgba(245, 245, 245, 0.85);
  letter-spacing: -0.02em;
  margin-bottom: 8px;
}
.stem-cta-desc {
  font-size: 0.8125rem;
  color: rgba(245, 245, 245, 0.4);
  line-height: 1.5;
  margin-bottom: 20px;
}
.stem-cta-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 24px;
  border: 1px solid rgba(212, 105, 31, 0.3);
  background: rgba(212, 105, 31, 0.08);
  color: rgba(245, 245, 245, 0.8);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
  border-radius: 8px;
}
.stem-cta-btn:hover {
  background: rgba(212, 105, 31, 0.15);
  border-color: rgba(212, 105, 31, 0.5);
  color: #F5F5F5;
}
.stem-btn-icon {
  display: flex;
  align-items: center;
}
.stem-btn-ring {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid rgba(212, 105, 31, 0.3);
  border-top-color: rgba(212, 105, 31, 0.8);
  animation: stemRingSpin 1s linear infinite;
}
.stem-btn-ready .stem-btn-ring { display: none; }
.stem-btn-ready {
  border-color: rgba(212, 105, 31, 0.5);
  background: rgba(212, 105, 31, 0.12);
  box-shadow: 0 0 20px rgba(212, 105, 31, 0.08);
}
@keyframes stemRingSpin {
  to { transform: rotate(360deg); }
}

/* ── Stem Progress Stepper ── */
.stem-progress {
  padding: 32px 24px;
  text-align: center;
  transition: opacity 0.4s ease, max-height 0.4s ease;
}
.stem-stages {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  margin-bottom: 16px;
}
.stem-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  min-width: 80px;
}
.stage-indicator {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  transition: all 0.3s ease;
}
.stage-fill {
  position: absolute;
  inset: 0;
  background: rgba(212, 105, 31, 0.15);
  transform: scaleY(0);
  transform-origin: bottom;
  transition: transform 0.6s ease;
}
.stage-check {
  opacity: 0;
  color: #4ade80;
  transition: opacity 0.3s ease;
  position: relative;
  z-index: 1;
}
.stage-label {
  font-size: 0.6875rem;
  color: rgba(245, 245, 245, 0.3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  transition: color 0.3s ease;
}
.stage-connector {
  width: 40px;
  height: 2px;
  background: rgba(255, 255, 255, 0.06);
  margin: 0 4px;
  margin-bottom: 24px; /* Align with indicator, not label */
  position: relative;
}
/* Stage states */
.stage-active .stage-indicator {
  border-color: rgba(212, 105, 31, 0.5);
}
.stage-active .stage-fill {
  transform: scaleY(1);
  animation: stagePulse 2s ease-in-out infinite;
}
.stage-active .stage-label {
  color: rgba(245, 245, 245, 0.6);
}
.stage-complete .stage-indicator {
  border-color: rgba(74, 222, 128, 0.4);
  background: rgba(74, 222, 128, 0.08);
}
.stage-complete .stage-check {
  opacity: 1;
}
.stage-complete .stage-fill {
  transform: scaleY(0);
}
.stage-complete .stage-label {
  color: rgba(74, 222, 128, 0.6);
}
@keyframes stagePulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

.stem-context-text {
  font-size: 0.8125rem;
  color: rgba(245, 245, 245, 0.35);
  transition: opacity 0.2s ease;
  min-height: 1.2em;
}

/* ── Progressive Stem Channel Animation ── */
.stem-channel-enter {
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 0.4s ease, transform 0.4s ease;
}
.stem-channel-enter.visible {
  opacity: 1;
  transform: translateY(0);
}

/* ── Mix Tab Notification Dot ── */
.stem-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4ade80;
  display: inline-block;
  margin-left: 6px;
  animation: stemDotPulse 1.5s ease-in-out infinite;
}
@keyframes stemDotPulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

/* ── Meta Chip Update Animation ── */
.chip-updated {
  animation: chipFlash 0.6s ease;
}
@keyframes chipFlash {
  0% { background: rgba(212, 105, 31, 0.15); }
  100% { background: transparent; }
}

/* ── Mixer Shimmer (first interaction after completion) ── */
@keyframes mixerShimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}
.channel.shimmer {
  background: linear-gradient(90deg, transparent 0%, rgba(212,105,31,0.04) 50%, transparent 100%);
  background-size: 200% 100%;
  animation: mixerShimmer 0.8s ease forwards;
}
```

---

## PART 3: Backend Changes (`app.py`)

### A. Progressive Status Publishing in the `run()` Function

The deep analysis `run()` function already sets `jobs[job_id]["progress"]` at each stage. But stems, tabs, and intelligence are only written to the job dict in `_finalize()`. For progressive loading, publish them as each stage completes:

**After Stage 1 (stem separation):**
```python
# Already done — stems are published after separation
stems = separate_stems(audio_path, job_id, progress_callback=on_progress)
jobs[job_id]["stems"] = {k: {"label": v.get("label", k), "energy": v.get("energy", 0), "active": v.get("active", True)} for k, v in stems.items()}
jobs[job_id]["progress"] = "Stems ready — analyzing..."
```

**After Stage 3 (tab generation):**
```python
tabs = generate_tabs(...)
jobs[job_id]["tabs"] = tabs  # <-- ADD THIS LINE
jobs[job_id]["progress"] = "Tabs complete — analyzing harmony..."
```

**After Stage 5 (harmonic analysis / intelligence):**
```python
intelligence = analyze_song(...)
jobs[job_id]["intelligence"] = intelligence  # <-- ADD THIS LINE
jobs[job_id]["progress"] = "Harmony analyzed — finishing..."
```

Make sure these lines are added BEFORE `_finalize()` runs. The frontend polls `/api/status/<job_id>` and will pick up partial data as it appears.

### B. Ensure `/api/status` Returns Partial Data

The current `/api/status` endpoint returns `jsonify(job)` which includes whatever is in the job dict. Since we're now writing `stems`, `tabs`, and `intelligence` progressively, the frontend will automatically see them on the next poll. **No changes needed to the endpoint itself** — it already works.

### C. Ensure Stem Audio Files Are Available During Processing

The current stem separation writes audio files to `UPLOAD_DIR / job_id / stem_name.wav`. The `/api/audio/<job_id>/<stem>` endpoint serves these files. As long as the stem audio file exists on disk before `_finalize()`, the frontend can load it. **Verify this is the case** — if stem files are written one at a time during Demucs output, they'll be available progressively. If Demucs writes all at once at the end, the progressive stem UI will still work but all stems will appear simultaneously (which is fine).

---

## PART 4: Edge Cases

1. **User switches track while stem separation is running**: `selectTrack()` clears `_stemPollTimer`, resets `_stemSeparationActive`, clears `_loadedStems`. The orphaned deep job continues on the server but the frontend ignores it.

2. **Prefetch fails**: `_prefetchReady` stays false. The "Separate Stems" button still works — clicking it starts a fresh download via `/api/download mode=full`. Slightly slower but functional.

3. **Stem separation fails**: `_showStemError()` shows a retry button in the CTA area. User's instant results are untouched.

4. **User clicks "Separate Stems" before prefetch completes**: Works fine — `_startStemSeparation()` calls `/api/download mode=full` which waits for the in-progress prefetch (up to 60s), then reuses it.

5. **Cache hit with existing stems**: `selectTrack()` detects `hasStems` and calls `renderResults()` directly — no CTA card, no stem separation flow. Mixer loads with all stems immediately.

6. **No `yt_query`**: No prefetch fires, "Separate Stems" button can be hidden or disabled (no full track available for separation).

---

## PART 5: Testing Checklist

- [ ] Select a track → results appear in <3 seconds with NO loading screen
- [ ] Mix tab shows preview player + "Isolate Instruments" CTA card
- [ ] Button shows spinning ring while prefetch downloads
- [ ] Button visual changes when prefetch completes (ring disappears, subtle glow)
- [ ] Clicking button → CTA transforms to progress stepper
- [ ] Progress stepper shows Download → Separating → Analyzing stages
- [ ] Can still browse Lyrics/Key/Recommended while stems separate
- [ ] Green dot appears on Mix tab when stems arrive while on another tab
- [ ] Stems animate into mixer one by one (or all at once if Demucs outputs together)
- [ ] Preview channel disappears when stems are loaded
- [ ] Progress stepper collapses when complete
- [ ] Meta chips update silently if full-track analysis refines key/BPM
- [ ] Harmonic sections populate after deep analysis
- [ ] Lyrics update if better version found
- [ ] Switching tracks cancels in-flight stem separation
- [ ] Prefetch failure → button still works (starts fresh download)
- [ ] Stem separation failure → error with retry button, instant results intact
- [ ] Cached results with stems → full mixer renders directly, no CTA
- [ ] No timer, no "Taking too long?", no waveform loading animation anywhere
- [ ] Works end-to-end on Render deployment

---

## Summary: What Gets Removed vs Added

### Removed
| Function/Element | Reason |
|---|---|
| `_showHeroLoading()` | Blocking loading screen |
| `buildProcessingBanner()` | Wrapper for above |
| `_startElapsedTimer()` / `_stopElapsedTimer()` | Timer display |
| `_showCancelBtnAfterDelay()` | "Taking too long?" panic button |
| `_autoTriggerDeepAnalysis()` | Auto-trigger removed — user clicks button |
| `_pollDeepDownload()` | Part of auto-trigger chain |
| `_startDeepProcessing()` | Replaced by `_startStemSeparation()` |
| `_pollDeepResults()` | Replaced by `_pollStemProgress()` |
| `_upgradeToStemMixer()` | Replaced by progressive `_addStemChannel()` |
| `_showDeepAnalysisIndicator()` family | Replaced by in-panel progress stepper |
| Deep analysis pill CSS | Replaced by stepper CSS |
| `_deepAnalysisTriggered` / `_deepAnalysisJobId` / `_deepPollTimer` / `_currentStemData` | Replaced by new state vars |

### Added
| Function/Element | Purpose |
|---|---|
| `_startStemSeparation()` | Main entry point — user clicks button |
| `_waitForDownload()` | Promise-based download poller |
| `_showStemProgress()` | Renders segmented progress stepper |
| `_updateStemStage()` | Updates individual stepper stages |
| `_updateStemContextText()` | Updates stepper context text |
| `_pollStemProgress()` | Progressive polling with stem/tab/intel updates |
| `_addStemChannel()` | Animates single stem into mixer |
| `_loadSingleStemAudio()` | Loads one stem's audio buffer |
| `_notifyMixTab()` | Green dot on Mix tab |
| `_silentlyUpdateMetaChips()` | Animated chip updates |
| `_updateStemButton()` | Button visual state management |
| `_showStemError()` | Non-blocking error with retry |
| CTA card HTML/CSS | "Isolate Instruments" card in Mix panel |
| Progress stepper HTML/CSS | 3-stage visual stepper |
| Stem animation CSS | Fade-in, shimmer, notification dot |
| Progressive `jobs[job_id]` writes | Backend publishes stems/tabs/intel as each stage completes |
