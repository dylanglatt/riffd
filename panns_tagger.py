"""panns_tagger.py — PANNs (AudioSet) audio tagging for stem components.

⚠️  CHILD PROCESS ONLY. torch is imported at module scope on purpose, so that
importing this module anywhere is loud and obvious rather than a lazy surprise.
Importing it in the parent gunicorn worker is the same class of bug as
importing tensorflow was: ~230MB of RSS the worker never gives back, in the one
process CLAUDE.md requires to stay lean. processor.tag_components() spawns a
child that imports this; nothing else may.

Model: PANNs CNN10 (Kong et al., "PANNs: Large-Scale Pretrained Audio Neural
Networks for Audio Pattern Recognition", 2020), trained on AudioSet-2M,
527 sigmoid outputs. The architecture below is a faithful transcription of
Cnn10 from qiuqiangkong/audioset_tagging_cnn (MIT) — the checkpoint's tensor
names must match it exactly, so do not rename layers.

Why CNN10 and not CNN14:

    checkpoint          size      AudioSet mAP
    Cnn14_mAP=0.431     327 MB    0.431
    Cnn10_mAP=0.380      25 MB    0.380
    MobileNetV1          24 MB    0.389

CNN14 is 13x the download and ~6x the parameters for a 0.05 mAP gain on the
full 527-class problem. We use ~40 of those classes and only at family
granularity (is this strings or brass?), which is the part of the label set
CNN10 already does well. On a 2048MB/1vCPU box the 300MB is the whole argument.

Why not the `panns_inference` package: it pulls matplotlib (unused, ~40MB in a
child that must stay small) and hardcodes the CNN14 checkpoint and its download
— and the download-at-first-use is exactly what must not happen during a job.
Weights are fetched once by build.sh instead; see _model_dir().
"""

import csv
import os
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# ── Checkpoint location ──────────────────────────────────────────────────────
# Populated by build.sh at build time, never during a job: a 25MB download on
# the request path would be a multi-second stall the watchdog can't see through,
# and on Render the first job after a deploy would pay it.
CHECKPOINT_NAME = "Cnn10_mAP=0.380.pth"
LABELS_NAME = "class_labels_indices.csv"
CHECKPOINT_SHA256 = "5240bdc47444e331bbeb54fd741d2b3f933a4526e84b0c17bf3593df94b13962"

# CNN10's training-time front end. These are not tunable: the checkpoint's
# conv/mel weights were fit to exactly this analysis, and changing any of them
# silently degrades every score.
SAMPLE_RATE = 32000
WINDOW_SIZE = 1024
HOP_SIZE = 320
MEL_BINS = 64
FMIN = 50
FMAX = 14000
CLASSES_NUM = 527


def _model_dir() -> Path:
    return Path(os.getenv("PANNS_MODEL_DIR", str(Path(__file__).parent / "models" / "panns")))


# ── Front end (transcribed from torchlibrosa, weights taken from the ckpt) ───
#
# torchlibrosa is the package PANNs trains and infers with, and these three
# classes are its Spectrogram / STFT / LogmelFilterBank with one change: the
# DFT and mel matrices are allocated empty and filled by load_state_dict()
# instead of being recomputed by librosa in __init__.
#
# That is not a micro-optimisation. torchlibrosa imports librosa at module
# scope and touches it during construction, which drags in numba/llvmlite —
# measured at +193MB of child RSS, more than the rest of the tagger put
# together, to rebuild matrices the 25MB checkpoint already carries
# (spectrogram_extractor.stft.conv_{real,imag}.weight, logmel_extractor.melW).
# Using the checkpoint's own weights is also strictly more faithful: no
# librosa-version drift in the filterbank.
#
# Parameter names below must keep matching the checkpoint exactly.

