# Blocked: Modal workspace out of spend

Three items in this round need Modal compute and **could not be run**. The
workspace hit its spend limit partway through the session:

```
modal.exception.ConflictError: workspace ac-sN9pp3u1GCtEzHiFOBzCQ8 is disabled
Workspace ac-sN9pp3u1GCtEzHiFOBzCQ8 has exceeded its spend limit
```

(Consistent with the earlier `InvalidError: Please add a payment method to use
L40S GPU functions` — this account is on a capped plan.)

Nothing below is estimated, simulated, or filled in from an earlier run. It is
simply not done.

## Unblock

Raise the workspace spend limit or add a payment method at
`modal.com/settings/<workspace>/billing`, then:

```bash
modal deploy modal_worker/worker.py
python modal_worker/eval/run_cascade.py                       # finding 5
modal run    modal_worker/worker.py::_test_corrupt_weight     # finding 2
python modal_worker/eval/run_cascade.py take_it_easy          # expect loud failure
modal run    modal_worker/worker.py::populate_models          # repair
python modal_worker/eval/run_cascade.py take_it_easy          # expect success
python modal_worker/eval/run_long_track.py                    # finding 6
```

## 1. Manifest — the unhappy path (finding 2)

**Status: logic tested, container-start integration unproven.**

- ✅ `eval/check_manifest.py` — 13 assertions, all passing, against the real
  `verify_weights()` / `_block_downloads()` with a temporary MODEL_DIR:
  truncation, same-size corruption, missing weight, missing catalogue, and both
  branches of the download blocker.
- ✅ Verification demonstrably runs at container start on the real 1,949 MB
  Volume — container logs from before the cutoff show
  `[verify] 9 weight files OK (1949 MB)`.
- ✅ **The blocker caught a real request-time download.** On its first deploy it
  fired on the serving path:

  ```
  worker._DownloadsBlocked: serving path tried to download
  'https://raw.githubusercontent.com/TRvlvr/application_data/main/filelists/download_checks.json'
  -> '/models/download_checks.json'
  ```

  That is exactly the class of fetch finding 2 exists to prevent, and it proves
  the guard is load-bearing rather than decorative. It also exposed a bug in the
  guard itself: `download_file_if_not_exists` is called unconditionally and
  checks existence internally, so refusing every call broke startup for files
  that were present. Fixed to block only calls that would actually fetch.
- ❌ **Not proven:** that the *fixed* worker serves a request from a healthy
  Volume, and that a deliberately truncated checkpoint on the real Volume fails
  at container start. `_test_corrupt_weight()` is written and deployed for this.

## 2. Warm re-measurement (finding 5)

**Status: not re-run. Table relabelled instead.**

The task allowed either. With compute unavailable, `REPORT.md` was relabelled so
every number matches a retained `_meta.json` — Layla is now shown as **cold,
208.4 s**, which is what its artifact says, rather than the warm 186.0 s the
table previously claimed from an overwritten run. Mean cost recomputed from the
retained artifacts: **$0.0358**.

The consequence is recorded in the table: three tracks are warm, Layla is cold,
so Layla's row is not comparable with the other three and is marked as such.

## 3. 20-minute track (finding 6)

**Status: `memory=` sized and set; the run itself is not done.**

`memory=8192` is derived from a first-principles calculation of the resident
float32 arrays at `MAX_TRACK_MINUTES = 20` (see the comment on the `@app.cls`
decorator) — **a calculation, not a measurement.** The purpose of the run was to
replace it with a measurement, and that has not happened.

`eval/run_long_track.py` is written and ready; it records peak container memory,
wall time, and output transfer size into `eval/out/long_track/`. Until it runs,
**the worker is not known to survive a 20-minute input**, which
`MAX_TRACK_MINUTES` permits.
