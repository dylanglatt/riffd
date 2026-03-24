"""
app.py — Flask web server.
"""

import uuid
import threading
import traceback
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

from processor import separate_stems, generate_tabs
from spotify_search import search_spotify, get_recommendations_for_track
from music_intelligence import analyze_song_from_notes
from external_apis import get_lyrics, get_track_tags, enrich_recommendations_with_lastfm
from downloader import download_audio_from_youtube

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
jobs = {}


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/spotify/search")
def spotify_search_route():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "No query provided"}), 400
    try:
        return jsonify(search_spotify(query))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def download_track():
    data = request.json
    query = data.get("query")
    url = data.get("url")
    if not query and not url:
        return jsonify({"error": "Provide 'query' or 'url'"}), 400
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "downloading", "progress": "Starting download..."}

    def run():
        try:
            audio_path = download_audio_from_youtube(query or url, job_id)
            jobs[job_id].update({"status": "ready", "audio_path": str(audio_path), "progress": "Download complete"})
        except Exception as e:
            jobs[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename or not allowed_file(f.filename):
        return jsonify({"error": "Unsupported file type."}), 400
    job_id = str(uuid.uuid4())[:8]
    filename = secure_filename(f.filename)
    save_path = UPLOAD_DIR / job_id / filename
    save_path.parent.mkdir(parents=True, exist_ok=True)
    f.save(save_path)
    jobs[job_id] = {"status": "ready", "audio_path": str(save_path), "progress": "File uploaded"}
    return jsonify({"job_id": job_id, "filename": filename})


@app.route("/api/process/<job_id>", methods=["POST"])
def process_audio(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Unknown job ID"}), 404
    job = jobs[job_id]
    if job["status"] != "ready":
        return jsonify({"error": f"Job not ready (status: {job['status']})"}), 400
    audio_path = job.get("audio_path")
    if not audio_path:
        return jsonify({"error": "No audio file"}), 400

    req_data = request.json or {}
    spotify_track_id = req_data.get("track_id")
    spotify_artist_id = req_data.get("artist_id")
    track_meta = req_data.get("track_meta", {})

    jobs[job_id]["status"] = "processing"
    jobs[job_id]["progress"] = "Separating stems..."

    def run():
        try:
            def on_progress(msg):
                jobs[job_id]["progress"] = msg

            # 1. Separate stems
            stems = separate_stems(audio_path, job_id, progress_callback=on_progress)
            jobs[job_id]["stems"] = stems

            # 2. Generate tabs + collect note events
            on_progress("Generating tabs...")
            tabs = {}
            note_events_all = {}
            active_stems = {k: v for k, v in stems.items() if v.get("active", True)}

            for stem_key, stem_info in active_stems.items():
                label = stem_info.get("label", stem_key)
                on_progress(f"Tabbing {label}...")
                tab_result = generate_tabs(stem_info["path"], job_id, stem_key, label=label)
                tabs[stem_key] = tab_result
                csv_path = tab_result.get("notes_csv")
                if csv_path:
                    try:
                        import pandas as pd
                        df = pd.read_csv(csv_path)
                        if len(df) > 0:
                            note_events_all[stem_key] = df
                    except Exception:
                        pass

            # 3. Song intelligence
            on_progress("Analyzing key and progression...")
            intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 120, "progression": "Unknown"}
            if note_events_all:
                try:
                    intelligence = analyze_song_from_notes(
                        note_events_all,
                        song_name=track_name,
                        artist=artist_name,
                    )
                    print(f"[intel] key={intelligence['key']}, bpm={intelligence['bpm']}, prog={intelligence['progression']}, source={intelligence.get('progression_source')}")
                except Exception as e:
                    print(f"[intel] FAILED: {e}")
                    traceback.print_exc()

            # 4. Lyrics (Genius)
            lyrics = None
            artist_name = track_meta.get("artist", "")
            track_name = track_meta.get("name", "")
            if artist_name and track_name:
                on_progress("Fetching lyrics...")
                try:
                    lyrics = get_lyrics(artist_name, track_name)
                    print(f"[lyrics] {'found' if lyrics else 'not found'} ({len(lyrics) if lyrics else 0} chars)")
                except Exception as e:
                    print(f"[lyrics] FAILED: {e}")

            # 5. Tags (Last.fm)
            tags = []
            if artist_name and track_name:
                try:
                    tags = get_track_tags(artist_name, track_name)
                    print(f"[tags] {tags}")
                except Exception:
                    pass

            # 6. Recommendations (Spotify + Last.fm enrichment)
            recs = {"more_like_this": [], "same_style": [], "around_this_time": []}
            if spotify_track_id:
                on_progress("Finding recommendations...")
                try:
                    recs = get_recommendations_for_track(
                        track_id=spotify_track_id,
                        artist_id=spotify_artist_id or track_meta.get("artist_id"),
                        track_name=track_name,
                        artist_name=artist_name,
                        year=track_meta.get("year", ""),
                        detected_key=intelligence.get("key", ""),
                        detected_bpm=intelligence.get("bpm", 120),
                    )
                except Exception as e:
                    print(f"[recs spotify] FAILED: {e}")
                    traceback.print_exc()

                # Enrich with Last.fm
                if artist_name and track_name:
                    try:
                        recs = enrich_recommendations_with_lastfm(recs, artist_name, track_name)
                    except Exception as e:
                        print(f"[recs lastfm] FAILED: {e}")

                for k, v in recs.items():
                    print(f"[recs] {k}: {len(v)} tracks")

            jobs[job_id].update({
                "status": "complete",
                "tabs": tabs,
                "intelligence": intelligence,
                "lyrics": lyrics,
                "tags": tags,
                "recommendations": recs,
                "progress": "Done!",
            })

        except Exception as e:
            print(f"[process] FATAL: {e}")
            traceback.print_exc()
            jobs[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "processing", "job_id": job_id})


