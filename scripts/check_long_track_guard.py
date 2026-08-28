"""Exercise the long-track routing guard with a stubbed duration (item 5).

Runs separate_stems() with both hosted backends replaced by sentinels, so the
only thing under test is WHICH backend the router picks. No audio, no GPU.

    python scripts/check_long_track_guard.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import processor  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def which_backend(duration_min, backend, has_token, limit=None, tmp=Path("/tmp")):
    """Return 'modal' | 'replicate' — whichever separate_stems() routed to."""
    os.environ["USE_HOSTED_SEPARATION"] = "true"
    os.environ["SEPARATION_BACKEND"] = backend
    os.environ["REPLICATE_API_TOKEN"] = "stub-token" if has_token else ""
    if limit is None:
        os.environ.pop("LONG_TRACK_ROUTE_MINUTES", None)
    else:
        os.environ["LONG_TRACK_ROUTE_MINUTES"] = str(limit)

    orig_probe = processor._probe_duration_s
    orig_modal = processor._separate_stems_modal
    orig_repl = processor._separate_stems_replicate

    processor._probe_duration_s = lambda p: duration_min * 60.0

    def _sentinel(tag):
        def f(*a, **kw):
            raise RuntimeError(f"SENTINEL::{tag}")
        return f

    processor._separate_stems_modal = _sentinel("modal")
    processor._separate_stems_replicate = _sentinel("replicate")
    try:
        processor.separate_stems(str(tmp / "nonexistent.mp3"), "guardtest")
        return "no-exception"
    except Exception as e:
        msg = str(e)
        for tag in ("modal", "replicate"):
            if f"SENTINEL::{tag}" in msg:
                return tag
        return f"unexpected: {msg[:120]}"
    finally:
        processor._probe_duration_s = orig_probe
        processor._separate_stems_modal = orig_modal
        processor._separate_stems_replicate = orig_repl


def main():
    print("== default threshold is 12 minutes ==")
    check("short track on modal stays on modal",
          which_backend(3, "modal", True) == "modal")
    check("11.9 min stays on modal (just under)",
          which_backend(11.9, "modal", True) == "modal")
    check("12.1 min routes to replicate (just over)",
          which_backend(12.1, "modal", True) == "replicate")
    check("20 min routes to replicate",
          which_backend(20, "modal", True) == "replicate")

    print("== no Replicate token: nothing to route to ==")
    check("20 min stays on modal when there is no token",
          which_backend(20, "modal", False) == "modal")

    print("== the guard is modal-only ==")
    check("replicate backend unaffected by duration",
          which_backend(20, "replicate", True) == "replicate")

    print("== threshold is configurable ==")
    check("LONG_TRACK_ROUTE_MINUTES=30 keeps a 20 min track on modal",
          which_backend(20, "modal", True, limit=30) == "modal")
    check("LONG_TRACK_ROUTE_MINUTES=2 routes a 3 min track",
          which_backend(3, "modal", True, limit=2) == "replicate")

    print("\nFAILURES:", fails if fails else "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
