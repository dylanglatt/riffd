"""
insight.py
LLM-powered structured song analysis using Claude Haiku.
Returns JSON with progression names, smart recommendations, and key context.
"""

import json
import os
import time

# Lazy singleton — avoids re-creating HTTP connection pool on every call
_client = None


def _get_client():
    """Return a cached Anthropic client, or None if no API key is set."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
        return _client
    except Exception as e:
        print(f"[insight] failed to init client: {e}")
        return None


def predict_instruments(song_name: str, artist: str, tags: list[str] | None = None) -> dict | None:
    """
    Use Claude Haiku to predict what instruments are in a song before Demucs runs.
    Returns dict with instruments list, boolean flags, and notable info, or None on failure.
    """
    client = _get_client()
    if not client:
        print("[hints] no ANTHROPIC_API_KEY set — skipping instrument prediction")
        return None

    genre = ", ".join(tags[:5]) if tags else "Unknown"
    user_msg = f'Song: "{song_name}" by {artist}\nGenre/Tags: {genre}\n\nReturn the JSON instrument prediction.'

    system_prompt = (
        "You are a music production expert. Given a song title, artist, and genre tags, "
        "predict the sound sources in the production.\n\n"
        "CRITICAL: Use the right vocabulary for the genre. Each category has distinct sounds:\n"
        "- electronic (house, techno, trance, EDM): 'synth lead', 'synth pad', 'sub bass', "
        "'arpeggiator', 'pluck synth', 'FX/riser', 'acid bassline', 'vocoder'\n"
        "- hiphop (hip-hop, trap, drill, boom bap, R&B): '808 bass', '808 kick', 'hi-hat rolls', "
        "'vocal chops', 'sampled loop', 'synth bells', 'tag/producer tag'\n"
        "- band (rock, punk, metal, alternative, indie): 'electric guitar', 'distorted guitar', "
        "'bass guitar', 'drum kit', 'rhythm guitar', 'lead guitar'\n"
        "- jazz (jazz, soul, funk, neo-soul, blues): 'upright bass', 'Rhodes piano', 'Wurlitzer', "
        "'brushed drums', 'walking bass', 'horn section', 'vibraphone'\n"
        "- classical (orchestral, film score, chamber): 'violin section', 'cello section', "
        "'French horn', 'timpani', 'oboe', 'harp', 'woodwind section'\n"
        "- singer_songwriter (acoustic, folk, country, indie folk): 'acoustic guitar', "
        "'fingerpicked guitar', 'upright piano', 'harmonica', 'banjo', 'pedal steel'\n"
        "- world (Latin, Afrobeat, reggae, K-pop, Bollywood, reggaeton): 'congas', 'bongos', "
        "'steel drums', 'sitar', 'tabla', 'dembow beat', 'marimba', 'kora'\n"
        "- ambient (ambient, shoegaze, dream pop, post-rock, drone): 'reverb guitar', "
        "'shimmer pad', 'granular texture', 'feedback', 'bowed guitar', 'field recording'\n\n"
        "Pick the BEST matching category. If the song blends genres, pick the dominant one.\n"
        "Only list sounds you're confident are in the recording. Return valid JSON only.\n"
        'Format: {"instruments": ["vocals", "synth lead", "sub bass", ...], '
        '"category": "electronic|hiphop|band|jazz|classical|singer_songwriter|world|ambient", '
        '"has_piano": false, "has_guitar": false, "has_synth": true, '
        '"has_strings": false, "has_brass": false, "has_acoustic_guitar": false, '
        '"has_sub_bass": true, "has_808": false, "has_sampled_elements": true, '
        '"vocal_arrangement": "solo|harmonized|multi_vocalist", '
        '"notable": "short note about production techniques, tuning, sound design, or unusual elements"}\n\n'
        "vocal_arrangement values:\n"
        '- "solo": single lead vocalist, no distinct harmony or backing vocal parts (most songs)\n'
        '- "harmonized": lead vocal + backing harmonies or layered vocals from same artist\n'
        '- "multi_vocalist": genuinely multiple distinct vocalists (e.g. duets, call-and-response, choir, band harmonies like Queen or Crosby Stills Nash)'
    )

    try:
        t0 = time.time()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            timeout=8.0,
        )
        raw = response.content[0].text.strip()
        elapsed = time.time() - t0
        print(f"[hints] predicted in {elapsed:.1f}s ({len(raw)} chars)")

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        instruments = result.get("instruments", [])
        print(f"[hints] predicted: {', '.join(instruments)}")
        return result
    except json.JSONDecodeError as e:
        print(f"[hints] JSON parse failed: {e}")
        return None
    except Exception as e:
        print(f"[hints] instrument prediction failed: {e}")
        return None


def generate_insight(song_name, artist, intelligence, lyrics=None, tags=None, exclude_songs=None):
    """
    Generate structured musical insight using Claude Haiku.

    Args:
        song_name: Track name
        artist: Artist name
        intelligence: Dict with key, bpm, harmonic_sections, progression, etc.
        lyrics: Raw lyrics text (first 20 lines used)
        tags: List of genre/style tags
        exclude_songs: Optional list of song titles to exclude from recommendations

    Returns:
        dict | None: Structured insight data, or None on failure
    """
    client = _get_client()
    if not client:
        print("[insight] no ANTHROPIC_API_KEY set — skipping")
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

    system_prompt = f"""You are a music theory expert. Given analysis data about a song, return ONLY a JSON object with these fields:

1. "progression_names": For each harmonic section provided, if the chord progression matches or closely resembles a well-known named progression, provide the name. Return as an object mapping section labels to names. Only include sections where you're confident of the name. Examples of named progressions: "50s progression", "Axis of Awesome", "Andalusian cadence", "12-bar blues", "Nashville progression", "Royal Road", "Pachelbel's Canon", "ii-V-I turnaround", "Plagal cadence". If a section's progression doesn't have a well-known name, omit it.

2. "smart_recs": Recommend songs based on MUSICAL DNA, not genre or mood. Return an object with exactly four categories:

   - "same_progression": 2-3 songs using the same chord progression PATTERN.
     GOOD reasons: "Same I-V-vi-IV progression", "Identical vi-IV-I-V loop"
     BAD reasons: "Similar harmonic movement", "Comparable progression feel"

   - "same_key_tempo": 2-3 songs in {key} near {round(bpm) if bpm else '?'} BPM (±15).
     GOOD reasons: "{key} at ~{round(bpm) if bpm else '?'} BPM", "{key}, {round(bpm)-10 if bpm else '?'}-{round(bpm)+10 if bpm else '?'} BPM range"
     BAD reasons: "Same key with similar tempo feel", "Mid-tempo ballad pacing"
     ONLY include songs you are CERTAIN are in {key}. If unsure, skip.

   - "similar_harmony": 2-3 songs sharing a SPECIFIC harmonic technique.
     GOOD reasons: "Descending chromatic bass line", "Borrows iv from parallel minor", "Same I-IV plagal cadence", "Pedal tone under changing chords"
     BAD reasons: "Emotional harmonic landscape", "Rich harmonic movement", "Melancholic major key feel", "Vulnerable harmonic character"
     The reason MUST name a theory concept. If you can't name one, don't include the song.

   - "more_by_artist": 3-4 other songs by {artist}.
     GOOD reasons: "Waltz time — rare 3/4 for them", "Only minor-key single", "Extended jazz chords in bridge", "12-string acoustic, open tuning"
     BAD reasons: "Classic hit", "Fan favorite", "Emotional ballad", "Upbeat feel-good track"

   Rules:
   - same_progression, same_key_tempo, similar_harmony: DIFFERENT artists than {artist}
   - more_by_artist: songs by {artist} ONLY
   - Each entry: {{"title": "...", "artist": "...", "reason": "..."}}
   - Reasons MUST be under 8 words. No exceptions.
   - BANNED words in reasons: "feel", "vibe", "emotional", "intimate", "landscape", "character", "comparable", "accessible", "melancholic", "vulnerable", "yearning", "carefree", "laid-back", "breezy", "smooth", "warm", "rich"
   - Genre proximity matters: when two songs equally satisfy the musical criteria, prefer the one closer in genre and era to {artist}. A hip-hop track should not surface classic rock recommendations unless the musical match is exceptionally specific and strong.
   - Pick well-known songs musicians would recognize

3. "key_context": A single sentence (under 20 words) about the character or common usage of the detected key. Examples: "A bright, open key — the natural home of folk and country guitar." or "Dark and dramatic — a favorite of classical composers and metal bands alike." Do NOT mention specific instruments or production details you cannot know from the data.

Return ONLY the JSON object. No markdown, no code fences, no explanation."""

    if exclude_songs:
        system_prompt += f"\n\nDo NOT recommend these songs: {', '.join(exclude_songs)}. Pick different songs instead."

    try:
        t0 = time.time()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1100,
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
