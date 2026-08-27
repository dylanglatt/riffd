"""Riffd stem separation on Modal — a cascade of open source separators.

Phase A: standalone. Nothing in this directory is imported by the riffd app, and
this worker imports nothing from it. The output contract is chosen to make the
Phase B swap a drop-in: the same six stem names riffd already uses.

    audio bytes in  ->  {vocals, drums, bass, guitar, piano, other} FLAC out

Why a cascade rather than one 6-stem model
------------------------------------------
htdemucs_6s (the incumbent) does all six in one pass, and its vocals and its
guitar/piano heads are the same size and vintage. Splitting the job lets each
stage use the best available model for its own stem:

  1. vocals   MelBand RoFormer (Kimberley Jensen) on the full mix.
              The strongest open vocal separator; also yields the instrumental.
  2. drums    Demucs htdemucs_ft on the instrumental. Meta's fine-tuned v4 —
     bass     four dedicated heads, and the drum/bass heads are the part of
              Demucs that RoFormers have not clearly beaten.
  3. guitar   BS-RoFormer-SW on the instrumental. The only open RoFormer-class
     piano    checkpoint that emits guitar and piano stems at all.
  4. other    = mix - (vocals + drums + bass + guitar + piano), by subtraction.

Step 4 makes the sum close in the FLOAT DOMAIN: `other` absorbs the arithmetic,
so error there is bounded by float32 rounding (-168 to -183 dB measured).

What the CALLER gets is weaker, and the difference is real. The stems are
delivered as 24-bit FLAC, so delivery quantises; and integer PCM cannot hold a
sample above full scale, so the residual is clipped where it overshoots. Layla
measured -83.6 dB delivered with 31 samples clipped out of 18.7 M; the other
three tracks measured -133 to -136 dB. So the honest contract is:

    float domain   exact to float32 rounding
    as delivered   ~ -80 dB worst case, with rare residual clipping

-80 dB is about one ten-thousandth of the signal amplitude — far below audible,
and far below the noise floor of the lossy source this pipeline is fed in the
first place. It is stated because it is the number a caller can actually rely
on, not because it is a defect.

Signal chain (CLAUDE.md "Audio signal-chain rules")
---------------------------------------------------
float32 end to end, no lossy step anywhere inside the cascade:

  - the input is decoded once to a 32-bit float WAV. audio-separator picks its
    output subtype from the *input* subtype, so a float input is what makes every
    intermediate float; hand it an mp3 and it silently writes PCM_16 between
    stages, quantising twice before anything reaches the caller.
  - normalization_threshold=1.0 makes its per-stem normalisation a no-op
    (spec_utils.normalize only rescales when peak > threshold). The default 0.9
    rescales every stem independently, which would both change levels and break
    the sum-to-mix property.
  - only the final encode is lossless-compressed (FLAC), once.
"""

import os
import time

import modal

APP_NAME = "riffd-separation"
MODEL_DIR = "/models"          # Modal Volume: checkpoints, downloaded once
CACHE_DIR = "/cache"           # Modal Volume: HF/torch caches
# Fixed, because audio-separator captures output_dir into the model instance at
# load_model() time — reassigning separator.output_dir afterwards is silently
# ignored and the stems land back in the directory it was constructed with.
WORK_DIR = "/tmp/work"

# Pinned. --no-deps is not used here (unlike riffd's basic-pitch install), but the
# version is pinned for the same reason: the cascade depends on this package's
# model catalogue and on write_audio_soundfile() taking its subtype from the
# input file. Both are behaviours, not APIs.
AUDIO_SEPARATOR_VERSION = "0.47.0"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        f"audio-separator[gpu]=={AUDIO_SEPARATOR_VERSION}",
        "soundfile>=0.12",
        "numpy>=2",
    )
    .env({
        "HF_HOME": CACHE_DIR,
        "TORCH_HOME": CACHE_DIR,
        # audio-separator is chatty at INFO; the useful lines are ours.
        "OMP_NUM_THREADS": "4",
    })
)

models_volume = modal.Volume.from_name("riffd-sep-models", create_if_missing=True)
cache_volume = modal.Volume.from_name("riffd-sep-cache", create_if_missing=True)

