# Mixer Panel Polish — One-Run Prompt

You are editing `templates/decompose.html` in a Flask app. The mixer panel (Mix tab) needs a visual overhaul. It currently looks like a debug tool — bare sliders, tiny bordered M/S buttons, oversized white transport controls. Make it look like a premium product.

Do NOT change any JavaScript functionality, audio logic, or variable names. Only change CSS and HTML structure/classes for visual polish.

## 1. Channel Strip Rows

Each stem row (`.stem-row` or equivalent) should feel like a proper channel strip:
- Add a subtle background to alternating rows: `rgba(245,245,245,0.02)` on even rows, transparent on odd
- Add `padding: 12px 16px` and `border-radius: 2px` to each row
- Stem name label: `font-size: 0.8125rem`, `font-weight: 500`, `color: rgba(245,245,245,0.7)`, `min-width: 120px`
- The volume value number (shows "100"): make it `font-size: 0.75rem`, `color: rgba(245,245,245,0.35)`, `font-family: monospace`, `min-width: 32px`, `text-align: right`
- Overall row should use `align-items: center` and `gap: 16px`

## 2. Slider Styling

The range input sliders need to match the design system:
- Track: `height: 2px` (thinner), `background: rgba(245,245,245,0.1)`, no border-radius (sharp edges match the app)
- Filled portion (progress): `background: #D4691F` (burnt orange accent)
- Thumb: `width: 10px`, `height: 10px`, `background: #D4691F`, `border: none`, `border-radius: 50%`
- On hover, thumb grows to 12px
- Remove any default browser slider styling with `-webkit-appearance: none`

Use this CSS pattern for webkit and mozilla:
```css
input[type="range"] { -webkit-appearance:none; background:transparent; width:100%; cursor:pointer; }
input[type="range"]::-webkit-slider-runnable-track { height:2px; background:rgba(245,245,245,0.1); }
input[type="range"]::-webkit-slider-thumb { -webkit-appearance:none; width:10px; height:10px; background:#D4691F; border-radius:50%; margin-top:-4px; transition:transform .15s; }
input[type="range"]::-webkit-slider-thumb:hover { transform:scale(1.3); }
input[type="range"]::-moz-range-track { height:2px; background:rgba(245,245,245,0.1); border:none; }
input[type="range"]::-moz-range-thumb { width:10px; height:10px; background:#D4691F; border:none; border-radius:50%; }
input[type="range"]::-moz-range-progress { background:#D4691F; height:2px; }
```

## 3. Mute/Solo Buttons

The M and S buttons currently look like spreadsheet cells. Redesign:
- Both: `width: 28px`, `height: 28px`, `font-size: 0.6875rem`, `font-weight: 600`, `border: 1px solid rgba(255,255,255,0.08)`, `background: transparent`, `color: rgba(245,245,245,0.35)`, `cursor: pointer`, `transition: all .15s`, `padding: 0`, `text-align: center`, `line-height: 28px`
- Remove border-radius (or keep at 0 — sharp corners match the app)
- **Mute active state**: `background: rgba(245,245,245,0.06)`, `color: rgba(245,245,245,0.15)`, `border-color: rgba(255,255,255,0.04)` — the row should also dim (add `opacity: 0.4` to the whole row when muted)
- **Solo active state**: `background: rgba(212,105,31,0.15)`, `color: #D4691F`, `border-color: rgba(212,105,31,0.3)`
- On hover (inactive): `border-color: rgba(255,255,255,0.15)`, `color: rgba(245,245,245,0.55)`
- Gap between M and S: `4px`

## 4. Transport Controls

The play/stop buttons are oversized white filled boxes. Fix:
- Play button: `width: 40px`, `height: 40px`, `background: transparent`, `border: 1px solid rgba(255,255,255,0.15)`, `color: rgba(245,245,245,0.7)`
- Play hover: `border-color: #D4691F`, `color: #D4691F`
- Play active/playing state: `background: rgba(212,105,31,0.1)`, `border-color: #D4691F`, `color: #D4691F`
- Stop button: same sizing and style but `width: 36px`, `height: 36px`
- The play icon (triangle) and stop icon (square) should be smaller — if they're text characters, reduce font-size. If SVG, scale down.
- Remove any white background fills on these buttons
- Time display (`0:00 / 3:31`): keep as-is, it looks fine

## 5. Loop & Key Controls

The bottom bar with LOOP and KEY controls:
- "LOOP" label: `font-size: 0.6875rem`, `font-weight: 600`, `color: rgba(245,245,245,0.3)`, `letter-spacing: 0.06em`, `text-transform: uppercase`
- Time inputs: `background: rgba(245,245,245,0.04)`, `border: 1px solid rgba(255,255,255,0.08)`, `color: rgba(245,245,245,0.6)`, `font-size: 0.8125rem`, `font-family: monospace`, `padding: 6px 10px`
- "to" text: `color: rgba(245,245,245,0.25)`, `font-size: 0.8125rem`
- Reset button (the circular arrow): `color: rgba(245,245,245,0.3)`, hover `color: #D4691F`
- "KEY" label: same style as LOOP label
- +/- buttons: `width: 28px`, `height: 28px`, `border: 1px solid rgba(255,255,255,0.08)`, `background: transparent`, `color: rgba(245,245,245,0.5)`, hover `border-color: rgba(255,255,255,0.15)`
- Key value (the "0"): `font-family: monospace`, `color: rgba(245,245,245,0.6)`, `min-width: 24px`, `text-align: center`
- Add a subtle top border to separate from stems: `border-top: 1px solid rgba(255,255,255,0.06)`, `padding-top: 20px`, `margin-top: 20px`

## 6. "New Song" Button

- Remove the heavy border/background. Make it: `background: transparent`, `border: 1px solid rgba(255,255,255,0.1)`, `color: rgba(245,245,245,0.5)`, `font-size: 0.875rem`, `padding: 10px 28px`, `cursor: pointer`
- Hover: `border-color: rgba(255,255,255,0.2)`, `color: rgba(245,245,245,0.7)`
- Center it with `margin: 32px auto 0`, `display: block`

## 7. Overall Mix Panel Wrapper

- Add `padding: 24px` to the mix panel wrapper if not already present
- The thin divider lines between transport and stems, and between stems and loop controls, should be `border-color: rgba(255,255,255,0.06)` (very subtle)

---

IMPORTANT:
- Do NOT change the accent color (#D4691F), background (#0B0B0B), text color (#F5F5F5), or font family (Inter)
- Do NOT change any JavaScript audio/playback logic
- Do NOT add border-radius anywhere (the app uses sharp corners throughout)
- Do NOT touch any other tabs or panels — only the Mix panel
- Test that play/pause, mute, solo, volume sliders, loop controls, and key transpose all still work after your CSS changes