class _STFT(nn.Module):
    """librosa-equivalent STFT as a strided Conv1d over the real/imag DFT."""

    def __init__(self):
        super().__init__()
        out_channels = WINDOW_SIZE // 2 + 1
        self.conv_real = nn.Conv1d(1, out_channels, kernel_size=WINDOW_SIZE,
                                   stride=HOP_SIZE, bias=False)
        self.conv_imag = nn.Conv1d(1, out_channels, kernel_size=WINDOW_SIZE,
                                   stride=HOP_SIZE, bias=False)

    def forward(self, x):
        x = x[:, None, :]                                   # (B, 1, samples)
        x = F.pad(x, (WINDOW_SIZE // 2, WINDOW_SIZE // 2), mode="reflect")  # center=True
        real = self.conv_real(x)[:, None, :, :].transpose(2, 3)
        imag = self.conv_imag(x)[:, None, :, :].transpose(2, 3)
        return real, imag                                   # (B, 1, frames, freq)


class _Spectrogram(nn.Module):
    def __init__(self):
        super().__init__()
        self.stft = _STFT()

    def forward(self, x):
        real, imag = self.stft(x)
        return real ** 2 + imag ** 2                        # power=2.0


class _LogmelFilterBank(nn.Module):
    def __init__(self):
        super().__init__()
        self.melW = nn.Parameter(torch.zeros(WINDOW_SIZE // 2 + 1, MEL_BINS),
                                 requires_grad=False)

    def forward(self, x):
        mel = torch.matmul(x, self.melW)
        # power_to_db with PANNs' settings: amin=1e-10, ref=1.0, top_db=None.
        # The ref term is 10*log10(max(amin, 1.0)) = 0, so it drops out; with
        # top_db=None there is no floor clamp either.
        return 10.0 * torch.log10(torch.clamp(mel, min=1e-10))


# ── Model (transcribed from audioset_tagging_cnn/pytorch/models.py) ──────────

class ConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=(3, 3),
                               stride=(1, 1), padding=(1, 1), bias=False)
        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=(3, 3),
                               stride=(1, 1), padding=(1, 1), bias=False)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)

    def forward(self, x, pool_size=(2, 2)):
        x = F.relu_(self.bn1(self.conv1(x)))
        x = F.relu_(self.bn2(self.conv2(x)))
        return F.avg_pool2d(x, kernel_size=pool_size)


class Cnn10(nn.Module):
    """PANNs CNN10. Inference only — the training-time SpecAugment block is
    omitted (it holds no parameters, so the checkpoint loads strictly)."""

    def __init__(self):
        super().__init__()
        self.spectrogram_extractor = _Spectrogram()
        self.logmel_extractor = _LogmelFilterBank()
        self.bn0 = nn.BatchNorm2d(MEL_BINS)
        self.conv_block1 = ConvBlock(1, 64)
        self.conv_block2 = ConvBlock(64, 128)
        self.conv_block3 = ConvBlock(128, 256)
        self.conv_block4 = ConvBlock(256, 512)
        self.fc1 = nn.Linear(512, 512, bias=True)
        self.fc_audioset = nn.Linear(512, CLASSES_NUM, bias=True)

    def forward(self, waveform):
        x = self.spectrogram_extractor(waveform)   # (B, 1, frames, freq)
        x = self.logmel_extractor(x)               # (B, 1, frames, mel)
        x = x.transpose(1, 3)
        x = self.bn0(x)
        x = x.transpose(1, 3)
        x = self.conv_block1(x)
        x = self.conv_block2(x)
        x = self.conv_block3(x)
        x = self.conv_block4(x)
        x = torch.mean(x, dim=3)                   # collapse mel
        x1, _ = torch.max(x, dim=2)
        x2 = torch.mean(x, dim=2)
        x = x1 + x2                                # PANNs' max+avg time pooling
        x = F.relu_(self.fc1(x))
        return torch.sigmoid(self.fc_audioset(x))  # (B, 527) per-class presence


_model = None
_labels = None


def load_labels() -> list:
    """AudioSet display names, indexed to match fc_audioset's 527 outputs."""
    global _labels
    if _labels is not None:
        return _labels
    path = _model_dir() / LABELS_NAME
    if not path.exists():
        raise FileNotFoundError(
            f"AudioSet label index missing: {path}. build.sh fetches it; see "
            "CLAUDE.md 'Component labels come from a PANNs audio tagger'.")
    rows = list(csv.DictReader(path.open()))
    if len(rows) != CLASSES_NUM:
        raise RuntimeError(f"{path} has {len(rows)} classes, expected {CLASSES_NUM}")
    names = [None] * CLASSES_NUM
    for row in rows:
        names[int(row["index"])] = row["display_name"]
    if any(n is None for n in names):
        raise RuntimeError(f"{path} does not cover indices 0..{CLASSES_NUM - 1}")
    _labels = names
    return _labels


def load_model():
    """Load CNN10 once per child process."""
    global _model
    if _model is not None:
        return _model
    path = _model_dir() / CHECKPOINT_NAME
    if not path.exists():
        raise FileNotFoundError(
            f"PANNs checkpoint missing: {path}. It is fetched once at build time "
            "(build.sh), never during a job. Set PANNS_MODEL_DIR or re-run build.sh.")
    # 1 vCPU: torch's default thread pool oversubscribes a single core and the
    # intra-op spin-wait actively costs wall clock here.
    torch.set_num_threads(1)
    model = Cnn10()
    ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
    state = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
    model.load_state_dict(state)   # strict: a silent partial load would score noise
    model.eval()
    _model = model
    return _model


# ── Crop selection ───────────────────────────────────────────────────────────

