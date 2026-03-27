# Claude Code Prompt: LLM-Powered Natural Language Search for Studio

## Overview

Add an AI-powered "Ask anything" search mode to the Studio (/learn) page. When a user types a natural language question into the existing search bar (e.g., "What chords work well in Bossa Nova?", "Show me dark minor scales for metal", "What's a good jazz turnaround?"), the system detects it's a question (not a keyword filter) and routes it to Claude Haiku, which queries against the loaded theory data and returns relevant results with a short contextual explanation.

This sits **alongside** the existing keyword filter — not replacing it. Short keyword searches ("Cmaj7", "blues", "pentatonic") continue to work exactly as they do now via client-side filtering. Only natural language questions trigger the LLM path.

---

## Architecture

### Detection (client-side)
In `learn.html`, update the search input handler to detect natural language queries vs keyword filters.

**Heuristic — treat as LLM query if ANY of these are true:**
- Input starts with a question word: "what", "which", "how", "why", "show", "give", "find", "suggest", "recommend", "list", "tell", "explain", "can", "is", "are", "do", "does"
- Input contains a "?" character
- Input is longer than 40 characters (likely a sentence, not a keyword)

If none of these match, use the existing client-side keyword filter path unchanged.

### API Endpoint (server-side)
Add a new endpoint in `app.py`:

```
POST /api/theory/ask
Body: { "question": "...", "section": "chords"|"scales"|"progressions"|"keys"|null }
Response: { "answer": "...", "results": ["item_name_1", "item_name_2", ...], "section": "chords" }
```

- `section` is optional. If the user is currently viewing "Chords", pass `"chords"` so the LLM can scope its search. If null, the LLM decides which section(s) are relevant.
- `results` is an array of item names from the theory data that the LLM identified as relevant. The frontend uses these to filter the existing grid (matching by `item.name`).
- `answer` is a 1-3 sentence plain text explanation the LLM provides (displayed above the results grid).

### LLM Call (server-side)
Create a new file `theory_search.py` (mirrors the pattern of `insight.py`):

```python
"""
theory_search.py
LLM-powered natural language search over music theory data.
Uses Claude Haiku for fast, cheap responses.
"""

import json
import os
import time


def ask_theory(question, section=None, theory_data=None):
    """
    Answer a natural language music theory question using Claude Haiku.

    Args:
        question: The user's natural language query
        section: Optional current section ("chords", "scales", "progressions", "keys")
        theory_data: Dict of { section_name: [items...] } — all loaded theory data

    Returns:
        dict | None: { "answer": str, "results": [str], "section": str }
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[theory_search] no ANTHROPIC_API_KEY set — skipping")
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=12.0)
    except Exception as e:
        print(f"[theory_search] failed to init client: {e}")
        return None

    # Build a compact index of available items per section
    # Only send names + key metadata to stay within token limits
    index_parts = []
    sections_to_search = [section] if section else ["chords", "scales", "progressions", "keys"]

    for sec in sections_to_search:
        items = theory_data.get(sec, [])
        if not items:
            continue
        names = [item["name"] for item in items]
        # For chords/scales, also include formula for better matching
        if sec in ("chords", "scales"):
            entries = [f'{item["name"]} ({item.get("formula", "")}) [{item.get("quality", item.get("family", ""))}] - {item.get("desc", "")[:60]}' for item in items]
        elif sec == "progressions":
            entries = [f'{item["name"]} ({item.get("formula", "")}) [{item.get("category", "")}] - {item.get("desc", "")[:60]}' for item in items]
        elif sec == "keys":
            entries = [f'{item["name"]} [{item.get("mode", "")}] - {item.get("desc", "")[:60]}' for item in items]
        else:
            entries = names

        index_parts.append(f"=== {sec.upper()} ({len(items)} items) ===\n" + "\n".join(entries))

    data_index = "\n\n".join(index_parts)

    system_prompt = f"""You are a music theory expert assistant embedded in a learning tool called Riffd Studio.
You have access to a database of music theory items. Answer the user's question and identify which items from the database are most relevant.

Return ONLY a JSON object with these fields:
1. "answer": A helpful 1-3 sentence answer to their question. Be specific and practical. Reference actual items from the database when possible. No markdown formatting.
2. "results": An array of exact item names from the database that are relevant to the question. Return 3-12 items, ordered by relevance. Names must match EXACTLY as they appear in the database.
3. "section": Which section the results come from: "chords", "scales", "progressions", or "keys". If results span multiple sections, pick the most relevant one.

Rules:
- Only return items that actually exist in the database below. Never invent items.
- If the question doesn't relate to music theory or the available data, return {{"answer": "I can help with chords, scales, progressions, and keys. Try asking about those!", "results": [], "section": null}}
- Keep answers concise and practical — this is a tool for musicians, not a textbook.
- If the user asks about a specific genre, mood, or style, match items whose genres/mood/context fields align.

Available data:
{data_index}

Return ONLY the JSON object. No markdown, no code fences."""

    try:
        t0 = time.time()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )
        raw = response.content[0].text.strip()
        elapsed = time.time() - t0
        print(f"[theory_search] responded in {elapsed:.1f}s ({len(raw)} chars)")

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        print(f"[theory_search] answer={result.get('answer', '')[:50]}... results={len(result.get('results', []))}")
        return result
    except json.JSONDecodeError as e:
        print(f"[theory_search] JSON parse failed: {e}\nRaw: {raw[:200]}")
        return None
    except Exception as e:
        print(f"[theory_search] LLM call failed: {e}")
        return None
```

