# Phase A evaluation — Modal cascade vs. Replicate htdemucs_6s

**Verdict: the cascade wins clearly on guitar/piano and on bleed, loses on
latency and cost, and is blocked on a licensing question that has nothing to do
with quality. Phase B is justified on the evidence — but the licence has to be
resolved by a human first.**

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

| track | length | incumbent wall | cascade warm wall | cascade GPU | ratio |
|---|---|---|---:|---:|---:|
| Layla | 7:04 | 66.2 s | 186.0 s | 158.1 s | 2.81× |
| Livin' Thing | 3:33 | 44.0 s | 99.5 s | 82.9 s | 2.26× |
| One More Time | 5:20 | 61.0 s | 154.2 s | 121.9 s | 2.53× |
| Take It Easy | 3:32 | 39.4 s | 96.6 s | 82.2 s | 2.45× |

**The cascade is ~2.3–2.8× slower end to end, and that is structural, not a
tuning problem.** It runs three models over the whole track where htdemucs_6s
runs one, and one of the three (`htdemucs_ft`) is itself a bag of four models.
Warm, it costs 0.38× the track's own duration in GPU time.

Two things were tuned and measured rather than assumed:

- `demucs shifts=2 → 0` cut the drums/bass stage 39.7 s → 26.2 s. The default
  runs the model twice on shifted copies *and* htdemucs_ft is four models, so it
  was eight passes over the track.
