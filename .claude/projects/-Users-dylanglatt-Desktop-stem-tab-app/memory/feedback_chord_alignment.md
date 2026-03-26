---
name: Chord sheet must not include lyrics
description: Chords tab displays section-based chord chart only — no lyrics, no copyrighted text, no chord-to-lyric alignment
type: feedback
---

Chords tab must be a pure chord chart. No lyrics at all — not aligned, not loosely grouped, not displayed separately.

**Why:** (1) No beat-level alignment data exists, so guessing looks bad. (2) Avoid copyrighted text output in the chord view. (3) Clean chord charts are more trustworthy and musician-friendly.

**How to apply:** Display chords at section level only, in bar-style layout (e.g. `| G | D | Em | C | x4`). Use section labels from metadata when available, generic labels otherwise. Never render lyric text in the Chords panel.