app = modal.App(APP_NAME)

# ── The cascade lineup ───────────────────────────────────────────────────────
#
# NONE of these has a verified licence permitting commercial use, and neither
# does the htdemucs_6s riffd runs in production today. Read LICENSES.md before
# building on any of it — in particular do not restore the "all MIT" claim that
# used to sit here, which was wrong about two of the three:
#
#   vocals   declared MIT first-party, but training data undisclosed
#   demucs   code is MIT; the AUTHOR states the weights are not, and are
#            "provided only for scientific purposes" (demucs#327)
#   SW       licence never established; the MIT tag is a re-uploader's claim,
#            contradicted by two other redistributors of the same weights
VOCAL_MODEL = "vocals_mel_band_roformer.ckpt"      # see LICENSES.md §1
DEMUCS_MODEL = "htdemucs_ft.yaml"                  # see LICENSES.md §2
SIX_STEM_MODEL = "BS-Roformer-SW.ckpt"             # see LICENSES.md §3

ALL_MODELS = [VOCAL_MODEL, DEMUCS_MODEL, SIX_STEM_MODEL]

# ── Weights manifest ─────────────────────────────────────────────────────────
#
# Every file the cascade loads, with its exact size and SHA-256. Generated by
# inspect_volume() against a known-good Volume; regenerate and paste after any
# lineup change.
#
# Embedded in this module rather than kept as a sibling JSON on purpose: it has
# to travel with the code to the container, and `modal deploy worker.py` mounts
# this file, not the directory around it. Keeping it here removes a way for the
# manifest and the code that enforces it to arrive out of step.
#
# The serving path VERIFIES this and never downloads. See verify_weights().
WEIGHTS_MANIFEST = {
    "04573f0d-f3cf25b2.th": {"bytes": 84141271, "sha256": "f3cf25b222c4eed7cd49dd8b2c9597d50c18bd154090f7b919cfa5f93cf22c49"},
    "92cfc3b6-ef3bcb9c.th": {"bytes": 84141271, "sha256": "ef3bcb9c8b40d14ae5d51b6db2587339cc12c6b77c0be151ce6d69002e087bf2"},
    "d12395a8-e57c48e6.th": {"bytes": 84141271, "sha256": "e57c48e6b0e38af4f7118d7bd08c49f0a0c0edf7d09143bdd902ea0d237303e6"},
    "f7e0c4bc-ba3fe64a.th": {"bytes": 84141271, "sha256": "ba3fe64ae8ef66ac9a4857222ce48efbdc5eb3ad375cb79dd13debee5aaa4066"},
    "htdemucs_ft.yaml": {"bytes": 149, "sha256": "69470b8c1bbd674437b51bc9fb491327a10ab0396b702c93389b9cf750016346"},
    "BS-Roformer-SW.ckpt": {"bytes": 699412152, "sha256": "24e7d35ee9c64415673d3fd33e06a67cac2c103c5df6267ba1576459c775916e"},
    "BS-Roformer-SW.yaml": {"bytes": 4653, "sha256": "b558996f1e25eb48798bd6502505a5de94c4f966d6edfb1a0420f06cc40b501a"},
    "vocals_mel_band_roformer.ckpt": {"bytes": 913106900, "sha256": "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"},
    "vocals_mel_band_roformer.yaml": {"bytes": 944, "sha256": "b958b29c8f7195f0d86bee6759a33980db675c4ecaf2fcaa80fa125828e6cd38"},
}

# audio-separator's model catalogue. Checked for PRESENCE only, deliberately not
# hashed: it is refreshed upstream independently of the weights, so hashing it
# would fail serving for a reason that has nothing to do with the models. It
# must exist, though, or load_model() would fetch it mid-request.
CATALOG_FILES = ("download_checks.json",)

POPULATE_HINT = ("run `modal run modal_worker/worker.py::populate_models` "
                 "to (re)populate the Volume")

