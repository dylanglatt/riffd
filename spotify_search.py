"""
spotify_search.py
Spotify API: search-based track discovery.
Only uses endpoints available on client-credentials flow (search + artist info).
Audio features, top-tracks, related-artists, and recommendations are restricted.

When Spotify is rate-limited, falls back to local search over history + discovery data.
"""

import json
import os
import requests
from pathlib import Path
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


_rate_limit_until = 0  # epoch timestamp — shared across search_spotify and _safe_search
_MAX_COOLDOWN = 15     # never allow cooldown longer than this (seconds)


def _set_cooldown(retry_after: int):
    """Set shared cooldown. Always replaces (never accumulates). Clamped to _MAX_COOLDOWN."""
    global _rate_limit_until
    import time
    clamped = min(max(retry_after, 1), _MAX_COOLDOWN)
    _rate_limit_until = time.time() + clamped
    print(f"[spotify] cooldown set: {clamped}s (raw retry_after={retry_after})")
    return clamped


def search_spotify(query: str, limit: int = 8) -> list[dict]:
    """
    Search for tracks. Tries Spotify API first; on rate limit, falls back to
    local search over history + discovery data. Returns results in the same format
    regardless of source.
    """
    global _rate_limit_until
    import time

    # If in cooldown, skip Spotify entirely and use fallback
    remaining = _rate_limit_until - time.time()
    if remaining > 0:
        print(f"[spotify] in cooldown ({remaining:.0f}s left) — using fallback search")
        return _fallback_search(query, limit)

    # Try Spotify
    try:
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            headers=_headers(),
            params={"q": query, "type": "track", "limit": limit},
            timeout=10,
        )
    except Exception as e:
        print(f"[spotify] request failed ({e}) — using fallback search")
        return _fallback_search(query, limit)

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        _set_cooldown(retry_after)
        print(f"[spotify] 429 from Spotify — using fallback search")
        return _fallback_search(query, limit)

    if resp.status_code != 200:
        print(f"[spotify] HTTP {resp.status_code} — using fallback search")
        return _fallback_search(query, limit)

    # Success — clear cooldown if any
    if _rate_limit_until > 0:
        print(f"[spotify] cooldown cleared (successful search)")
        _rate_limit_until = 0

    results = [_format_track(t) for t in resp.json().get("tracks", {}).get("items", [])]
    print(f"[spotify] live results: {len(results)}")
    return results


# ─── Fallback Search (local data) ─────────────────────────────────────────────

# Static discovery tracks — imported from app.py's _STATIC_DISCOVERY.
# Duplicated here to avoid circular import. Same data, same format.
_DISCOVERY_TRACKS = [
    {"id":"40riOy7x9W7GXjyGp4pjAv","name":"Hotel California","artist":"Eagles","artist_id":"0ECwFtbIWEVNwjlrfc6xoL","year":"1977","image_url":"https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e024637341b9f507521afa9a778","yt_query":"Eagles - Hotel California official audio","album":"Hotel California","duration_ms":391376},
    {"id":"4u7EnebtmKWzUH433cf5Qv","name":"Bohemian Rhapsody","artist":"Queen","artist_id":"1dfeR4HaWDbWqFHLkxsg1d","year":"1975","image_url":"https://i.scdn.co/image/ab67616d00001e02ce4f1737bc8a646c8c4bd25a","yt_query":"Queen - Bohemian Rhapsody official audio","album":"A Night at the Opera","duration_ms":354947},
    {"id":"5CQ30WqJwcep0pYcV4AMNc","name":"Stairway to Heaven","artist":"Led Zeppelin","artist_id":"36QJpDe2go2KgaRleHCDTp","year":"1971","image_url":"https://i.scdn.co/image/ab67616d00001e02c8a11e48c91a982d086afc69","yt_query":"Led Zeppelin - Stairway to Heaven official audio","album":"Led Zeppelin IV","duration_ms":482830},
    {"id":"7J1uxwnxfQLu4APicE5Rnj","name":"Billie Jean","artist":"Michael Jackson","artist_id":"3fMbdgg4jU18AjLCKBhRSm","year":"1982","image_url":"https://i.scdn.co/image/ab67616d00001e024121faee8df82c526cbab2be","yt_query":"Michael Jackson - Billie Jean official audio","album":"Thriller","duration_ms":293827},
    {"id":"1h2xVEoJORqrg71HocgqXd","name":"Superstition","artist":"Stevie Wonder","artist_id":"7guDJrEfX3qb6FEbdPA5qi","year":"1972","image_url":"https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e029e447b59bd3e2cbefaa31d91","yt_query":"Stevie Wonder - Superstition official audio","album":"Talking Book","duration_ms":245493},
    {"id":"3EYOJ1ST5O5ZQNBKuYh9VQ","name":"Wonderwall","artist":"Oasis","artist_id":"2DaxqgrOhkeH0fpeiQq2f4","year":"1995","image_url":"https://i.scdn.co/image/ab67616d00001e02ff5429125128b43572dbdccd","yt_query":"Oasis - Wonderwall official audio","album":"(What's the Story) Morning Glory?","duration_ms":258773},
    {"id":"2RnPATK05MTl8pVGObsqm4","name":"Come As You Are","artist":"Nirvana","artist_id":"6olE6TJLqED3rqDCT0FyPh","year":"1991","image_url":"https://i.scdn.co/image/ab67616d00001e02e175a19e530c898d167d39bf","yt_query":"Nirvana - Come As You Are official audio","album":"Nevermind","duration_ms":219219},
    {"id":"4yugZvBYaoREkJKtbG08Qr","name":"Take It Easy","artist":"Eagles","artist_id":"0ECwFtbIWEVNwjlrfc6xoL","year":"1972","image_url":"https://i.scdn.co/image/ab67616d00001e0284243a01af3c77b56fe01ab1","yt_query":"Eagles - Take It Easy official audio","album":"Eagles","duration_ms":211760},
    {"id":"7snQQk1zcKl8gGSbzO08Cs","name":"Purple Rain","artist":"Prince","artist_id":"5a2EaR3hamoenG9rDuVn8j","year":"1984","image_url":"https://i.scdn.co/image/ab67616d00001e02d4daf28d55fe4197ede848be","yt_query":"Prince - Purple Rain official audio","album":"Purple Rain","duration_ms":520000},
    {"id":"3AhXZa8sUQht0UEdBJgpGc","name":"Let It Be","artist":"The Beatles","artist_id":"3WrFJ7ztbogyGnTHbHJFl2","year":"1970","image_url":"https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e020cb0884829c5503b2e242541","yt_query":"The Beatles - Let It Be official audio","album":"Let It Be","duration_ms":243027},
    {"id":"0vFOzaXqZHahrZp6enQwQb","name":"Blinding Lights","artist":"The Weeknd","artist_id":"1Xyo4u8uXC1ZmMpatF05PJ","year":"2020","image_url":"https://i.scdn.co/image/ab67616d00001e028863bc11d2aa12b54f5aeb36","yt_query":"The Weeknd - Blinding Lights official audio","album":"After Hours","duration_ms":200040},
]