CROP_SECS = 10.0
N_CROPS = 3


def _resample(x, sr):
    if sr == SAMPLE_RATE:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr), SAMPLE_RATE)
    return resample_poly(x, SAMPLE_RATE // g, int(sr) // g).astype(np.float32)


def read_crops(path, n_crops=N_CROPS, crop_secs=CROP_SECS):
    """Return (n, crop_samples) float32 at SAMPLE_RATE: the loudest windows.

    Reads the RMS envelope in 1 s blocks and then seeks to only the chosen
    windows, so a 5-minute stereo stem costs ~1MB of transient rather than the
    ~100MB a whole-file read would — this runs alongside a parent that has to
    stay under the CLAUDE.md budget.
    """
    import soundfile as sf

    info = sf.info(str(path))
    sr = int(info.samplerate)
    crop_frames = int(crop_secs * sr)
    out_len = int(crop_secs * SAMPLE_RATE)

    def _grab(start_frame, frames):
        block, _ = sf.read(str(path), start=start_frame, frames=frames,
                           dtype="float32", always_2d=True)
        mono = block.mean(axis=1)
        mono = _resample(mono, sr)
        # Peak-normalize: we are asking what the component *is*, not how loud it
        # is, and components in one song span ~30dB. PANNs scores are level
        # sensitive, so an un-normalized quiet component scores near zero on
        # everything — which is precisely the component the energy-threshold
        # rescue needs an opinion about.
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        if peak > 1e-6:
            mono = mono * (0.95 / peak)
        if len(mono) < out_len:
            mono = np.pad(mono, (0, out_len - len(mono)))
        return mono[:out_len].astype(np.float32)

    if info.frames <= crop_frames:
        return np.stack([_grab(0, info.frames)]) if info.frames > 0 else np.zeros((0, out_len), np.float32)

    # 1 s RMS envelope, streamed.
    env = []
    with sf.SoundFile(str(path)) as f:
        while True:
            block = f.read(sr, dtype="float32", always_2d=True)
            if len(block) == 0:
                break
            mono = block.mean(axis=1)
            env.append(float(np.mean(mono * mono)))
    env = np.asarray(env, dtype=np.float64)

    w = max(1, int(round(crop_secs)))
    if len(env) < w:
        return np.stack([_grab(0, crop_frames)])
    window_energy = np.convolve(env, np.ones(w), mode="valid")  # index = start second

    picked = []
    for i in np.argsort(window_energy)[::-1]:
        i = int(i)
        if all(abs(i - j) >= w for j in picked):   # non-overlapping
            picked.append(i)
            if len(picked) == n_crops:
                break
    picked.sort()
    return np.stack([_grab(i * sr, crop_frames) for i in picked])


# ── Public entry point ───────────────────────────────────────────────────────

def tag_files(items: dict) -> dict:
    """{key: wav_path} -> {key: {audioset_class_name: score}} (all 527 classes).

    Class -> display-label mapping deliberately lives in processor.py, next to
    the other label code; this module only reports what it heard.
    """
    model = load_model()
    names = load_labels()
    results = {}
    for key, path in items.items():
        try:
            crops = read_crops(path)
        except Exception as e:
            print(f"[tagger] {key}: could not read crops ({type(e).__name__}: {e}) — skipping")
            continue
        if crops.shape[0] == 0:
            print(f"[tagger] {key}: empty audio — skipping")
            continue
        # One crop at a time, not one batch of three. conv_block1 runs at
        # (B, 64, 1001, 64) and its activations dominate this model's
        # footprint; measured on 3x10s crops, peak child RSS was 413MB at B=1
        # vs 748MB at B=3, for 0.26s vs 0.32s of wall clock. On 1 vCPU there is
        # no parallelism for a batch to exploit, so batching buys nothing and
        # costs 335MB in a 2048MB worker. See CLAUDE.md "Deploy target".
        with torch.no_grad():
            scores = np.stack([
                model(torch.from_numpy(crops[i:i + 1]))[0].numpy()
                for i in range(crops.shape[0])
            ])                                                # (n_crops, 527)
        # Mean, not max, across crops. The crops are already the component's
        # loudest windows, so the dominant source should appear in all of them;
        # averaging then suppresses a single-crop false positive (a horn stab
        # inside a string pad) without diluting an instrument that is genuinely
        # the component. Max would do the opposite.
        agg = scores.mean(axis=0)
        results[key] = {names[i]: float(agg[i]) for i in range(CLASSES_NUM)}
        top = sorted(results[key].items(), key=lambda kv: kv[1], reverse=True)[:3]
        print(f"[tagger] {key}: crops={crops.shape[0]} top=" +
              ", ".join(f"{n}={s:.2f}" for n, s in top))
    return results
