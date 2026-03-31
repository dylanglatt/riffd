# Claude Code Prompt: Generalized Melodic Stem Splitting

## Objective

Add a generalized melodic splitting stage to the Riffd stem separation pipeline in `processor.py`. After Demucs produces raw stems and before tab generation, any non-drum/bass stem that contains multiple musical parts (e.g. lead guitar + rhythm guitar, lead vocal + backing harmonies, solo piano + comping piano) should be split into separate stems using pitch data from Basic Pitch.

This replaces the current approach of relying solely on stereo panning + spectral heuristics to sub-classify instruments, which is unreliable and misclassifies frequently.

**CRITICAL CONSTRAINTS:**
- Do NOT add any new pip dependencies. Use only numpy, scipy (already available), and Basic Pitch (already imported).
- Do NOT change the Demucs model or add additional Demucs passes. The separation still runs once.
- Do NOT change the return contract of `separate_stems()` — it must still return `{stem_key: {path: str, energy: float, active: bool, label: str}}`.
- Do NOT change the API routes in `app.py` — the frontend dynamically renders whatever stems come back.
- Do NOT break the existing stereo refinement — it should still run. The melodic split is an additional refinement stage, not a replacement.
- Do NOT increase `MAX_REFINED_STEMS` beyond 10. The melodic split must respect this cap.
- Performance budget: the new splitting stage must add no more than ~2 seconds per stem on a standard server. No new model inference — only STFT math on existing data.

---

## Architecture Overview

The current pipeline in `processor.py` is:

```
1. Demucs separation → raw stems (vocals, drums, bass, guitar, piano, other)
2. For each non-drum/bass stem:
   a. Stereo field analysis (_stereo_separate)
   b. Spectral feature extraction (_spectral_features)
   c. Classification (_classify_component)
   d. Save sub-parts (_save_sub_parts)
3. Return refined stem dict
```

The new pipeline should be:

```
1. Demucs separation → raw stems (vocals, drums, bass, guitar, piano, other)
2. For each non-drum/bass stem:
   a. Stereo field analysis (_stereo_separate) [KEEP AS-IS]
   b. Spectral feature extraction (_spectral_features) [KEEP AS-IS]
   c. Classification (_classify_component) [KEEP AS-IS]
   d. Save sub-parts (_save_sub_parts) [KEEP AS-IS]
3. NEW — Melodic split pass:
   a. For each refined stem that is NOT drums/bass:
      i.   Run Basic Pitch on the stem (or reuse if already computed)
      ii.  Build a melodic mask from the detected notes
      iii. Apply the mask via STFT to separate "lead" (monophonic melody) from "accompaniment" (everything else)
      iv.  Evaluate split quality — only keep if both halves have meaningful energy
      v.   If kept: replace the single stem entry with two entries (e.g. "lead_guitar" + "rhythm_guitar")
      vi.  If not kept: leave the original stem unchanged
4. Return refined stem dict
```

---

## Detailed Implementation Plan

### Step 1: Create `_extract_melodic_mask()`

New function. Takes a WAV file path and returns a time-frequency binary/soft mask identifying the monophonic melodic content.

```python
def _extract_melodic_mask(stem_path: str, sr: int, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """
    Use Basic Pitch note events to build an STFT-domain mask for melodic content.

    Returns a 2D soft mask (n_freq_bins x n_time_frames) where values near 1.0
    indicate melodic (lead) content and values near 0.0 indicate accompaniment.
    """
```

Logic:
1. Run Basic Pitch `predict()` on the stem (use the `note_list` config — widest frequency range).
2. From the note events, identify the **dominant monophonic line**: at each point in time, if there is exactly one note (or one note that is significantly louder/more confident than others), that's the melody. If there are multiple overlapping notes of similar confidence, that's chordal/accompaniment.
3. For each identified melody note, compute the fundamental frequency and first 3-4 harmonics.
4. Build a soft Gaussian mask in the STFT domain centered on those frequencies, with a bandwidth of ~50-80 Hz per harmonic (tunable).
5. Return the mask.

**Key heuristic for monophonic detection:** At each time frame, sort overlapping notes by confidence. If the top note's confidence is >1.5× the second note's confidence, treat it as a monophonic melody note. If multiple notes have similar confidence, treat them all as accompaniment (chords).

### Step 2: Create `_split_melodic_stem()`

New function. Takes a stem's audio data and the melodic mask, applies it, and returns two audio arrays.

```python
def _split_melodic_stem(left: np.ndarray, right: np.ndarray, sr: int, melodic_mask: np.ndarray,
                         n_fft: int = 2048, hop_length: int = 512) -> tuple:
    """
    Split a stem into lead (melodic) and accompaniment using an STFT mask.

    Returns ((lead_left, lead_right), (acc_left, acc_right)) or None if split is not meaningful.
    """
```