@app.route("/api/status/<job_id>")
def job_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Unknown job ID"}), 404
    return jsonify(jobs[job_id])


@app.route("/api/audio/<job_id>/<stem_name>")
def serve_stem_audio(job_id, stem_name):
    return send_from_directory(str(OUTPUT_DIR / job_id / "stems"), f"{stem_name}.wav")


@app.route("/api/download_midi/<job_id>/<stem_name>")
def download_midi(job_id, stem_name):
    return send_from_directory(str(OUTPUT_DIR / job_id / "tabs"), f"{stem_name}.mid", as_attachment=True)


# ─── Discovery endpoint ───────────────────────────────────────────────────────

CURATED_SONGS = [
    ("Eagles", "Take It Easy"),
    ("Eagles", "Hotel California"),
    ("Fleetwood Mac", "Go Your Own Way"),
    ("Led Zeppelin", "Stairway to Heaven"),
    ("Pink Floyd", "Wish You Were Here"),
    ("Queen", "Bohemian Rhapsody"),
    ("Michael Jackson", "Billie Jean"),
    ("Stevie Wonder", "Superstition"),
    ("Daft Punk", "Get Lucky"),
    ("Adele", "Rolling in the Deep"),
    ("Adele", "Someone Like You"),
    ("Coldplay", "Clocks"),
    ("The Weeknd", "Blinding Lights"),
    ("Childish Gambino", "Redbone"),
    ("Creedence Clearwater Revival", "Have You Ever Seen the Rain"),
    ("America", "Ventura Highway"),
    ("The Beatles", "Let It Be"),
    ("Elton John", "Tiny Dancer"),
    ("Tom Petty", "Free Fallin'"),
    ("The Rolling Stones", "Wild Horses"),
    ("The Allman Brothers Band", "Ramblin' Man"),
    ("James Taylor", "Mexico"),
    ("Nirvana", "Come as You Are"),
    ("Oasis", "Wonderwall"),
    ("Arctic Monkeys", "Do I Wanna Know?"),
    ("John Mayer", "Slow Dancing in a Burning Room"),
    ("Radiohead", "Creep"),
    ("Tracy Chapman", "Fast Car"),
    ("Prince", "Purple Rain"),
    ("The Killers", "Mr. Brightside"),
]

_discovery_cache = {"tracks": None, "fetched": False}


@app.route("/api/discovery")
def discovery():
    """
    Return enriched curated songs for the landing page.
    Fetches from Spotify on first call, caches for subsequent requests.
    Returns a shuffled subset of 8.
    """
    import random

    if not _discovery_cache["fetched"]:
        enriched = []
        for artist, title in CURATED_SONGS:
            try:
                results = search_spotify(f"artist:{artist} {title}", limit=1)
                if results:
                    t = results[0]
                    # Verify it's actually the right song (basic check)
                    if artist.lower().split()[0] in t.get("artist", "").lower():
                        enriched.append(t)
            except Exception:
                pass
        _discovery_cache["tracks"] = enriched
        _discovery_cache["fetched"] = True
        print(f"[discovery] enriched {len(enriched)}/{len(CURATED_SONGS)} curated songs")

    tracks = _discovery_cache["tracks"] or []
    if not tracks:
        return jsonify([])

    # Shuffle and return 8
    pool = list(tracks)
    random.shuffle(pool)
    return jsonify(pool[:8])


if __name__ == "__main__":
    print("\n  riffa running at http://localhost:5001\n")
    app.run(debug=True, port=5001, threaded=True)
