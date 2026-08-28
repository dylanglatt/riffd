# Phase A evaluation — Modal cascade vs. Replicate htdemucs_6s

**Verdict: the owner's listening test confirms the cascade is clearly better on
piano and on vocals. Energy allocation independently shows the incumbent losing
the piano entirely. It is 2.25–3.20× slower, and its checkpoint licences are
unresolved — as are the incumbent's. Phase B is justified on that evidence; the
licence is a human decision.**

Guitar and `other` have **not** been judged by ear yet, and no objective metric
here settles them.

## Method

Four real commercial mixes, fetched through riffd's own `downloader.py` — the
same yt-dlp path every riffd track already uses, so the input is exactly the
generation production sees (44.1 kHz stereo, VBR mp3 ~208–247 kbps).

| track | length | why |
|---|---|---|
| Derek and the Dominos — *Layla* | 7:04 | the guitar-and-piano-heavy case; layered guitars + a two-minute piano coda |
| ELO — *Livin' Thing* | 3:33 | strings + guitar + piano; the track behind riffd's Horns-vs-Strings labelling bug |
| Eagles — *Take It Easy* | 3:32 | acoustic + electric guitars, close harmony, no piano |
| Daft Punk — *One More Time* | 5:20 | electronic; a negative control — should contain no real guitar or piano |

Both systems got the identical file. The incumbent was run through
`processor._separate_stems_replicate()` — the live production function,
unmodified — and stopped at the raw 6-stem output. riffd's stereo-refinement and
labelling stages were deliberately not applied, since they would apply equally to
either separator.

There is **no ground truth** here (these are commercial mixes, not MUSDB), so
everything below is a proxy, not SDR. Proxies are stated as such and paired with
timestamps for a human to actually listen to.

## Latency

Every row below is reproducible from a retained artifact:
`out/cascade/<slug>/_meta.json` and `out/incumbent/<slug>/_timing.json`. The
`container` column is reported by the worker itself, not inferred. **All four
cascade rows are now genuine warm runs**, re-measured after the earlier table
was found to be quoting a warm time for a run whose artifact said cold.

| track | length | incumbent wall | cascade wall | cascade GPU | container | ratio |
|---|---|---|---:|---:|---|---:|
| Livin' Thing | 3:33 | 44.0 s | 99.0 s | 84.1 s | warm | 2.25× |
| One More Time | 5:20 | 61.0 s | 147.9 s | 125.3 s | warm | 2.42× |
| Take It Easy | 3:32 | 39.4 s | 106.7 s | 84.1 s | warm | 2.71× |
| Layla | 7:04 | 66.2 s | 211.6 s | 163.2 s | warm | 3.20× |

**Warm end to end, the cascade is 2.25–3.20× slower**, and it is structural
rather than a tuning problem: three models over the whole track where
htdemucs_6s runs one, and `htdemucs_ft` is itself a bag of four. GPU-only the
spread is tighter (1.91–2.47×); the wall figures additionally carry 15–48 s of
upload/download per request, which is why Layla — the largest payload — has the
worst wall ratio. Warm GPU time is **0.390× the track's own duration**.

The previous version of this table listed Layla as *warm, 186.0 s / 158.1 s*
from an ad-hoc run whose artifact was overwritten, then (after the first
correction) as *cold, 208.4 s*. It is now warm and measured: **211.6 s / 163.2 s**.

One caveat still bounds every ratio: the **incumbent's** cold/warm state was
never captured — `run_incumbent.py` does not record it and Replicate does not
report it in the prediction body — so the denominators may mix cold and warm
runs.

### Cold start — the one latency dimension the cascade wins

| | cold-start overhead |
|---|---|
| cascade (Modal, weights on a Volume) | **26.9 s** — container boot + 10.0 s to load 1,949 MB of checkpoints |
| incumbent (Replicate) | up to **50 s** observed — one prediction recorded `predict_time` 15.1 s against `total_time` 65.2 s |