# Container memory request, MiB. One constant because it is reported back in
# _meta as well as requested, and the two drifted apart when they were separate
# literals.
#
# MEASURED, not calculated. A real 20.65-minute track (Rush, "2112" — just past
# riffd's MAX_TRACK_MINUTES = 20), run twice, peaked at 13,537.0 MB and
# 13,628.7 MB — reproducible to within 0.7%, ~490 s of GPU each:
#
#     eval/out/long_track/_meta.json
#
# An earlier version of this file requested 8192, derived from a first-principles
# count of the resident float32 audio arrays. That was 1.65x too low, because the
# arrays are not the dominant term — the loaded models plus the torch/CUDA
# runtime are, and those do not shrink for a short track. Do not re-derive this
# number from array sizes; re-measure it.
#
# 16384 is the measured worst case (13,628.7 MB) plus 20.2%, and the second of
# those two runs was made AT 16384 to confirm it survives there. Note Modal bills whichever is
# HIGHER of request or usage, so this is not free for short tracks (~$0.003 of a
# ~$0.026 track); it is bought deliberately, because the request is also a
# scheduling guarantee and under-requesting risks placement on a node without
# the headroom to finish. Passing a tuple (request, limit) would add a hard OOM
# cap; not used here, since a legitimate long track must not be killed.
MEMORY_MB = 16384


def _sha256(path, chunk=1 << 20):
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify_weights(hash_check=True):
    """Raise unless the Volume matches WEIGHTS_MANIFEST exactly.

    Called from @modal.enter, so a bad Volume fails at container start with a
    message naming the file — not thirty seconds into someone's request with a
    shape error from deep inside a model loader.
    """
    t0 = time.time()
    problems = []
    for rel, meta in WEIGHTS_MANIFEST.items():
        path = os.path.join(MODEL_DIR, rel)
        if not os.path.exists(path):
            problems.append(f"{rel}: MISSING")
            continue
        size = os.path.getsize(path)
        if size != meta["bytes"]:
            problems.append(
                f"{rel}: size {size} != manifest {meta['bytes']} "
                f"({size - meta['bytes']:+d} bytes — truncated or replaced)")
            continue
        if hash_check:
            got = _sha256(path)
            if got != meta["sha256"]:
                problems.append(
                    f"{rel}: sha256 {got[:16]}... != manifest {meta['sha256'][:16]}...")
    for rel in CATALOG_FILES:
        if not os.path.exists(os.path.join(MODEL_DIR, rel)):
            problems.append(f"{rel}: MISSING (catalogue; presence-only check)")

    if problems:
        raise RuntimeError(
            "Model Volume failed verification against WEIGHTS_MANIFEST:\n  "
            + "\n  ".join(problems)
            + f"\nVolume={MODEL_DIR!r}. Nothing is downloaded on the serving "
              f"path by design, so this will not self-heal: " + POPULATE_HINT)
    mb = sum(m["bytes"] for m in WEIGHTS_MANIFEST.values()) / 1e6
    dt = time.time() - t0
    # Timed because it is on the cold-start path, which is the one latency axis
    # this worker beats Replicate on — a verification that costs more than it is
    # worth would quietly give that back.
    print(f"[verify] {len(WEIGHTS_MANIFEST)} weight files OK ({mb:.0f} MB) "
          f"in {dt:.1f}s ({mb / max(dt, 1e-6):.0f} MB/s)")


class _DownloadsBlocked(RuntimeError):
    pass


def _block_downloads():
    """Make any attempted fetch inside audio-separator raise instead.

    verify_weights() already proves every file is present, so this should never
    fire — it is here so that "the serving path never downloads" is enforced by
    the code rather than asserted by a comment. A new model filename, a changed
    catalogue entry, or an upstream URL change would otherwise silently turn a
    request into a multi-hundred-MB download.
    """
    from audio_separator.separator import Separator

    def _refuse(self, url, output_path, *a, **kw):
        # The real download_file_if_not_exists() is a no-op when the target is
        # already there, and audio-separator calls it unconditionally — notably
        # for download_checks.json on every load_model(). Refusing those calls
        # outright breaks startup for a file that is present. Block only what
        # would actually put bytes on the wire.
        if os.path.exists(output_path):
            return
        raise _DownloadsBlocked(
            f"serving path tried to download {url!r} -> {output_path!r}. "
            f"Weights must come from the Volume: " + POPULATE_HINT)

    Separator.download_file_if_not_exists = _refuse

