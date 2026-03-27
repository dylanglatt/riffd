Read this entire prompt before writing any code. Execute every section in order.

You have full access to the Riffd codebase. The app is Flask + Jinja2 + vanilla JS + inline CSS.
An Anthropic API key is in the .env file as ANTHROPIC_API_KEY and also set on Render.

This prompt covers 5 changes. Do all of them in one pass. Do NOT stop between sections.

---

## CHANGE 1: Make the home page public (no login required)

In app.py, line 168, change:

```python
AUTH_PUBLIC_PATHS = ("/login", "/static/")
```

to:

```python
AUTH_PUBLIC_PATHS = ("/login", "/static/", "/")
```

Also make sure the favicon path doesn't 404 for unauthenticated users — add "/favicon.ico" to
the public paths too if it's not already handled.

That's it. Do not change the auth system itself. All other pages still require login.

---

## CHANGE 2: Remove Library and Practice from the navigation

In templates/base.html, lines 125-126, remove these two nav links entirely:

```html
<a href="/library" class="nav-link {% if active_page == 'library' %}active{% endif %}">Library <span class="nav-badge">Coming Soon</span></a>
<a href="/practice" class="nav-link {% if active_page == 'practice' %}active{% endif %}">Practice <span class="nav-badge">Coming Soon</span></a>
```

The routes and template files can stay — just remove them from the visible navigation.
After this, the nav should show only: Decompose, Studio, About.

---

## CHANGE 3: Remove the "Tab" tab from Decompose results

In templates/decompose.html:

A) Remove the Tab button from the results nav bar (around line 349):
Delete this line:
```html
<button class="results-nav-btn" data-panel="tab" onclick="switchPanel('tab',this)">Tab</button>
```

B) Remove the entire Tab panel HTML (around lines 383-403):
Delete from `<div class="results-panel" id="panel-tab">` through its closing `</div>`.

C) Remove the Tab panel CSS (around lines 205-210):
Delete the "Tab panel — Coming Soon" CSS block:
```css
/* ═══ Tab panel — Coming Soon ═══ */
.tab-coming-soon { ... }
.tab-preview-blur { ... }
.tab-coming-overlay { ... }
.tab-coming-label { ... }
.tab-coming-sub { ... }
```

D) Remove any JS references to the tab panel:
- Line ~1341: Remove the comment "// Tab panel is Coming Soon — no dynamic rendering needed"
- Line ~1469: Remove the comment "// Tab panel is Coming Soon — showTab disabled"

After this change, the results tabs should be: Mix, Key, Lyrics (plus the new Insight tab from Change 5).

---

## CHANGE 4: Improve VISUAL readability across the entire app

"Readability" here means physical legibility — font sizes, line-heights, spacing, and
contrast ratios that make text easy to see and scan on screen. Do NOT simplify content
or dumb down language. Keep every word as-is; just make it visually easier to read.
Apply these changes to ALL templates (decompose.html, home.html, learn.html,
about.html, base.html, login.html):

### Typography improvements
- Body text line-height should be at least 1.7 wherever it's below that
- Body text font-size should be at least 0.9375rem (15px) — increase any smaller text
  EXCEPT labels, badges, and meta chips which can stay small
- Letter-spacing on body text: -0.01em (already used in some places, make it consistent)
- Paragraph max-width: cap at 680px for any long-form text blocks so lines aren't too wide

### Spacing improvements
- Section gaps: at least 48px between major sections on all pages
- Padding inside cards/panels: at least 24px
- The results panels in decompose.html (mix, key, lyrics, and the new insight panel)
  should have comfortable padding — at least 24px on all sides

### Lyrics panel readability
In decompose.html, the lyrics panel text is currently rgba(245,245,245,0.55) which is
quite dim. Change lyrics line color to rgba(245,245,245,0.7) for better readability.
Keep the hover state at #F5F5F5.

### Harmonic sections readability
The harmonic section cards (rendered by _renderHarmonicSections in JS) should have
comfortable padding and the chord text should be easily readable.

