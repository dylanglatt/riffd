"""
cache_version.py — Single source of truth for the analysis cache version.

BUMP THIS when you change:
  - The shape of any field in the analysis result (intelligence, stems, lyrics, etc.)
  - The frontend UI in a way that requires new/renamed fields from the backend
  - The processing pipeline in a way that changes what gets stored (e.g. new stem types)
  - Any post-processing step that affects what the cache file contains

DO NOT bump for:
  - Pure frontend styling changes that don't need new data
  - Backend refactors that don't change the output shape
  - Bugfixes that don't change the result structure

Incrementing this immediately invalidates all existing caches — users will
re-analyze on next search. Old cache files are NOT deleted, just ignored.
"""

import os

# The separation backend is part of the cache key, not a separate bump.
#
# A flat bump to "v8" would invalidate every cached track the moment this file
# deploys — but deploying it changes nothing, because SEPARATION_BACKEND
# defaults to "replicate" and the output is byte-for-byte the old pipeline.
# Making the backend part of the version instead means the cache turns over
# exactly when the output actually changes: flipping SEPARATION_BACKEND=modal
# invalidates the Replicate-era caches (the separation genuinely differs —
# see modal_worker/eval/REPORT.md), and flipping it back restores them, because
# the version string goes back to what those entries were stored under.
#
# That is also what makes the rollback a pure env change with no deploy: the
# code path AND the cache both revert together.
_BASE_VERSION = "v7"
_BACKEND = os.getenv("SEPARATION_BACKEND", "replicate").strip().lower() or "replicate"
ANALYSIS_VERSION = _BASE_VERSION if _BACKEND != "modal" else f"{_BASE_VERSION}-modal"
