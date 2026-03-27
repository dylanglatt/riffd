"""
insight.py
LLM-powered structured song analysis using Claude Haiku.
Returns JSON with progression names, smart recommendations, and key context.
"""

import json
import os
import time


def generate_insight(song_name, artist, intelligence, lyrics=None, tags=None):
    """
    Generate structured musical insight using Claude Haiku.

    Args:
        song_name: Track name
        artist: Artist name
        intelligence: Dict with key, bpm, harmonic_sections, progression, etc.
        lyrics: Raw lyrics text (first 20 lines used)
        tags: List of genre/style tags

    Returns:
        dict | None: Structured insight data, or None on failure
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

    user_msg += "\nReturn the JSON analysis object."

    system_prompt = """You are a music theory expert. Given analysis data about a song, return ONLY a JSON object with these fields:

1. "progression_names": For each harmonic section provided, if the chord progression matches or closely resembles a well-known named progression, provide the name. Return as an object mapping section labels to names. Only include sections where you're confident of the name. Examples of named progressions: "50s progression", "Axis of Awesome", "Andalusian cadence", "12-bar blues", "Nashville progression", "Royal Road", "Pachelbel's Canon", "ii-V-I turnaround", "Plagal cadence". If a section's progression doesn't have a well-known name, omit it.

2. "smart_recs": Theory-based song recommendations. This is the KEY feature — recommend songs based on MUSICAL DNA, not genre. Return an object with three categories:
   - "same_progression": 2-3 songs that use the same or very similar chord progression pattern (e.g., if the song uses I-V-vi-IV, find other songs with that exact progression regardless of genre). Include the shared progression in the "reason" field.
   - "same_key_tempo": 2-3 songs in the same key AND a similar tempo range (within ~15 BPM). These are ideal for DJ sets, mashups, or practice sessions. Include key and BPM in the "reason" field.
   - "similar_harmony": 2-3 songs with similar harmonic movement or voice leading — songs that "feel" harmonically similar even if the exact chords differ (e.g., both use descending bass lines, both use modal interchange, both use the same cadence patterns).
   Each entry should be: {"title": "...", "artist": "...", "reason": "..."}
   The "reason" should be SHORT (under 12 words) and specific — e.g., "Same I-V-vi-IV progression", "G Major at 122 BPM", "Descending bass line over major chords".
   Pick well-known songs musicians would recognize. Never pick songs by the same artist as the input.

3. "key_context": A single sentence (under 20 words) about the character or common usage of the detected key. Examples: "A bright, open key — the natural home of folk and country guitar." or "Dark and dramatic — a favorite of classical composers and metal bands alike." Do NOT mention specific instruments or production details you cannot know from the data.

Return ONLY the JSON object. No markdown, no code fences, no explanation."""

    try:
        t0 = time.time()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        elapsed = time.time() - t0
        print(f"[insight] generated in {elapsed:.1f}s ({len(raw)} chars)")

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        print(f"[insight] parsed: prog_names={list(result.get('progression_names', {}).keys())}, recs={list(result.get('smart_recs', {}).keys())}")
        return result
    except json.JSONDecodeError as e:
        print(f"[insight] JSON parse failed: {e}\nRaw: {raw[:200]}")
        return None
    except Exception as e:
        print(f"[insight] LLM call failed: {e}")
        return None
