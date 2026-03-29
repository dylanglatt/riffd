"""
external_apis.py
Genius lyrics (with strict matching) and Last.fm similar tracks / tags.
"""

import os
import re
import requests
from difflib import SequenceMatcher
from dotenv import load_dotenv

load_dotenv()

GENIUS_API_KEY = os.getenv("GENIUS_API_KEY", "")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")

# Minimum similarity score to accept a Genius match (0-1)
GENIUS_MIN_CONFIDENCE = 0.45


# ─── Text Normalization ──────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """
    Normalize a track title for matching:
    - strip remaster/live/version suffixes
    - remove parenthetical info
    - lowercase, strip punctuation, collapse whitespace
    """
    t = title.lower()
    # Remove common suffixes: "- 2013 Remaster", "- Remastered 2021", "(Live)", etc.
    t = re.sub(r"\s*[-–]\s*\d{4}\s*remaster(ed)?", "", t)
    t = re.sub(r"\s*[-–]\s*remaster(ed)?(\s*\d{4})?", "", t)
    t = re.sub(r"\s*[-–]\s*live\b.*", "", t)
    t = re.sub(r"\s*[-–]\s*mono\b.*", "", t)
    t = re.sub(r"\s*[-–]\s*stereo\b.*", "", t)
    t = re.sub(r"\s*[-–]\s*single\s*version\b.*", "", t)
    t = re.sub(r"\s*[-–]\s*deluxe\b.*", "", t)
    t = re.sub(r"\s*[-–]\s*bonus\s*track\b.*", "", t)
    # Remove parentheticals: (Remastered), (Live at ...), (feat. ...)
    t = re.sub(r"\([^)]*\)", "", t)
    t = re.sub(r"\[[^\]]*\]", "", t)
    # Strip punctuation except spaces
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_artist(artist: str) -> str:
    """Normalize artist name: lowercase, take first artist if comma-separated."""
    a = artist.lower().strip()
    # Take first artist only (e.g. "Eagles, Glenn Frey" → "eagles")
    if "," in a:
        a = a.split(",")[0].strip()
    a = re.sub(r"[^\w\s]", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    return a


def _similarity(a: str, b: str) -> float:
    """String similarity ratio (0-1)."""
    return SequenceMatcher(None, a, b).ratio()


# ─── Genius Lyrics ────────────────────────────────────────────────────────────

def get_lyrics(artist: str, track_name: str) -> str | None:
    """
    Search Genius with strict matching. Returns lyrics only if confidence is high enough.
    """
    if not GENIUS_API_KEY:
        return None

    norm_title = _normalize_title(track_name)
    norm_artist = _normalize_artist(artist)

    print(f"[genius] searching: artist='{norm_artist}' title='{norm_title}'")

    try:
        # Search with normalized query
        query = f"{norm_artist} {norm_title}"
        resp = requests.get(
            "https://api.genius.com/search",
            headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
            params={"q": query},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[genius] search failed: HTTP {resp.status_code}")
            return None

        hits = resp.json().get("response", {}).get("hits", [])
        if not hits:
            print("[genius] no hits")
            return None

        # Score each result
        best_url = None
        best_score = 0
        best_info = ""

        for hit in hits[:8]:
            result = hit.get("result", {})
            g_title = _normalize_title(result.get("title") or "")
            g_artist = _normalize_artist(
                result.get("primary_artist", {}).get("name") or ""
            )

            # Title similarity (most important)
            title_sim = _similarity(norm_title, g_title)

            # Artist similarity
            artist_sim = _similarity(norm_artist, g_artist)

            # Combined score: artist match is a hard requirement
            # If artist doesn't match at all, heavily penalize
            if artist_sim < 0.3:
                score = title_sim * 0.3  # almost certainly wrong song
            elif artist_sim >= 0.7:
                score = title_sim * 0.7 + artist_sim * 0.3  # good artist match
            else:
                score = title_sim * 0.5 + artist_sim * 0.5

            # Bonus for exact title match
            if norm_title == g_title:
                score += 0.15

            info = f"'{g_title}' by '{g_artist}' → title_sim={title_sim:.2f} artist_sim={artist_sim:.2f} score={score:.2f}"
            print(f"[genius]   candidate: {info}")

            if score > best_score:
                best_score = score
                best_url = result.get("url")
                best_info = info

        print(f"[genius] best match: {best_info} (threshold={GENIUS_MIN_CONFIDENCE})")

        if best_score < GENIUS_MIN_CONFIDENCE:
            print(f"[genius] REJECTED — below confidence threshold")
            return None

        if not best_url:
            return None

        # Scrape lyrics
        page = requests.get(best_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        })
        if page.status_code != 200:
            print(f"[genius] page fetch failed: HTTP {page.status_code}")
            return None

        lyrics = _extract_lyrics_from_html(page.text)
        if lyrics:
            print(f"[genius] lyrics found: {len(lyrics)} chars")
        else:
            print("[genius] no lyrics extracted from page")
        return lyrics

    except Exception as e:
        print(f"[genius] ERROR: {e}")
        return None


def _extract_lyrics_from_html(html: str) -> str | None:
    """
    Extract full lyrics from Genius HTML page.
    Uses stack-based div parsing to handle nested divs inside lyrics containers.
    """
    # Find all lyrics container start positions
    container_starts = [m.start() for m in re.finditer(
        r'<div[^>]*data-lyrics-container="true"', html
    )]

    if not container_starts:
        return None

    # Extract content from each container using div depth counting
    raw_parts = []
    for start in container_starts:
        tag_end = html.index(">", start) + 1
        depth = 1
        pos = tag_end
        while depth > 0 and pos < len(html):
            next_open = html.find("<div", pos)
            next_close = html.find("</div>", pos)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                pos = next_open + 4
            else:
                depth -= 1
                if depth == 0:
                    raw_parts.append(html[tag_end:next_close])
                pos = next_close + 6

    if not raw_parts:
        return None

    raw = "\n".join(raw_parts)

    # Convert HTML to text
    text = re.sub(r"<br\s*/?>", "\n", raw)
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#x27;", "'", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)

    # Clean the text
    text = _clean_lyrics(text)

    print(f"[genius] extracted {len(text)} chars, first 120: {repr(text[:120])}")
    return text if len(text) > 30 else None


def _clean_lyrics(text: str) -> str:
    """
    Remove page chrome, metadata, contributor counts from extracted lyrics.
    Keep legitimate section labels like [Verse], [Chorus], etc.
    """
    lines = text.split("\n")
    cleaned = []
    seen_lyric = False  # track if we've hit actual lyrics yet

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Preserve blank lines (verse spacing) only after lyrics have started
            if seen_lyric and cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        # Skip known chrome patterns
        if _is_chrome_line(stripped, seen_lyric):
            continue

        # This is a real lyric line or section label
        seen_lyric = True
        cleaned.append(stripped)

    # Trim trailing blanks
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    result = "\n".join(cleaned)
    # Collapse 3+ blank lines into 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _is_chrome_line(line: str, seen_lyric: bool) -> bool:
    """Detect Genius page chrome / metadata lines that aren't lyrics."""
    lower = line.lower()

    # Contributor counts: "60 Contributors", "12 ContributorsTake It Easy Lyrics"
    if re.match(r"^\d+\s*contributor", lower):
        return True

    # Title + "Lyrics" suffix: "Take It Easy Lyrics"
    if lower.endswith(" lyrics") and not lower.startswith("["):
        return True

    # Standalone "Lyrics" or page labels
    if lower in ("lyrics", "embed", "see live photos", "you might also like"):
        return True

    # "Song Title is a song written by..." description lines (before lyrics start)
    if not seen_lyric and ("is a song" in lower or "is the" in lower or "was released" in lower
                           or "written by" in lower or "produced by" in lower):
        return True

    # Genius embed/ad markers
    if re.match(r"^\d+embed$", lower.replace(" ", "")):
        return True

    # Pyong/share/translation links
    if lower.startswith("share") or lower.startswith("translations") or "pyong" in lower:
        return True

    return False


