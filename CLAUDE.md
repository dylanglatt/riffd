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

**`basic_pitch` must never be imported in the parent process.** Basic Pitch runs
in a child process (`app.py` ~1600) specifically so the OS reclaims its RSS on
exit. The child is the binding memory constraint: while it runs, the parent must
stay under roughly `2048 − 360 ≈ 1690 MB`. Any code path that calls `predict()`
inline is a bug, not an optimization.

**`torch` must never be imported in the parent process either**, for exactly the
same reason. `panns_tagger.py` imports it at module scope on purpose — so that
importing that module anywhere is loud rather than accidental — and it is only
ever imported by `processor.tag_components_child()`, which runs in the child
`run_tagger()` spawns. Torch alone is ~190 MB; the tagging child peaks at
~478 MB, so while *it* runs the parent must stay under roughly
`2048 − 480 ≈ 1570 MB`. See "Component labels come from a PANNs audio tagger".

The two children never overlap: tagging finishes inside `separate_stems()`,
Basic Pitch starts after it returns.

⚠️ **Every child-RSS figure on this page (~360 MB, ~478 MB, ~820 MB) is measured
on macOS arm64, not on Render.** They are the right order of magnitude and the
*relative* result is solid, but no Linux measurement exists yet — see the
"Render verification checklist" below before treating them as production
numbers.

That 360 MB used to be ~820 MB, and the difference was entirely TensorFlow — see
"Basic Pitch runs on ONNX" below. The child boundary is *kept* anyway: 360 MB
retained per stem in a 2048 MB worker is still worth handing back to the OS, and
the isolation also contains a segfaulting native runtime.

This rule was aspirational until the deferred imports were split in two, and the
split is what enforces it:

- `_ensure_imports()` — numpy + pandas only. Safe anywhere, called on every job.
- `_ensure_pitch_imports()` — `basic_pitch.inference`, and with it librosa, numba
  and onnxruntime. Only `extract_note_events()` calls it, and app.py only ever
  invokes that in a child process.

So: **a new parent-side caller of `_ensure_pitch_imports()` reintroduces the
bug.** Before the split, `_ensure_imports()` pulled in `basic_pitch.inference`
for everyone, and app.py separately imported TensorFlow just to call
`clear_session()` on a parent that had never run inference — so the rule above
was being violated by the very code that documented it. Both are gone.

`MEMORY_GUARD_MB` gates new jobs on parent RSS. It must stay **below 2048** or the
Linux OOM killer fires first and the guard never triggers. (It was 2200 for a while
— inoperative, not lenient.) At 1400 it was still above the real TensorFlow-era
budget of ~1200 MB; dropping the child to 360 MB is what made 1400 sound rather
than merely non-fatal.

### Basic Pitch runs on ONNX — do not let `tensorflow` back into the environment

basic-pitch 0.4.0 ships four copies of the same ICASSP-2022 weights (all under
300 KB, all in the wheel) and four runtimes to execute them: TensorFlow, CoreML,
TFLite, ONNX. The weights are the same, so **the note events are identical** — the
only thing that changes is what it costs to get them.

Measured on one 90 s stem each, macOS arm64 / Python 3.11, same child-process path
app.py uses (`tensorflow-macos` 2.15.0 vs `onnxruntime` 1.29.0):

| stem | notes | wall (TF → ONNX) | child peak RSS (TF → ONNX) |
|---|---|---|---|
| vocals | 377 → 377 | 7.20 s → 1.85 s | 796 MB → 363 MB |
| bass | 157 → 157 | 4.64 s → 1.79 s | 838 MB → 362 MB |
| guitar | 382 → 382 | 4.53 s → 1.88 s | 831 MB → 362 MB |
| **mean** | **0.0 % drift** | **5.46 s → 1.84 s (3.0×)** | **822 MB → 362 MB (−459 MB)** |

Onset times, MIDI pitches and confidences matched exactly, not just in aggregate.
That part is platform-independent: identical weights, identical arithmetic.

**The memory and timing numbers are not.** They are macOS arm64, and Render is
Linux/x86 — a different TensorFlow build, a different allocator, and a slower
core. The *expectation* is that the saving is at least as large there (Linux
TensorFlow wheels are generally heavier than `tensorflow-macos`, and the ONNX
side ships the same 23 MB `manylinux_2_28_x86_64` cp311 wheel), but that is a
prediction, not a measurement. Nothing on Render has been measured. Do not quote
these as production figures until the checklist below has been run.

### Render verification checklist — run this before merging to `main`

The Linux numbers are still unmeasured. To close that out:

1. Deploy the `basic-pitch-onnx` branch to Render.
2. Confirm the build gate ran: `[build] OK: no tensorflow; backend=onnx model=nmp.onnx`
   in the build log. If build.sh did not run at all, the deploy is not using
   `render.yaml` / the dashboard build command — stop and fix that first.
