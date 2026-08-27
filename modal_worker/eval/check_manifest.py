"""Tests for the weights manifest — both directions, run locally.

These exercise the real verify_weights() / _block_downloads() code against a
temporary MODEL_DIR, so they need neither a GPU nor a Modal container. What they
do NOT cover is the container-start integration (that verify_weights() is what
runs in @modal.enter before any request) — see eval/BLOCKED.md.

    python modal_worker/eval/check_manifest.py

(Named check_ rather than test_ because the repo's .gitignore drops `test_*.py`,
which would have left this uncommitted and invisible.)
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def build(tmp, contents):
    """Write files and return a manifest describing them truthfully."""
    manifest = {}
    for rel, data in contents.items():
        p = os.path.join(tmp, rel)
        with open(p, "wb") as f:
            f.write(data)
        manifest[rel] = {"bytes": len(data), "sha256": worker._sha256(p)}
    return manifest


def main():
    with tempfile.TemporaryDirectory() as tmp:
        contents = {
            "big.ckpt": b"\xab" * 4096,
            "small.yaml": b"model: test\n",
        }
        manifest = build(tmp, contents)
        open(os.path.join(tmp, "download_checks.json"), "wb").write(b"{}")

        orig_dir, orig_manifest = worker.MODEL_DIR, worker.WEIGHTS_MANIFEST
        worker.MODEL_DIR, worker.WEIGHTS_MANIFEST = tmp, manifest
        try:
            print("== healthy volume ==")
            try:
                worker.verify_weights()
                check("verifies clean", True)
            except RuntimeError as e:
                check("verifies clean", False, str(e))

            print("== truncated file (the reviewer's case) ==")
            with open(os.path.join(tmp, "big.ckpt"), "r+b") as f:
                f.truncate(100)
            try:
                worker.verify_weights()
                check("raises on truncation", False, "no exception")
            except RuntimeError as e:
                msg = str(e)
                check("raises on truncation", True)
                check("names the offending file", "big.ckpt" in msg)
                check("reports the size delta", "-3996 bytes" in msg, msg.splitlines()[1].strip())
                check("names the fix", "populate_models" in msg)
                check("says it will not self-heal", "not self-heal" in msg)

            print("== same size, wrong bytes (hash catches it) ==")
            with open(os.path.join(tmp, "big.ckpt"), "wb") as f:
                f.write(b"\xcd" * 4096)
            try:
                worker.verify_weights()
                check("raises on same-size corruption", False, "no exception")
            except RuntimeError as e:
                check("raises on same-size corruption", True)
                check("reports a sha256 mismatch", "sha256" in str(e))

            print("== missing file ==")
            os.remove(os.path.join(tmp, "big.ckpt"))
            try:
                worker.verify_weights()
                check("raises on missing weight", False, "no exception")
            except RuntimeError as e:
                check("raises on missing weight", True, )
                check("says MISSING", "MISSING" in str(e))

            print("== missing catalogue (presence-only) ==")
            build(tmp, contents)                       # restore weights
            os.remove(os.path.join(tmp, "download_checks.json"))
            try:
                worker.verify_weights()
                check("raises on missing catalogue", False, "no exception")
            except RuntimeError as e:
                check("raises on missing catalogue", "download_checks.json" in str(e))
        finally:
            worker.MODEL_DIR, worker.WEIGHTS_MANIFEST = orig_dir, orig_manifest

    print("== download blocker ==")

    class FakeSep:
        pass

    import types
    fake_mod = types.SimpleNamespace(Separator=FakeSep)
    sys.modules.setdefault("audio_separator", types.ModuleType("audio_separator"))
    sys.modules["audio_separator.separator"] = fake_mod
    worker._block_downloads()
    with tempfile.TemporaryDirectory() as tmp:
        present = os.path.join(tmp, "here.json")
        open(present, "wb").write(b"{}")
        try:
            FakeSep.download_file_if_not_exists(FakeSep(), "http://x/y", present)
            check("no-op when the file already exists", True)
        except Exception as e:
            check("no-op when the file already exists", False, repr(e))
        try:
            FakeSep.download_file_if_not_exists(
                FakeSep(), "http://x/y", os.path.join(tmp, "absent.bin"))
            check("raises when it would actually fetch", False, "no exception")
        except worker._DownloadsBlocked as e:
            check("raises when it would actually fetch", True)
            check("blocker names the fix", "populate_models" in str(e))

    print("\nFAILURES:", fails if fails else "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