# ─── Last.fm ─────────────────────────────────────────────────────────────────

def get_similar_tracks(artist: str, track_name: str, limit: int = 12) -> list[dict]:
    if not LASTFM_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "track.getSimilar",
                "artist": artist.split(",")[0].strip(),
                "track": _normalize_title(track_name),
                "api_key": LASTFM_API_KEY,
                "format": "json",
                "limit": limit,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        tracks = resp.json().get("similartracks", {}).get("track", [])
        results = []
        for t in tracks:
            images = t.get("image", [])
            image_url = None
            for img in reversed(images):
                if img.get("#text") and "2a96cbd8b46e442fc41c2b86b821562f" not in img["#text"]:
                    image_url = img["#text"]
                    break
            results.append({
                "name": t.get("name", ""),
                "artist": t.get("artist", {}).get("name", ""),
                "match_score": float(t.get("match", 0)),
                "image_url": image_url,
            })
        return results
    except Exception:
        return []


def get_track_tags(artist: str, track_name: str) -> list[str]:
    if not LASTFM_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "track.getTopTags",
                "artist": artist.split(",")[0].strip(),
                "track": _normalize_title(track_name),
                "api_key": LASTFM_API_KEY,
                "format": "json",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        tags = resp.json().get("toptags", {}).get("tag", [])
        return [t["name"] for t in tags[:8] if t.get("name")]
    except Exception:
        return []