Replicate's own metrics separate compute from boot+queue, and the gap is where
its cold boots hide. Modal's 26.9 s is roughly half the worst gap seen, and well
under the 1–4 min the task cites. Warm-container overhead is 14.4–32.3 s, almost
all of it uploading the mix and downloading six FLACs.

## Cost per track

Modal A10G at **$0.000306/s** (modal.com/pricing), GPU seconds from each
track's retained `_meta.json` (`total_s`), all warm:

| track | GPU s | cost |
|---|---:|---:|
| Livin' Thing | 84.1 | $0.0257 |
| Take It Easy | 84.1 | $0.0257 |
| One More Time | 125.3 | $0.0383 |
| Layla | 163.2 | $0.0499 |

**Mean $0.0349/track.** (Earlier figures: $0.034 from an inconsistent subset,
then $0.0358 from a table with one cold row. This one is four warm runs.)
Memory is billed at whichever is higher, request or usage — see "Memory" below,
where the request is now sized from measurement.

### GPU choice, by measurement

Same 3:33 track, identical config:

| GPU | model load | vocals | drums+bass | guitar+piano | total GPU | cost |
|---|---:|---:|---:|---:|---:|---:|
| T4 | 18.5 s | 63.0 | 42.2 | 79.8 | **192.0 s** | $0.0315 |
| L4 | 12.2 s | 52.7 | 26.2 | 53.5 | **139.2 s** | $0.0309 |
| A10G | 9.7 s | 34.2 | 25.6 | 36.9 | **101.2 s** | $0.0310 |

**Cost is flat across the tier — within 2% — because Modal's price scales with
speed.** So "pick the smallest GPU that fits" is the wrong frame for this
workload: the tier is a pure latency choice and the fastest one wins at the same
price. A10G it is, and Layla (7:04) runs without OOM in its 24 GB.

L40S and A100 would likely continue the trend but are gated behind a payment
method on this account (`InvalidError: Please add a payment method to use L40S
GPU functions`), so A10G is also the ceiling currently available.

## Quality

`energy dB/mix` = stem RMS relative to the mix.

⚠️ **`bleed` is a within-system diagnostic, not a score, and it must not be
compared across separators.** It correlates each stem with *that same system's
own* vocals estimate. If a separator under-extracts vocals, its vocals stem is
weak, and everything correlates less with it — so **missing the vocal scores as
"clean"**. The number tells you where a given system is leaking relative to
itself; it cannot tell you which system leaks less. An earlier version of this
report used it for exactly that, and those verdicts have been withdrawn.

The claims that survive rest on two things only: **energy allocation** (how much
of the mix each system put in each stem, which needs no ground truth and no
cross-system normalisation) and **the owner's controlled listening test**.

### Guitar and piano — the question Phase A exists to answer

**Layla** (piano coda from ~3:10):

| stem | incumbent | cascade |
|---|---|---|
| guitar | −5.9 dB (42.1% of energy), bleed **0.208** | −9.4 dB (19.9%), bleed **0.061** |
| piano | **−21.0 dB (1.3%)** | **−9.4 dB (19.7%)** |

The incumbent effectively **misses Layla's piano**: 1.3% of the separated energy
for a coda that is half the record. Its guitar stem instead holds 42% of all
energy — consistent with it acting as a dumping ground that has absorbed the
piano. The cascade splits the two almost evenly (19.9% / 19.7%).

(Its bleed figure is the highest in the study at 0.208, but per the caveat above
that is a within-system diagnostic and is not offered as a comparison.)

**Livin' Thing**: incumbent piano −33.3 dB (0.1%); cascade −17.2 dB (2.3%) —
about 23× more piano energy recovered.

**One More Time** (the negative control): incumbent guitar 0.3%, piano 0.1%;
cascade guitar 0.0%, piano 0.0%. The cascade correctly finds nothing where there
is nothing; the incumbent hallucinates a little.

