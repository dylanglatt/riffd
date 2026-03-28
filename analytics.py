"""
analytics.py — Lightweight event logging for Riffd.
Writes JSON lines to data/analytics.jsonl. No external dependencies.
"""

import json
import time
import threading
from pathlib import Path

_LOG_PATH = Path("data/analytics.jsonl")
_lock = threading.Lock()

def log_event(event_type, properties=None):
    """
    Log an analytics event.

    event_type: string like "song_selected", "stems_requested", "prefetch_hit", etc.
    properties: optional dict of event-specific data
    """
    entry = {
        "ts": time.time(),
        "event": event_type,
    }
    if properties:
        entry["props"] = properties

    try:
        with _lock:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[analytics] write failed: {e}")
