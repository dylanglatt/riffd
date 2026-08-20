# Riffd — project context for Claude Code

Flask + vanilla-JS web app. Search a song → acquire audio → Demucs stem separation on
cloud GPU → interactive multitrack mixer + key/BPM/chords/lyrics.

## Deploy target — these two numbers constrain almost every decision

**Render Standard: 2048 MB RAM, 1 vCPU.** Not a dev box. Before proposing any change
that allocates memory or spends CPU, check it against these.

### Memory architecture (do not break this)

The parent gunicorn worker must stay lean. Heavy imports are deferred to first job —
startup RSS is ~40 MB instead of ~300 MB. That is intentional; don't hoist them to
module scope.

**TensorFlow must never be loaded in the parent process.** Basic Pitch runs in a
child process (`app.py` ~1505) specifically so the OS reclaims its ~1.2 GB on exit.
The child is the binding memory constraint: while it runs, the parent must stay
under roughly `2048 − 1200 ≈ 850 MB`. Any code path that calls `predict()` inline is
a bug, not an optimization.

This rule was aspirational until the deferred imports were split in two, and the
split is what enforces it:

- `_ensure_imports()` — numpy + pandas only. Safe anywhere, called on every job.
- `_ensure_pitch_imports()` — `basic_pitch.inference`, and therefore TensorFlow.
  Only `extract_note_events()` calls it, and app.py only ever invokes that in a
  child process.

So: **a new parent-side caller of `_ensure_pitch_imports()` (or a bare
`import tensorflow`) reintroduces the bug.** Before the split, `_ensure_imports()`
pulled in `basic_pitch.inference` for everyone, and app.py separately imported
TensorFlow just to call `clear_session()` on a parent that had never run inference —
so the rule above was being violated by the very code that documented it.

`MEMORY_GUARD_MB` gates new jobs on parent RSS. It must stay **below 2048** or the
Linux OOM killer fires first and the guard never triggers. (It was 2200 for a while
— inoperative, not lenient.)

### 1 vCPU changes what "parallelize" means

- **Do parallelize network I/O** — stem downloads from Replicate, external API calls.
  That's wait time, and it overlaps fine on one core.
- **Do NOT parallelize CPU work** — thread-pooling ffmpeg conversions or DSP gives
  roughly nothing on 1 vCPU and adds failure modes. Prefer *eliminating* work over
  spreading it.
- Long DSP loops hold the GIL and starve `/api/status` polling in the same worker,
  so progress appears frozen. Vectorized numpy (batched `np.fft.rfft(frames, axis=-1)`)
  releases the GIL and is 5–10× faster for identical math.

## Audio signal-chain rules

The chain is already 5–6 lossy generations deep (YouTube → yt-dlp → Demucs → delivery).
Treat every additional generation as a real cost.

- **Never downgrade audio upstream of Demucs.** Bitrate reduction before separation
  destroys the HF stereo cues the model separates on, *and* the panning information
  `_stereo_separate()` later reads back out.
- **float32 in DSP, never float16.** numpy has no native float16 arithmetic on x86 —
  it's emulated, so it's ~29× slower per accumulate *and* drops overlap-add
  reconstruction from ~141 dB to ~66 dB SNR (≈11-bit audio inside a 16-bit file).
  It is not a memory win either: peak RSS is set during the normalize phase, where
  float16 upcasts to float32 anyway. Measured: 5-min track, 538 MB (f16) vs 589 MB
  (f32) peak, but 13.3 s vs 6.6 s wall.
- **Round + dither** when quantizing to 16-bit. `.astype(np.int16)` truncates.
  Generate the dither with `np.random.default_rng().random(shape, dtype=np.float32)`
  and process in chunks. `np.random.random()` ignores dtype and returns **float64**:
  the whole-track version of this allocated ~900 MB of transient on a 5-minute
  track (two full-length float64 noise arrays plus their difference), which on a
  2048 MB instance can OOM on its own. Clip *after* adding dither, or full-scale
  samples wrap.
- Stems from Demucs sum back to the original mix. Unity is the correct default fader
  position; attenuating quiet stems makes users think separation failed.
- If you switch Demucs to FLAC output, **pair it with parallel stem downloads** — FLAC
  is ~32 MB/stem vs ~5.6 MB for MP3, so alone it adds ~25 s of sequential download.

## Known-deliberate deletions — do not reintroduce

These were removed on purpose. If you find yourself re-adding one, stop and ask.

1. **128 kbps pre-upload transcode** in `_separate_stems_replicate`. Cost ~3–15 s to
   save <1 s of upload, and was the single worst quality loss in the chain.
2. **float16 accumulators** in `_stereo_separate`. See above.
3. **`_melodic_split_pass`** and its helpers. It was a pitch percentile, not source
   separation: it tiled a 90-second mask across the whole song (so >50% of a 4-min
   track was unrelated to the audio), kept only 4 harmonics in ±100 Hz windows
   (deleting everything above ~1.4 kHz), and loaded TensorFlow *in-process*.

## Caching a partial job

`_finalize()` in `app.py` caches results and calls `set_track_status(..., "available")`,
which serves that cache to **everyone** who opens the track later. So "partial" has to
be split by cause:

- a non-stem stage failed (lyrics, recs, insight) → cache it. Those degrade
  gracefully and re-running won't reliably fix them.
- a required stem is missing → **do not cache**. Otherwise one transient download
  failure is pinned permanently as an analysis with an instrument missing. Skipping
  the cache costs the current user nothing and makes the track re-analyze next time.

`missing_stems` carries that distinction. Note the difference between a stem the
model never returned (fine — htdemucs 4-stem has no guitar/piano) and one it gave a
URL for that we failed to fetch (a missing instrument).

Bump `ANALYSIS_VERSION` in `cache_version.py` when the pipeline changes what gets
stored — removing stem types counts. `db.py` is the only consumer; a bump makes every
older cache "unavailable" so tracks re-analyze once.

## State model

Single gunicorn worker (`workers = 1`, sync). Everything is per-process: the `jobs`
dict, `_bg_downloads`, `_job_queue`, `_active_processing`, the locks, the history
cache. **The app cannot currently run more than one worker** — a second would double
the concurrency limits, double TF memory, and 404 half the job IDs. Making job state
external (the `job_checkpoints` table already exists) is the precondition for scaling.

`threading.Lock` has no ownership — `release()` from a thread that never acquired it
silently frees another thread's lock. Track acquisition with an explicit flag.

## Working agreements

- **No test suite.** Verify by running the app and exercising the real path; don't
  claim something works because it compiles. `python -m py_compile` is a floor, not
  a check.
- Work on a branch. `main` frequently has uncommitted changes.
- `app.py` is ~2,240 lines with a 734-line function (`process_audio`/`run()`).
  Several live bugs exist *only* because that control flow is too long to hold in
  one's head. Prefer extracting a stage over adding to it. Stage boundaries are
  already marked with `# ── Stage N ──`.
- The mixer engine is duplicated between `templates/decompose.html` and
  `templates/demo.html` (~45% of JS byte-identical). **Any audio fix must be applied
  to both**, or extract to `/static/js/mixer.js` first.
- Secrets belong in env vars with no hardcoded fallback.