STEM_NAMES = ("vocals", "drums", "bass", "guitar", "piano", "other")


def _separator(model_filename, output_dir, mdxc_batch=4, demucs_shifts=0):
    """An audio-separator Separator with riffd's signal-chain settings applied.

    The two defaults that matter for wall clock, both of which the library gets
    wrong for this use:

      demucs shifts=2 (library default) makes Demucs run the model twice on
      randomly shifted copies and average. htdemucs_ft is already a *bag of
      four* models, so shifts=2 means eight full passes over the track for a
      fraction of a dB. shifts=0 is one pass per model.

      mdxc batch_size=None falls back to the model YAML, which is 1 for these
      checkpoints — one chunk at a time on a GPU sized for far more.
    """
    from audio_separator.separator import Separator

    sep = Separator(
        log_level=40,                      # WARNING; our own prints carry the signal
        model_file_dir=MODEL_DIR,
        output_dir=output_dir,
        output_format="WAV",
        # 1.0 turns the built-in per-stem normalisation into a no-op. See module
        # docstring — the 0.9 default would rescale each stem on its own.
        normalization_threshold=1.0,
        amplification_threshold=0.0,
        mdxc_params={"segment_size": 256, "override_model_segment_size": False,
                     "batch_size": mdxc_batch, "overlap": None, "pitch_shift": 0},
        demucs_params={"segment_size": "Default", "shifts": demucs_shifts,
                       "overlap": 0.25, "segments_enabled": True},
    )
    sep.load_model(model_filename=model_filename)
    return sep


@app.function(
    image=image,
    volumes={MODEL_DIR: models_volume, CACHE_DIR: cache_volume},
    timeout=3600,
)
def populate_models():
    """Fetch the checkpoints onto the Volume and verify them. Deploy-time only.

    `modal run modal_worker/worker.py::populate_models`

    This is the ONLY place a download may happen. The serving path verifies and
    loads; it never fetches — a multi-hundred-MB download on the latency path
    would land on whichever user arrived first after a deploy, which is the same
    reason riffd's own build.sh fetches the PANNs checkpoint at build time
    rather than at first use.

    Fetch, then verify against WEIGHTS_MANIFEST before committing, so a
    truncated or substituted download is caught here rather than at first
    inference.
    """
    import glob

    os.makedirs(MODEL_DIR, exist_ok=True)

    # Drop anything that does not match the manifest before fetching.
    # audio-separator only downloads a file when it is ABSENT, so a truncated or
    # substituted checkpoint would otherwise survive re-population untouched and
    # keep failing verification forever.
    for rel, meta in WEIGHTS_MANIFEST.items():
        path = os.path.join(MODEL_DIR, rel)
        if not os.path.exists(path):
            continue
        if os.path.getsize(path) != meta["bytes"] or _sha256(path) != meta["sha256"]:
            print(f"[populate] {rel} does not match the manifest — removing so it refetches")
            os.remove(path)

    for name in ALL_MODELS:
        t0 = time.time()
        print(f"[populate] {name} ...")
        _separator(name, output_dir="/tmp")     # load_model() fetches if absent
        print(f"[populate] {name} done in {time.time() - t0:.0f}s")

    total = 0
    for path in glob.glob(f"{MODEL_DIR}/**/*", recursive=True):
        if os.path.isfile(path):
            size = os.path.getsize(path)
            total += size
            if size > 1_000_000:
                print(f"   {size/1e6:8.1f} MB  {os.path.relpath(path, MODEL_DIR)}")
    print(f"[populate] volume total: {total/1e6:.0f} MB")

    verify_weights()                            # fail before committing a bad Volume
    models_volume.commit()
    cache_volume.commit()
    print("[populate] verified and committed")
    return total


