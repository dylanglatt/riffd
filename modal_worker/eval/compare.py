"""Compare the Modal cascade against the incumbent Replicate htdemucs_6s.

There is no ground truth here — these are commercial mixes, not MUSDB — so this
computes *proxies*, not SDR, and says so. Three of them:

  energy share      How much of the mix each separator put in each stem. The
                    incumbent's known weakness is emitting a near-silent guitar
                    or piano stem on tracks that plainly contain both, which
                    shows up directly here.

  vocal bleed       Correlation between each instrumental stem and that same
                    separator's own vocals stem. A guitar stem that correlates
                    with the vocals it was separated from still contains them.
                    Self-referential on purpose: it needs no ground truth, and
                    each separator is judged against its own vocal estimate.

  spectral overlap  Cosine similarity of mean magnitude spectra between stem
                    pairs. Catches bleed that waveform correlation misses when
                    the leaked copy is phase-shifted.

Lower is better for the last two. All of it is circumstantial: the report pairs
it with timestamps for a human to listen to, which is the actual test.
"""

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).parent
AUDIO = ROOT / "audio"
STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


def load_stem(d: Path, name: str):
    for ext in (".wav", ".flac"):
        p = d / f"{name}{ext}"
        if p.exists():
            data, sr = sf.read(p, dtype="float32", always_2d=True)
            return data.mean(axis=1).astype(np.float64), sr
    return None, None


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def db(x, ref):
    return 20 * np.log10(max(x, 1e-12) / max(ref, 1e-12))


def corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    da, dbb = np.sqrt((a * a).sum()), np.sqrt((b * b).sum())
    if da < 1e-12 or dbb < 1e-12:
        return 0.0
    return float(abs((a * b).sum() / (da * dbb)))


def mean_spectrum(x, sr, n_fft=4096):
    hop = n_fft // 2
    frames = max(1, (len(x) - n_fft) // hop)
    step = max(1, frames // 400)                    # sample ~400 frames
    win = np.hanning(n_fft)
    acc = np.zeros(n_fft // 2 + 1)
    count = 0
    for i in range(0, frames, step):
        seg = x[i * hop: i * hop + n_fft]
        if len(seg) < n_fft:
            break
        acc += np.abs(np.fft.rfft(seg * win))
        count += 1
    return acc / max(count, 1)


def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def loud_windows(x, sr, k=3, win_s=10.0):
    """Timestamps of the k loudest non-overlapping windows — what to listen to."""
    w = int(win_s * sr)
    if len(x) < w:
        return ["0:00"]
    hop = sr
    e = np.array([rms(x[i:i + w]) for i in range(0, len(x) - w, hop)])
    picked, order = [], np.argsort(e)[::-1]
    for i in order:
        if all(abs(int(i) - j) >= win_s for j in picked):
            picked.append(int(i))
            if len(picked) == k:
                break
    return [f"{p // 60}:{p % 60:02d}" for p in sorted(picked)]


def analyse(slug):
    mix_path = next(AUDIO.glob(f"{slug}.*"))
    mix, sr = sf.read(mix_path, dtype="float32", always_2d=True)
    mix = mix.mean(axis=1).astype(np.float64)
    mix_rms = rms(mix)

    out = {"slug": slug, "duration_s": round(len(mix) / sr, 1), "systems": {}}
    for system, sub in (("incumbent", "incumbent"), ("cascade", "cascade")):
        d = ROOT / "out" / sub / slug
        if not d.exists():
            continue
        stems, srs = {}, None
        for name in STEMS:
            x, s = load_stem(d, name)
            if x is not None:
                stems[name], srs = x, s
        if not stems:
            continue

        spectra = {k: mean_spectrum(v, srs) for k, v in stems.items()}
        rec = {
            "energy_db_rel_mix": {k: round(db(rms(v), mix_rms), 1) for k, v in stems.items()},
            "energy_share_pct": {},
            "vocal_bleed_corr": {},
            "spectral_overlap_with_vocals": {},
            "listen_at": {k: loud_windows(v, srs) for k, v in stems.items()},
        }
        total_e = sum(rms(v) ** 2 for v in stems.values()) or 1e-12
        for k, v in stems.items():
            rec["energy_share_pct"][k] = round(100 * rms(v) ** 2 / total_e, 1)
        if "vocals" in stems:
            for k in stems:
                if k == "vocals":
                    continue
                rec["vocal_bleed_corr"][k] = round(corr(stems[k], stems["vocals"]), 3)
                rec["spectral_overlap_with_vocals"][k] = round(
                    cosine(spectra[k], spectra["vocals"]), 3)

        # Cross-check: do this system's stems sum back to the mix?
        n = min(min(len(v) for v in stems.values()), len(mix))
        total = sum(v[:n] for v in stems.values())
        err = total - mix[:n]
        rec["reconstruction_rms_err_db"] = round(db(rms(err), mix_rms), 1)
        out["systems"][system] = rec

    meta_files = {
        "cascade": ROOT / "out" / "cascade" / slug / "_meta.json",
        "incumbent": ROOT / "out" / "incumbent" / slug / "_timing.json",
    }
    for k, p in meta_files.items():
        if p.exists() and k in out["systems"]:
            out["systems"][k]["timing"] = json.loads(p.read_text())
    return out


def main(slugs):
    results = [analyse(s) for s in slugs]
    (ROOT / "out" / "comparison.json").write_text(json.dumps(results, indent=1))

    for r in results:
        print(f"\n{'='*78}\n{r['slug']}  ({r['duration_s']}s)\n{'='*78}")
        sysnames = [s for s in ("incumbent", "cascade") if s in r["systems"]]
        print(f"{'stem':8s} " + "".join(
            f"{'  '+s+' dB/mix   share  bleed  spec':<34s}" for s in sysnames))
        for stem in STEMS:
            row = f"{stem:8s} "
            for s in sysnames:
                d = r["systems"][s]
                e = d["energy_db_rel_mix"].get(stem)
                sh = d["energy_share_pct"].get(stem)
                bl = d["vocal_bleed_corr"].get(stem, "")
                sp = d["spectral_overlap_with_vocals"].get(stem, "")
                row += (f"  {e:>7} {sh:>7} {bl:>6} {sp:>6}   " if e is not None
                        else " " * 34)
            print(row)
        for s in sysnames:
            print(f"  {s:9s} stems sum to mix: "
                  f"{r['systems'][s]['reconstruction_rms_err_db']} dB")
    print(f"\nwrote {ROOT / 'out' / 'comparison.json'}")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(p.stem for p in AUDIO.iterdir() if p.is_file()))