3. Confirm the parent logged no `[startup] WARNING` banner about the
   `tensorflow` package being installed.
4. Analyse one real track end to end.
5. From the job logs record, per stem: `Basic Pitch → <stem> done` wall time, and
   the child's peak RSS from the `[mem] [extract_notes] post-predict` line. The
   child's output is forwarded into the parent log prefixed `[bp:<stem>]`.
6. Confirm `[bp:<stem>] [processor] pitch imports loaded (basic_pitch, onnx
   backend, model=nmp.onnx)` appears for each stem.
7. Replace the macOS table above with the Linux numbers, and delete the ⚠️ caveat
   in the memory-architecture section.

The PANNs tagger has the same gap — every number in "Component labels come from
a PANNs audio tagger" is macOS arm64. While you are there:

8. Confirm `[build] OK: PANNs CNN10 loaded, 527 classes, N mapped to riffd
   labels` in the build log, and that the checksum step passed.
9. Record the `[tagger] tagged N/M component(s) in Xs` line and the child's peak
   RSS from `[tagger:child] [mem] [tag_components] post-tag`. Expect ~3 s and
   ~478 MB; a 1 vCPU Render core will be slower.
10. Spot-check the per-component `[tagger] <key>: ...` lines on the first few
    real songs. That log line exists precisely so the thresholds can be
    re-calibrated on production audio rather than on `static/demo`.

Until step 7 is done, `MEMORY_GUARD_MB` and the ~1690 MB parent budget are
derived from macOS figures.

**The trap:** `basic_pitch/__init__.py` selects its backend by import probe, in the
order TF → CoreML → TFLite → ONNX. Merely having `tensorflow` *importable* puts it
back on the TF path. So the win is not "use ONNX", it is "**do not have TensorFlow
installed**".

Five things defend that, in order from "keeps TF out of the image" to "survives
TF being in the image anyway". All five are load-bearing:

1. **`--no-deps`.** basic-pitch's metadata carries
   `Requires-Dist: tensorflow<2.15.1; platform_system != "Darwin" and python_version >= "3.11"`
   — a *hard* requirement on Render's exact platform, not an extra. `pip install
   basic-pitch[onnx]` therefore installs TensorFlow anyway. build.sh installs it
   from `requirements-basic-pitch.txt` with `--no-deps`, and requirements.txt owns
   its real runtime deps instead. Pin the version when bumping: `--no-deps` means
   we are asserting we know that version's dependency list.
2. **basic-pitch appears in no file that gets normal dependency resolution.**
   Not requirements.txt, not requirements.lock — only requirements-basic-pitch.txt,
   which is only ever installed `--no-deps`. A plain `basic-pitch==0.4.0` line in
   a lockfile silently reinstalls TensorFlow on Linux and undoes everything above.
   (It did, briefly. That is why the lock carries a warning header.)
3. **`render.yaml`.** build.sh is where 1 and 4 live, so a deploy that runs a bare
   `pip install -r requirements.txt` instead ships without basic-pitch at all and
   dies at first inference — after separation has already succeeded. The build
   command is source-controlled so that cannot happen by dashboard drift.
4. **The build gate.** build.sh fails the build if `import tensorflow` succeeds or
   if the resolved backend is not `onnx`. This exists because the regression is
   otherwise silent — everything still works, just 3× slower and 460 MB fatter.
5. **`_force_onnx_backend()` in processor.py.** The child installs a `sys.meta_path`
   finder that makes `import tensorflow` raise ImportError, *before* basic_pitch is
   imported. basic_pitch guards its TF probe with `except ImportError`, so this is
   the same code path as TF genuinely being absent — it just takes it on demand.
   It deliberately **declines to block** when no non-TF runtime is installed:
   basic_pitch's `__init__` picks its default suffix with an if/elif chain and no
   else, so with all four backends absent `import basic_pitch` dies on
   `NameError: _default_model_type`. Blocking a TF-only environment would convert
   a slow-but-working install into a hard failure with an error that names
   nothing. It warns and lets TF through instead.

Point 5 is the one that matters when 1–4 have failed, and it is not cosmetic.
Selecting the `.onnx` model path is **not** sufficient on its own: `basic_pitch`
decides `TF_PRESENT` by probing `import tensorflow` at module scope, so on an
environment carrying both runtimes the old code ran ONNX *and still paid for
TensorFlow*. Measured on a venv with both installed, same stem, same 382 notes:

| | TF modules in `sys.modules` | child peak RSS |
|---|---|---|
| explicit `.onnx` path only | 1596 | 672 MB |
| + `_force_onnx_backend()` | 0 | 418 MB |

So `_PITCH_BACKENDS` pins which *graph* runs; the import block is what stops the
memory being spent. Both are needed.

Note also `setuptools<81` in requirements.txt: resampy 0.4.2 (a basic-pitch dep we
now own) imports `pkg_resources`, which setuptools 81 removed. Without the pin a
fresh build installs setuptools 82 and every inference child dies on import.

### Component labels come from a PANNs audio tagger — heuristics are the fallback

`_classify_component()` labels a component by thresholding its spectral
centroid. It never listens for *what* the instrument is. `apply_instrument_hints()`
then rewrites vague labels by first-keyword-matching the LLM's **song-level**
instrument list, called with `features=None` — so it does not listen either, and
whichever branch of `_match_predicted_label()` comes first wins for every vague
component in the song. A song predicting both "strings" and "horns" therefore
labelled all of them "Horns", because that branch was first. Observed in
production on ELO's "Livin' Thing": the string intro, labelled Horns.

So the order of authority is now:

1. **PANNs tagger**, if confident → it wins outright.
2. **Abstain** otherwise → the heuristic + hint path runs exactly as before.
   This is the common outcome and it is a first-class one, not a failure.
3. **Hints as tie-breaker only** → when the tagger's top two mapped labels are
   within `TAGGER_TIE_MARGIN`, prefer the one the LLM also predicted — but only
   if that alternative *also* clears the confidence bar. Being inside the margin
   makes a label a candidate, not evidence for it. Without that check the margin
   leaks the threshold: at a 0.50 bar, Strings=0.50 beside Horns=0.41 handed the
   label to Horns purely because a hint said "horns".

**The axis is confident-vs-abstained, not changed-vs-unchanged.** A confident
tagger *confirmation* — PANNs agreeing with the label already there, or with a
less specific version of it — is a positive result and locks the label just as
firmly as a change does. `TaggerDecision.confident` carries that, and
`_apply_component_tagging()` sets `tagged: True` on both. Conflating the two was
a bug: a component confidently heard as a synth at 0.80 was still rewritten to
"Strings" by a song-level hint that never listened to anything. On the 38
components in `static/demo` this locks 9 that were previously open to the hint
chain.

`apply_instrument_hints()` skips any stem carrying `tagged: True`. That key is
private to the in-process `stems` dict — app.py projects to
`{label, energy, active}` before anything is cached or served, so it never
reaches the API.

**Model: PANNs CNN10**, 25 MB, AudioSet mAP 0.380 (CNN14 is 327 MB for 0.431 —
13x the download for 0.05 mAP on a 527-class problem we use ~40 family-level
classes of). Fetched by build.sh into `models/panns/` (gitignored) and
checksummed; **never downloaded during a job**. `torch` already ships via
demucs. There is no `panns_inference` dependency and no `torchlibrosa`
dependency — see "Known-deliberate deletions".

**Thresholds are calibrated, not guessed.** Over the 38 components in the 18
analysed songs under `static/demo`, top mapped scores are sharply bimodal:

| bucket | evidence | bar |
|---|---|---|
| `other` (label was a centroid guess) | true positives at 0.54 and 0.78; every score ≤ 0.25 was wrong; one false positive at 0.42 | `TAGGER_MIN_CONFIDENCE = 0.50` |
| `guitar`, `piano` (dedicated Demucs heads) | tagger was wrong on *every* confident call — 0.55 "Organ" on Kill Bill's guitar, 0.41 "Strings" on Bohemian Rhapsody's piano | `TAGGER_DEDICATED_HEAD_MIN_CONFIDENCE = 0.60` |
| `vocals`, `drums`, `bass` | dedicated heads, nothing to gain | never tagged |
| below-energy-gate rescue | no real rescue observed yet to calibrate on | `TAGGER_RESCUE_MIN_CONFIDENCE = 0.60` |

The absolute numbers are low because an isolated Demucs stem is nothing like
AudioSet's training distribution of full mixes. **0.5 is a confident call here.**
Do not "fix" these by raising them toward 0.9 — that switches the tagger off.

Net effect on those 38 components: 2 labels change. Bohemian Rhapsody's
"Synth" → "Strings" (the song famously has no synthesiser) and Take It Easy's
hint-assigned "Banjo" → "Guitar" (Banjo scored 0.003). Everything else abstains.
That ratio is the design working, not the tagger idling.

Two rules that exist because measurement said so:

- **A confirmation is not a correction.** If the tagger's label is a word-subset
  of the current one ("Rhythm Guitar" → "Guitar", "Synth Pad" → "Synth"), abstain.
  Four of ten changes at the first threshold were this, all of them losing
  specificity.
- **Generic AudioSet parents are absent from `AUDIOSET_TO_LABEL`, not outranked.**
  PANNs scores "Music" ~0.85 on any musical input. The map is an explicit
  allow-list of exact `display_name` strings; build.sh fails if one drifts.

**Threshold rescue.** A component that fails `MIN_RELATIVE_ENERGY` /
`MIN_ABSOLUTE_ENERGY` is staged as `_cand_*.wav` and tagged anyway; a confident
one is kept, subject to `MAX_REFINED_STEMS` — a rescue never breaks the cap.
Unrescued candidates are deleted, and the `finally` in `separate_stems()` sweeps
`_cand_*` on the crash path.

`TAGGER_RESCUE_ENERGY_FLOOR` (`0.05 × MIN_ABSOLUTE_ENERGY` = 4.0e-4) is the
backstop under that. `read_crops()` peak-normalises deliberately — components in
one song span ~30 dB and PANNs is level sensitive — and the price is that
normalisation cannot tell quiet from *absent*. Measured: a side component 33 dB
below the centre of the same stem scored **Guitar=0.55 against the centre's
0.54**. Level is simply not in the answer. Below the floor a component is never
staged, never written, never seen by the child.

The floor is sized from measurement, not from a round number. Across 7 full
local separations the components reaching the staging branch had mono RMS
9.2e-4 .. 1.4e-2; `0.25 × MIN_ABSOLUTE_ENERGY` would have rejected more than
half of them. Both the floor and the tagger judge the **mono downmix**, which is
what keeps them consistent: `_stereo_separate()` can emit a component whose
channels carry real audio but whose downmix is exactly 0.0 (verified with
anti-phase input), and `read_crops()` downmixes too — so the floor cannot reject
anything the tagger would have labelled confidently.

**Child log bounds are UTF-8 bytes, not `len(str)`.** `forward_child_log()`
caps 40 lines, 400 bytes per line, 8192 bytes per child, and truncates on a
codepoint boundary so a cut multibyte sequence never reaches the log. Counting
codepoints instead forwarded **23,147 bytes against the 8,192 cap** on a CJK
probe — nearly 3x, since the point of the cap is what the log transport costs.
The per-line marker is inside the per-line budget, not added on top.

**Tagging can never fail a job.** `run_tagger()` returns `{}` on a missing
checkpoint, a non-zero child, a timeout or any exception, and every component
then keeps the label it arrived with. Verified for all of those.

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

## Separation backend — `SEPARATION_BACKEND`, default Replicate

Two hosted backends now exist behind one env var. `USE_HOSTED_SEPARATION` still
gates hosted-vs-local; `SEPARATION_BACKEND` picks which hosted one.

| value | path | code |
|---|---|---|
| `replicate` (**default**) | htdemucs_6s on Replicate | `_separate_stems_replicate()` |
| `modal` | the Phase A cascade on Modal A10G | `_separate_stems_modal()` |

Both return the same `(raw_stems, model_name)` and the same `report` semantics
(`omitted` vs `stem_failures` — see "Caching a partial job"). **Deploying the
integration flips nothing**: the default is `replicate` and the Replicate path
is untouched.

### Rollback is a pure env change, no deploy

Set `SEPARATION_BACKEND=replicate` and restart. That reverts the code path *and*
the cache, because `ANALYSIS_VERSION` is backend-aware: `cache_version.py`
resolves to `v7` on Replicate and `v7-modal` on Modal. A flat bump to `v8` would
have invalidated every cached track on a deploy that changes nothing; this way
the cache turns over exactly when the output actually changes, and flipping back
makes the pre-existing entries valid again rather than re-analysing the world
twice. **Consequence of switching TO modal:** every cached track re-analyses
once, by design — the separation genuinely differs.

### Measured, both backends, same track (Take It Easy, 3:32)

End to end through the real HTTP path, `scripts/e2e_backend_check.py`:

| | replicate | modal |
|---|---:|---:|
| wall, upload → complete | **90.3 s** | **171.6 s** |
| separation stage | 33.6 s | 138.4 s (103.9 s GPU, cold container) |
| parent RSS peak | 831 MB | **820 MB** |
| refined stems / with notes | 7 / 5 | 5 / 3 |
| watchdog, memory guard | never fired, untouched | never fired, untouched |

Parent memory is the number that mattered going in: the Modal worker returns
FLAC **bytes inline** rather than URLs, so the fear was a large transient in the
one process that has to stay lean. Measured, it is not — 820 MB against the
Replicate path's 831 MB.

### Long tracks route to Replicate — `LONG_TRACK_ROUTE_MINUTES` (default 12)

That 820 MB is a 3:32 track. The inline payload scales with duration, and the
Phase A worker eval returned **423.9 MB of FLAC for a 20.65-minute track**. Into
riffd's parent that is a **projected** ~+424 MB of transient — projected, not
measured, because nobody has run a 20-minute track through riffd itself — in the
one process that must stay under ~1690 MB while Basic Pitch children draw on the
same budget.

So `separate_stems()` routes rather than gambles: on the Modal backend, a track
longer than `LONG_TRACK_ROUTE_MINUTES` goes to Replicate instead, which streams
each stem to disk and never holds a whole track in memory. With no
`REPLICATE_API_TOKEN` there is nothing to route to, so it proceeds on Modal with
a loud warning naming the projected payload. The guard is Modal-only; the
Replicate path is unaffected by duration.

**Lifting this requires one measured 20-minute run through riffd** — not through
`modal_worker/eval`, which measures the worker's own container and says nothing
about the parent. `MAX_TRACK_MINUTES` allows 20, so the gap between 12 and 20 is
real and deliberate.

### Three things the integration had to get right

- **Heartbeats.** The cascade is one 100–500 s phase with nothing to say. It is
  driven with `spawn()` + poll rather than a blocking `remote()` precisely so
  `progress_callback` (and through it `_touch_job`) fires every 3 s — the same
  shape as the Replicate poll loop, for the reason in "`_touch_job()` is the
  heartbeat". A blocking call here would be killed by the watchdog on any track
  over ~5 minutes.
- **`MAX_WAIT` from measurement.** `min(300 + 1.5 × duration, 1500)`. Phase A
  measured Modal wall at ~0.46× track duration, linear from 3:33 to 20:39, so
  this is ~6× headroom on a short track and ~2.6× on a 20-minute one. Same
  1500 s cap as Replicate, and the same known edge: a long track that retries
  can still exceed `JOB_MAX_SECONDS`.
- **Permanent vs retryable.** The classifier now short-circuits on failures a
  human has to fix — spend limit, missing payment method, `lookup failed` (app
  not deployed), and a Volume that fails `WEIGHTS_MANIFEST` verification.
  Retrying those costs three attempts plus backoff and still fails; worse, a bad
  Volume makes Modal retry the *container* underneath us too. Modal transients
  (gRPC, preemption, cancelled task) are retryable as before.

### Client dependency and auth

`modal>=1.0` is in requirements.txt — a gRPC client, not a model runtime, and
imported lazily inside `_separate_stems_modal()` so the default path never pays
for it. On Render, auth is `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`; locally it
reads `~/.modal.toml`. Missing credentials surface as a lookup failure, which
the classifier treats as permanent rather than retrying three times.

The GPU worker itself lives in `modal_worker/` and deploys separately
(`modal deploy modal_worker/worker.py`). Its licence position is unresolved and
applies to the incumbent too — read `modal_worker/LICENSES.md` before treating
either backend as commercially clear.

### Two conclusions not to re-derive

Both were got wrong once already:

- **Replicate output is FLAC, not MP3.** `_separate_stems_replicate()` at
  processor.py ~1779 requests `"output_format": "flac"`. The `"mp3"` at ~340 is
  inside `_warmup_replicate()`, which boots a container with silence and throws
  the result away. An earlier note here claimed today's stems were lossy on the
  strength of that line; they are not.
- **The incumbent's −13.7 to −23.0 dB reconstruction is structural, not codec.**
  Demucs emits six independent estimates with nothing forcing them to sum to the
  input; the cascade *defines* `other` as the residual, so its sum closes by
  identity. That figure compares how each system defines its stems, not how well
  either separates — a cascade of pure noise would reconstruct just as exactly.

## Known-deliberate deletions — do not reintroduce

These were removed on purpose. If you find yourself re-adding one, stop and ask.

1. **128 kbps pre-upload transcode** in `_separate_stems_replicate`. Cost ~3–15 s to
   save <1 s of upload, and was the single worst quality loss in the chain.
2. **float16 accumulators** in `_stereo_separate`. See above.
3. **`_melodic_split_pass`** and its helpers. It was a pitch percentile, not source
   separation: it tiled a 90-second mask across the whole song (so >50% of a 4-min
   track was unrelated to the audio), kept only 4 harmonics in ±100 Hz windows
   (deleting everything above ~1.4 kHz), and loaded TensorFlow *in-process*.
4. **`torchlibrosa`** as the PANNs front end. It is the package PANNs trains
   with, and it recomputes the STFT/mel matrices with `librosa` in `__init__` —
   dragging librosa + numba into the tagging child for matrices the 25 MB
   checkpoint already carries. `panns_tagger.py` reimplements those three
   layers (~40 lines) and lets `load_state_dict()` fill them instead. Measured:
   **−193 MB of child RSS, bit-identical scores** on all 527 classes. Adding
   the dependency back undoes exactly that.
5. **The `tensorflow` package**, in any form — including as a transitive dependency,
   including `basic-pitch[tf]`, including "just to call `clear_session()`". Basic
   Pitch runs on ONNX for identical note output at a third of the wall clock and
   460 MB less per child; `basic_pitch` reverts to TF the moment TF is importable,
   so installing it *is* the regression, whatever the intent. build.sh fails the
   build on it. See "Basic Pitch runs on ONNX" above.

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

## Timeouts — the watchdog measures stall, not elapsed

Measured p50 for a full analysis is **~147 s** end to end. Every number below is
positioned against that, and the ordering between them is load-bearing.

| Bound | Where | Value | What it means |
|---|---|---|---|
| `STALL_TIMEOUT_S` | `app.py` | 300 s | No heartbeat for this long → the job is wedged, error it |
| `MAX_WAIT` | `processor.py` | `min(420 + extra, 1500)` | Ceiling for **one** cloud separation attempt |
| `JOB_MAX_SECONDS` | `app.py` | 1800 s | Absolute backstop for a job that heartbeats forever |
| `POLL_TIMEOUT_MS` | `decompose.html` | 20 min | Client stops polling |

**The watchdog in `job_status()` measures silence, not total elapsed time.** It
used to do the latter, which conflates "stuck" with "slow" — and the slow path
here is legitimate and recoverable. Measuring stall is strictly better on both
axes: a wedged job dies in ~5 min instead of 20, and a slow-but-progressing job
is no longer punished for being slow.

### `_touch_job()` is the heartbeat — and the rule that follows

`_touch_job(job_id)` stamps `_last_progress_at`. That timestamp is the only
thing standing between a long phase and the watchdog. It is called from:

- `on_progress()` — covers separation (which ticks every `POLL_INTERVAL` = 3 s
  through its own Replicate poll loop), key/structure, and insight
- the `"Stems ready — analyzing..."` transition
- the `"Waiting for inference slot..."` branch
- the PANNs tagging child, before and after (`run_tagger(..., heartbeat=)`),
  via the `"Identifying instruments..."` progress string
- the Basic Pitch loop, **twice per stem** (before and after each child)

That last one is the point of the rule:

> **Any new phase that can run quiet for minutes must call `_touch_job()`, or
> the watchdog will kill its jobs.** "Quiet" means not changing the progress
> string — a stage can be working hard and still look dead from here.

Basic Pitch is the live example: a child process per stem, up to 120 s each,
with no progress-string change for the whole loop. Without its per-stem
heartbeats a six-stem track would go silent for far longer than
`STALL_TIMEOUT_S` and be killed mid-inference.

The PANNs tagging child is the cheap case — measured 2.5-3.0 s for one song's
components — but it is bounded at 120 s and prints nothing the parent sees
until it exits, so it heartbeats on both sides rather than relying on being
fast.

There is one known gap, currently unreachable: the blocking
`_inference_lock.acquire()` heartbeats once and then waits indefinitely. Safe
only because `MAX_CONCURRENT_JOBS` defaults to 1, so the lock is uncontended.
Raising that env var without adding a heartbeat there reintroduces the bug.
(The lock was `_tf_inference_lock` until Basic Pitch moved to ONNX. It still
serialises inference — one 1 vCPU core, ~360 MB per child — it just no longer
serialises a TensorFlow load.)

### `MAX_WAIT` is not a duration estimate — don't tune it down

It bounds **one** separation attempt, and `separate_stems()` makes up to
`MAX_RETRIES = 3` of them with 10 s/15 s backoff between. So one retry reaches
~850 s and three ~1285 s on a normal track, all of it progressing normally.

Two measurements from `logs/riffd.log` (15 separations) that fix the trade:

- A **healthy separation took 376.5 s** of its 420 s base budget — 44 s of
  headroom. Lowering `MAX_WAIT` kills legitimate separations.
- A retry fired once and **succeeded on attempt 2**. It had failed on a 15 s
  socket timeout, not by exhausting `MAX_WAIT`, so the common retry is cheap and
  ~850 s is the pathological branch, not the typical one.

Three further failures in those logs carried the old collapsed
`"no downloadable stems"` message, which matched none of the retry classifier's
patterns and so never retried at all. That is fixed, so the retry path now
engages roughly 4× more often than those logs show.

A watchdog short enough to cut a retry short converts a recoverable blip into a
guaranteed failure. That regression has already been shipped once — don't
re-derive it.

**Known edge:** `MAX_WAIT` scales with track length (up to 1500 s, and
`MAX_TRACK_MINUTES` allows 20-minute tracks), so a long track that retries can
exceed `JOB_MAX_SECONDS` while still progressing and be cut off by the backstop.
The stall bound governs every realistic failure, so this has not been tuned —
but it is the first thing to revisit if long tracks start timing out.

### Queue wait is not processing time

`_started_at` is stamped when the request arrives, before the job may sit in
`_job_queue` waiting for a slot. `_dequeue_next()` therefore **restamps both
`_started_at` and `_last_progress_at`** when the job actually begins. Without
that, a job that queued for five minutes arrives with five minutes already
charged against the watchdog. Jobs with no `_last_progress_at` fall back to
`_started_at`.

### The ordering constraint

A **wedged** job must be caught by `STALL_TIMEOUT_S` well before
`POLL_TIMEOUT_MS`, so the user sees the backend's real error rather than the
client's generic "taking a while" overlay. A **progressing** job must be allowed
to outlive a successful separation retry (~850 s of separation plus ~150 s of
pipeline), which is why the client sits at 20 min and not near the p50.

Getting this backwards is a silent failure: the client gives up first and the
backend's diagnosis never reaches anyone. It shipped that way once, with a
19-minute client against a 20-minute server bound, and the comment on the server
constant asserted the opposite of what the numbers did.

## State model

Single gunicorn worker (`workers = 1`, sync). Everything is per-process: the `jobs`
dict, `_bg_downloads`, `_job_queue`, `_active_processing`, the locks, the history
cache. **The app cannot currently run more than one worker** — a second would double
the concurrency limits and 404 half the job IDs. It would also double inference
memory: each worker has its own `_inference_lock`, so two workers can run two
Basic Pitch children at once (~360 MB each, measured on macOS — see below) on top
of two parents. That is more survivable than it was under TensorFlow, where the
same mistake meant two ~820 MB children, but the job-ID problem is fatal on its
own. Making job state external (the `job_checkpoints` table already exists) is the
precondition for scaling.

The PANNs tagging child is **not** serialised by `_inference_lock` — it is
spawned from `separate_stems()` in processor.py, which knows nothing about
app.py's locks. That is safe only because `MAX_CONCURRENT_JOBS` defaults to 1,
so there is one job and one tagging child. Raising that env var puts two
~478 MB children in flight concurrently with no lock between them; either take
`_inference_lock` around `run_tagger()` (and heartbeat while blocked, see the
known gap above) or don't raise it.

`threading.Lock` has no ownership — `release()` from a thread that never acquired it
silently frees another thread's lock. Track acquisition with an explicit flag.

## Working agreements

- **No test suite.** Verify by running the app and exercising the real path; don't
  claim something works because it compiles. `python -m py_compile` is a floor, not
  a check.
- **Eval claims keep the artifacts that prove them.** Every number in a report
  must be reproducible from a file checked in or retained beside it, and must say
  which conditions produced it (cold/warm, which GPU, which input). A number from
  a run whose artifact was overwritten is not evidence — the Phase A latency
  table quoted a warm Layla time for a run that the retained `_meta.json` says
  was cold, and nothing in the repo could have caught it.
- `main` is the trunk and tracks `origin/main`; the `pipeline-fixes` work has
  been merged and the branch deleted. It is currently clean, so check
  `git status` before assuming otherwise rather than branching reflexively.
- `app.py` is ~2,240 lines with a 734-line function (`process_audio`/`run()`).
  Several live bugs exist *only* because that control flow is too long to hold in
  one's head. Prefer extracting a stage over adding to it. Stage boundaries are
  already marked with `# ── Stage N ──`.
