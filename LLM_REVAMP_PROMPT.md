# LLM Revamp: Kill Insight Tab → Progression Names + Smart Recommendations + Key Context

**Goal**: Remove the Insight tab. Replace it with a **Recommended** tab (theory-based song recs — the crown jewel), plus progression names on harmonic sections and a key context line in the Key tab. Also fix BPM and Genre chips not displaying.

**Design system**: `#0B0B0B` background, `#D4691F` burnt orange accent, `#F5F5F5` text, Inter font, sharp corners (no border-radius).

---

## Overview of changes

1. **`insight.py`** — Rewrite the LLM prompt to return structured JSON instead of prose paragraphs
2. **`app.py`** — Fix BPM/Genre chips not displaying (data flow issue)
3. **`templates/decompose.html`** — Remove Insight tab, replace with Recommended tab (last position, AI corner mark), add progression names on harmonic sections, key context line in Key tab

---

## Change 1: Rewrite `insight.py`

Replace the entire `generate_insight()` function. Same inputs, new output format.

**New system prompt** — tell the LLM to return ONLY valid JSON, no prose:

```python
system_prompt = """You are a music theory expert. Given analysis data about a song, return ONLY a JSON object with these fields:

1. "progression_names": For each harmonic section provided, if the chord progression matches or closely resembles a well-known named progression, provide the name. Return as an object mapping section labels to names. Only include sections where you're confident of the name. Examples of named progressions: "50s progression", "Axis of Awesome", "Andalusian cadence", "12-bar blues", "Nashville progression", "Royal Road", "Pachelbel's Canon", "ii-V-I turnaround", "Plagal cadence". If a section's progression doesn't have a well-known name, omit it.

2. "smart_recs": Theory-based song recommendations. This is the KEY feature — recommend songs based on MUSICAL DNA, not genre. Return an object with three categories:
   - "same_progression": 2-3 songs that use the same or very similar chord progression pattern (e.g., if the song uses I-V-vi-IV, find other songs with that exact progression regardless of genre). Include the shared progression in the "reason" field.
   - "same_key_tempo": 2-3 songs in the same key AND a similar tempo range (within ~15 BPM). These are ideal for DJ sets, mashups, or practice sessions. Include key and BPM in the "reason" field.
   - "similar_harmony": 2-3 songs with similar harmonic movement or voice leading — songs that "feel" harmonically similar even if the exact chords differ (e.g., both use descending bass lines, both use modal interchange, both use the same cadence patterns).
   Each entry should be: {"title": "...", "artist": "...", "reason": "..."}
   The "reason" should be SHORT (under 12 words) and specific — e.g., "Same I-V-vi-IV progression", "G Major at 122 BPM", "Descending bass line over major chords".
   Pick well-known songs musicians would recognize. Never pick songs by the same artist as the input.

3. "key_context": A single sentence (under 20 words) about the character or common usage of the detected key. Examples: "A bright, open key — the natural home of folk and country guitar." or "Dark and dramatic — a favorite of classical composers and metal bands alike." Do NOT mention specific instruments or production details you cannot know from the data.

Return ONLY the JSON object. No markdown, no code fences, no explanation."""
```

**New user message** — same data assembly as before, but end with:

```python
user_msg += "\nReturn the JSON analysis object."
```

**Parse the response** — wrap in try/except to handle malformed JSON:

```python
import json

try:
    t0 = time.time()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    elapsed = time.time() - t0
    print(f"[insight] generated in {elapsed:.1f}s ({len(raw)} chars)")

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    result = json.loads(raw)
    print(f"[insight] parsed: prog_names={list(result.get('progression_names', {}).keys())}, recs={list(result.get('smart_recs', {}).keys())}")
    return result
except json.JSONDecodeError as e:
    print(f"[insight] JSON parse failed: {e}\nRaw: {raw[:200]}")
    return None
except Exception as e:
    print(f"[insight] LLM call failed: {e}")
    return None
```

**Return type changes from `str` to `dict | None`.**

---

## Change 2: Fix BPM and Genre chips not showing + update data flow

### 2a. BPM chip not displaying

**Problem**: The intelligence dict defaults to `"bpm": 120` (line ~496) but **no `bpm_confidence` field**. The JS check `bpmConf >= 0.15` fails because `intel.bpm_confidence` is undefined → 0. So even when Essentia successfully detects BPM and sets `bpm_confidence`, if it fails (e.g., module not installed), the default 120 BPM has no confidence → chip hidden.