### Home page
home.html is the first thing anyone sees now (since we're making it public).
Make sure:
- Hero text is large and clear
- Feature descriptions are readable
- The overall page makes you want to try the product
- If there are any "Coming Soon" references to Library or Practice, remove them

### About page
Make sure text blocks have comfortable line-height and aren't too wide.

### Learn/Studio page
Make sure theory content is readable — comfortable font size and line height.

### Login page
Clean and simple. Make sure it looks professional.

IMPORTANT: Do not change the color palette, the accent color, the background colors,
the font family, or the overall design language. Only improve spacing, sizing, and
line-height for readability. The dark theme stays exactly as is.

---

## CHANGE 5: Add LLM-powered "Insight" tab to Decompose results

This is the biggest change. Follow each step carefully.

### Step 1: Add anthropic to requirements.txt

Add this line to requirements.txt:
```
anthropic>=0.40.0
```

### Step 2: Create insight.py

Create a new file `insight.py` in the project root with this exact structure:

```python
"""
insight.py
LLM-powered song analysis using Claude Haiku.
Generates a plain-language musical breakdown from analysis data.
"""

import os
import time

def generate_insight(song_name, artist, intelligence, lyrics=None, tags=None):
    """
    Generate a musical insight summary using Claude Haiku.

    Args:
        song_name: Track name
        artist: Artist name
        intelligence: Dict with key, bpm, harmonic_sections, progression, etc.
        lyrics: Raw lyrics text (first 20 lines used)
        tags: List of genre/style tags

    Returns:
        str: Generated insight text, or None on failure
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[insight] no ANTHROPIC_API_KEY set — skipping")
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
    except Exception as e:
        print(f"[insight] failed to init client: {e}")
        return None

    # Build context from analysis data
    key = intelligence.get("key", "Unknown")
    bpm = intelligence.get("bpm", 0)
    bpm_conf = intelligence.get("bpm_confidence", 0)
    progression = intelligence.get("progression")
    harmonic_sections = intelligence.get("harmonic_sections", [])

    # Format harmonic sections
    section_text = ""
    if harmonic_sections:
        for s in harmonic_sections[:8]:
            label = s.get("section", "Section")
            chords = s.get("chords_display", s.get("chords", ""))
            numerals = s.get("numerals_display", s.get("roman_numerals", ""))
            section_text += f"  {label}: {chords}"
            if numerals:
                section_text += f" ({numerals})"
            section_text += "\n"

    # Truncate lyrics to first 20 lines
    lyrics_snippet = ""
    if lyrics:
        lines = [l for l in lyrics.strip().split("\n") if l.strip()][:20]
        lyrics_snippet = "\n".join(lines)

    genre = ", ".join(tags[:3]) if tags else "Unknown"

    # Build the user message
    user_msg = f"""Song: "{song_name}" by {artist}
Key: {key}
BPM: {round(bpm) if bpm else 'Unknown'} {"(confident)" if bpm_conf >= 0.4 else "(estimated)" if bpm else ""}
Genre/Tags: {genre}
"""
    if progression:
        user_msg += f"Main progression: {progression}\n"
    if section_text:
        user_msg += f"\nHarmonic structure by section:\n{section_text}"
    if lyrics_snippet:
        user_msg += f"\nLyrics (excerpt):\n{lyrics_snippet}\n"

    user_msg += "\nWrite a musical analysis of this song."

    system_prompt = """You are a music analyst writing for musicians and curious listeners.
Be specific and insightful — reference the actual chords, key, tempo, and structure from the data provided.
Explain what makes this song work musically. Talk about the harmony, the feel, the rhythm, and how the parts fit together.
If the chord progression is a well-known pattern, name it and mention other famous songs that use it.
If the key has a particular character or is common in the genre, mention that.
Make it feel like a smart friend explaining the song over coffee — not a textbook, not a blog post.
Write 2-3 short paragraphs. No bullet points. No headers. No bold text. No markdown formatting.
Keep it under 200 words. Every sentence should say something specific and interesting."""

    try:
        t0 = time.time()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text.strip()
        elapsed = time.time() - t0
        print(f"[insight] generated in {elapsed:.1f}s ({len(text)} chars)")
        return text
    except Exception as e:
        print(f"[insight] LLM call failed: {e}")
        return None
```

### Step 3: Wire insight into the instant analysis path

In app.py, in the `_process_instant` function:

A) Add import at the top of the function (inside the function, lazy import style):
```python
from insight import generate_insight
```

B) After the tags are fetched (around line 565, after the tags try/except block),
add the insight call:

```python
# LLM Insight (non-blocking — failure is fine)
insight_text = None
if artist and track_name:
    try:
        jobs[job_id]["progress"] = "Generating insight..."
        insight_text = generate_insight(
            song_name=track_name,
            artist=artist,
            intelligence=intelligence,
            lyrics=lyrics,
            tags=tags,
        )
    except Exception as e:
        print(f"[job {job_id}] insight failed: {e}")
```

C) Add "insight" to the result dict (around line 570):
Add `"insight": insight_text,` to the result dict.

D) Add "insight" to the cache save dict (around line 590):
Add `"insight": insight_text,` to the save_cached_result call.