_HISTORY_FILE = Path("history.json")


def _load_history_tracks() -> list[dict]:
    """Load history entries and convert to search result format."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(_HISTORY_FILE.read_text())
        if not isinstance(data, dict):
            return []
        tracks = []
        for tid, entry in data.items():
            tracks.append({
                "id": entry.get("track_id", tid),
                "name": entry.get("name", ""),
                "artist": entry.get("artist", ""),
                "artist_id": entry.get("artist_id"),
                "album": "",
                "year": entry.get("year", ""),
                "duration_ms": 0,
                "image_url": entry.get("image_url"),
                "yt_query": entry.get("yt_query", ""),
            })
        return tracks
    except Exception:
        return []


def _fallback_search(query: str, limit: int = 8) -> list[dict]:
    """
    Search local data (history + discovery) when Spotify is unavailable.
    Matches query as substring against track name and artist name.
    """
    q = query.lower().strip()
    if not q:
        return []

    # Build pool: history first (user's own songs), then discovery
    pool = _load_history_tracks() + _DISCOVERY_TRACKS

    # Dedupe by track id
    seen = set()
    unique = []
    for t in pool:
        tid = t.get("id", "")
        if tid and tid not in seen:
            seen.add(tid)
            unique.append(t)

    # Score by match quality: name match > artist match > partial
    scored = []
    for t in unique:
        name = (t.get("name") or "").lower()
        artist = (t.get("artist") or "").lower()
        score = 0
        if q in name:
            score += 10
            if name.startswith(q):
                score += 5
        if q in artist:
            score += 8
            if artist.startswith(q):
                score += 4
        # Also match individual words
        for word in q.split():
            if word in name:
                score += 2
            if word in artist:
                score += 1
        if score > 0:
            scored.append((score, t))

    scored.sort(key=lambda x: -x[0])
    results = [t for _, t in scored[:limit]]

    print(f"[fallback] query='{query}' → {len(results)} results from local data")
    return results


def _safe_search(query, limit=8):
    """Search that never throws. Respects shared cooldown."""
    import time
    if time.time() < _rate_limit_until:
        return []  # still in cooldown
    try:
        return search_spotify(query, limit)
    except RateLimitError:
        return []  # cooldown already set by search_spotify
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
    Build recommendation pools. Returns empty pools for Spotify-based categories
    (more_like_this, around_this_time) — these previously used Spotify search API
    calls that consumed rate limit quota and starved user search.

    The same_style pool is filled by Last.fm via enrich_recommendations_with_lastfm()
    in app.py, which does not use Spotify API at all.

    Returns: {
        "more_like_this": [],       -- disabled (was Spotify search)
        "same_style": [],           -- filled by Last.fm enrichment in app.py
        "around_this_time": [],     -- disabled (was Spotify search)
    }
    """
    # No Spotify API calls here. All Spotify quota is reserved for user search.
    print(f"[recs] skipping Spotify searches (quota reserved for user search)")
    print(f"[recs] same_style will be filled by Last.fm enrichment")

    return {
        "more_like_this": [],
        "same_style": [],
        "around_this_time": [],
    }
