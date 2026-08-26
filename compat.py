"""
compat.py — Shared compatibility patches.
"""


def patch_lzma():
    """Stub out _lzma if missing — pandas imports it but we never use lzma compression.

    Only fires on interpreters built without liblzma (a common pyenv-on-macOS
    build). Render has liblzma-dev via the Aptfile, so this is a dev-box path.

    The stub has to satisfy the *stdlib* `lzma` module, not just `import _lzma`:
    librosa -> pooch does a plain `import lzma`, and `lzma.py` does
    `from _lzma import _encode_filter_properties, _decode_filter_properties`.
    Without those two names the stub raised ImportError from inside librosa and
    Basic Pitch inference could not start at all. The constants alone were not
    enough.

    Idempotent: calling it twice used to raise `ValueError: _lzma.__spec__ is
    None` from find_spec, because the stub is in sys.modules with no spec.
    """
    import importlib
    import importlib.util
    import sys
    if "_lzma" in sys.modules and getattr(sys.modules["_lzma"], "_riffd_stub", False):
        return
    try:
        if importlib.util.find_spec("_lzma") is not None:
            return
    except ValueError:
        return  # already in sys.modules with a null spec — nothing to do
    import types

    def _unsupported(*_args, **_kwargs):
        raise RuntimeError("lzma compression is unavailable in this interpreter")

    _fake = types.ModuleType("_lzma")
    for attr, val in [("FORMAT_AUTO", 0), ("FORMAT_XZ", 1), ("FORMAT_ALONE", 2),
                      ("FORMAT_RAW", 3), ("CHECK_NONE", 0), ("CHECK_CRC32", 1),
                      ("CHECK_CRC64", 4), ("CHECK_SHA256", 10), ("CHECK_UNKNOWN", 16),
                      ("MEM_ERROR", 5),
                      ("LZMADecompressor", None), ("LZMACompressor", None)]:
        setattr(_fake, attr, val)
    _fake.LZMAError = type("LZMAError", (Exception,), {})
    _fake.is_check_supported = lambda _check: False
    _fake._encode_filter_properties = _unsupported
    _fake._decode_filter_properties = _unsupported
    _fake._riffd_stub = True
    sys.modules["_lzma"] = _fake
    print("[compat] patched missing _lzma module")
