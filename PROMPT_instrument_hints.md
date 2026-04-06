# Implementation Prompt: Claude-Powered Instrument Hints for Stem Processing

## Goal

Add a pre-analysis step that uses Claude Haiku to predict what instruments are present in a song **before** Demucs runs. Use those predictions to improve three specific downstream systems: stem classification, energy filtering, and note detection configuration.

This does NOT change how Demucs separates audio — it changes how the app **interprets** Demucs output.

---

## Architecture Overview

The app is a Flask backend (`app.py`) that runs a deep analysis pipeline. The relevant pipeline stages are:

```
Stage 1 (parallel):  Demucs separation + lyrics fetch + tags fetch + early key/BPM
Stage 2:             Stereo refinement + component classification  (processor.py)
Stage 3:             Note extraction via Basic Pitch               (processor.py)
Stage 5:             Harmonic analysis                             (music_intelligence.py)
Stage 6.5:           LLM insight generation                        (insight.py)
```

The new instrument hints call should run **in Stage 1's parallel pool** alongside lyrics, tags, and early key/BPM — it only needs artist name, track name, and genre tags (which come from the Spotify metadata already available before the pool starts).

---

## What to Build

### 1. New function: `predict_instruments()` in `insight.py`

Create a new function in `insight.py` (it already has the lazy Anthropic client singleton — reuse `_get_client()`).

**Signature:**
```python
def predict_instruments(song_name: str, artist: str, tags: list[str] | None = None) -> dict | None:
```

**Claude prompt design:**
- Use `claude-haiku-4-5-20251001` (same model the file already uses)
- Max tokens: ~300 (this should be a fast, small response)
- Timeout: 8 seconds (must not slow down the pipeline — Demucs takes 20s+ anyway)
- Ask Claude to return JSON with this structure:

```json
{
  "instruments": ["vocals", "electric guitar", "bass", "drums", "synthesizer", "strings"],
  "has_piano": false,
  "has_guitar": true,
  "has_synth": true,
  "has_strings": false,
  "has_brass": false,
  "has_acoustic_guitar": false,
  "notable": "Drop D tuning, heavy distortion, layered rhythm and lead guitars"
}
```

The `instruments` array is a plain-English list of what's in the song. The boolean flags are quick-access for the most common classification decisions. The `notable` field captures anything about instrumentation that's unusual (tuning, register, playing style, prominent effects) — this is what helps with edge cases.

**Prompt guidelines:**
- System prompt should say: "You are a music expert. Given a song title, artist, and genre tags, predict the instrumentation. Be specific (e.g., 'Rhodes piano' not just 'keyboard'). Only list instruments you're confident are in the recording. Return valid JSON only, no explanation."
- Keep it tight — no preamble, no markdown fences. Parse with `json.loads()` and strip markdown fences as a fallback (same pattern `generate_insight()` already uses).

**Fallback behavior:**
- If no API key → return `None` (graceful skip, same as `generate_insight`)
- If timeout or parse error → log warning, return `None`
- The entire rest of the pipeline must work identically when hints are `None`

---

### 2. Wire into Stage 1 parallel pool in `app.py`

In `app.py`, around line 970, the parallel pool already runs 4 futures:
```python
with _TPE(max_workers=4) as _pool:
    fut_demucs    = _pool.submit(_run_demucs)
    fut_lyrics    = _pool.submit(_fetch_lyrics)
    fut_tags      = _pool.submit(_fetch_tags)
    fut_early_key = _pool.submit(_run_early_key)
```

**Changes:**
1. Bump `max_workers` to 5
2. Add a new future:
```python
def _run_instrument_hints():
    try:
        from insight import predict_instruments
        return predict_instruments(track_name, artist_name, tags=tags)
    except Exception as e:
        _fail("instrument_hints", e)
        return None

fut_hints = _pool.submit(_run_instrument_hints)
```