@app.function(
    image=image,
    volumes={MODEL_DIR: models_volume, CACHE_DIR: cache_volume},
    timeout=1800,
)
def inspect_volume():
    """Hash everything on the model Volume and print a WEIGHTS_MANIFEST block.

    Maintenance only — run it after changing the lineup and paste the output
    into WEIGHTS_MANIFEST. `modal run modal_worker/worker.py::inspect_volume`
    """
    import hashlib
    import glob

    entries = {}
    for path in sorted(glob.glob(f"{MODEL_DIR}/**/*", recursive=True)):
        if not os.path.isfile(path):
            continue
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        rel = os.path.relpath(path, MODEL_DIR)
        entries[rel] = {"bytes": os.path.getsize(path), "sha256": h.hexdigest()}

    print("WEIGHTS_MANIFEST = {")
    for rel, meta in entries.items():
        print(f'    "{rel}": {{"bytes": {meta["bytes"]}, '
              f'"sha256": "{meta["sha256"]}"}},')
    print("}")
    return entries


@app.function(
    image=image,
    volumes={MODEL_DIR: models_volume, CACHE_DIR: cache_volume},
    timeout=600,
)
def _test_corrupt_weight(name: str = "htdemucs_ft.yaml", keep: int = 40):
    """TEST FIXTURE — truncate one file on the Volume to prove verification bites.

    Used to check the unhappy half of finding 2: a damaged Volume must fail at
    container start, not mid-request. Repair with populate_models(), which now
    removes manifest mismatches before refetching.

    `modal run modal_worker/worker.py::_test_corrupt_weight`
    """
    path = os.path.join(MODEL_DIR, name)
    before = os.path.getsize(path)
    with open(path, "r+b") as f:
        f.truncate(keep)
    models_volume.commit()
    print(f"[test] truncated {name}: {before} -> {os.path.getsize(path)} bytes")
    return os.path.getsize(path)