### API Route in app.py

Add this near the existing `/api/theory/<section>` route:

```python
@app.route("/api/theory/ask", methods=["POST"])
def theory_ask():
    """LLM-powered natural language search over theory data."""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    section = data.get("section")

    if not question:
        return jsonify({"error": "No question provided"}), 400
    if len(question) > 500:
        return jsonify({"error": "Question too long"}), 400

    # Load all theory data
    theory_data = {}
    for sec in ("chords", "scales", "progressions", "keys"):
        theory_data[sec] = _load_theory(sec)

    from theory_search import ask_theory
    result = ask_theory(question, section=section, theory_data=theory_data)

    if result is None:
        return jsonify({"error": "Could not process question"}), 500

    return jsonify(result)
```

**IMPORTANT:** This route MUST be defined BEFORE the existing `/api/theory/<section>` route in app.py, because Flask matches routes top-down and `/api/theory/ask` would otherwise be caught by the `<section>` parameter. Alternatively, add validation to the existing route to reject "ask" as a section name.

---

## Frontend Changes (learn.html)

### 1. Add AI search indicator CSS

Add these styles within `{% block extra_css %}`:

```css
/* AI Search */
.ai-answer {
  max-width:680px; margin-bottom:24px; padding:16px 20px;
  border:1px solid rgba(212,105,31,0.15); background:rgba(212,105,31,0.03);
  font-size:0.9375rem; color:rgba(245,245,245,0.7); line-height:1.7; letter-spacing:-0.01em;
  animation:fadeUp .2s ease;
}
.ai-answer-label {
  font-size:0.75rem; font-weight:500; color:#D4691F; text-transform:uppercase;
  letter-spacing:0.04em; margin-bottom:6px; opacity:0.7;
}
.ai-searching {
  font-size:0.875rem; color:rgba(245,245,245,0.4); padding:20px 0; text-align:center;
  letter-spacing:-0.01em;
}
.ai-searching::after {
  content:''; display:inline-block; width:12px; height:12px; margin-left:8px;
  border:1.5px solid rgba(212,105,31,0.3); border-top-color:#D4691F;
  border-radius:50%; animation:spin .6s linear infinite; vertical-align:middle;
}
@keyframes spin { to { transform:rotate(360deg) } }
.search-mode-hint {
  font-size:0.75rem; color:rgba(245,245,245,0.25); margin-top:6px; text-align:center;
  letter-spacing:-0.01em; transition:opacity .2s;
}
```

