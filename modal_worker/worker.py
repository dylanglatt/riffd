"""Riffd stem separation on Modal — a cascade of MIT-licensed source separators.

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

Step 4 is what makes the stems sum to the mix *exactly* rather than
approximately: `other` absorbs the arithmetic, so reconstruction error is bounded
by float32 rounding and nothing else. It is also the same contract htdemucs
already gives riffd, so downstream code sees no change.

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
# Every checkpoint here is MIT. That is a hard constraint, not a preference:
# most community RoFormer checkpoints (becruily, Gabox, several unwa) ship with
# no declared license at all — which means all rights reserved, not "free" — and
# at least one sibling repo is explicitly CC-BY-NC-4.0. See README.md.
VOCAL_MODEL = "vocals_mel_band_roformer.ckpt"      # Kimberley Jensen, MIT
DEMUCS_MODEL = "htdemucs_ft.yaml"                  # Meta, MIT
SIX_STEM_MODEL = "BS-Roformer-SW.ckpt"             # jarredou, MIT (see README)

ALL_MODELS = [VOCAL_MODEL, DEMUCS_MODEL, SIX_STEM_MODEL]

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
def download_models():
    """Populate the model Volume. Run once per model change, never per request.

    `modal run modal_worker/worker.py::download_models`

    Downloading inside a request would put a multi-hundred-MB fetch on the
    latency path of whichever user happened to arrive first after a deploy —
    the same reason riffd's own build.sh fetches the PANNs checkpoint at build
    time rather than at first use.
    """
    import glob

    os.makedirs(MODEL_DIR, exist_ok=True)
    for name in ALL_MODELS:
        t0 = time.time()
        print(f"[download] {name} ...")
        _separator(name, output_dir="/tmp")     # load_model() fetches if absent
        print(f"[download] {name} done in {time.time() - t0:.0f}s")
    models_volume.commit()
    cache_volume.commit()

    total = 0
    for path in glob.glob(f"{MODEL_DIR}/**/*", recursive=True):
        if os.path.isfile(path):
            size = os.path.getsize(path)
            total += size
            if size > 1_000_000:
                print(f"   {size/1e6:8.1f} MB  {os.path.relpath(path, MODEL_DIR)}")
    print(f"[download] volume total: {total/1e6:.0f} MB")
    return total


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

        The six stems sum to the decoded input to within float32 rounding;
        `_meta["reconstruction"]` reports the measured error.
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

            out["_meta"] = {
                "sample_rate": sr,
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
