# Task 02 — Fixes from the independent review

An external review (Codex) went over `ed6b9ce`, `590d5e6`, `a6568dd`. It confirmed the
melodic-split deletion was correct and independently reproduced the float16→float32 SNR
result (~65–66 dB vs ~140–141 dB). It also found real defects. This task fixes them.

Work on `pipeline-fixes`. Nothing here is deployed yet, so none of this is urgent in the
"production is broken" sense — but **item 1 must land before any deploy.**

---

## 1. HIGH — `_write_wav` allocates ~815 MB of transient on a 5-minute track

This is a bug I introduced in `590d5e6`. `np.random.random()` returns **float64**
regardless of the dtype it's dithering, so the whole-track version allocates two
full-length float64 noise arrays plus their difference — dwarfing the float32 audio.

Measured on a 5-minute stereo track: **+815 MB over baseline inside `_write_wav` alone.**
On a 2 GB instance that can OOM by itself, which is precisely what the rest of this
branch was meant to prevent.

Replace `_write_wav` in `processor.py` entirely with:

```python
def _write_wav(filepath, left, right, sr):
    """
    Write stereo 16-bit WAV with rounding + TPDF dither, streamed in chunks.

    Chunked because the whole-track version allocated ~815MB of transient on a
    5-minute track: np.random.random() returns FLOAT64 regardless of the audio's
    dtype, so two full-length noise arrays plus their difference dwarfed the
    float32 signal being dithered. On a 2GB instance that alone could OOM.

    Peak transient is now a function of CHUNK, not track length.
    """
    n = min(len(left), len(right))
    CHUNK = 1 << 20                      # ~1M frames ≈ 8MB of float32 work per pass
    rng = np.random.default_rng()

    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for s in range(0, n, CHUNK):
            e = min(s + CHUNK, n)
            blk = np.empty((e - s, 2), dtype=np.float32)
            blk[:, 0] = left[s:e]
            blk[:, 1] = right[s:e]
            np.clip(blk, -1.0, 1.0, out=blk)
            blk *= 32767.0
            # TPDF dither, float32 end to end (Generator.random honours dtype)
            d = rng.random((e - s, 2), dtype=np.float32)
            d -= rng.random((e - s, 2), dtype=np.float32)
            blk += d
            np.round(blk, out=blk)
            np.clip(blk, -32768.0, 32767.0, out=blk)   # clip AFTER dither — no wraparound
            wf.writeframes(blk.astype(np.int16).tobytes())
```

Verified: **+35 MB** peak instead of +815 MB, and all the properties you checked before
still hold — mean error 3.8e-08 (no DC bias), 87.3 dB SNR on a 1 kHz sine, `+1.0` → max
32767 / `-1.0` → min -32768 with no wraparound, and dither demonstrably live at full
scale. Please re-run your own checks rather than trusting mine.

Note it now truncates to `min(len(left), len(right))` rather than relying on
`np.column_stack` raising on a length mismatch. If any caller depends on that raising,
say so instead of silently changing it.

---

## 2. HIGH — partial download failures produce a silently incomplete "complete" job

`_fetch_stem()` turns every HTTP/ffmpeg error into `None`, and the caller succeeds if
even one stem arrived (`processor.py` ~1021, ~1057, ~1077). A transient failure on
drums, bass or vocals yields a job marked complete, missing an instrument, with no
warning to the user.

**Fair attribution:** the silent-partial *semantic* predates `a6568dd` — the original
sequential loop also caught per-stem, printed, and continued. Your change didn't
introduce it, but it's worth fixing now since you're in here.

Wanted:
- Distinguish "model didn't return this stem" from "download/convert failed."
- Retry a failed required stem at least once.
- If a required stem is still missing, mark the job `partial` and surface which one —
  don't report `complete`.
- Preserve the real underlying errors instead of collapsing them into "no downloadable
  stems," which no longer matches what the outer retry classifier expects.

---

## 3. MEDIUM — failed conversions leak `_raw_*.wav`

The `.flac` source is cleaned in `finally`, but `wav_dest` is never removed when ffmpeg
fails or times out (`processor.py` ~1032, ~1060). The later `_raw_*.wav` sweep only runs
after Replicate returns successfully, so all-failure, callback-exception and pool-abort
paths leak partial WAVs. On an instance with a 1500 MB free-disk floor that accumulates.

Convert to a temp destination, validate, atomically rename, and clean both temp paths in
`finally`.

---

## 4. MEDIUM — the "lean parent" rule is aspirational, not enforced

I wrote in `CLAUDE.md` that "TensorFlow must never be loaded in the parent process." The
code doesn't honour that, and deleting the melodic split didn't establish it:

- `_ensure_imports()` (`processor.py` ~1097 call site) does
  `from basic_pitch.inference import predict` **in the parent**, which pulls in TF.
- `app.py` ~1561 imports TensorFlow *explicitly* to `clear_session()` on a parent that
  never ran inference — the child already exited and took its memory with it.

Wanted:
- Split DSP-only imports (numpy, pandas) from pitch imports (`basic_pitch.inference`) so
  the parent only loads the latter if it genuinely needs in-process inference.
- Remove the parent `clear_session()` import.
- **Then update `CLAUDE.md`** so the rule describes what the code actually does. A rule
  that's silently violated is worse than no rule — it makes the next session confident
  about something false. That one's on me.

---

## 5. LOW — stale references and product claims

- `processor.py` ~1374 — comment claims the split is called from `app.py`. It isn't.
- `README.md` ~118 — still advertises lead/accompaniment decomposition as a feature.
  This is a product claim about a capability that no longer exists.
- `app.py` ~1054, `ingest_demo.py` ~67 — historical references.
- Demo data still ships Lead/Rhythm Guitar stems, and frontend sorting still supports
  those names. Leaving the frontend tolerant is fine; the demos advertising a capability
  the live pipeline no longer has is the open product question, not a code bug.

---

## 6. LOW — redundant `.astype(np.float32)` after the accumulator change

`processor.py` ~496: now that the accumulators are already float32, those
`.astype(np.float32)` calls in the normalize phase are float32→float32 **copies**. Use
`.astype(np.float32, copy=False)` or drop them. Good catch — pure waste I left behind.

---

## Two calibrations on the review, for the record

Neither changes the fixes above; both are worth knowing so the next reader isn't misled.

**"float32 is not a memory win" was too confident.** The review is right that the seven
accumulators grow ~177 MiB. My claim was about *peak RSS*, which I measured at 538 MB
(f16) vs 589 MB (f32) on a 5-minute track — a ~51 MB delta, because the float16 path
upcasts to float32 during normalize anyway. Both numbers are true; they measure different
things. Given item 1 above, I was clearly too casual about memory, so take the review's
framing as the safer default.

**The "1.4 kHz cutoff" shorthand was sloppy.** The ceiling is the 4th harmonic, so it
scales with pitch — 416 Hz at E2, 1405 Hz at E4, 2723 Hz at E5. The original review had
the per-note table; the one-line summary flattened it. The algorithm is still
indefensible, but the number isn't universal.

---

## Acceptance

- [ ] `_write_wav` peak transient measured and bounded, dither properties re-verified
- [ ] A forced single-stem download failure produces `partial`, not `complete`, and names
      the missing stem
- [ ] A forced ffmpeg failure leaves no `_raw_*.wav` behind
- [ ] `CLAUDE.md` describes the actual import behaviour
- [ ] Then, and only then, deploy and run the before/after comparison
