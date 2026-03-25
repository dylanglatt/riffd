"""
spotify_search.py
Spotify API: search-based track discovery.
Only uses endpoints available on client-credentials flow (search + artist info).
Audio features, top-tracks, related-artists, and recommendations are restricted.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
_token_cache = {"token": None, "expires_at": 0}


def _get_token() -> str:
    import time
    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise ValueError("Spotify credentials not set.")
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    import time
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data["expires_in"] - 60
    return _token_cache["token"]


def _headers():
    return {"Authorization": f"Bearer {_get_token()}"}


def _format_track(t) -> dict:
    artists = t.get("artists") or []
    album = t.get("album") or {}
    images = album.get("images") or []
    release = album.get("release_date") or ""
    return {
        "id": t.get("id", ""),
        "name": t.get("name", ""),
        "artist": ", ".join(a["name"] for a in artists),
        "artist_id": artists[0]["id"] if artists else None,
        "album": album.get("name", ""),
        "year": release[:4] if release else "",
        "duration_ms": t.get("duration_ms", 0),
        "image_url": images[0]["url"] if images else None,
        "yt_query": f"{', '.join(a['name'] for a in artists)} - {t.get('name', '')} official audio",
    }


def _dedupe(tracks, exclude_id, limit=6):
    seen = set()
    out = []
    for t in tracks:
        tid = t.get("id", "")
        if not tid or tid == exclude_id or tid in seen:
            continue
        seen.add(tid)
        out.append(t)
        if len(out) >= limit:
            break
    return out


class RateLimitError(Exception):
    """Raised when Spotify returns 429."""
    def __init__(self, retry_after=5):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s.")


def search_spotify(query: str, limit: int = 8) -> list[dict]:
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers=_headers(),
        params={"q": query, "type": "track", "limit": limit},
        timeout=10,
    )
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"[spotify] 429 rate limited, retry-after={retry_after}s")
        raise RateLimitError(retry_after)
    resp.raise_for_status()
    return [_format_track(t) for t in resp.json().get("tracks", {}).get("items", [])]


_rate_limit_until = 0  # timestamp when rate limit expires

def _safe_search(query, limit=8):
    """Search that never throws. Respects rate limit backoff."""
    global _rate_limit_until
    import time
    if time.time() < _rate_limit_until:
        return []  # still in backoff window
    try:
        return search_spotify(query, limit)
    except RateLimitError as e:
        _rate_limit_until = time.time() + e.retry_after
        print(f"[spotify] backing off for {e.retry_after}s")
        return []
    except Exception:
        return []


def get_recommendations_for_track(
    track_id="",
    artist_id=None,
    track_name="",
    artist_name="",
    year="",
    **kwargs,
):
    """
    Build honest recommendation pools using only Spotify search.

    We can verify:
    - Artist match (for "More Like This")
    - Year range (for "Around This Time")

    We cannot verify without audio features:
    - Key/mode of candidates (so "Great to Jam With" uses genre/style similarity)
    - Progression of candidates (so "Same Chords" is not shown unless we have data)

    Returns: {
        "more_like_this": [...],  -- same/similar artist, verified
        "same_style": [...],      -- genre/style pool, honest label
        "around_this_time": [...], -- year-verified
    }
    """
    all_used_ids = {track_id}

    # ── MORE LIKE THIS: same artist (verified) ──
    more_like_this = []
    if artist_name:
        results = _safe_search(f"artist:{artist_name}", 10)
        more_like_this = _dedupe(results, track_id, 6)
        for t in more_like_this:
            all_used_ids.add(t["id"])

        print(f"[recs] more_like_this: {len(more_like_this)} (artist:{artist_name})")
        for t in more_like_this:
            print(f"[recs]   + {t['name']} by {t['artist']}")

    # ── SAME STYLE: initially empty — populated by Last.fm similar tracks ──
    # Spotify search is too noisy for style matching (returns literal word matches).
    # Last.fm's "similar tracks" API uses actual listening pattern data.
    # This pool starts empty and gets filled by enrich_recommendations_with_lastfm().
    same_style = []
    print(f"[recs] same_style: starts empty (will be filled by Last.fm)")

    # ── AROUND THIS TIME: year-verified ──
    around_this_time = []
    try:
        yr = int(str(year)[:4])
        results = _safe_search(f"year:{yr - 3}-{yr + 3}", 10)
        for t in results:
            try:
                t_yr = int(str(t.get("year", "0"))[:4])
                if abs(t_yr - yr) <= 5 and t["id"] not in all_used_ids:
                    around_this_time.append(t)
                    all_used_ids.add(t["id"])
                    print(f"[recs]   + {t['name']} by {t['artist']} ({t['year']}) ✓ year={t_yr}")
                else:
                    if t["id"] not in all_used_ids:
                        print(f"[recs]   - {t['name']} ({t.get('year','?')}) ✗ year out of range")
            except (ValueError, TypeError):
                pass

        # Also search artist + era
        if artist_name:
            era_results = _safe_search(f"{artist_name} year:{yr - 3}-{yr + 3}", 6)
            for t in era_results:
                if t["id"] not in all_used_ids:
                    around_this_time.append(t)
                    all_used_ids.add(t["id"])
    except (ValueError, TypeError):
        pass

    around_this_time = around_this_time[:6]
    print(f"[recs] around_this_time: {len(around_this_time)}")

    return {
        "more_like_this": more_like_this,
        "same_style": same_style,
        "around_this_time": around_this_time,
    }