- The mixer engine is duplicated between `templates/decompose.html` and
  `templates/demo.html` (~45% of JS byte-identical). **Any audio fix must be applied
  to both**, or extract to `/static/js/mixer.js` first.
- Secrets belong in env vars with no hardcoded fallback.

## Project status — the running state of the work

**This section is the cross-session memory. Every task updates it as its
last step** (the /execute-task ceremony requires it): move finished items to
Shipped with the date, adjust In flight, add anything new you discovered to
Backlog. Keep each line short; history lives in git, not here.

### Shipped (production, main)
- 2026-08-27 — Basic Pitch TF→ONNX: ~3× faster note children, ~460 MB less
  RSS, TF excluded via --no-deps + build gate + import blocker. Regressions
  to watch: reinstalling `tensorflow` (backend preference), skipping
  build.sh (render.yaml + dashboard build command guard it).
- 2026-08-27 — PANNs component tagger: labels components by listening
  (child process, torch never in parent), hints demoted to tie-breaker,
  threshold rescue with energy floor. Fixed the ELO strings-as-Horns bug.
- 2026-08-28 — HTTP 429 added to the Replicate retry classifier (was an
  instant hard failure under low-credit throttling).

### In flight
- **Modal separation backend** — branch `modal-integration` (contains the
  Phase A worker in modal_worker/). RoFormer cascade: vocals → drums/bass →
  guitar/piano → other-as-residual. Owner's listening verdict: piano and
  vocals clearly better than htdemucs_6s; 2.25–3.20× slower warm; wins cold
  start (34.6 s vs Replicate boot gaps up to 50 s); ~$0.035/track A10G.
  Behind SEPARATION_BACKEND (default replicate; cache key v7/v7-modal makes
  rollback a pure env change).
  **2026-08-28 — PROMPT_modal_final.md all six items done; the branch is
  ready to merge.** A corrupt Volume now fails a request in 10.6 s instead of
  hanging ~10 min; tracks over `LONG_TRACK_ROUTE_MINUTES` (12) route to
  Replicate rather than pulling a ~424 MB inline payload into the parent; the
  eval JSON evidence is committed and reruns no longer overwrite it; the
  licensing docs no longer claim anything unbacked.
  Next: merge → deploy (inert, default is replicate) → set
  SEPARATION_BACKEND=modal on Render → verify with a real track → then decide
  whether to keep it.
