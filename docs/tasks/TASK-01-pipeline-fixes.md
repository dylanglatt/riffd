# Task 01 — Separation quality + memory fixes

Goal: remove the three things degrading stem quality, and stop the OOMs. Net effect
should be **better-sounding stems, 1–4 minutes faster, and lower peak memory** —
mostly by deleting code.

Read `CLAUDE.md` first. Do the steps **in this order** — the ordering is what keeps
peak memory in budget at every point.

Setup: `git switch -c pipeline-fixes` (commit or stash first — `main` has uncommitted
changes in `app.py`, `processor.py`, and `templates/decompose.html`).

---

## Step 1 — Delete the melodic lead/rhythm split

Do this **first**. It frees memory, which gives the later steps headroom.

Why it's going: it splits on a pitch percentile (top 45% of detected notes = "lead"),
so it always fires even when no lead exists, and it splits a single arpeggiating
guitar across two channels. Its mask keeps only 4 harmonics in ±100 Hz windows —
everything above ~1.4 kHz is multiplied by zero. And it tiles a 90-second mask across
the full track, so past 1:30 the output is unrelated to the audio. It also loads
TensorFlow in-process, which is likely the root cause of the OOMs.

**Remove:**
- `app.py` ~1584–1590 — the `from processor import _melodic_split_pass` import and the
  call. Keep whatever `stems` was before the call.
- `processor.py` ~758–1284 — one contiguous, self-contained block:
  `_extract_melodic_mask`, `_split_melodic_stem` (and its nested `_apply_mask_channel`),
  `_get_split_labels`, `_ALREADY_SPLIT_KEYWORDS`, `_melodic_split_pass`.

**Keep** `_save_sub_parts` (~1914) — it belongs to the stereo path, not this one.

Then: update the stale comment at `processor.py` ~586 that references the split, and
grep the frontend for handling of `lead`/`accompaniment`/`rhythm` stem labels. Those
stems will no longer be produced — make sure nothing renders an empty channel or a
dangling group header.

**Verify:** `grep -rn "melodic\|_get_split_labels\|_ALREADY_SPLIT" app.py processor.py templates/`
returns nothing live. Run a full analysis end to end and confirm stems still appear.

---

## Step 2 — Apply the prepared patch

`git apply riffd-quick-wins.patch`

It applies cleanly to the current tree and both Python files compile after. It makes
ten changes; review the diff rather than trusting this list:

| Where | Change |
|---|---|
| `processor.py` | drop the 128 kbps pre-upload transcode |
| `processor.py` | float16 → float32 accumulators in `_stereo_separate` |
| `processor.py` | round + TPDF dither on 16-bit write (was truncating) |
| `processor.py` | Demucs `output_format` mp3 → flac |
| `processor.py` | `DEMUCS_MODEL` `htdemucs_6stems` → `htdemucs_6s` (wasn't a real model name, so local mode always fell back to 4-stem) |
| `app.py` | pre-bind `_demucs_exc` — `NameError` if Demucs returns empty without raising |
| `app.py` | admin auth fails **closed** when `ADMIN_SECRET` is unset (was: everyone is admin) |
| `app.py` | `hmac.compare_digest` for the admin secret |
| `app.py` | Sentry DSN from env only, no hardcoded fallback |
| `app.py` | `MEMORY_GUARD_MB` 2200 → 1400 (2200 is above the 2048 MB instance ceiling, so the guard could never fire) |
| `decompose.html` | faders open at unity |

**Two follow-ups the patch does not do:**

1. **Rotate the Sentry DSN** — the old one is in git history and in a public repo.
2. **The fader change must be mirrored into `templates/demo.html`**, which has a forked
   copy of the mixer engine. The patch only touches `decompose.html`.

**Note on the FLAC change:** it removes a lossy generation but makes stem downloads
~6× larger. It is only net-positive once Step 3 lands. If you stop after Step 2,
revert the FLAC line.

---

## Step 3 — Parallelize stem downloads

`processor.py` ~1545 downloads six stems and ffmpeg-converts each, strictly
sequential, one nested inside the other. Wrap the download+convert per stem in a
`ThreadPoolExecutor(max_workers=6)` created and joined **inside** the function.

The existing comment says this was made sequential to avoid "cannot schedule new
futures after interpreter shutdown" — that only affects a pool outliving worker
recycling, which a locally-scoped pool doesn't. Keep the per-stem `try/except` so one
failed stem doesn't kill the others.

This is network I/O, so it parallelizes fine on 1 vCPU. **Do not** also thread-pool
the ffmpeg conversions elsewhere in the pipeline — that's CPU-bound and buys nothing
on one core.

---

## Step 4 — Reconcile the duration claims

Four numbers currently disagree about how long analysis takes:

- `README.md` — "approximately 20 seconds"
- `processor.py` `MAX_WAIT` — 420 s base
- `templates/decompose.html` ~568 — "Analysis takes 5–10 minutes"
- `templates/decompose.html` `POLL_TIMEOUT_MS` — 19 minutes

Measure the real p50 and p90 after Steps 1–3, then make all four consistent. Also move
the duration disclosure onto the "Dive In" button — right now it's `rgba(245,245,245,0.35)`
(~3.4:1, below WCAG AA) below the fold, and `#proc-estimate` is `display:none` on the
screen where the user actually commits.

---

## Acceptance criteria

- [ ] A full analysis completes end to end on a 4-minute track.
- [ ] Peak parent RSS during a job is logged (`_log_memory` is already wired) and stays
      under ~850 MB while the Basic Pitch child is alive.
- [ ] Wall-clock time is captured before and after, on the same track.
- [ ] Stems are audibly compared before/after on headphones — specifically listen for
      grit on decay tails and in solo'd stems (that's the float16 artifact), and check
      that guitar/piano no longer sound band-limited.
- [ ] `grep -rn "float16" processor.py` returns nothing.
- [ ] Admin endpoints return 403 with `ADMIN_SECRET` unset.
- [ ] The mixer opens with all faders at unity in **both** `decompose.html` and `demo.html`.

## Out of scope for this task

Don't start these here — they're separate sessions:
- The `_active_processing` counter / `try/finally` restructuring
- The transposition transport bug (needs the mixer engine extracted first)
- Job-state externalization
- Any model swap or hosting migration