- `mdxc batch_size` does nothing. audio-separator ignores it for RoFormer models
  by design (`mdxc_separator.py:490`: *"for Roformer models, `batch_size` is not
  utilized due to negligible performance improvements"*). Those two stages are
  68% of the runtime and run one chunk at a time. Raising that means forking the
  demix loop — the largest remaining speedup available, and untried here.

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

Modal A10G at **$0.000306/s** (modal.com/pricing), warm, GPU seconds only:

| track | GPU s | cost |
|---|---:|---:|
| Layla | 158.1 | $0.048 |
| Livin' Thing | 82.9 | $0.025 |
| One More Time | 121.9 | $0.037 |
| Take It Easy | 82.2 | $0.025 |

**≈ $0.034/track mean.** CPU and memory add a fraction of a cent.

The incumbent's cost could **not** be pinned down: Replicate's API reports
`predict_time` (15.1–34.9 s across the runs here) but does not expose the
hardware tier for `ryan5453/demucs`, and Replicate's rates span $0.000225/s (T4)
to $0.0014/s (A100-80GB). That puts the incumbent somewhere between **$0.004 and
$0.049** per track — an order of magnitude of uncertainty that straddles the
cascade's $0.034. Stated as a range rather than guessed at.

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

`energy dB/mix` = stem RMS relative to the mix. `bleed` = correlation of the stem
with that system's *own* vocals stem — needs no ground truth, lower is better.

### Guitar and piano — the question Phase A exists to answer

**Layla** (piano coda from ~3:10):

| stem | incumbent | cascade |
|---|---|---|
| guitar | −5.9 dB (42.1% of energy), bleed **0.208** | −9.4 dB (19.9%), bleed **0.061** |
| piano | **−21.0 dB (1.3%)** | **−9.4 dB (19.7%)** |

The incumbent effectively **misses Layla's piano**: 1.3% of the separated energy
for a coda that is half the record. Its guitar stem instead holds 42% of all
energy with the highest vocal bleed in the whole study (0.208) — it is acting as
a dumping ground, with the piano and some vocal inside it. The cascade splits the
two almost evenly (19.9% / 19.7%) and carries a third of the vocal bleed.

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

### Vocal bleed, all tracks

The cascade is cleaner on every *extracted* stem: drums 0.009 vs 0.031, bass
0.000 vs 0.008, guitar 0.061 vs 0.208 (Layla). That is the MelBand RoFormer vocal
model doing its job before anything else sees the audio.

**It is dirtier on `other`** — 0.225 vs 0.032 on Layla, 0.569 vs 0.047 on Take It
Easy. This is structural, not a bug: `other` is defined as the residual, so every
error the five extracted stems make lands in it. The cascade buys clean specific
stems by concentrating the mess in the one stem labelled "everything else".
Whether that is a good trade is a product call — for riffd's mixer it probably is,
since `other` is the stem users are least likely to solo.

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

The incumbent's stems reconstruct badly because `processor.py:340` requests
`output_format: "mp3"` from Replicate — **the incumbent's stems are lossy**, and
lossy per stem, independently. The cascade returns FLAC. That is a real quality
difference on top of separation quality, and it costs the incumbent nothing to
fix (CLAUDE.md already notes the FLAC option and its download-time trade).

Layla's delivered figure is −84 dB rather than −134 dB because **31 samples** of
its residual `other` stem exceeded full scale and were clipped by the 24-bit FLAC
encode (0.00017% of 18.7 M samples). Reported rather than hidden: the worker now
measures reconstruction on the *decoded FLACs* it actually returns, not only on
the float arrays, because the second number is the only one that is a promise to
a caller.

## Licensing — the actual blocker

Fully covered in [`../README.md`](../README.md). The short version:

- `htdemucs_ft` (Meta) and the MelBand RoFormer vocal model (KimberleyJSN) are
  **MIT with solid first-party provenance**. No concern.
- **`BS-Roformer-SW` — the only checkpoint in the ecosystem that emits guitar and
  piano at all — has an unverifiable licence.** Its author (jarredou) deleted
  their HuggingFace account; the copy in use is a third-party re-upload whose
  README says so explicitly, and the MIT tag is the *re-uploader's* claim. A
  re-uploader cannot grant a licence they never held.
- Most community RoFormer checkpoints (becruily, Gabox, several unwa) declare no
  licence at all, and becruily's `mel-band-roformer-deux` is explicitly
  CC-BY-NC-4.0. They were excluded on licence, not on quality — including
  becruily's guitar model, which the task suggested.

**Every measured guitar/piano win in this report rests on that one
unverifiable checkpoint.** That is a decision for a human.

## Honest read, per stem

| stem | verdict |
|---|---|
| vocals | **cascade better.** Lower bleed everywhere downstream; energy share within 1–2 pts. |
| drums | **cascade slightly better.** Bleed 0.009 vs 0.031; otherwise near-identical. |
| bass | **wash.** Both clean, bleed ~0 for both. |
| guitar | **cascade better**, mainly by *not* being a dumping ground — 3× less vocal bleed on Layla. |
| piano | **cascade decisively better.** The incumbent misses it: 1.3% vs 19.7% of energy on Layla, 0.1% vs 2.3% on Livin' Thing. This is the single strongest result here. |
| other | **incumbent better.** The cascade's residual absorbs every upstream error; bleed 0.569 vs 0.047 on Take It Easy. |

## Recommendation

Proceed to Phase B **conditional on the licence**, because the guitar/piano gap is
exactly the weakness riffd already works around — CLAUDE.md's own notes describe
htdemucs_6s emitting no guitar/piano stem, and the component tagger exists partly
to relabel what the incumbent gets wrong.

Before Phase B:

1. **Resolve the BS-RoFormer-SW licence.** Everything else is contingent on it.
2. Decide whether 2.3–2.8× latency is acceptable. riffd's measured p50 is ~147 s
   end to end and `MAX_WAIT` bounds one separation attempt at 420 s base, so a
   ~160 s separation fits the existing budget — but it roughly doubles the
   separation share of a job and eats headroom the retry path currently relies
   on (CLAUDE.md, "`MAX_WAIT` is not a duration estimate").
3. Consider the cheap independent win first: **switching the incumbent to FLAC
   output** removes the lossy-stem problem for one line of config, with no model
   change and no licence question.
4. If latency matters more than the piano, the RoFormer chunk loop is the
   untapped 68% — batching it needs a fork of audio-separator's `demix`.

## Reproducing

```bash
modal run    modal_worker/worker.py::download_models
modal deploy modal_worker/worker.py
python modal_worker/eval/fetch_audio.py      # via riffd's own downloader
python modal_worker/eval/run_incumbent.py    # Replicate baseline
python modal_worker/eval/run_cascade.py      # deployed Modal cascade
python modal_worker/eval/compare.py          # proxies + listening timestamps
```