- **Uncommitted in the working tree**: templates/decompose.html +
  templates/demo.html carry the Orchestral mixer family (strings/brass no
  longer grouped under Keys) plus the owner's own edits. Commit with the
  next template change; apply mixer fixes to BOTH files.

### Next up (agreed order)
1. scripts/smoke.py — PROMPT_smoke_test.md exists, never run. Cheap
   self-verification for every future agent pass; do it soon.
2. Note-level lead/rhythm guitar split — cluster the guitar stem's Basic
   Pitch note events (register, polyphony, onset density) into Lead and
   Rhythm tab lanes. No audio split, no new models. This ships original
   ask C.
3. Tagger calibration round: observed acoustic_guitar → "Accordion" on
   Take It Easy (accordion/guitar are AudioSet neighbors). Collect a few
   more [tagger] log examples before tuning thresholds/mapping.

### Backlog / known debts
- Modal 20-min tracks: ~424 MB inline payload projected; `separate_stems()`
  routes anything over `LONG_TRACK_ROUTE_MINUTES` (12) to Replicate. Lifting
  the guard requires one measured 20-min run **through riffd** — the Phase A
  eval measures the worker's own container and says nothing about the parent.
  `MAX_TRACK_MINUTES` allows 20, so the 12–20 gap is deliberate.
