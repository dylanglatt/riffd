"""
musicbrainz.py — MusicBrainz recording credits lookup for Riffd.

Entry point:
  get_credits(artist: str, track_name: str) -> dict | None

Returns structured credits dict or None if lookup fails.
No API key needed — just a User-Agent header.
Rate limited to 1 request/second (MusicBrainz requirement).
"""

import re
import time
import requests

_BASE_URL = "https://musicbrainz.org/ws/2"
_HEADERS = {
    "User-Agent": "Riffd/1.0 ( dylanglatt@gmail.com )",
    "Accept": "application/json",
}
_last_request_time = 0


# Patterns commonly appended to Spotify/streaming track names that don't exist in MusicBrainz
_TITLE_NOISE = re.compile(
    r"\s*[-–]\s*(remaster(ed)?|re-?master(ed)?)\b.*$"   # " - Remastered 2009", " - Remaster"
    r"|\s*\(remaster(ed)?[^)]*\)"                         # "(Remastered 2009)"
    r"|\s*\(re-?master(ed)?[^)]*\)"
    r"|\s*[-–]\s*(radio\s+edit|single\s+(version|edit)|album\s+version|original\s+mix)\s*$"
    r"|\s*\((radio\s+edit|single\s+(version|edit)|album\s+version|mono\s+version)\)"
    r"|\s*\(feat\.?[^)]*\)"                               # "(feat. Someone)"
    r"|\s*\(ft\.?[^)]*\)"
    r"|\s*[-–]\s*feat\.?\s+.+$",                          # " - feat. Someone"
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """Strip Spotify-style suffixes that won't match MusicBrainz titles."""
    return _TITLE_NOISE.sub("", title).strip()


def _rate_limit():
    """Enforce 1 request/second to comply with MusicBrainz rate limits."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_request_time = time.time()


def _search_recording(artist: str, title: str) -> str | None:
    """Search MusicBrainz for a recording. Returns the recording MBID or None."""
    clean = _clean_title(title)
    query = f'artist:"{artist}" AND recording:"{clean}"'
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
            print(f"[musicbrainz] no results for: {artist} - {clean}")
            return None

        artist_lower = artist.lower()
        clean_lower = clean.lower()
        for rec in recordings:
            rec_title = (rec.get("title") or "").lower()
            rec_artists = " ".join(
                (ac.get("name") or ac.get("artist", {}).get("name", "")).lower()
                for ac in rec.get("artist-credit", [])
            )
            # Match if the cleaned title appears in the recording title (or vice versa)
            title_match = clean_lower in rec_title or rec_title in clean_lower
            if title_match and artist_lower in rec_artists:
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
            params={"inc": "artist-credits+artist-rels+work-rels+releases+release-groups", "fmt": "json"},
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


def _get_work_writers(work_id: str) -> list[str]:
    """Fetch a work's artist relationships to extract composers/lyricists/writers."""
    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/work/{work_id}",
            params={"inc": "artist-rels", "fmt": "json"},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 503:
            return []
        resp.raise_for_status()
        writers = []
        for rel in resp.json().get("relations", []):
            rel_type = (rel.get("type") or "").lower()
            if rel_type in ("composer", "lyricist", "writer", "songwriter"):
                name = rel.get("artist", {}).get("name")
                if name and name not in writers:
                    writers.append(name)
        return writers
    except Exception as e:
        print(f"[musicbrainz] work lookup failed: {e}")
        return []


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


def get_credits(artist: str, track_name: str, spotify_album: str = "", spotify_year: str = "") -> dict | None:
    """
    Main entry point. Search for a recording, fetch its relationships, and return
    structured credits data.

    spotify_album / spotify_year: if provided, these are used directly instead of
    deriving album/date from MusicBrainz releases (Spotify is authoritative for this).

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

    # Writers/composers live on the linked Work, not the recording — follow work-rels
    if not credits["writers"]:
        for rel in data.get("relations", []):
            if rel.get("target-type") == "work" and rel.get("work"):
                work_id = rel["work"].get("id")
                if work_id:
                    writers = _get_work_writers(work_id)
                    for w in writers:
                        if w not in credits["writers"]:
                            credits["writers"].append(w)
                    if credits["writers"]:
                        break  # Stop after first work — typically one per recording

    # Album + release date: Spotify is authoritative — use it when available.
    # Only fall back to MusicBrainz releases if Spotify didn't provide album info.
    if spotify_album:
        credits["album"] = spotify_album
        if spotify_year:
            credits["release_date"] = spotify_year

        # Still fetch label from MusicBrainz (Spotify doesn't expose it on track objects)
        releases = data.get("releases", [])
        if releases:
            def _release_score(r):
                rg = r.get("release-group") or {}
                primary = (rg.get("primary-type") or "").lower()
                secondary = [t.lower() for t in rg.get("secondary-types") or []]
                status = (r.get("status") or "").lower()
                score = 0
                if primary == "album":                             score += 20
                elif primary in ("single", "ep"):                  score += 5
                if "live" in secondary or primary == "live":       score -= 30
                if "compilation" in secondary or primary == "compilation": score -= 20
                if "soundtrack" in secondary:                      score -= 10
                if status == "official":                           score += 10
                return score

            best = sorted(releases, key=lambda r: (-_release_score(r), r.get("date") or "9999"))[0]
            release_id = best.get("id")
            if release_id:
                release_data = _get_release_details(release_id)
                if release_data:
                    label_info = release_data.get("label-info", [])
                    if label_info:
                        label_name = label_info[0].get("label", {}).get("name")
                        if label_name:
                            credits["label"] = label_name
    else:
        # No Spotify data — derive everything from MusicBrainz releases
        releases = data.get("releases", [])
        if releases:
            def _release_score(r):
                rg = r.get("release-group") or {}
                primary = (rg.get("primary-type") or "").lower()
                secondary = [t.lower() for t in rg.get("secondary-types") or []]
                status = (r.get("status") or "").lower()
                score = 0
                if primary == "album":                             score += 20
                elif primary in ("single", "ep"):                  score += 5
                if "live" in secondary or primary == "live":       score -= 30
                if "compilation" in secondary or primary == "compilation": score -= 20
                if "soundtrack" in secondary:                      score -= 10
                if status == "official":                           score += 10
                return score

            best = sorted(releases, key=lambda r: (-_release_score(r), r.get("date") or "9999"))[0]
            credits["album"] = best.get("title")
            credits["release_date"] = best.get("date")
            credits["release_country"] = best.get("country")

            release_id = best.get("id")
            if release_id:
                release_data = _get_release_details(release_id)
                if release_data:
                    label_info = release_data.get("label-info", [])
                    if label_info:
                        label_name = label_info[0].get("label", {}).get("name")
                        if label_name:
                            credits["label"] = label_name

    # Only the fields we actually display need to have data
    has_data = any([
        credits["writers"],
        credits["label"],
        credits["album"],
        credits["release_date"],
    ])

    if not has_data:
        print(f"[musicbrainz] no credits data found for: {artist} - {track_name}")
        return None

    print(f"[musicbrainz] credits found: {sum(len(v) for v in credits.values() if isinstance(v, list))} entries")
    return credits
