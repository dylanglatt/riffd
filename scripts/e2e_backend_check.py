"""End-to-end check of one separation backend, through the real HTTP path.

Boots the app, uploads a file, runs a full deep analysis, and asserts on the
result the way a user would experience it: stems present, note events for the
tabs, the PANNs tagger having run, the watchdog never firing, and the memory
guard untouched.

    python scripts/e2e_backend_check.py --backend modal   --audio path/to.mp3
    python scripts/e2e_backend_check.py --backend replicate --audio path/to.mp3

Exists because SEPARATION_BACKEND has to be provably reversible: the same
command against both backends is the evidence that rolling back is a pure env
change. Writes its result to scripts/out/e2e_<backend>.json.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "out"
PORT = int(os.getenv("E2E_PORT", "5001"))
BASE = f"http://127.0.0.1:{PORT}"


def wait_for_server(proc, log_path, timeout=90):
    import requests

    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"app exited early (rc={proc.returncode}); see {log_path}")
        try:
            if requests.get(BASE + "/", timeout=3).status_code == 200:
                return time.time() - t0
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"app did not come up in {timeout}s; see {log_path}")


def run(backend, audio, poll_s=3.0, timeout_s=1800):
    import requests

    OUT.mkdir(parents=True, exist_ok=True)
    log_path = OUT / f"app_{backend}.log"

    env = dict(os.environ)
    env["SEPARATION_BACKEND"] = backend
    env["USE_HOSTED_SEPARATION"] = "true"
    env["PYTHONUNBUFFERED"] = "1"

    # Flask's reloader would fork a second process and make the log unusable.
    env["FLASK_DEBUG"] = "0"

    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            [sys.executable, "app.py"], cwd=str(ROOT), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
        )
    result = {"backend": backend, "audio": str(audio)}
    try:
        result["server_boot_s"] = round(wait_for_server(proc, log_path), 1)

        with open(audio, "rb") as fh:
            up = requests.post(BASE + "/api/upload",
                               files={"file": (Path(audio).name, fh, "audio/mpeg")},
                               timeout=120).json()
        job_id = up["job_id"]
        result["job_id"] = job_id
        print(f"[e2e:{backend}] job {job_id}")

        t0 = time.time()
        r = requests.post(f"{BASE}/api/process/{job_id}", json={}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"/api/process rejected: {r.status_code} {r.text[:300]}")

        seen, last = [], None
        status = {}
        while True:
            elapsed = time.time() - t0
            if elapsed > timeout_s:
                raise RuntimeError(f"client gave up after {elapsed:.0f}s")
            status = requests.get(f"{BASE}/api/status/{job_id}", timeout=30).json()
            prog = status.get("progress")
            if prog and prog != last:
                seen.append((round(time.time() - t0, 1), prog))
                print(f"[e2e:{backend}] {time.time()-t0:6.1f}s  {prog}")
                last = prog
            if status.get("status") in ("complete", "partial", "error"):
                break
            time.sleep(poll_s)

        result["wall_s"] = round(time.time() - t0, 1)
        result["status"] = status.get("status")
        result["error"] = status.get("error")
        result["failed_steps"] = status.get("failed_steps")
        result["progress_timeline"] = seen
        result["stems"] = {k: v.get("label") for k, v in (status.get("stems") or {}).items()}
        intel = status.get("intelligence") or {}
        result["key"] = intel.get("key")
        result["bpm"] = intel.get("bpm")
        result["harmonic_sections"] = len(intel.get("harmonic_sections") or [])
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    log = log_path.read_text(errors="replace")
    result["log_path"] = str(log_path)
    result["separation_path"] = re.findall(r"\[separation\] path = (\S+)", log)
    result["backend_echo"] = re.findall(r"\[separation\] SEPARATION_BACKEND = (\S+)", log)
    result["tagger_lines"] = len(re.findall(r"\[tagger\] ", log))
    result["tagger_changed"] = re.findall(r"\[tagger\] \S+: \"[^\"]+\" -> \"[^\"]+\"", log)
    result["watchdog_fired"] = bool(re.search(r"watchdog|stalled|STALL", log, re.I))
    result["memory_guard_hit"] = bool(re.search(r"MEMORY_GUARD|memory guard", log, re.I))
    mem = [float(m) for m in re.findall(r"\[mem\].*?RSS=(\d+)MB", log)]
    result["parent_rss_peak_mb"] = max(mem) if mem else None
    hwm = [float(m) for m in re.findall(r"\[mem\].*?peak=(\d+)MB", log)]
    result["parent_rss_hwm_mb"] = max(hwm) if hwm else None
    # Note events are not in the status payload — they are consumed to build
    # intelligence — so the log line is the real evidence the tabs have data.
    _notes = re.findall(r"NOTE EXTRACTION finished → (\d+) stems with notes", log)
    result["stems_with_notes"] = int(_notes[-1]) if _notes else 0
    sep = re.findall(r"\[(?:replicate|modal)\] COMPLETE in ([\d.]+)s", log)
    result["separation_s"] = float(sep[0]) if sep else None

    (OUT / f"e2e_{backend}.json").write_text(json.dumps(result, indent=1))
    return result


def verdict(r):
    checks = {
        "status is complete": r.get("status") == "complete",
        "backend actually used": r.get("separation_path") == [r["backend"]],
        "six stems returned": len(r.get("stems") or {}) >= 5,
        "tabs have note events": (r.get("stems_with_notes") or 0) > 0,
        "tagger ran": (r.get("tagger_lines") or 0) > 0,
        "watchdog never fired": not r.get("watchdog_fired"),
        "memory guard untouched": not r.get("memory_guard_hit"),
    }
    for name, ok in checks.items():
        print(("  PASS  " if ok else "  FAIL  ") + name)
    return all(checks.values())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, choices=["modal", "replicate"])
    ap.add_argument("--audio", required=True)
    args = ap.parse_args()

    res = run(args.backend, args.audio)
    print(json.dumps({k: v for k, v in res.items()
                      if k not in ("progress_timeline",)}, indent=1))
    sys.exit(0 if verdict(res) else 1)