### 2. Add AI answer container to each section

Add a `<div class="ai-answer-box" id="ai-answer-{section}"></div>` element before each `<div class="filter-row">` in every theory section. Example for chords:

```html
<div class="theory-section active" id="section-chords">
  <div class="ai-answer-box" id="ai-answer-chords"></div>  <!-- NEW -->
  <div class="filter-row" id="filters-chords"></div>
  ...
</div>
```

Do the same for scales, progressions, and keys sections.

### 3. Add hint below search bar

After the search input, add:
```html
<div class="search-mode-hint" id="search-hint">Try asking a question: "What scales work for jazz?"</div>
```

### 4. Update JavaScript

Replace the existing search input event listener and add AI search logic:

```javascript
// AI search state
let _aiDebounce = null;
let _aiAbort = null;
let _aiMode = false;

const AI_TRIGGERS = /^(what|which|how|why|show|give|find|suggest|recommend|list|tell|explain|can|is|are|do|does|should|could|would)\b/i;

function _isAiQuery(q) {
  if (!q || q.length < 3) return false;
  if (q.includes('?')) return true;
  if (q.length > 40) return true;
  if (AI_TRIGGERS.test(q.trim())) return true;
  return false;
}

document.getElementById('global-search').addEventListener('input', function() {
  const raw = this.value.trim();
  const q = raw.toLowerCase();
  const hint = document.getElementById('search-hint');

  // Clear any pending AI search
  if (_aiDebounce) clearTimeout(_aiDebounce);
  if (_aiAbort) { _aiAbort.abort(); _aiAbort = null; }

  if (_isAiQuery(raw)) {
    // AI mode: debounce 600ms then call API
    _aiMode = true;
    if (hint) hint.style.opacity = '0';

    // Clear keyword filter results while typing
    _search[_currentSection] = '';
    _applyFilters(_currentSection);

    // Show searching indicator
    const answerBox = document.getElementById('ai-answer-' + _currentSection);
    if (answerBox) answerBox.innerHTML = '<div class="ai-searching">Searching</div>';
    _renderSection(_currentSection);

    _aiDebounce = setTimeout(() => _doAiSearch(raw, _currentSection), 600);
  } else {
    // Keyword mode: existing behavior
    _aiMode = false;
    _clearAiAnswer(_currentSection);
    if (hint) hint.style.opacity = raw.length > 0 ? '0' : '1';

    _search[_currentSection] = q;
    _applyFilters(_currentSection);
    _renderSection(_currentSection);
  }
});

async function _doAiSearch(question, section) {
  const answerBox = document.getElementById('ai-answer-' + section);
  _aiAbort = new AbortController();

  try {
    const resp = await fetch('/api/theory/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, section }),
      signal: _aiAbort.signal,
    });

    if (!resp.ok) throw new Error('API error');
    const data = await resp.json();

    // If the LLM says results are in a different section, switch to it
    if (data.section && data.section !== _currentSection && ['chords','scales','progressions','keys'].includes(data.section)) {
      const link = document.querySelector(`.sidebar-link[onclick*="${data.section}"]`);
      _showSection(data.section, link);
    }

    const targetSection = data.section || section;

    // Show answer
    if (answerBox || targetSection !== section) {
      const box = document.getElementById('ai-answer-' + targetSection);
      if (box && data.answer) {
        box.innerHTML = `<div class="ai-answer"><div class="ai-answer-label">AI Answer</div>${_esc(data.answer)}</div>`;
      }
    }

    // Filter grid to only show matched items
    if (data.results && data.results.length > 0) {
      const matchSet = new Set(data.results.map(r => r.toLowerCase()));
      _filtered[targetSection] = (_data[targetSection] || []).filter(
        item => matchSet.has(item.name.toLowerCase())
      );
      // Sort by the order the LLM returned them (relevance)
      const orderMap = {};
      data.results.forEach((r, i) => orderMap[r.toLowerCase()] = i);
      _filtered[targetSection].sort((a, b) =>
        (orderMap[a.name.toLowerCase()] ?? 999) - (orderMap[b.name.toLowerCase()] ?? 999)
      );
      _page[targetSection] = 0;
      _renderCards(targetSection);
    } else {
      // No results — show the answer only, with empty grid message
      _filtered[targetSection] = [];
      _renderCards(targetSection);
    }
  } catch (e) {
    if (e.name === 'AbortError') return; // User kept typing, ignore
    console.warn('[ai-search]', e);
    if (answerBox) answerBox.innerHTML = '';
    // Fallback to keyword search
    _search[section] = question.toLowerCase();
    _applyFilters(section);
    _renderSection(section);
  }
}

function _clearAiAnswer(section) {
  const box = document.getElementById('ai-answer-' + section);
  if (box) box.innerHTML = '';
}
```