@app.cls(
    image=image,
    volumes={MODEL_DIR: models_volume, CACHE_DIR: cache_volume},
    # A10G, chosen by measurement (modal_worker/eval/REPORT.md). On a 3:33 track:
    # T4 192.0s, L4 139.2s, A10G 101.2s of GPU time — but at Modal's per-second
    # rates that is $0.0315 / $0.0309 / $0.0310 respectively. Cost is flat across
    # the tier because price scales with speed, so the tier is a pure latency
    # choice and the fastest one wins. L40S and A100 are faster still but are
    # gated behind a payment method on this account.
    gpu=os.environ.get("RIFFD_GPU", "A10G"),
    # Modal's default memory request is 128 MiB, which is a scheduling
    # guarantee rather than a cap — a single `memory=` value does not OOM-kill,
    # it reserves. Leaving it at the default would have this function scheduled
    # as though it were tiny while it actually peaks above 13 GB. See MEMORY_MB.
    memory=MEMORY_MB,
    # Measured: the 20.65-minute track took 492 s of GPU, 573 s wall. 1800 s is
    # ~3.1x that, which absorbs a slow cold start without letting a wedged
    # container bill for half an hour.
    timeout=1800,
    scaledown_window=240,
)
class Cascade:
    """Holds the three models resident so warm requests pay no load cost.

    mdxc_batch / demucs_shifts are modal.parameter()s so a sweep gets its own
    container pool per setting without a redeploy — the models load in
    @modal.enter(), so these cannot be per-request arguments.
    """

    mdxc_batch: int = modal.parameter(default=4)
    demucs_shifts: int = modal.parameter(default=0)

    @modal.enter()
    def load(self):
        import numpy as np  # noqa: F401  (fail fast if the image is wrong)

        # Verify BEFORE loading and block downloads BEFORE constructing any
        # Separator: together these make "the serving path only ever verifies
        # and loads" a property of the code rather than a convention.
        verify_weights()
        _block_downloads()

        t0 = time.time()
        os.makedirs(WORK_DIR, exist_ok=True)
        opts = dict(mdxc_batch=self.mdxc_batch, demucs_shifts=self.demucs_shifts)
        self.vocals = _separator(VOCAL_MODEL, WORK_DIR, **opts)
        self.demucs = _separator(DEMUCS_MODEL, WORK_DIR, **opts)
        self.sixstem = _separator(SIX_STEM_MODEL, WORK_DIR, **opts)
        self.load_s = time.time() - t0
        # Whether the NEXT separate() call is the first on this container. The
        # caller cannot infer this: model_load_s is reported on every request,
        # and "first call of the script" is not the same thing as "cold" once a
        # container survives between scripts.
        self.first_call = True
        print(f"[cascade] models loaded in {self.load_s:.1f}s "
              f"(mdxc_batch={self.mdxc_batch}, demucs_shifts={self.demucs_shifts})")

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _read(path):
        """Read a WAV as float32 (samples, channels), always 2-D stereo."""
        import numpy as np
        import soundfile as sf

        data, sr = sf.read(path, dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        return data, sr

    @staticmethod
    def _align(*arrays):
        """Trim to the shortest length. Models can differ by a frame at the tail."""
        n = min(a.shape[0] for a in arrays)
        return [a[:n] for a in arrays]

    def _run(self, sep, in_path, tag, wanted):
        """Run one model, return {canonical stem name: float32 array}.

        `wanted` maps the name we want to the set of names the model might use
        for it. The aliasing is not cosmetic: the two-stem vocal RoFormer calls
        its instrumental output "(other)", which collides with the name of
        riffd's residual stem, so the mapping has to be explicit per stage.

        Output files are discovered by globbing WORK_DIR rather than by trusting
        separate()'s return value, which mixes basenames and absolute paths
        depending on the backend.
        """
        import glob

        for stale in glob.glob(f"{WORK_DIR}/*"):
            os.remove(stale)
        t0 = time.time()
        sep.separate(in_path)
        dt = time.time() - t0

        produced = sorted(glob.glob(f"{WORK_DIR}/*.wav"))
        by_name = {}
        for path in produced:
            # audio-separator names files "<input>_(Stem)_<model>.wav"
            base = os.path.basename(path)
            stem = base.split("_(")[-1].split(")_")[0].lower() if "_(" in base else ""
            by_name[stem] = path

        found = {}
        for canonical, aliases in wanted.items():
            for alias in aliases:
                if alias in by_name:
                    found[canonical], _ = self._read(by_name[alias])
                    break
        print(f"[cascade] {tag}: {dt:.1f}s -> {sorted(found)} "
              f"(model emitted {sorted(by_name)})")
        missing = set(wanted) - set(found)
        if missing:
            raise RuntimeError(
                f"{tag} produced no {sorted(missing)}; files were "
                f"{[os.path.basename(p) for p in produced]}")
        return found, dt

    # ── entry point ──────────────────────────────────────────────────────────

    @modal.method()
    def separate(self, audio_bytes: bytes, filename: str = "input") -> dict:
        """bytes in -> {stem: FLAC bytes} plus a `_meta` dict of measurements.

        The six stems sum back to the decoded input: exactly in the float
        domain, and to ~-80 dB worst case once quantised to 24-bit FLAC and
        clipped where the residual overshoots full scale.
        `_meta["reconstruction"]` reports both, measured per request.
        """
        import subprocess
        import tempfile

        import numpy as np
        import soundfile as sf

        t_start = time.time()
        was_cold = self.first_call
        self.first_call = False
        with tempfile.TemporaryDirectory() as td:
            raw = os.path.join(td, "raw_input")
            with open(raw, "wb") as f:
                f.write(audio_bytes)

            # Decode ONCE to 32-bit float 44.1k stereo. Everything downstream
            # inherits this subtype — see the module docstring.
            mix_path = os.path.join(td, "mix.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", raw,
                 "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", mix_path],
                check=True,
            )
            mix, sr = self._read(mix_path)
            os.makedirs(WORK_DIR, exist_ok=True)
            timings = {}

            # 1. vocals + instrumental, from the full mix
            got, timings["vocals"] = self._run(
                self.vocals, mix_path, "vocals",
                {"vocals": ("vocals",), "instrumental": ("instrumental", "other")})
            vocals, instrumental = got["vocals"], got["instrumental"]

            inst_path = os.path.join(td, "instrumental.wav")
            sf.write(inst_path, instrumental, sr, subtype="FLOAT")

            # 2. drums + bass, from the instrumental
            got, timings["drums_bass"] = self._run(
                self.demucs, inst_path, "demucs",
                {"drums": ("drums",), "bass": ("bass",)})
            drums, bass = got["drums"], got["bass"]

            # 3. guitar + piano, from the instrumental. Fed the instrumental
            #    rather than the post-drums residual: BS-Roformer-SW was trained
            #    on mixes, and a residual with drums subtracted is further from
            #    that than the instrumental is.
            got, timings["guitar_piano"] = self._run(
                self.sixstem, inst_path, "sw-6stem",
                {"guitar": ("guitar",), "piano": ("piano",)})
            guitar, piano = got["guitar"], got["piano"]

            # 4. other = whatever is left. This is what makes the sum exact.
            mix, vocals, drums, bass, guitar, piano = self._align(
                mix, vocals, drums, bass, guitar, piano)
            other = mix - (vocals + drums + bass + guitar + piano)

            stems = {"vocals": vocals, "drums": drums, "bass": bass,
                     "guitar": guitar, "piano": piano, "other": other}

            denom = float(np.sqrt(np.mean(mix.astype(np.float64) ** 2))) or 1e-12

            def _err_db(delta):
                e = float(np.sqrt(np.mean(delta.astype(np.float64) ** 2)))
                return {"rms_err": e,
                        "max_abs_err": float(np.abs(delta).max()),
                        "rms_err_db": float(20 * np.log10(max(e, 1e-20) / denom))}

            # Two reconstruction numbers, because they answer different questions.
            # `float32` is the arithmetic: does the cascade's algebra close?
            # `delivered` is what the caller can actually reassemble from the
            # FLACs it receives, after 24-bit quantisation and after clipping any
            # residual sample that overshot full scale. Only the second one is a
            # promise to a caller, and it is the weaker of the two — reporting
            # only the first would overstate the contract by ~50dB.
            recon = {"float32": _err_db(sum(stems.values()) - mix)}

            out = {}
            clipped = {}
            for name, arr in stems.items():
                path = os.path.join(td, f"{name}.flac")
                over = int(np.count_nonzero(np.abs(arr) > 1.0))
                if over:
                    clipped[name] = over
                # FLAC is lossless but integer-PCM, so >0 dBFS cannot be
                # represented and the residual stem occasionally overshoots.
                sf.write(path, np.clip(arr, -1.0, 1.0), sr,
                         format="FLAC", subtype="PCM_24")
                with open(path, "rb") as f:
                    out[name] = f.read()

            decoded = sum(self._read(os.path.join(td, f"{n}.flac"))[0][:mix.shape[0]]
                          for n in stems)
            recon["delivered"] = _err_db(decoded - mix)
            recon["clipped_samples"] = clipped

            import resource
            # Linux: ru_maxrss is KiB. Peak for the whole container process, so
            # it covers audio-separator's buffers as well as ours.
            peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

            out["_meta"] = {
                "sample_rate": sr,
                "peak_rss_mb": round(peak_rss_mb, 1),
                "memory_request_mb": MEMORY_MB,
                "samples": int(mix.shape[0]),
                "duration_s": round(mix.shape[0] / sr, 2),
                "model_load_s": round(self.load_s, 1),
                "container": "cold" if was_cold else "warm",
                "stage_s": {k: round(v, 1) for k, v in timings.items()},
                "total_s": round(time.time() - t_start, 1),
                "reconstruction": recon,
                "gpu": os.environ.get("RIFFD_GPU", "A10G"),
                "mdxc_batch": self.mdxc_batch,
                "demucs_shifts": self.demucs_shifts,
                "energy_rms": {k: float(np.sqrt(np.mean(v.astype(np.float64) ** 2)))
                               for k, v in stems.items()},
            }
            return out


@app.local_entrypoint()
def main(path: str, out_dir: str = "modal_worker/eval/out/cascade",
         mdxc_batch: int = 4, demucs_shifts: int = 0):
    """Separate one local file. `modal run modal_worker/worker.py --path song.mp3`"""
    import json
    import pathlib

    src = pathlib.Path(path)
    dest = pathlib.Path(out_dir) / src.stem
    dest.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    result = Cascade(mdxc_batch=mdxc_batch,
                     demucs_shifts=demucs_shifts).separate.remote(
        src.read_bytes(), src.name)
    wall = time.time() - t0

    meta = result.pop("_meta")
    meta["client_wall_s"] = round(wall, 1)
    for name, data in result.items():
        (dest / f"{name}.flac").write_bytes(data)
    (dest / "_meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    print(f"wrote {len(result)} stems to {dest}")