- Cascade speed headroom: mdxc stages are ~68% of GPU time at batch 1
  (audio-separator design); untapped.
- **Checkpoint licences are unresolved for BOTH backends** and that is the
  merge-blocking business question, not a code one. Demucs weights are
  research-only per the author, which covers today's htdemucs_6s too. Known
  alternative: ZFTurbo's MVSep Mega 53 Stems — first-party, emits guitar and
  piano, but still no explicit weight-licence grant, and it is 53 single-stem
  checkpoints so adopting it redesigns the cascade. See
  `modal_worker/LICENSES.md`; revisit trigger is any monetisation.
- Replicate account is under $5, which throttles it to 6 req/min burst 1. The
  prewarm can consume that allowance and throttle the real request behind it;
  the 429 retry absorbs it, but the fallback path is degraded until topped up.
- Modal Volume corruption is invisible to already-running containers (they
  hold the version mounted at start). Only new containers verify, so after a
  repair you may need `modal app stop` to retire a stale one.
- Licensing: BS-RoFormer-SW provenance chain is unverifiable (see
  modal_worker/LICENSES.md); htdemucs weights research-use statement
  applies to the incumbent too. Accepted risk while riffd is free;
  REVISIT TRIGGER: any monetization. Alternatives: ZFTurbo Mega 53-stem
  (first-party), commercial APIs (Music.AI/AudioShake).
- Replicate account under $5 → burst-of-1 throttling degrades the
  fallback path until topped up.
- Lead/rhythm audio separation (the ambitious version): DadaGP-rendered
  synthetic training data + fine-tuned RoFormer/Banquet. Scoped, not
  started — see conversation notes; months, not weekends.
- Ghostty on macOS may lose Desktop-folder TCC access (reads fail with
  EPERM while writes work). Fix: Full Disk Access for the terminal app,
  full quit, reopen. Terminal.app is the fallback.

### Working with agents on this repo
- Prompt-file workflow: task prompts live in repo root as PROMPT_*.md
  (gitignored). /execute-task <file> runs one with the ceremony in
  .claude/commands/execute-task.md; reviews follow AGENTS.md.
- One task per session. Freeze the branch while a review runs — writer
  commits during review cost a full re-anchor once.
- Review depth by blast radius: full adversarial review for anything
  touching dependencies, memory, caching, watchdog, or the separation
  path; quick diff sanity for the rest; nothing for one-liners.