**Fix in `app.py`**: Change the default intelligence dict on BOTH the instant path (line ~496) and deep path (line ~728):

```python
# OLD:
intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 120, "progression": None}

# NEW — add bpm_confidence default of 0:
intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 0, "bpm_confidence": 0, "progression": None}
```

Change the default BPM from 120 to 0. A default of 120 is misleading — it pretends there's a BPM when nothing was detected. With 0, the chip correctly hides when detection fails, and correctly shows when Essentia succeeds.

### 2b. Genre chip not displaying

**Problem**: Genre comes from `get_track_tags()` in `external_apis.py` which calls the Last.fm API. It requires `LASTFM_API_KEY` environment variable. If not set, it silently returns `[]`.

**Fix**: Make sure `LASTFM_API_KEY` is set in your local `.env` and on Render. No code change needed — just ensure the env var exists. You can get a free key at https://www.last.fm/api/account/create.

To verify locally, add a debug log. In the instant analysis path, after the tags call (~line 560), add:
```python
print(f"[job {job_id}] instant: tags={tags}")
```

### 2c. Insight data flow

The `insight` field currently passes a string. Now it's a dict. **No structural changes needed** — the field is already passed through as-is in all three places:

- Instant path: `"insight": insight_text,` (line ~591)
- Deep path: same pattern
- Cache: same

The frontend will now receive `data.insight` as an object like:
```json
{
  "progression_names": {"Verse": "I-IV shuttle", "Chorus": "50s progression"},
  "smart_recs": {
    "same_progression": [
      {"title": "Free Fallin'", "artist": "Tom Petty", "reason": "Same I-IV progression in verse"},
      {"title": "With or Without You", "artist": "U2", "reason": "I-V-vi-IV throughout"}
    ],
    "same_key_tempo": [
      {"title": "Brown Eyed Girl", "artist": "Van Morrison", "reason": "G Major at 120 BPM"},
      {"title": "Sweet Home Alabama", "artist": "Lynyrd Skynyrd", "reason": "G Major at 118 BPM"}
    ],
    "similar_harmony": [
      {"title": "Peaceful Easy Feeling", "artist": "Eagles", "reason": "Same open-position major key movement"}
    ]
  },
  "key_context": "A bright, open key — the natural home of folk and country guitar."
}
```

---

## Change 3: Update `templates/decompose.html`

### 3a. Replace Insight tab with Recommended tab

**Delete the Insight nav button** (line ~366):
```html
<button class="results-nav-btn active" data-panel="insight" onclick="switchPanel('insight',this)">Insight</button>
```

**Delete the entire `panel-insight` div** (lines ~371-385, everything from `<div class="results-panel active" id="panel-insight">` through its closing `</div>`).

**Delete ALL Insight CSS** (lines ~216-221, the entire `/* ═══ Insight panel ═══ */` block including `.insight-content`, `.insight-header`, `.insight-icon`, `.insight-label`, `.insight-sublabel`, `.insight-body`, `.insight-text`, `.insight-footer`, `.insight-empty`).

**Delete the Insight population JS** — there are TWO blocks that populate insight text. Delete both:
1. Deep result handler (~lines 1356-1363): the block starting `// Insight` through the closing `}`
2. Instant/preview handler (~lines 1438-1444): same pattern

**Add the Recommended tab button** — at the END of the nav (after Lyrics), with a small AI sparkle mark:

```html
<button class="results-nav-btn" data-panel="mix" onclick="switchPanel('mix',this)">Mix</button>
<button class="results-nav-btn" data-panel="key" onclick="switchPanel('key',this)">Key</button>
<button class="results-nav-btn" data-panel="lyrics" onclick="switchPanel('lyrics',this)">Lyrics</button>
<button class="results-nav-btn" data-panel="recommended" onclick="switchPanel('recommended',this)">Recommended <span class="ai-mark">AI</span></button>
```

Make Mix the default active tab (add `active` class to Mix button).

**Add the Recommended panel HTML** — after the other panel divs (after `panel-lyrics`):

```html
<div class="results-panel" id="panel-recommended">
  <div id="smart-recs-content"></div>
</div>
```

