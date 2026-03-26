"""Tests for harmonic_analysis.py"""

from harmonic_analysis import (
    parse_lyric_sections,
    align_chords_to_sections,
    infer_key_from_chords,
    _chords_to_numerals,
    _summarize_section,
    build_harmonic_analysis,
)


def test_parse_lyric_sections():
    """Test section parsing from lyrics with tags."""
    lyrics = """[Verse 1]
I like the way your sparkling earrings lay
Against your skin so brown

[Chorus]
I got a peaceful easy feeling
And I know you won't let me down

[Verse 2]
And I found out a long time ago
What a woman can do to your soul"""

    sections = parse_lyric_sections(lyrics)
    assert len(sections) == 3
    assert sections[0]["label"] == "Verse 1"
    assert sections[0]["normalized_label"] == "verse"
    assert sections[1]["label"] == "Chorus"
    assert sections[1]["normalized_label"] == "chorus"
    assert sections[2]["label"] == "Verse 2"
    assert sections[2]["normalized_label"] == "verse"
    assert sections[0]["line_count"] == 2
    assert sections[1]["line_count"] == 2
    print("  PASS: parse_lyric_sections")


def test_parse_empty_lyrics():
    """Test graceful handling of empty/no lyrics."""
    assert parse_lyric_sections(None) == []
    assert parse_lyric_sections("") == []
    assert parse_lyric_sections("No section tags here\nJust lines") == []
    print("  PASS: parse_empty_lyrics")


def test_chord_to_numerals_major():
    """Test Roman numeral conversion in major key."""
    # G major (key_num=7, mode_num=1)
    chords = ["G", "D", "C", "Em"]
    numerals = _chords_to_numerals(chords, key_num=7, mode_num=1)
    assert numerals == ["I", "V", "IV", "vi"], f"Got {numerals}"
    print("  PASS: chord_to_numerals_major")


def test_chord_to_numerals_minor():
    """Test Roman numeral conversion in minor key."""
    # A minor (key_num=9, mode_num=0)
    chords = ["Am", "F", "C", "G"]
    numerals = _chords_to_numerals(chords, key_num=9, mode_num=0)
    assert numerals == ["i", "VI", "III", "VII"], f"Got {numerals}"
    print("  PASS: chord_to_numerals_minor")


def test_chord_to_numerals_nondiatonic():
    """Test handling of non-diatonic chords."""
    # C major, but with Bb (bVII)
    chords = ["C", "Bb", "F"]
    numerals = _chords_to_numerals(chords, key_num=0, mode_num=1)
    assert numerals[0] == "I"
    assert "bVII" in numerals[1] or "bvii" in numerals[1].lower()
    assert numerals[2] == "IV"
    print("  PASS: chord_to_numerals_nondiatonic")


def test_summarize_repeating_pattern():
    """Test that summarization detects repeating patterns."""
    # Use a clean repeating pattern without consecutive dupes
    chords = ["G", "D", "C", "G", "D", "C", "G", "D", "C"]
    summary = _summarize_section(chords, key_num=7, mode_num=1)
    assert summary["is_repeating_pattern"] == True
    assert summary["repeated_units"] >= 2
    assert len(summary.get("summary_chords", [])) >= 2
    print("  PASS: summarize_repeating_pattern")


def test_summarize_no_repetition():
    """Test that summarization does NOT fake a pattern."""
    chords = ["G", "D", "Em", "C", "Am", "F", "Dm", "Bb"]
    summary = _summarize_section(chords, key_num=7, mode_num=1)
    assert summary["is_repeating_pattern"] == False
    print("  PASS: summarize_no_repetition")


def test_align_with_sections():
    """Test chord alignment to lyric sections."""
    chords = ["G", "D", "C", "G", "Em", "C", "G", "D"]
    lyrics = "[Verse 1]\nLine one\nLine two\nLine three\nLine four\n\n[Chorus]\nChorus line one\nChorus line two"
    sections = parse_lyric_sections(lyrics)
    result = align_chords_to_sections(chords, sections, key_num=7, mode_num=1)
    assert len(result) == 2
    assert result[0]["label"] == "Verse 1"
    assert result[1]["label"] == "Chorus"
    assert len(result[0]["chords"]) + len(result[1]["chords"]) == len(chords)
    # Both should have roman numerals
    assert len(result[0]["roman_numerals"]) > 0
    assert len(result[1]["roman_numerals"]) > 0
    print("  PASS: align_with_sections")


def test_align_no_sections():
    """Test fallback when no lyric sections."""
    chords = ["G", "D", "C", "G"]
    result = align_chords_to_sections(chords, [], key_num=7, mode_num=1)
    assert len(result) == 1
    assert result[0]["label"] == "Full Song"
    assert result[0]["chords"] == chords
    print("  PASS: align_no_sections")


def test_infer_key_from_chords():
    """Test key inference from chord sequence."""
    # G major chords: G, C, D, Em — should infer G major
    chords = ["G", "C", "D", "Em", "G", "C", "D", "G"]
    result = infer_key_from_chords(chords)
    assert result["tonic"] == "G"
    assert result["mode"] == "major"
    assert result["confidence"] > 0.3
    print("  PASS: infer_key_from_chords")


def test_infer_key_minor():
    """Test key inference for minor songs."""
    chords = ["Am", "F", "C", "G", "Am", "F", "C", "Am"]
    result = infer_key_from_chords(chords)
    # Should infer either A minor or C major (relative)
    assert result["tonic"] in ["A", "C"]
    assert result["confidence"] > 0.2
    print("  PASS: infer_key_minor")


def test_build_full_analysis():
    """Test the full build_harmonic_analysis pipeline."""
    chords = ["G", "D", "C", "G", "Em", "C", "G", "D", "Em", "C", "G", "D"]
    lyrics = "[Verse 1]\nLine one\nLine two\nLine three\n\n[Chorus]\nChorus one\nChorus two\nChorus three"

    result = build_harmonic_analysis(
        chord_sequence=chords,
        lyrics_text=lyrics,
        key_num=7,
        mode_num=1,
        key_confidence=0.8,
    )

    assert "key" in result
    assert result["key"]["display"] == "G major"
    assert "harmonic_sections" in result
    assert len(result["harmonic_sections"]) == 2

    verse = result["harmonic_sections"][0]
    assert verse["label"] == "Verse 1"
    assert len(verse["chords"]) > 0
    assert len(verse["roman_numerals"]) == len(verse["chords"])
    assert "summary" in verse

    print("  PASS: build_full_analysis")


def test_low_confidence_ambiguous():
    """Test behavior with ambiguous/low-confidence data."""
    chords = ["C"]  # Only one chord — very low confidence
    result = build_harmonic_analysis(chord_sequence=chords)
    sections = result["harmonic_sections"]
    assert len(sections) == 1
    assert sections[0]["label"] == "Full Song"
    print("  PASS: low_confidence_ambiguous")


if __name__ == "__main__":
    print("Running harmonic analysis tests...\n")
    test_parse_lyric_sections()
    test_parse_empty_lyrics()
    test_chord_to_numerals_major()
    test_chord_to_numerals_minor()
    test_chord_to_numerals_nondiatonic()
    test_summarize_repeating_pattern()
    test_summarize_no_repetition()
    test_align_with_sections()
    test_align_no_sections()
    test_infer_key_from_chords()
    test_infer_key_minor()
    test_build_full_analysis()
    test_low_confidence_ambiguous()
    print("\nAll tests passed!")
