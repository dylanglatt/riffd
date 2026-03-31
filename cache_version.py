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

ANALYSIS_VERSION = "v4"