**Take It Easy**: guitar 22.0% vs 23.8% — a wash. Piano ~0 for both, correctly.

**Listen to these to confirm** (loudest windows in each piano stem):

- Layla piano — cascade `3:11, 4:22, 5:36` vs incumbent `3:25, 3:43, 4:42`. Both
  find the coda; the incumbent's is 11.6 dB quieter and thinner.
- Livin' Thing piano — both `3:01, 3:12`; compare level and vocal bleed.
- Layla guitar, incumbent `0:12, 2:19, 3:37` — listen for piano and vocal inside
  the guitar stem.

### Vocal bleed — diagnostic only

Recorded because it is useful *within* a system, and withdrawn as a
cross-system comparison. The figures are in `out/comparison.json`.

Read within the incumbent, its guitar stem (0.208 on Layla) leaks far more than
its drums (0.031) or bass (0.008) — which corroborates, from a second angle,
that its guitar stem is where unassigned content ends up.

Read within the cascade, `other` is its leakiest stem (0.225 on Layla, 0.569 on
Take It Easy) and its extracted stems are its cleanest. That is structural
rather than surprising: `other` is defined as the residual, so every error the
five extracted stems make is deposited there by construction.

What cannot be said from these numbers — and was previously said — is that
either system is "cleaner" than the other.

### Stems sum back to the mix

Two different measurements appear below and they are not interchangeable. The
worker measures in stereo against the mix it decoded itself; `compare.py`
measures on a mono downmix against an independently decoded mix, which reads a
few dB differently. Both are given rather than blended:

| | worker (stereo, self-decoded) | compare.py (mono, independent) |
|---|---|---|
| cascade, float32 arithmetic | **−168 to −183 dB** — float32 rounding, nothing else | — |
| cascade, as delivered (24-bit FLAC) | **−133 to −136 dB**; Layla **−84 dB** | −122.8 dB; Layla −85.7 dB |
| incumbent, as delivered | — | **−13.7 to −23.0 dB** |

**Correction.** An earlier version of this report blamed the incumbent's figure
on MP3, citing `output_format: "mp3"` at `processor.py:340`. That was wrong. Line
340 is inside `_warmup_replicate()`, which sends a clip of silence purely to boot
a Replicate container and discards the result. The production call is
`_separate_stems_replicate()` at `processor.py:1779`, and it requests
**`"output_format": "flac"`** — lossless, and has been for some time. The
incumbent's stems are not lossy, and there is nothing to "switch".

The real explanation is structural, and it is not a quality difference at all:

- The cascade's `other` is **defined** as `mix - (the other five)`. Its sum
  closes because it is an identity, not because the separation is good. A
  cascade of pure noise would reconstruct just as exactly.
- Demucs emits six **independent estimates**. Nothing constrains them to sum to
  the input, so whatever energy the model assigns to no stem — or to two — shows
  up here.

So this row measures *how each system defines its stems*, not how well either
separates. It is reported because the sum-to-mix property matters to riffd
downstream (it is what makes unity faders correct), not as evidence for the
cascade.

Layla's delivered figure is −84 dB rather than −134 dB because **31 samples** of
its residual `other` stem exceeded full scale and were clipped by the 24-bit FLAC
encode (0.00017% of 18.7 M samples). Reported rather than hidden: the worker now
measures reconstruction on the *decoded FLACs* it actually returns, not only on
the float arrays, because the second number is the only one that is a promise to
a caller.

## Long track — does it survive `MAX_TRACK_MINUTES`?

riffd permits 20-minute inputs, so the worker has to survive one. Measured on
Rush, *2112* (20.65 min, deliberately just past the cap), artifact in
`out/long_track/_meta.json`:

Run twice — once at a deliberately oversized `memory=24576` to measure safely,
then again at the production `memory=16384` to confirm it survives there:

| | measurement run | production-setting run |
|---|---|---|
| memory requested | 24,576 MB | **16,384 MB** |
| wall / GPU | 573.0 s / 492.0 s | 561.0 s / 489.5 s |
| **peak RSS** | **13,537.0 MB** | **13,628.7 MB** |
| headroom | — | **2,755 MB (20.2%)** |
| returned | 423.9 MB FLAC | 423.9 MB FLAC |
| reconstruction | −128.7 dB, 0 clipped | −128.7 dB, 0 clipped |
| stages (v/db/gp) | 105.7 / 130.1 / 217.6 s | 104.8 / 130.3 / 220.1 s |

Both cold containers, input 1,239.0 s (20.65 min) 44.1 kHz stereo. Peak RSS
reproduces to within 0.7%, so 13.6 GB is a stable figure rather than one
sample. Realtime factor 0.395×, consistent with the 0.390× measured warm on
short tracks.

It survives, and scaling is linear rather than cliff-shaped — the realtime
factor barely moves between a 3½-minute track and a 20-minute one.

**The memory request was wrong before this ran.** It had been calculated at
8192 MB from the resident float32 audio arrays; the truth is 13,537 MB, 1.65×
higher, because the dominant term is the loaded models and the torch/CUDA
runtime rather than the audio. A 3:32 track already peaks near 8.4 GB. The
request is now `memory=16384` — the measured worst case (13,628.7 MB) plus 20.2%.

⚠️ **`peak_rss_mb` / `container_peak_rss_mb` is `ru_maxrss` — a CONTAINER
LIFETIME high-water mark, not a per-song figure. Do not average these across
songs.** The four short-track `_meta.json` files all record an identical
8,375.1 MB because they were served by one warm container; that is the maximum
over all four requests, not any one track's cost. The worker now emits it as
`container_peak_rss_mb` for exactly this reason, keeping `peak_rss_mb` as an
alias so the already-committed artifacts still read.

## Licensing — the actual blocker

Fully covered in [`../LICENSES.md`](../LICENSES.md), which is authoritative —
this is the summary, and where the two have disagreed in the past LICENSES.md
was the correct one.