Logic:
1. STFT both channels with the same n_fft and hop_length used for the mask.
2. Apply `melodic_mask` to get the lead component: `lead_stft = full_stft * mask`
3. Apply `(1 - melodic_mask)` to get the accompaniment: `acc_stft = full_stft * (1 - mask)`
4. Inverse STFT both to get time-domain audio.
5. Compute RMS energy of both components.
6. **Quality gate:** If lead energy < 15% of total stem energy, OR accompaniment energy < 15% of total, the split is not meaningful — return `None`. The caller should keep the original unsplit stem.
7. If both sides pass the gate, return the two pairs of audio arrays.

### Step 3: Create `_get_split_labels()`

New function. Given a stem's existing label, return the label pair for the lead and accompaniment components.

```python
def _get_split_labels(original_label: str) -> tuple[str, str]:
    """
    Map an instrument label to (lead_label, accompaniment_label).

    Examples:
        "Guitar" → ("Lead Guitar", "Rhythm Guitar")
        "Vocals" → ("Lead Vocals", "Backing Vocals")
        "Piano" → ("Piano Solo", "Piano Accompaniment")
        "Keyboard" → ("Keyboard Lead", "Keyboard Pad")
        "Synth" → ("Synth Lead", "Synth Pad")
        "Other" → ("Lead", "Accompaniment")
        "Acoustic Guitar" → ("Lead Acoustic Guitar", "Acoustic Guitar Accompaniment")
    """
```

This should handle all labels that `_classify_component()` can produce. Use a mapping dict, with a sensible default fallback for unknown labels.

### Step 4: Create `_melodic_split_pass()`

New function. This is the orchestrator that runs after the existing stereo refinement.

```python
def _melodic_split_pass(refined: dict, out_dir: Path, progress_callback=None) -> dict:
    """
    Post-processing pass: attempt to split non-drum/bass stems into
    lead (melodic) and accompaniment components using pitch detection.

    Modifies `refined` in place and returns it.
    """
```

