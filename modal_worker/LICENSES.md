# Checkpoint licences — what is actually established

An earlier version of this directory called all three checkpoints "MIT", and
called two of them "solid". That was wrong, and this file replaces it. Every
status below is either backed by a link that says so, or recorded as **not
established**.

Short version: **no checkpoint in this cascade has a verified licence permitting
commercial use, and neither does the `htdemucs_6s` riffd is already running in
production today.** That is a pre-existing condition of the whole open
source-separation ecosystem, not something this worker introduced.

## Code

| component | licence | established? |
|---|---|---|
| `audio-separator` (karaokenerds) | MIT | yes — declared in the repo and on PyPI |
| Demucs *code* (facebookresearch) | MIT | yes — declared in the repo |
| this worker | same as riffd | — |

Code is the easy part. The weights are the problem.

## Weights

### 1. `vocals_mel_band_roformer.ckpt` — vocals stage

- **Source:** [`KimberleyJSN/melbandroformer`](https://huggingface.co/KimberleyJSN/melbandroformer) (`MelBandRoformer.ckpt`); `audio-separator` fetches an identical-weights copy from the UVR mirror.
- **Declared:** `license: mit`, by the author, on their own repo.
- **Status:** ⚠️ **partially established.** The declaration is first-party and that
  is worth something. But the model card is two lines long and discloses **no
  training data**. MelBand RoFormer vocal models in this community are commonly
  trained on MUSDB18(-HQ), whose terms restrict derived models to research use —
  and if that is the case here, the author was not in a position to grant MIT.
  Not disputed, not verified.

### 2. `htdemucs_ft` — drums/bass stage

- **Source:** `dl.fbaipublicfiles.com/demucs/hybrid_transformer/` (Meta, first-party).
- **Code licence:** MIT.
- **Weights licence:** ❌ **not MIT, and explicitly not for commercial use.**

  Alexandre Défossez (`adefossez`), the Demucs author, in
  [facebookresearch/demucs#327](https://github.com/facebookresearch/demucs/issues/327),
  on an issue titled *"License of pre-trained models"* opened by someone asking
  precisely about commercial distribution:

  > "The model weights are not covered by the MIT license, and are provided only
  > for scientific purposes."

  A contributor (`CarlGao4`) adds the reason: the models are trained on MUSDB18,
  "which requires the result model can only be used for research purpose."

- **This applies equally to what riffd runs today.** `processor.py` calls
  `ryan5453/demucs` with `model: "htdemucs_6s"` — the same family of weights,
  under the same statement. Swapping to this worker does not create this
  exposure; it inherits it.

### 3. `BS-Roformer-SW.ckpt` — guitar/piano stage

The only open checkpoint that emits guitar and piano at all, and the one every
guitar/piano result in `eval/REPORT.md` depends on.

**Provenance chain, as far as it can be traced:**

| # | who | what | declared licence |
|---|---|---|---|
| 1 | **jarredou** | original author | — HuggingFace account deleted; nothing survives to check |
| 2 | [`enerjazzer/BS-ROFO-SW-Fixed`](https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed) | *"Edited version of that checkpoint to allow its use with UVR and MSST"* | **`license: unknown`** |
| 3 | [`Blakus/bs_roformer_sw_6stem`](https://huggingface.co/Blakus/bs_roformer_sw_6stem) | *"a restoration and re-upload of the bs_roformer_sw_6stem model created by jarredou, whose HugginFace account no longer exists"* | **`license: mit`** ← what this worker downloads |
| — | [`MrSimmo/BS_Roformer_SW-MLX`](https://huggingface.co/MrSimmo/BS_Roformer_SW-MLX) | independent conversion of the same weights, sourced from #2 | **`license: unknown`**, and states *"Upstream licence: undeclared"* |

- **Status:** ❌ **not established, and the MIT tag is contradicted.** Blakus is
  the only party in the chain claiming MIT. They are downstream of a repo that
  says `unknown`, they are a re-uploader rather than the author, and a fourth
  independent redistributor of the same weights explicitly records the upstream
  licence as undeclared. A re-uploader cannot grant a licence they never held.
  There is no surviving first-party statement to check against.

## The practical position

**riffd is currently free — no payments, no subscription, no ads.** Every
constraint above is a *commercial* restriction. "Research / scientific purposes"
is not a clean fit for a free public web app, but it is a materially different
posture from selling the output, and the incumbent already sits in exactly the
same place.

So the accepted position is:

> **Documented, accepted risk while riffd is free.** Not resolved, not ignored.

**Revisit trigger — any monetisation.** Payments, subscriptions, ads, paid tiers,
B2B licensing, or bundling the output into a paid product. On that day this file
gets re-opened *before* launch, not after, and the same question applies to the
incumbent `htdemucs_6s` path, so switching back is not an escape route.

**Alternatives for that day:**

- **ZFTurbo Mega 53-stem** — a first-party release from an author who publishes
  licence terms directly, rather than a chain of re-uploads. The most promising
  route to a checkpoint that is actually clearable.
- **Commercial separation APIs** (LALAL.AI, Audioshake, Moises and similar) —
  licensed for commercial use by contract, at a per-track cost that would need to
  be weighed against the ~$0.036/track this worker measures.
- **Train or commission a replacement** on licence-clean data — most expensive,
  most durable, and the only route that ends the question permanently.

## What was removed

For the record, so the earlier claims are not quietly reinstated:

- ~~"Every checkpoint here is MIT"~~ (`worker.py`) — false for two of three.
- ~~"MIT — **solid** — first-party, permissive, commercial use explicit"~~
  (`README.md`, htdemucs_ft) — the author states the opposite.
- ~~"MIT — **solid** — declared by the author on their own repo"~~ (`README.md`,
  the vocals model) — the declaration is real, the entitlement to make it is not
  verified.
