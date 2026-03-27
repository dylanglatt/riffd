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