Logic:
1. Iterate over a **snapshot** of `refined.items()` (since we'll be modifying the dict).
2. Skip stems where:
   - Label contains "drum" or "bass" (case-insensitive)
   - Label already contains "lead", "backing", "rhythm", "solo", "pad", "accompaniment" — these were already split by stereo refinement
   - `active` is False
   - Energy is below `MIN_ABSOLUTE_ENERGY`
3. For each eligible stem:
   a. Check if `len(refined) >= MAX_REFINED_STEMS` — if so, stop splitting entirely.
   b. Read the stem WAV with `_read_wav()`.
   c. Call `_extract_melodic_mask()` on the stem path.
   d. Call `_split_melodic_stem()` with the audio and mask.
   e. If the split returns None (quality gate failed), leave the stem unchanged.
   f. If the split succeeds:
      - Get labels from `_get_split_labels()`
      - Write both new WAV files to `out_dir`
      - Remove the original stem entry from `refined`
      - Add two new entries with the split labels
      - Delete the original WAV file to save disk
4. Return `refined`.

### Step 5: Integrate into `separate_stems()`

In `separate_stems()` (line 692), add the melodic split pass **after** the existing stereo refinement loop (after line 951, before the final `return refined`).

```python
    # ── Step 3: Melodic split pass ──
    if progress_callback:
        progress_callback("Refining instrument separation...")
    refined = _melodic_split_pass(refined, out_dir, progress_callback)

    return refined
```

Insert this BEFORE the "promote Vocals → Lead Vocals" block (line 931), because the melodic split may itself produce Lead Vocals / Backing Vocals and that logic should run on the final stem set.

Actually — move the "promote Vocals → Lead Vocals" logic INTO `_melodic_split_pass()` or run it AFTER the melodic split. Either way, the vocal promotion logic at line 931-951 should run on the final state of `refined` after all splitting is done.

### Step 6: Update `_classify_component()` — MINOR TWEAK ONLY

The existing spectral classification for guitar (lines 343-353) should remain as a fallback, but **lower the confidence** of its sub-classifications. Currently it aggressively labels things as "Lead Guitar" or "Rhythm Guitar" based on spectral centroid alone, and the melodic split pass will then skip them because they already have "lead"/"rhythm" in the label.

Change: In `_classify_component()`, for the guitar stem_category, make the sub-classification more conservative. Only label as "Lead Guitar" or "Rhythm Guitar" if the spectral evidence is very strong. Otherwise, label as just "Guitar" and let the melodic split pass handle it.

Suggested thresholds (raise them):
```python
if stem_category == "guitar":
    if c > 2500 and bw > 1400 and zcr > 0.12:  # was 2200, 1200, 0.10
        return "Acoustic Guitar"
    if c > 2200 and c_std > 600:  # was 1800, 400  — much stricter
        return "Lead Guitar"
    if c > 1400:  # was 1000
        return "Rhythm Guitar"
    return "Guitar"
```

This ensures more stems fall through to "Guitar" and get properly split by the melodic pass rather than being pre-labeled incorrectly by spectral heuristics.

Do the same for the "other" category — raise the thresholds so more things stay as generic labels and get split properly.

### Step 7: Verify tab generation still works

After the melodic split, stems like `lead_guitar` and `rhythm_guitar` need to produce tabs. Verify that:
- `_get_tab_renderer()` correctly maps both "Lead Guitar" and "Rhythm Guitar" to `guitar_tab` — it already does (line 387 checks for "guitar" substring).
- `generate_tabs()` is called with the new stem names and labels — this happens in `app.py`, not `processor.py`, so check that the tab generation loop in `app.py` iterates over whatever stems `separate_stems()` returns. It should, since it uses the dict keys dynamically.

No changes needed here — just verify.

---

## Testing Checklist

After implementation, verify the following:

1. **Basic functionality:** Upload a track with clear lead + rhythm guitar (e.g. any classic rock song). Verify that the pipeline produces separate `lead_guitar` and `rhythm_guitar` stems in the mixer.

2. **Vocals split:** Upload a track with harmonies. Verify lead vs backing vocal separation.

3. **No-split case:** Upload a simple acoustic track with one guitar. Verify it does NOT split into two weak stems — it should remain as a single "Guitar" or "Acoustic Guitar" stem.

4. **Cap enforcement:** Upload a complex track (e.g. Bohemian Rhapsody). Verify total stems stay ≤ 10.

5. **Performance:** Time the pipeline before and after. The melodic split pass should add ≤ 2-3 seconds total across all stems.

6. **Tab generation:** Verify that split stems still produce correct ASCII tabs and MIDI files.

7. **Energy values:** Verify that the energy values in the returned dict are reasonable (lead + accompaniment energies should roughly sum to original stem energy).

8. **Demo compatibility:** After running the updated pipeline on demo tracks, verify the demo page still works with the new stem names.

---

## Files to Modify

1. **`processor.py`** — All new functions + integration into `separate_stems()` + threshold tweaks in `_classify_component()`
2. **No other files need modification** for the core feature. The frontend and API routes are already dynamic.

## Files to verify (read-only)

1. **`app.py`** — Confirm tab generation loop iterates over all stems from `separate_stems()` return value
2. **`templates/`** — Confirm mixer UI renders stems dynamically from the analysis response
3. **`static/demo/*/analysis.json`** — Note current format for when demos are refreshed later

---

## Performance Notes

- Basic Pitch inference is the most expensive part of this new stage (~5-10s per stem). However, Basic Pitch is ALREADY being run on every pitched stem for tab generation. **You MUST find a way to reuse that inference** rather than running it twice. Options:
  - Run `extract_note_events()` first, cache the note events, then use them in `_extract_melodic_mask()` instead of re-running `predict()`.
  - OR: restructure so `_melodic_split_pass()` runs inside the tab generation loop where note events are already available.
  - The best approach: have `_extract_melodic_mask()` accept note events (a DataFrame) as input instead of running its own Basic Pitch inference. Then in `separate_stems()`, do a pre-pass that runs Basic Pitch on each eligible stem, stores the note events, passes them to both the melodic splitter AND later to tab generation.

- STFT + mask application: ~200-500ms per stem. This is the only NEW compute.
- The n_fft and hop_length for the melodic mask STFT should match what the existing `_stereo_separate()` uses (n_fft=4096, hop=1024) for consistency, OR use a smaller window (2048/512) for better temporal resolution on fast lead runs. The latter is recommended for melodic content.

---

## Edge Cases to Handle

1. **Stems with no pitch data:** If Basic Pitch returns 0 notes for a stem, skip the melodic split entirely.
2. **Stems where melody IS the whole stem:** If the melodic mask covers >85% of the energy, don't split — the stem is essentially all melody (e.g. a solo instrument).
3. **Stems where melody is tiny:** If the melodic mask covers <15% of the energy, don't split — there's no meaningful lead part.
4. **Very short stems:** If the stem is < 5 seconds, skip splitting (not enough data for meaningful separation).
5. **Memory:** Delete intermediate STFT arrays immediately after use. Each full-length STFT can be 50-100MB. Use `del` + `gc.collect()` as the existing code does.
