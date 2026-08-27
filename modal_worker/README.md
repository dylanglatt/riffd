# modal_worker — GPU stem separation on Modal (Phase A)

A standalone Modal app that turns one audio file into riffd's six stems. It is
**not wired into riffd**: nothing here imports `app.py` or `processor.py`, and
nothing in riffd imports this. Phase A exists to answer one question with
measurements — *is a cascade of specialist separators better than the incumbent
htdemucs_6s-on-Replicate, and at what latency and cost?*

The answer, in short: **the owner's listening test confirms it is clearly better
on piano and vocals; it is ~2.3-2.8x slower; and its checkpoint licences are
unresolved (as are the incumbent's).** Cost is ~$0.036/track measured; the
incumbent's could not be priced, so no cost ratio is claimed. Full numbers:
[`eval/REPORT.md`](eval/REPORT.md), licences: [`LICENSES.md`](LICENSES.md).

## Output contract

```python
{
  "vocals": b"...FLAC...",   "drums": b"...",  "bass":  b"...",
  "guitar": b"...",          "piano": b"...",  "other": b"...",
  "_meta":  {...timings, reconstruction error, per-stem RMS...},
}
```

Six stems, the same names riffd already uses, FLAC, 44.1 kHz stereo. `other` is
computed as `mix - (vocals + drums + bass + guitar + piano)`, so **the stems sum
to the input exactly** — measured at **-168 dB** RMS error, which is float32
rounding and nothing else. That is the same contract htdemucs gives riffd today,
so Phase B is a swap rather than a rework.

## Deploy

```bash
modal token new                                    # once per machine
modal run    modal_worker/worker.py::download_models   # once per model change
modal deploy modal_worker/worker.py
```

`download_models` populates a Modal Volume (`riffd-sep-models`, 1,949 MB) with
the three checkpoints. Weights are **never** fetched during a request — same
reasoning as riffd's own `build.sh` fetching the PANNs checkpoint at build time
rather than on the first job.

Run one file locally:

```bash
modal run modal_worker/worker.py --path song.mp3
```

Call the deployed app (this is the Phase B shape):

```python
import modal
cascade = modal.Cls.from_name("riffd-separation", "Cascade")()
out = cascade.separate.remote(audio_bytes, "song.mp3")
```

### GPU

`A10G`, chosen by measurement — see the report. Override with `RIFFD_GPU=L4
modal deploy ...`. Cost per track is flat across T4/L4/A10G (price scales with
speed), so the tier is a pure latency choice.

## The cascade

| stage | model | takes | licence |
|---|---|---|---|
| 1 | MelBand RoFormer (Kimberley Jensen) | `vocals`, instrumental | unverified — [LICENSES.md](LICENSES.md) |
| 2 | Demucs `htdemucs_ft` (Meta) | `drums`, `bass` | research-only per the author |
| 3 | BS-RoFormer-SW 6-stem (jarredou) | `guitar`, `piano` | never established |
| 4 | subtraction | `other` | — |

Stages 2 and 3 run on the instrumental from stage 1, not on the raw mix, so
neither has to fight the vocal. Stage 3 is fed the instrumental rather than the
post-drums residual: BS-RoFormer-SW was trained on mixes, and an instrumental is
much closer to that than a residual with drums subtracted out.

### Two library defaults that had to be overridden

Both are in `_separator()`, both measured:

- **`demucs_params shifts=2`** → `0`. `shifts=2` runs the model twice on randomly
  shifted copies and averages; `htdemucs_ft` is *already a bag of four models*,
  so the default is eight full passes over the track. Setting it to 0 cut the
  drums/bass stage from 39.7 s to 26.2 s for a fraction of a dB.
- **`normalization_threshold=0.9`** → `1.0`. The default rescales *every stem
  independently* to 0.9 peak, which both changes levels and destroys the
  sum-to-mix property. At 1.0 it is a no-op (`spec_utils.normalize` only scales
  when peak exceeds the threshold).

`mdxc_params batch_size` is set but has no effect on the RoFormer stages:
audio-separator ignores it for them on purpose
(`mdxc_separator.py:490` — *"for Roformer models, `batch_size` is not utilized
due to negligible performance improvements"*). Those two stages are 68% of the
runtime and run one chunk at a time; changing that means forking the demix loop.

### Signal chain

CLAUDE.md's rules apply and are the reason for the odd-looking float32 WAV
intermediate. audio-separator picks its output subtype from the **input** file's
subtype, so handing it an mp3 makes it write `PCM_16` between stages — two
silent quantisations before anything reaches the caller. The input is therefore
decoded once to 32-bit float WAV, every intermediate stays float32, and the only
quantisation is the final 24-bit FLAC encode.

## Licences — read this before Phase B

Full detail, with the provenance chains and the quotes, is in
[`LICENSES.md`](LICENSES.md). The summary:

| checkpoint | declared | established? |
|---|---|---|
| `htdemucs_ft` (Meta) | code MIT, **weights not** | ❌ author states weights are "provided only for scientific purposes" ([demucs#327](https://github.com/facebookresearch/demucs/issues/327)) |
| `vocals_mel_band_roformer.ckpt` | MIT, first-party | ⚠️ declaration is real; training data undisclosed, so entitlement to grant it is unverified |
| `BS-Roformer-SW.ckpt` | MIT, by a re-uploader | ❌ contradicted — upstream repo says `unknown`, author's account deleted |

**No checkpoint here has a verified licence permitting commercial use — and
neither does the `htdemucs_6s` riffd already runs in production.** This worker
inherits that exposure rather than creating it.

Most other community RoFormer checkpoints (becruily, Gabox, several unwa)
declare no licence at all, and becruily's `mel-band-roformer-deux` is explicitly
CC-BY-NC-4.0. They were excluded on licence, not quality.

**Practical position:** riffd is free today, so these commercial restrictions do
not currently bite. This is a documented, accepted risk with a **revisit trigger
— any monetisation** — at which point `LICENSES.md` names the alternatives
(ZFTurbo's first-party Mega release; commercial APIs). Switching back to the
incumbent is not an escape route, since it carries the same constraint.

## Files

```
worker.py              the Modal app: image, volume, populate_models, Cascade
LICENSES.md            per-checkpoint provenance and what is actually established
eval/run_incumbent.py  baseline — raw htdemucs_6s via the existing Replicate path
eval/run_cascade.py    runs the deployed cascade; also the Phase B call shape
eval/compare.py        objective proxies + which timestamps to listen to
eval/REPORT.md         the numbers and the honest read
eval/audio/, eval/out/ gitignored: multi-GB, regenerated by the scripts above
```