def _itunes_art(artist: str, track: str) -> str | None:
    """Quick iTunes Search lookup for album art. Returns URL or None."""
    try:
        q = f"{artist} {track}"
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": q, "media": "music", "entity": "song", "limit": 1},
            timeout=5,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                art = results[0].get("artworkUrl100", "")
                if art:
                    return art.replace("100x100bb", "300x300bb")
    except Exception:
        pass
    return None


from functools import lru_cache

@lru_cache(maxsize=256)
def _art_cache_lookup(artist_lower: str, track_lower: str) -> str | None:
    """Bounded LRU cache for iTunes art lookups."""
    return _itunes_art(artist_lower, track_lower)

def _get_art_for_track(artist: str, track: str) -> str | None:
    """Get album art URL with bounded LRU cache. Tries iTunes if no image available."""
    return _art_cache_lookup(artist.lower().strip(), track.lower().strip())


def enrich_recommendations_with_lastfm(recs: dict, artist: str, track_name: str) -> dict:
    """
    Add Last.fm similar tracks to the 'same_style' pool.
    Last.fm similarity is based on listening patterns — honest for style matching.
    """
    similar = get_similar_tracks(artist, track_name)
    if not similar:
        return recs

    lastfm_tracks = []
    for t in similar:
        if t["name"] and t["artist"]:
            lastfm_tracks.append({
                "id": f"lastfm_{t['name'][:20]}_{t['artist'][:20]}".replace(" ", "_"),
                "name": t["name"],
                "artist": t["artist"],
                "image_url": t.get("image_url"),
                "year": "", "duration_ms": 0, "album": "",
                "yt_query": f"{t['artist']} - {t['name']} official audio",
                "_source": "lastfm",
            })

    # Add to same_style — Last.fm "similar" is genuinely style-based
    all_existing = set()
    for pool_key in recs:
        for t in recs[pool_key]:
            all_existing.add((t.get("name", "").lower(), t.get("artist", "").lower()))

    for t in lastfm_tracks:
        key = (t["name"].lower(), t["artist"].lower())
        if key not in all_existing and len(recs.get("same_style", [])) < 8:
            recs.setdefault("same_style", []).append(t)
            all_existing.add(key)

    print(f"[recs] lastfm enriched same_style to {len(recs.get('same_style', []))} tracks")

    # Fill in missing album art via iTunes lookup
    filled = 0
    for pool_key in recs:
        for t in recs[pool_key]:
            if not t.get("image_url") and t.get("name") and t.get("artist"):
                art = _get_art_for_track(t["artist"], t["name"])
                if art:
                    t["image_url"] = art
                    filled += 1
    if filled:
        print(f"[recs] filled {filled} missing album art via iTunes")

    return recs
