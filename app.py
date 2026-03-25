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
from spotify_search import search_spotify, get_recommendations_for_track, RateLimitError
from music_intelligence import analyze_song_from_notes
from external_apis import get_lyrics, get_track_tags, enrich_recommendations_with_lastfm
from downloader import download_audio_from_youtube
from history import add_to_history, get_recent, get_cached_result, save_cached_result, touch_history

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
        print(f"[search] query='{query}'")
        results = search_spotify(query)
        print(f"[search] returned {len(results)} results")
        return jsonify(results)
    except RateLimitError as e:
        print(f"[search] RATE LIMITED (retry_after={e.retry_after}s)")
        return jsonify({"error": "Too many searches. Please wait a few seconds and try again.", "retry_after": e.retry_after}), 429
    except Exception as e:
        print(f"[search] ERROR: {type(e).__name__}: {e}")
        return jsonify({"error": "Search temporarily unavailable. Please try again."}), 500


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

            # 2. Quick BPM detection from first harmonic stem
            #    (needed before tab generation for correct grid quantization)
            on_progress("Detecting tempo...")
            active_stems = {k: v for k, v in stems.items() if v.get("active", True)}
            detected_bpm = 120.0  # default fallback

            # Find a harmonic stem for BPM — prefer guitar/piano/bass over vocals/drums
            bpm_stem_key = None
            for priority_list in [["guitar", "piano", "bass", "other"], list(active_stems.keys())]:
                for k in priority_list:
                    if k in active_stems and k not in ("drums",):
                        bpm_stem_key = k
                        break
                if bpm_stem_key:
                    break

            if bpm_stem_key:
                try:
                    from basic_pitch import ICASSP_2022_MODEL_PATH
                    from basic_pitch.inference import predict as bp_predict
                    _, _, bpm_notes = bp_predict(
                        active_stems[bpm_stem_key]["path"],
                        ICASSP_2022_MODEL_PATH,
                        minimum_note_length=80,
                        minimum_frequency=40,
                        maximum_frequency=2000,
                    )
                    if bpm_notes and len(bpm_notes) > 10:
                        import pandas as pd
                        bpm_df = pd.DataFrame(bpm_notes, columns=["start_time_s", "end_time_s", "pitch_midi", "confidence", "pitch_bends"][:len(bpm_notes[0])])
                        from music_intelligence import estimate_bpm
                        est_bpm, bpm_conf = estimate_bpm(bpm_df)
                        if est_bpm > 0 and bpm_conf >= 0.15:
                            detected_bpm = est_bpm
                            print(f"[process] early BPM detection: {detected_bpm} (confidence={bpm_conf:.2f}, from {bpm_stem_key})")
                except Exception as e:
                    print(f"[process] early BPM detection failed: {e}")

            # 3. Generate tabs + collect note events (with BPM for grid)
            on_progress("Generating tabs...")
            tabs = {}
            note_events_all = {}

            for stem_key, stem_info in active_stems.items():
                label = stem_info.get("label", stem_key)
                on_progress(f"Tabbing {label}...")
                tab_result = generate_tabs(stem_info["path"], job_id, stem_key, label=label, bpm=detected_bpm)
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

            # Extract names early (needed for intelligence + lyrics + recs)
            artist_name = track_meta.get("artist", "")
            track_name = track_meta.get("name", "")

            # 4. Song intelligence (full analysis — key, BPM refinement, chords)
            on_progress("Analyzing key and progression...")
            intelligence = {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": detected_bpm, "progression": None}
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

            final_result = {
                "status": "complete",
                "tabs": tabs,
                "intelligence": intelligence,
                "lyrics": lyrics,
                "tags": tags,
                "recommendations": recs,
                "progress": "Done!",
            }
            jobs[job_id].update(final_result)

            # Save to cache + history
            if spotify_track_id:
                try:
                    # Save stems metadata (labels, energy, active) — not file paths
                    stems_meta = {}
                    for sk, sv in stems.items():
                        stems_meta[sk] = {
                            "label": sv.get("label", sk),
                            "energy": sv.get("energy", 0),
                            "active": sv.get("active", True),
                        }
                    save_cached_result(job_id, {
                        "tabs": tabs,
                        "stems": stems_meta,
                        "intelligence": intelligence,
                        "lyrics": lyrics,
                        "tags": tags,
                        "recommendations": recs,
                        "job_id": job_id,
                        "track_id": spotify_track_id,
                    })
                    add_to_history(spotify_track_id, track_meta, job_id)
                except Exception as e:
                    print(f"[cache/history] save error: {e}")

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


# ─── History endpoints ────────────────────────────────────────────────────────

@app.route("/api/history")
def history_list():
    """Get recent songs."""
    return jsonify(get_recent(8))


@app.route("/api/cache/<track_id>")
def cache_check(track_id):
    """Check if a cached result exists for a track. Returns the result or 404."""
    result = get_cached_result(track_id)
    if result:
        touch_history(track_id)  # update last_viewed on reopen
        return jsonify(result)
    return jsonify({"error": "No valid cache"}), 404


# ─── Discovery endpoint ───────────────────────────────────────────────────────

_discovery_cache = {"tracks": None, "fetched": False}

# Pre-built discovery data — no Spotify API calls needed
# These are static entries with enough data to select and process
_STATIC_DISCOVERY = [
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


@app.route("/api/discovery")
def discovery():
    """
    Return curated songs for the landing page.
    Uses static pre-built data — NO Spotify API calls. Zero rate-limit risk.
    Returns stable order to prevent browser image cache mismatches.
    """
    return jsonify(_STATIC_DISCOVERY[:8])


if __name__ == "__main__":
    print("\n  riffa running at http://localhost:5001\n")
    app.run(debug=True, port=5001, threaded=True)