**IMPORTANT:** `tags` might not be resolved yet when `_run_instrument_hints` starts (it's fetched in the same pool). That's fine — pass whatever tags are available from Spotify metadata (`track_meta.get("genres", [])` or similar). If no tags are available pre-pool, pass `None` — Claude can work from just song name + artist. Alternatively, you could let the hints future collect `fut_tags.result()` first, but this adds latency. Prefer passing `None` for tags over waiting.

3. Collect the result after metadata but before Demucs finishes:
```python
instrument_hints = fut_hints.result()  # Fast — should complete in <2s
```

4. Store it so it's accessible downstream:
```python
# Make hints available for stem refinement
jobs[job_id]["instrument_hints"] = instrument_hints
```

---

### 3. Thread hints into `separate_stems()` in `processor.py`

**Change the signature** of `separate_stems()` (line 1120):
```python
def separate_stems(audio_path: str, song_id: str, progress_callback=None, instrument_hints: dict | None = None) -> dict:
```

**IMPORTANT:** `separate_stems` is called from `_run_demucs()` inside the parallel pool. Since Demucs and hints run concurrently, hints won't be available when `separate_stems` starts. There are two options:

**Option A (simpler):** Don't pass hints to `separate_stems`. Instead, pass them to the stereo refinement step separately. Look at how the pipeline actually works — `separate_stems()` does both Demucs AND refinement in one call. You may need to split refinement into a separate call that happens AFTER both Demucs and hints are done. This is the cleaner approach.

**Option B (pragmatic):** Keep `separate_stems` as-is for Demucs, then add a new function `refine_stems_with_hints(stems, instrument_hints)` that re-classifies and adjusts the already-refined stems using hints. This avoids restructuring the pipeline.

**Recommended: Option B.** It's less invasive. Create a new function that post-processes the stems dict.

---

### 4. New function: `apply_instrument_hints()` in `processor.py`

```python
def apply_instrument_hints(stems: dict, instrument_hints: dict | None) -> dict:
    """
    Post-process refined stems using LLM instrument predictions.
    Adjusts labels, energy thresholds, and active states.

    Mutates and returns the stems dict.
    """
    if not instrument_hints:
        return stems

    # ... (see specific improvements below)
    return stems
```

Call this in `app.py` right after `separate_stems` returns and hints are collected (after the parallel pool closes, around line 1004):

```python
stems = fut_demucs.result()
# ... existing error handling ...

# Apply instrument hints to improve classification
if stems and instrument_hints:
    from processor import apply_instrument_hints
    stems = apply_instrument_hints(stems, instrument_hints)
```

---

### 5. Specific improvements inside `apply_instrument_hints()`

#### A. Fix "other" stem classification (biggest win)

Currently `_classify_component()` (line 328) maps the "other" Demucs stem using spectral thresholds:
```python
if stem_category == "other":
    if c > 2500 and bw > 1400 and zcr > 0.12:
        return "Acoustic Guitar"
    if c > 1500 and bw > 1800:
        return "Synth"
    if c > 800:
        return "Atmosphere"
    return "Pad"
```

With hints, if Claude said `"instruments": ["strings", "organ"]`, and there's a stem currently labeled "Atmosphere" or "Pad" that came from the "other" bucket — relabel it to match what Claude predicted.

**Implementation approach:**
```python
predicted = [i.lower() for i in instrument_hints.get("instruments", [])]

for key, stem in stems.items():
    label = stem["label"].lower()

    # Only reclassify vague labels from the "other" bucket
    if label in ("pad", "atmosphere", "synth", "other"):
        # Check what Claude thinks is actually there
        if any(s in predicted for s in ("strings", "string section", "violin", "cello", "orchestra")):
            stem["label"] = "Strings"
        elif any(s in predicted for s in ("organ", "hammond", "b3")):
            stem["label"] = "Organ"
        elif any(s in predicted for s in ("brass", "trumpet", "horn", "trombone", "saxophone")):
            stem["label"] = "Brass"
        elif any(s in predicted for s in ("synth", "synthesizer", "synth lead", "synth pad")):
            stem["label"] = "Synth"  # Confirm it actually is synth, not just default
        elif any(s in predicted for s in ("flute", "woodwind", "clarinet", "oboe")):
            stem["label"] = "Woodwind"
        # ... etc. Keep this extensible.
```

Also update `_get_tab_renderer()` (line 368) to handle any new labels you introduce (Strings, Organ, Brass, Woodwind should all map to `"note_list"`).

#### B. Rescue quiet-but-real stems from energy filtering

Currently, stems below `MIN_RELATIVE_ENERGY = 0.25` or `MIN_ABSOLUTE_ENERGY = 0.008` are silently dropped (line 1289-1291).

If Claude says piano is in the song but the piano stem got filtered, resurrect it:

```python
# Check if any predicted instrument is missing from stems
has_piano_stem = any("piano" in s["label"].lower() or "keyboard" in s["label"].lower() for s in stems.values())

if instrument_hints.get("has_piano") and not has_piano_stem:
    # The piano stem was likely filtered as bleed — check if it exists in raw stems
    # Log this so we can track how often it happens
    print(f"[hints] Claude predicted piano but no piano stem survived filtering")
```

**For a more complete implementation:** You'll need to adjust the filtering behavior DURING `separate_stems`, not after. This means either:
- Passing hints into `separate_stems` (requires ensuring hints resolve before Demucs finishes, which they should — hints take <2s, Demucs takes 20s+), OR
- Storing the pre-filtered stems temporarily so `apply_instrument_hints` can recover them

**Pragmatic approach:** Pass `instrument_hints` into `separate_stems()` since in practice hints WILL resolve before Demucs finishes. Use them to create per-stem energy overrides:

```python
# Inside separate_stems, before the refinement loop:
energy_overrides = {}
if instrument_hints:
    if instrument_hints.get("has_piano"):
        energy_overrides["piano"] = {"min_relative": 0.10, "min_absolute": 0.004}
    if instrument_hints.get("has_strings") or instrument_hints.get("has_brass"):
        energy_overrides["other"] = {"min_relative": 0.10, "min_absolute": 0.004}
    # Guitar is rarely quiet, but for acoustic songs:
    if instrument_hints.get("has_acoustic_guitar"):
        energy_overrides["guitar"] = {"min_relative": 0.15, "min_absolute": 0.005}
```

Then in the filtering logic (line 1289):
```python
overrides = energy_overrides.get(stem_name, {})
rel_thresh = overrides.get("min_relative", MIN_RELATIVE_ENERGY)
abs_thresh = overrides.get("min_absolute", MIN_ABSOLUTE_ENERGY)
if energy < stem_energy * rel_thresh or energy < abs_thresh:
    continue
```

#### C. Dynamic note detection configs

Currently `INSTRUMENT_CONFIGS` (line 85) is static. With the `notable` field from hints, you can adjust frequency ranges.

**In `app.py`, before note extraction (around line 1160):**

```python
# Adjust note detection configs based on instrument hints
if instrument_hints:
    from processor import adjust_instrument_configs
    adjust_instrument_configs(instrument_hints)
```

**New function in `processor.py`:**
```python
def adjust_instrument_configs(hints: dict | None):
    """Temporarily adjust INSTRUMENT_CONFIGS based on LLM predictions."""
    if not hints:
        return
    notable = (hints.get("notable") or "").lower()

    # Drop tuning detection
    if "drop d" in notable or "drop c" in notable or "downtuned" in notable:
        INSTRUMENT_CONFIGS["guitar_tab"]["min_freq"] = 60  # Lower than standard E2=82Hz
        INSTRUMENT_CONFIGS["bass_tab"]["min_freq"] = 22    # Sub-bass territory
        print(f"[hints] adjusted guitar/bass freq range for drop tuning")

    # Higher register bass (e.g., slap bass, bass solo)
    if "slap" in notable or "high register bass" in notable:
        INSTRUMENT_CONFIGS["bass_tab"]["max_freq"] = 500
        print(f"[hints] expanded bass freq range for high register")
```

**IMPORTANT:** Since `INSTRUMENT_CONFIGS` is a module-level dict, mutating it affects all subsequent jobs. Either:
- Reset to defaults after each job (add a `reset_instrument_configs()` called in finalization), OR
- Make a per-job copy that gets passed through (cleaner but more invasive)

**Recommended:** Make a copy at the start of each job:
```python
def get_adjusted_configs(instrument_hints: dict | None) -> dict:
    """Return a copy of INSTRUMENT_CONFIGS adjusted for this song."""
    import copy
    configs = copy.deepcopy(INSTRUMENT_CONFIGS)
    if not instrument_hints:
        return configs
    # ... adjustments on the copy ...
    return configs
```

Then thread this `configs` dict through `extract_note_events()` instead of having it read the global.

---

## Files to Modify

| File | Change |
|------|--------|
| `insight.py` | Add `predict_instruments()` function |
| `app.py` (~line 970) | Add `_run_instrument_hints()` to Stage 1 parallel pool |
| `app.py` (~line 1004) | Collect hints result, pass to stem processing |
| `app.py` (~line 1160) | Pass hints to note extraction config |
| `processor.py` (line 328) | Update `_classify_component()` to accept optional hints |
| `processor.py` (line 1289) | Use per-stem energy overrides from hints |
| `processor.py` (line 85) | Add `get_adjusted_configs()` for per-job config copies |
| `processor.py` (line 368) | Update `_get_tab_renderer()` for new labels (Strings, Organ, Brass, etc.) |
| `processor.py` (new) | Add `apply_instrument_hints()` for post-processing |

---

## Non-Negotiable Constraints

1. **Everything must work when hints are `None`.** No API key, timeout, parse failure — the pipeline must behave identically to today. Every function that accepts hints must have `instrument_hints: dict | None = None` and early-return on `None`.

2. **Do not restructure the pipeline.** The parallel pool structure, the Demucs subprocess, the stereo refinement loop — these all stay as-is. Thread hints through as an optional parameter, don't reorganize the stages.

3. **Do not mutate module-level state across jobs.** If adjusting `INSTRUMENT_CONFIGS`, use a per-job copy. Multiple jobs can run concurrently (`MAX_CONCURRENT_JOBS`).

4. **Keep the Haiku call cheap and fast.** Max 300 tokens, 8-second timeout. This must never be the bottleneck. If it's slower than Demucs, something is wrong.

5. **Log everything.** Print when hints are used, what they changed, and when they're skipped. Follow the existing `[processor]` / `[insight]` prefix convention. Example:
   ```
   [hints] predicted: vocals, electric guitar, bass, drums, synthesizer
   [hints] reclassified "Pad" → "Strings" (stem: other_center)
   [hints] lowered piano energy threshold (predicted present)
   [hints] adjusted guitar freq range for drop tuning
   ```

6. **Match existing code style.** Look at `insight.py` for the Claude call pattern (lazy client, markdown fence stripping, JSON parsing with fallback). Look at `processor.py` for the logging and error handling style. Use the same patterns.

7. **The downstream contract must not change.** `separate_stems()` still returns `{stem_key: {path: str, energy: float, active: bool, label: str}}`. The API response shape doesn't change. The frontend doesn't need to know hints exist.

---

## Testing Approach

1. **Test with no API key** — verify pipeline works identically, no crashes, hints are `None` throughout.

2. **Test with a known song** — e.g., "Bohemian Rhapsody" by Queen. Claude should predict piano, guitar, vocals, drums, bass, operatic harmonies. Verify:
   - The "other" stem (if any) gets labeled as something more specific than "Pad"
   - Piano stem survives energy filtering if present

3. **Test with a simple acoustic track** — e.g., "Fast Car" by Tracy Chapman. Claude should predict acoustic guitar + vocals. Verify:
   - No phantom stems get relabeled to instruments that aren't there
   - The guitar stem doesn't get split into sub-components that don't exist

4. **Test the `None` fallback** — mock `predict_instruments` to return `None` and verify zero behavioral difference from current code.

5. **Test concurrent jobs** — run two jobs simultaneously and verify instrument configs from one don't leak into the other (this catches the module-level mutation bug).

---

## What NOT to Do

- Don't use Claude to analyze audio directly — it can't
- Don't add a new API endpoint for this — it's internal pipeline only
- Don't change the frontend — this is invisible to the user (they just see better labels)
- Don't make hints blocking — if the call fails, the pipeline continues
- Don't use a larger model — Haiku is perfect for this (fast, cheap, good enough for instrument prediction)
- Don't add hints to the result cache JSON — it's an implementation detail, not a user-facing feature