**Add CSS** for the AI mark on the tab button:

```css
.ai-mark {
  display: inline-block;
  font-size: 0.5625rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: #D4691F;
  border: 1px solid rgba(212,105,31,0.3);
  padding: 1px 4px;
  margin-left: 5px;
  vertical-align: middle;
  line-height: 1.2;
  position: relative;
  top: -1px;
}
```

**Update `_defaultTab`** everywhere it appears — change from `"insight"` to `"mix"`:
- Line ~461: `let _defaultTab = "mix";`
- Line ~1368: `switchPanel(_defaultTab || "mix");`
- Line ~1369: `_defaultTab = "mix";`
- Line ~1454: `switchPanel("mix");`
- Line ~1455: `_defaultTab = "mix";`

### 3b. Add progression names to harmonic sections

In the `renderSection()` function inside `_renderHarmonicSections()` (~line 1659), the function builds HTML for each harmonic section. It receives a `sec` object. We need to also pass in the progression names from the insight data.

**Add a module-level variable** near the top of the `<script>` block (near the other `let` declarations around line 460):
```javascript
let _progressionNames = {};
let _smartRecs = {};
let _keyContext = "";
```

**In both result handlers** (deep and instant/preview), where the old insight population code was, add:
```javascript
// LLM-derived intelligence
const insightData = data.insight || {};
_progressionNames = insightData.progression_names || {};
_smartRecs = insightData.smart_recs || {};
_keyContext = insightData.key_context || "";
```

Place this BEFORE the `_renderHarmonicSections()` call so the data is available when sections render.

**Update `renderSection()`** to show the progression name. Inside the function, after the `hs-header` div line, add:

```javascript
// Progression name from LLM
const progName = _progressionNames[sec.label] || _progressionNames[sec.section] || null;
if (progName) {
  s += `<div class="hs-prog-name">${esc(progName)}</div>`;
}
```

**Add CSS** for the progression name label. Put this right after the existing `.hs-summary` CSS:

```css
.hs-prog-name {
  font-size: 0.75rem;
  color: #D4691F;
  opacity: 0.7;
  margin-top: 6px;
  font-style: italic;
  letter-spacing: 0.01em;
}
```

### 3c. Populate the Recommended tab with smart recs

This is the crown jewel. Theory-based recommendations grouped by musical DNA, rendered inside the Recommended tab panel.

**Add CSS** for the recommendations content:

```css
/* ═══ Smart Recommendations (Recommended tab) ═══ */
.smart-recs { padding: 32px 36px; max-width: 780px; }
.smart-recs-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 28px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(212,105,31,0.15);
}
.smart-recs-title {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #D4691F;
}
.smart-recs-subtitle {
  font-size: 0.6875rem;
  color: rgba(245,245,245,0.25);
  margin-left: auto;
}
.sr-category { margin-bottom: 24px; }
.sr-category:last-child { margin-bottom: 0; }
.sr-cat-label {
  font-size: 0.6875rem;
  font-weight: 600;
  color: rgba(245,245,245,0.4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 10px;
}
.sr-songs { display: flex; flex-direction: column; gap: 6px; }
.sr-song {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 10px 16px;
  border: 1px solid rgba(255,255,255,0.06);
  background: rgba(245,245,245,0.02);
  transition: border-color 0.15s;
  cursor: default;
}
.sr-song:hover { border-color: rgba(255,255,255,0.12); }
.sr-song-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: rgba(245,245,245,0.8);
  white-space: nowrap;
}
.sr-song-artist {
  font-size: 0.8125rem;
  color: rgba(245,245,245,0.35);
  white-space: nowrap;
}
.sr-song-reason {
  font-size: 0.75rem;
  color: rgba(212,105,31,0.6);
  font-style: italic;
  margin-left: auto;
  white-space: nowrap;
}
.smart-recs-empty {
  padding: 48px 20px;
  text-align: center;
  color: rgba(245,245,245,0.3);
  font-size: 0.875rem;
}
```

**Add JS** — create a `_renderSmartRecs()` function. Place it near `_renderHarmonicSections()`:

```javascript
function _renderSmartRecs(recs) {
  const el = document.getElementById('smart-recs-content');
  if (!el) return;

  const categories = [
    { key: 'same_progression', label: 'Same Progression' },
    { key: 'same_key_tempo', label: 'Same Key + Tempo' },
    { key: 'similar_harmony', label: 'Similar Harmony' },
  ];

  const hasAny = categories.some(c => (recs[c.key] || []).length > 0);
  if (!hasAny) {
    el.innerHTML = '<div class="smart-recs-empty">Theory-based recommendations will appear here once analysis completes.</div>';
    return;
  }

  let html = '<div class="smart-recs">';
  html += '<div class="smart-recs-header"><span class="smart-recs-title">Songs with Similar Musical DNA</span><span class="smart-recs-subtitle">Based on harmony, not genre</span></div>';

  for (const cat of categories) {
    const songs = recs[cat.key] || [];
    if (!songs.length) continue;
    html += `<div class="sr-category">`;
    html += `<div class="sr-cat-label">${cat.label}</div>`;
    html += `<div class="sr-songs">`;
    for (const s of songs) {
      html += `<div class="sr-song">`;
      html += `<span class="sr-song-title">${esc(s.title)}</span>`;
      html += `<span class="sr-song-artist">${esc(s.artist)}</span>`;
      if (s.reason) html += `<span class="sr-song-reason">${esc(s.reason)}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }

  html += '</div>';
  el.innerHTML = html;
}
```

**Call it in both result handlers**, after parsing the insight data:

```javascript
_renderSmartRecs(_smartRecs);
```

**Clear it on reset** — in the reset function (where `harmonic-sections` innerHTML is cleared), also add:
```javascript
document.getElementById("smart-recs-content").innerHTML = "";
```

### 3d. Add key context to the Key tab

Find the `_initDecomposeKeyTab()` function. After the tonality map is rendered, add a key context line below it.

First, find the HTML for `panel-key`. It should have the tonality map container. Add a new div after the tonality map:

```html
<div class="key-context" id="key-context"></div>
```

In the JS, after `_initDecomposeKeyTab(intel.key);` is called in both result handlers, add:

```javascript
// Key context from LLM
const keyCtxEl = document.getElementById("key-context");
if (keyCtxEl) {
  keyCtxEl.textContent = _keyContext || "";
  keyCtxEl.style.display = _keyContext ? "block" : "none";
}
```

**Add CSS** for the key context line:

```css
.key-context {
  font-size: 0.8125rem;
  color: rgba(245,245,245,0.45);
  text-align: center;
  padding: 12px 24px 0;
  font-style: italic;
  letter-spacing: -0.01em;
}
```

---

## Summary checklist

- [ ] `insight.py`: Replace prose generation with structured JSON output (progression_names, smart_recs, key_context)
- [ ] `app.py`: Fix default BPM from 120→0, add `bpm_confidence: 0` to default intelligence dict (both instant + deep paths)
- [ ] `app.py`: Add debug log for tags after `get_track_tags()` call
- [ ] `decompose.html`: Delete Insight tab button, panel, CSS, and JS population blocks
- [ ] `decompose.html`: Add Recommended tab button at END of nav with `<span class="ai-mark">AI</span>` badge
- [ ] `decompose.html`: Add `panel-recommended` div with `smart-recs-content` inside
- [ ] `decompose.html`: Add `.ai-mark` CSS for the tab badge
- [ ] `decompose.html`: Change default tab from "insight" to "mix" (5 locations)
- [ ] `decompose.html`: Add `_progressionNames`, `_smartRecs`, `_keyContext` variables
- [ ] `decompose.html`: Parse `data.insight` object in both result handlers
- [ ] `decompose.html`: Show progression name in `renderSection()` with `.hs-prog-name` style
- [ ] `decompose.html`: Implement `_renderSmartRecs()` targeting `#smart-recs-content`
- [ ] `decompose.html`: Add Smart Recommendations CSS (`.smart-recs`, `.sr-category`, `.sr-song`, etc.)
- [ ] `decompose.html`: Call `_renderSmartRecs(_smartRecs)` in both result handlers
- [ ] `decompose.html`: Clear `smart-recs-content` on reset
- [ ] `decompose.html`: Add key context line below tonality map in Key tab
- [ ] Verify: no references to old insight panel remain (search for "panel-insight", "insight-text", "insight-content")

**Do NOT touch**: the mixer, the lyrics panel, the tonality map logic, the waveform, the loading page, or any analysis/detection logic in `music_intelligence.py`.