### Step 4: Wire insight into the full analysis path

In app.py, find the full processing function (the threaded one that runs Demucs).
After harmonic analysis and lyrics are done, add the same insight call.
Add "insight" to both the result dict and the cache save.
Same pattern as Step 3 — lazy import, try/except, non-blocking.

### Step 5: Add the Insight tab to decompose.html

A) Add the Insight button to the results nav bar. Make it the FIRST button:

Change the results-nav div to:
```html
<div class="results-nav" id="results-nav">
  <button class="results-nav-btn active" data-panel="insight" onclick="switchPanel('insight',this)">Insight</button>
  <button class="results-nav-btn" data-panel="mix" onclick="switchPanel('mix',this)">Mix</button>
  <button class="results-nav-btn" data-panel="key" onclick="switchPanel('key',this)">Key</button>
  <button class="results-nav-btn" data-panel="lyrics" onclick="switchPanel('lyrics',this)">Lyrics</button>
</div>
```

Note: Insight is "active" by default now. Mix is no longer "active" by default.

B) Add the Insight panel HTML. Place it BEFORE the mix panel:

```html
<div class="results-panel active" id="panel-insight">
  <div class="insight-content" id="insight-content">
    <div class="insight-text" id="insight-text"></div>
  </div>
</div>
```

And change the mix panel from `class="results-panel active"` to just `class="results-panel"`
since Insight is now the default.

C) Add CSS for the Insight panel. Add this to the style block:

```css
/* ═══ Insight panel ═══ */
.insight-content { padding:32px; max-width:680px; }
.insight-text { font-size:0.9375rem; line-height:1.8; color:rgba(245,245,245,0.85); letter-spacing:-0.01em; }
.insight-text p { margin-bottom:16px; }
.insight-text p:last-child { margin-bottom:0; }
.insight-empty { padding:40px 20px; text-align:center; color:rgba(245,245,245,0.4); font-size:0.875rem; }
```

D) In the renderResults function (the full results renderer), add insight rendering.
Find where lyrics are rendered and add BEFORE the switchPanel call:

```javascript
// Insight
const insightEl = document.getElementById("insight-text");
const insightData = data.insight || null;
if (insightData && insightData.trim()) {
  // Convert newlines to paragraphs
  insightEl.innerHTML = insightData.split(/\n\n+/).map(p => `<p>${esc(p.trim())}</p>`).join("");
} else {
  insightEl.innerHTML = '<div class="insight-empty">Insight is being generated and will appear on your next visit.</div>';
}
```

E) In the renderInstantResults function, add the same insight rendering code.

F) Change the default tab. In both renderResults and renderInstantResults, change:
```javascript
switchPanel("mix");
```
to:
```javascript
switchPanel("insight");
```

And change `_defaultTab = "mix"` to `_defaultTab = "insight"` wherever it appears.

G) For cached results that don't have insight yet (old cache entries), the insight
field will be null/undefined. The "will appear on your next visit" message handles this.

---

## CHANGE 6: Update the About page to reflect the Anthropic API integration

In templates/about.html, find the tech stack / technology section.
Add Claude (Anthropic API) to the list of technologies used.
Keep it consistent with how other technologies are listed on that page.
Something like: "Claude API (Anthropic) — AI-powered musical insight and analysis"
or whatever format matches the existing entries.

Do NOT add setup instructions or API key details. Just acknowledge the technology
in the same way Demucs, Basic Pitch, Spotify, Genius, etc. are listed.

---

## WHAT NOT TO CHANGE

- README.md — do not touch it
- The processing pipeline (Demucs, Basic Pitch, stem separation)
- Audio acquisition / download logic
- Authentication system (beyond adding "/" to public paths)
- The history.py backend caching logic
- The Tonality Map feature

---

## VERIFICATION CHECKLIST

After making all changes, verify:

1. Home page (/) loads WITHOUT requiring login
2. Login is still required for /decompose, /learn, /about
3. Nav shows only: Decompose, Studio, About (no Library, no Practice)
4. Results tabs show: Insight, Mix, Key, Lyrics (no Tab)
5. Insight tab is the default active tab when results load
6. insight.py exists and imports cleanly: `python -c "from insight import generate_insight; print('OK')"`
7. "anthropic" is in requirements.txt
8. Text across all pages is readable — line-height >= 1.7, font-size >= 15px for body text
9. No existing functionality breaks
10. App starts without errors: `python -c "from app import app; print('OK')"`

---

## REPORT

After completing all changes, list:
1. Every file you modified or created
2. What each change does
3. Confirm all 10 verification items pass
