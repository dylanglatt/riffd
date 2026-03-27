# Loading / Processing View Polish — Claude Code Prompt

You are editing `templates/decompose.html` in a Flask app. The processing/loading view (the screen users see while a song is being analyzed) needs visual polish. Currently it looks like a placeholder — too much dead space, disconnected elements, and the headline stays oversized during loading.

Only change CSS and minimal HTML structure. Do NOT change any JavaScript logic, audio processing, polling, or timer behavior.

## Current Structure

The page has an `.app-head` div at the top (always visible) containing:
- `.hero-headline` — "Decompose any song." (currently `font-size: 4rem`)
- `.hero-sub` — subtitle text
- `.beta-msg` — "Beta — continuously improving with every analysis."

Below that, when processing starts, `#view-confirm` becomes active containing `.proc-hero` with:
- `.proc-art-wrap` — album art (180x180)
- `.proc-title` — song name
- `.proc-sub` — artist, year
- `.proc-chips` — key/bpm/source chips (shown pre-loading)
- `#hero-actions` — "Dive In" button (hidden during loading)
- `#hero-loading` — waveform animation + status text + timer + cancel link

## Changes

### 1. Collapse the hero headline during processing

When `#view-confirm` is active, the big "Decompose any song." headline is distracting — the focus should be on the song being analyzed. Add CSS to shrink it:

```css
/* When confirm/processing view is active, shrink the page header */
#view-confirm.active ~ .app-head,
.app-head:has(~ #view-confirm.active) { /* neither of these selectors work since app-head comes before view-confirm */ }
```

Since CSS can't target a preceding sibling, use a class-based approach instead. In the existing `showView()` JavaScript function (which handles view switching), add a line that toggles a class on `.app-head`:

Find the `showView` function and add this line inside it:
```javascript
document.querySelector('.app-head').classList.toggle('compact', id === 'confirm' || id === 'results');
```

Then add CSS:
```css
.app-head.compact { padding-top:48px; margin-bottom:32px; }
.app-head.compact .hero-headline { font-size:1.5rem; margin-bottom:8px; }
.app-head.compact .hero-sub { display:none; }
.app-head.compact .beta-msg { display:none; }
```

This makes the headline shrink to a small title when processing or viewing results, and the subtitle/beta message hide entirely.

### 2. Tighten the processing layout

The `.proc-hero` has too much vertical space. Update:

```css
.proc-hero { min-height:40vh; padding:24px 24px 48px; gap:0; }
```

(Down from `min-height:60vh; padding:40px 24px`)

### 3. Reduce gap between album art and song info

Currently `.proc-art-wrap` has `margin-bottom:28px`. Reduce:

```css
.proc-art-wrap { margin-bottom:16px; }
```

### 4. Reduce gap between song info and waveform

Currently `.proc-sub` has `margin-bottom:32px`. Since `.proc-chips` also add spacing, tighten:

```css
.proc-sub { margin-bottom:16px; }
.proc-chips { margin-bottom:24px; }
```

(Down from `margin-bottom:32px` and `margin-bottom:48px`)

### 5. Make the waveform wider and more prominent

```css
.waveform-wrap { max-width:500px; height:56px; }
```

(Up from `max-width:400px; height:64px` — wider but slightly shorter, fills more horizontal space)

### 6. Clean up the "Taking too long?" text

The cancel button is created dynamically in JS with inline styles. Find the line:
```javascript
btn.style.cssText = "color:rgba(245,245,245,0.35);cursor:pointer;font-size:0.8rem;margin-top:24px;transition:color 0.2s;";
```

Change to:
```javascript
btn.style.cssText = "color:rgba(245,245,245,0.3);cursor:pointer;font-size:0.75rem;margin-top:20px;transition:color 0.2s;letter-spacing:0.01em;";
```

And change the text from "Taking too long? Try a different song" to just "Try a different song":
```javascript
btn.textContent = "Try a different song";
```

### 7. Subtle processing status animation

The `.proc-status` text (shows "Separating instruments...", etc.) should have a subtle fade transition between messages. It already has `.fading` class support — make sure the transition is smooth:

```css
.proc-status { margin-top:16px; color:rgba(245,245,245,0.45); font-size:0.8125rem; font-weight:400; min-height:1.4em; transition:opacity .3s; letter-spacing:-0.01em; }
```

(Bumped color slightly from `0.4` to `0.45`, and font-size from `0.875rem` to `0.8125rem` — slightly smaller and more subtle)

### 8. Timer styling

The elapsed timer (`#proc-elapsed`) is created dynamically. Find where it's created and change the color from `rgba(245,245,245,0.25)` to `rgba(245,245,245,0.2)` — more subtle, it shouldn't draw attention.

---

IMPORTANT:
- Do NOT change the accent color (#D4691F), background, or font family
- Do NOT change any JavaScript polling, processing, or audio logic
- Do NOT remove any elements — only restyle them
- The `showView()` function modification (adding `.compact` class toggle) is the ONLY JS change allowed
- Test that the loading state still works: search a song, click "Dive In", verify the waveform animates and status text rotates
