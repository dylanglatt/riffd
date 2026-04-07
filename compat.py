"""
compat.py — Shared compatibility patches.
"""


def patch_lzma():
    """Stub out _lzma if missing — pandas imports it but we never use lzma compression."""
    import importlib
    import importlib.util
    import sys
    if importlib.util.find_spec("_lzma") is None:
        import types
        _fake = types.ModuleType("_lzma")
        for attr, val in [("FORMAT_AUTO", 0), ("FORMAT_XZ", 1), ("FORMAT_ALONE", 2),
                          ("FORMAT_RAW", 3), ("CHECK_NONE", 0), ("CHECK_CRC32", 1),
                          ("CHECK_CRC64", 4), ("CHECK_SHA256", 10), ("MEM_ERROR", 5),
                          ("LZMADecompressor", None), ("LZMACompressor", None)]:
            setattr(_fake, attr, val)
        sys.modules["_lzma"] = _fake
        print("[compat] patched missing _lzma module")