### 5. Update _clearFilters to also clear AI state

```javascript
function _clearFilters(section) {
  for (const set of Object.values(_filters[section])) set.clear();
  _search[section] = '';
  document.getElementById('global-search').value = '';
  _clearAiAnswer(section);  // NEW
  _aiMode = false;           // NEW
  _applyFilters(section);
  _renderSection(section);
}
```

### 6. Update _showSection to clear AI answer when switching sections

In the `_showSection` function, add `_clearAiAnswer(_currentSection);` near the top before the section switch.

---

## Key Implementation Details

### Token Budget
The theory data index sent to Haiku should stay compact. Each item is ~80-100 chars, and with ~465 items across all sections, the index is ~40-50K chars (~10-12K tokens). This fits comfortably in Haiku's context. If the user is currently viewing a specific section, only send that section's data to save tokens and improve accuracy.

### Latency
Haiku typically responds in 0.5-1.5s. The 600ms debounce means total perceived latency is ~1.1-2.1s from when the user stops typing. The spinning indicator makes this feel responsive.

### Cost
At ~12K input tokens + ~200 output tokens per query, each search costs approximately $0.01. This is negligible.

### Graceful Degradation
- If no ANTHROPIC_API_KEY is set, the endpoint returns a 500 and the frontend falls back to keyword search silently.
- If the LLM call times out or fails, the frontend falls back to keyword search.
- If the user clears the search or types a short keyword, it instantly switches back to client-side filtering.

### Mobile
The AI answer box and searching indicator should work within the existing mobile breakpoints. No additional mobile CSS should be needed — the `.ai-answer` box is max-width constrained and will naturally fit.

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `theory_search.py` | **CREATE** — new file, LLM search logic |
| `app.py` | **MODIFY** — add `/api/theory/ask` route near existing theory route |
| `templates/learn.html` | **MODIFY** — add CSS, HTML containers, and JS for AI search |

## Do NOT Change
- Existing keyword filter behavior (must continue working identically for short non-question searches)
- Filter dropdowns, pills, pagination, card rendering
- Circle of Fifths section
- Any other templates or pages
- The `insight.py` file
- Any data files (chords.json, scales.json, etc.)

## Testing Checklist
1. Type "Cmaj7" → should use keyword filter (no API call), show matching chords
2. Type "What chords work for jazz?" → should hit AI endpoint, show answer + filtered results
3. Type "Show me dark scales for metal" → should show AI answer + relevant scales (may switch section)
4. Type "?" after any text → should trigger AI mode
5. Clear search → should reset to full unfiltered view, hide AI answer
6. Switch sections while AI answer is showing → should clear the answer
7. Type quickly (trigger debounce) → should only fire one API call
8. With no ANTHROPIC_API_KEY → should silently fall back to keyword search
9. On mobile → AI answer box should render properly within existing breakpoints
10. Existing filter dropdowns should still work independently and alongside search