- **`htdemucs_ft` (Meta): the code is MIT, the WEIGHTS are not.** Alexandre
  Défossez, the Demucs author, in
  [demucs#327](https://github.com/facebookresearch/demucs/issues/327): *"The
  model weights are not covered by the MIT license, and are provided only for
  scientific purposes."* An earlier version of this report called this "MIT with
  solid first-party provenance. No concern" — that was wrong, and it contradicted
  LICENSES.md. **The same statement covers the `htdemucs_6s` riffd runs today.**
- **The MelBand RoFormer vocal model (KimberleyJSN)** declares MIT first-party,
  which is real, but its card discloses no training data — so whether the author
  was entitled to grant MIT is unverified, not "no concern".
- **`BS-Roformer-SW` has an unverifiable licence.** The copy in use is a
  re-upload of a re-upload; the MIT tag is the last re-uploader's claim and is
  contradicted upstream. Details and the full chain in LICENSES.md.
- **It is *not* the only checkpoint that emits guitar and piano.** An earlier
  version of this report said so; that is false. ZFTurbo's **MVSep Mega 53 Stems**
  ([MSST release v1.0.21](https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/tag/v1.0.21))
  emits `guitar`, `acoustic-guitar`, `electric-guitar`, `piano` and
  `digital-piano` among its 53. Its provenance is much better — a first-party
  release from the author of a 1,512-star repo whose *code* is MIT — but the
  release notes still grant no explicit weight licence, and the HF mirror
  declares `license: NONE`. It is the known alternative, not a solved problem,
  and it is 53 single-stem checkpoints rather than one file, so adopting it is a
  redesign of the cascade rather than a swap.
- Most community RoFormer checkpoints (becruily, Gabox, several unwa) declare no
  licence at all, and becruily's `mel-band-roformer-deux` is explicitly
  CC-BY-NC-4.0. They were excluded on licence, not on quality — including
  becruily's guitar model, which the task suggested.

**Every measured guitar/piano win in this report rests on that one
unverifiable checkpoint.** That is a decision for a human.

## Honest read, per stem

Two sources of evidence, both stated: **L** = the owner's controlled listening
test, **E** = energy allocation. The bleed metric is deliberately absent.

| stem | verdict | basis |
|---|---|---|
| vocals | **cascade clearly better** | L |
| piano | **cascade decisively better.** The incumbent misses it: 1.3% vs 19.7% of separated energy on Layla, 0.1% vs 2.3% on Livin' Thing | L + E |
| drums | **no verdict.** Energy shares within ~1.5 pts; not judged by ear | E only, inconclusive |
| bass | **no verdict.** Energy shares within ~5 pts; not judged by ear | E only, inconclusive |
| guitar | **not yet judged.** Energy allocation differs sharply (42.1% vs 19.9% on Layla) but that is a *reallocation*, not a quality ordering — the incumbent's share is inflated by the piano it absorbed. Needs a listening pass | E, ambiguous |
| other | **not yet judged.** The cascade's residual necessarily carries the cascade's errors; whether that is audibly worse is unknown | — |

On the electronic negative control both systems correctly find ~no guitar and no
piano (cascade 0.0%/0.0%, incumbent 0.3%/0.1%).

## Recommendation

Proceed to Phase B **conditional on the licence**, because the guitar/piano gap is
exactly the weakness riffd already works around — CLAUDE.md's own notes describe
htdemucs_6s emitting no guitar/piano stem, and the component tagger exists partly
to relabel what the incumbent gets wrong.

Before Phase B:

1. **Resolve the BS-RoFormer-SW licence.** Everything else is contingent on it.
2. Decide whether 2.25–3.20× latency is acceptable. riffd's measured p50 is ~147 s
   end to end and `MAX_WAIT` bounds one separation attempt at 420 s base, so a
   ~160 s separation fits the existing budget — but it roughly doubles the
   separation share of a job and eats headroom the retry path currently relies
   on (CLAUDE.md, "`MAX_WAIT` is not a duration estimate").
3. If latency matters more than the piano, the RoFormer chunk loop is the
   untapped 68% — batching it needs a fork of audio-separator's `demix`.

## Deferred items, now closed

Three items in this report were deferred in an earlier round because the Modal
workspace hit its spend limit mid-session (`Workspace ... has exceeded its spend
limit`). They were recorded in `eval/BLOCKED.md` rather than estimated. Billing
was restored and all three have been run; BLOCKED.md is deleted.

| item | outcome |
|---|---|
| corrupt-Volume startup test | **passes.** Truncating `htdemucs_ft.yaml` to 40 bytes on the real Volume makes the container fail in `@modal.enter` before any inference, naming the file, the −109-byte delta and the fix. `populate_models` then removed, refetched, verified and committed it, and the worker served again. |
| warm re-measure | **done.** All four tracks re-run warm; the latency and cost tables above are rebuilt from the new artifacts. |
| 20-minute track | **done, and it corrected the memory request** — see "Long track" above. |

One operational finding came out of the corruption test and is worth keeping:
**when `@modal.enter` raises, Modal retries the container rather than failing the
caller fast.** The client hung until killed, and the first successful request
afterwards spent **377.5 s of scheduling backoff** for 84.5 s of GPU. So a
corrupted Volume in production presents as hung requests and a long recovery
tail, not as fast errors. The failure is loud in the logs — which is what the
manifest work set out to achieve — but it is not loud to the caller.

## Reproducing

```bash
modal run    modal_worker/worker.py::populate_models
modal deploy modal_worker/worker.py
python modal_worker/eval/fetch_audio.py      # via riffd's own downloader
python modal_worker/eval/run_incumbent.py    # Replicate baseline
python modal_worker/eval/run_cascade.py      # deployed Modal cascade
python modal_worker/eval/compare.py          # proxies + listening timestamps
```
