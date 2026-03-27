# Tonality Map Fixes — Claude Code Prompt

You are editing `templates/_tonality_map.html` and `templates/decompose.html` in a Flask app.

Make these 3 changes:

## 1. Remove the hover tooltip entirely

In `_tonality_map.html`, delete all tooltip-related code:
- The `tooltip` div creation (line ~141-143)
- The `_showTooltip` function (lines ~297-311)
- The `_hideTooltip` function (line ~313)
- The `mouseenter`, `mousemove`, `mouseleave` event listeners that call them (lines ~317-327)

The tooltip is the floating box that appears when you hover over a key segment showing key info (sharps, relative minor, chords, etc). Remove it completely.

## 2. Make the wheel non-interactive in decompose (lock to detected key)

In `templates/decompose.html`, find the `_initDecomposeKeyTab` function (~line 1783). Change it so:
- `interactive: false` (was `true`)
- Remove the `onKeySelect` callback entirely — the wheel should just display the detected key, not respond to clicks
- Keep `initialKey: detectedKey` so the detected key is highlighted
- Still call `renderKeyDetail` for the detected key below the wheel — that panel stays, it just doesn't change on click

The wheel in decompose should be a static visualization showing what key the song is in. No clicking, no hover effects.

NOTE: The Learn page may use this same component with `interactive: true` — that's fine, leave Learn page behavior alone. These changes only affect how decompose.html calls the function.

## 3. Improve wheel label readability

In `_tonality_map.html`, the text labels on the wheel are too small and too dim. Fix:

**Outer ring (major keys):**
- Font size: change from `compact ? "9" : "11"` to `compact ? "11" : "13"` (line ~194)
- Fill color: change from `rgba(245,245,245,0.55)` to `rgba(245,245,245,0.75)` (line ~193)
- Font weight: change from `"500"` to `"600"` (line ~195)

**Inner ring (minor keys):**
- Font size: change from `compact ? "7.5" : "9"` to `compact ? "9" : "11"` (line ~234)
- Fill color: change from `rgba(245,245,245,0.35)` to `rgba(245,245,245,0.55)` (line ~233)
- Font weight: change from `"400"` to `"500"` (line ~235)

**In _updateVisuals**, bump the default (non-highlighted) text fills to match:
- Outer ring default text: `rgba(245,245,245,0.75)` (was 0.55)
- Inner ring default text: `rgba(245,245,245,0.55)` (was 0.35)

Do NOT change the accent color (#D4691F), background, font family, or overall design. Only improve size and contrast of the key labels.

---

That's it — 3 changes, all in `_tonality_map.html` and `decompose.html`. Do not touch any other files.
