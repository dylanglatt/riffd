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
    index_parts = []
    sections_to_search = [section] if section else ["chords", "scales", "progressions", "keys"]

    for sec in sections_to_search:
        items = theory_data.get(sec, [])
        if not items:
            continue
        if sec in ("chords", "scales"):
            entries = [f'{item["name"]} ({item.get("formula", "")}) [{item.get("quality", item.get("family", ""))}] - {item.get("desc", "")[:60]}' for item in items]
        elif sec == "progressions":
            entries = [f'{item["name"]} ({item.get("formula", "")}) [{item.get("category", "")}] - {item.get("desc", "")[:60]}' for item in items]
        elif sec == "keys":
            entries = [f'{item["name"]} [{item.get("mode", "")}] - {item.get("desc", "")[:60]}' for item in items]
        else:
            entries = [item["name"] for item in items]

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
