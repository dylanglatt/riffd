# modal_worker — GPU stem separation on Modal (Phase A)

A standalone Modal app that turns one audio file into riffd's six stems. It is
**not wired into riffd**: nothing here imports `app.py` or `processor.py`, and
nothing in riffd imports this. Phase A exists to answer one question with
measurements — *is a cascade of specialist separators better than the incumbent
htdemucs_6s-on-Replicate, and at what latency and cost?*

The answer, in short: **better on vocals and drums/bass, decisively better on
guitar/piano, ~2.3x slower, ~1.6x the cost, and blocked on a licensing
question.** Full numbers and the honest read: [`eval/REPORT.md`](eval/REPORT.md).

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

| stage | model | takes | license |
|---|---|---|---|
| 1 | MelBand RoFormer (Kimberley Jensen) | `vocals`, instrumental | MIT |
| 2 | Demucs `htdemucs_ft` (Meta) | `drums`, `bass` | MIT |
| 3 | BS-RoFormer-SW 6-stem (jarredou) | `guitar`, `piano` | MIT (see below) |
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

## Licenses — read this before Phase B

`audio-separator` is MIT. The checkpoints are the problem, and this was the
sharpest constraint on the design.

**Most community RoFormer checkpoints cannot be used here.** becruily's and
Gabox's repos, and several of unwa's, declare **no license at all** — which is
all rights reserved, not "free" — and becruily's `mel-band-roformer-deux` is
explicitly **CC-BY-NC-4.0**. becruily's guitar checkpoint, which the task
suggested, is in that no-license group. They are all excluded on that basis, not
on quality.

What is actually used:

| checkpoint | source | license | confidence |
|---|---|---|---|
| `htdemucs_ft` | `dl.fbaipublicfiles.com` (Meta) | MIT | **solid** — first-party, permissive, commercial use explicit |
| `vocals_mel_band_roformer.ckpt` | `KimberleyJSN/melbandroformer` | MIT | **solid** — declared by the author on their own repo |
| `BS-Roformer-SW.ckpt` | `Blakus/bs_roformer_sw_6stem` | MIT | ⚠️ **unverifiable** |

⚠️ **The BS-RoFormer-SW license cannot be verified, and it is the only
checkpoint in the ecosystem that emits guitar and piano at all.** The original
author (jarredou) deleted their HuggingFace account. The copy in use is a
third-party re-upload whose README says, verbatim: *"This is a restoration and
re-upload of the bs_roformer_sw_6stem model created by jarredou, whose
HugginFace account no longer exists."* The MIT tag is the **re-uploader's**
claim, and a re-uploader cannot grant a license they never held. No primary
source survives to check it against.

So the guitar/piano win this whole cascade is built on rests on a checkpoint
with no verifiable licence. That is a decision for a human, not a code change:

- accept the risk (it is widely redistributed and was released publicly), or
- ship stages 1–2 only and keep htdemucs_6s for guitar/piano — which throws away
  the largest measured quality gain, or
- train or commission a replacement guitar/piano model.

Everything else in the cascade is clean.

## Files

```
worker.py              the Modal app: image, volume, download_models, Cascade
eval/run_incumbent.py  baseline — raw htdemucs_6s via the existing Replicate path
eval/run_cascade.py    runs the deployed cascade; also the Phase B call shape
eval/compare.py        objective proxies + which timestamps to listen to
eval/REPORT.md         the numbers and the honest read
eval/audio/, eval/out/ gitignored: multi-GB, regenerated by the scripts above
```
