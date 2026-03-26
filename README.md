# Riffd — Setup Guide

A music analysis and learning tool that separates songs into isolated stems,
analyzes harmonic structure (section-based chords, roman numerals, key detection),
generates tabs/MIDI, and fetches lyrics. Uses Spotify for search, yt-dlp for audio,
Demucs for stem separation, Basic Pitch for tabbing, and Genius/Last.fm for metadata.

---

## Requirements

- **Python 3.10 or 3.11** (recommended — Basic Pitch has issues on 3.12+)
- **pip** (comes with Python)
- A free **Spotify developer account** (optional — only needed for search)

---

## One-time Setup

### 1. Install Python

If you don't have Python, download it from https://python.org/downloads
Make sure to check "Add Python to PATH" during installation on Windows.

### 2. Open a terminal in the app folder

- **Mac**: Right-click the `stem-tab-app` folder → "New Terminal at Folder"
- **Windows**: Shift+right-click the folder → "Open PowerShell window here"

### 3. Install dependencies

Paste this command and press Enter:

```
pip install -r requirements.txt
```

This will take a few minutes — it's downloading the AI models (Demucs is ~150MB).

### 4. Set up Spotify search (optional but recommended)

1. Go to https://developer.spotify.com/dashboard and log in (free account)
2. Click "Create app", give it any name
3. Copy your **Client ID** and **Client Secret**
4. In the app folder, copy `.env.example` → rename it to `.env`
5. Open `.env` and paste in your credentials

Without this step, you can still use the app by uploading your own audio files —
search just won't work.

---

## Running the App

Each time you want to use it:

```
python app.py
```

Then open your browser and go to: **http://localhost:5000**

You'll need to enter the site password (set via `SITE_PASSWORD` in your `.env` file).

---

## How to Use

1. **Search** for a song using the Spotify search bar (or upload your own audio)
2. **Click a result** to select it — audio is downloaded automatically from YouTube
3. **Processing** runs automatically: stem separation → tab generation → harmonic analysis → lyrics
4. **Explore results:**
   - **Mix** — stem audio player with per-channel volume/mute/solo, seek, loop, transpose
   - **Harmony** — section-based chords with roman numerals (Verse, Chorus, etc.)
   - **Lyrics** — full lyrics with section markers
   - **Tab** — ASCII tablature (coming soon improvements)
5. **Download MIDI** files if you want to open them in GarageBand, Logic, etc.
6. **Learn** — explore music theory (chords, scales, progressions, keys) in the Studio page

---

## Notes

- Processing is done **entirely on your computer** — nothing is uploaded anywhere
- Stem separation quality is best for clearly mixed songs; dense mixes are harder
- Bass tabs tend to be most accurate; distorted guitar is the trickiest
- Harmonic analysis shows chords per song section (Verse, Chorus, etc.) with roman numerals
- Results are cached — returning to a previously processed song loads instantly
- MIDI files can be opened in GarageBand, Logic, Ableton, etc. for further editing

---

## Troubleshooting

**"demucs not found"** — Run `pip install demucs` separately

**"yt-dlp not found"** — Run `pip install yt-dlp` separately

**Processing takes forever** — Demucs is CPU-intensive. A 3-minute song
takes ~5 mins on a modern Mac. If you have a GPU it'll be much faster.

**Spotify search returns an error** — Check that your `.env` file has the
correct credentials and is in the `stem-tab-app` folder.
