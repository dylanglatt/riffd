"""
musicbrainz.py — MusicBrainz recording credits lookup for Riffd.

Entry point:
  get_credits(artist: str, track_name: str) -> dict | None

Returns structured credits dict or None if lookup fails.
No API key needed — just a User-Agent header.
Rate limited to 1 request/second (MusicBrainz requirement).
"""

import time
import requests

_BASE_URL = "https://musicbrainz.org/ws/2"
_HEADERS = {
    "User-Agent": "Riffd/1.0 ( dylanglatt@gmail.com )",
    "Accept": "application/json",
}
_last_request_time = 0


def _rate_limit():
    """Enforce 1 request/second to comply with MusicBrainz rate limits."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_request_time = time.time()


def _search_recording(artist: str, title: str) -> str | None:
    """Search MusicBrainz for a recording. Returns the recording MBID or None."""
    query = f'artist:"{artist}" AND recording:"{title}"'
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/recording",
            params={"query": query, "fmt": "json", "limit": 5},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            print("[musicbrainz] rate limited — skipping")
            return None
        resp.raise_for_status()
        recordings = resp.json().get("recordings", [])
        if not recordings:
            print(f"[musicbrainz] no results for: {artist} - {title}")
            return None

        artist_lower = artist.lower()
        title_lower = title.lower()
        for rec in recordings:
            rec_title = (rec.get("title") or "").lower()
            rec_artists = " ".join(
                (ac.get("name") or ac.get("artist", {}).get("name", "")).lower()
                for ac in rec.get("artist-credit", [])
            )
            if title_lower in rec_title and artist_lower in rec_artists:
                print(f"[musicbrainz] matched: {rec.get('title')} (id={rec['id'][:8]})")
                return rec["id"]

        first = recordings[0]
        print(f"[musicbrainz] fallback match: {first.get('title')} (id={first['id'][:8]})")
        return first["id"]

    except Exception as e:
        print(f"[musicbrainz] search failed: {e}")
        return None


def _get_recording_relationships(recording_id: str) -> dict | None:
    """Fetch recording with artist relationships, work relationships, and releases."""
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/recording/{recording_id}",
            params={"inc": "artist-credits+artist-rels+work-rels+releases", "fmt": "json"},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[musicbrainz] recording lookup failed: {e}")
        return None


def _get_release_details(release_id: str) -> dict | None:
    """Fetch release with label info."""
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/release/{release_id}",
            params={"inc": "labels+release-groups", "fmt": "json"},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[musicbrainz] release lookup failed: {e}")
        return None


def get_credits(artist: str, track_name: str) -> dict | None:
    """
    Main entry point. Search for a recording, fetch its relationships, and return
    structured credits data.

    Returns dict with keys:
      - producers, engineers, studios, writers, performers
      - label, release_date, release_country, album

    Returns None if lookup fails entirely.
    """
    if not artist or not track_name:
        return None

    print(f"[musicbrainz] looking up: {artist} - {track_name}")

    recording_id = _search_recording(artist, track_name)
    if not recording_id:
        return None

    data = _get_recording_relationships(recording_id)
    if not data:
        return None

    credits = {
        "producers": [],
        "engineers": [],
        "studios": [],
        "writers": [],
        "performers": [],
        "label": None,
        "release_date": None,
        "release_country": None,
        "album": None,
    }

    for rel in data.get("relations", []):
        rel_type = (rel.get("type") or "").lower()
        target_artist = rel.get("artist", {}).get("name") or rel.get("target", {}).get("name", "")
        attributes = [a.lower() for a in rel.get("attributes", [])]

        if not target_artist and rel.get("place"):
            place_name = rel["place"].get("name", "")
            if place_name and rel_type in ("recorded at", "recording location", "recorded in"):
                if place_name not in credits["studios"]:
                    credits["studios"].append(place_name)
            continue

        if not target_artist:
            continue

        if "producer" in rel_type:
            prod_type = rel_type.replace("_", " ").title()
            if not any(p["name"] == target_artist for p in credits["producers"]):
                credits["producers"].append({"name": target_artist, "type": prod_type})
        elif rel_type in ("engineer", "audio", "sound", "editor"):
            role = ", ".join(attributes) if attributes else rel_type
            if not any(e["name"] == target_artist for e in credits["engineers"]):
                credits["engineers"].append({"name": target_artist, "role": role})
        elif rel_type == "mix" or "mix" in attributes:
            if not any(e["name"] == target_artist for e in credits["engineers"]):
                credits["engineers"].append({"name": target_artist, "role": "mix"})
        elif rel_type == "mastering" or "mastering" in attributes:
            if not any(e["name"] == target_artist for e in credits["engineers"]):
                credits["engineers"].append({"name": target_artist, "role": "mastering"})
        elif rel_type in ("performer", "instrument", "vocal", "programming"):
            instrument = ", ".join(attributes) if attributes else rel_type
            if not any(p["name"] == target_artist for p in credits["performers"]):
                credits["performers"].append({"name": target_artist, "instrument": instrument})
        elif rel_type in ("writer", "composer", "lyricist", "songwriter"):
            if target_artist not in credits["writers"]:
                credits["writers"].append(target_artist)

    # Release info
    releases = data.get("releases", [])
    if releases:
        dated = [r for r in releases if r.get("date")]
        if dated:
            dated.sort(key=lambda r: r["date"])
            earliest = dated[0]
        else:
            earliest = releases[0]

        credits["album"] = earliest.get("title")
        credits["release_date"] = earliest.get("date")
        credits["release_country"] = earliest.get("country")

        release_id = earliest.get("id")
        if release_id:
            release_data = _get_release_details(release_id)
            if release_data:
                label_info = release_data.get("label-info", [])
                if label_info:
                    label_name = label_info[0].get("label", {}).get("name")
                    if label_name:
                        credits["label"] = label_name

    has_data = any([
        credits["producers"],
        credits["engineers"],
        credits["studios"],
        credits["writers"],
        credits["performers"],
        credits["label"],
        credits["release_date"],
    ])

    if not has_data:
        print(f"[musicbrainz] no credits data found for: {artist} - {track_name}")
        return None

    print(f"[musicbrainz] credits found: {sum(len(v) for v in credits.values() if isinstance(v, list))} entries")
    return credits
