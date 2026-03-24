# Stem & Tab — Setup Guide

A personal app that separates songs into isolated stems (vocals, bass, drums, guitar)
and generates tabs/MIDI for each part. Uses Spotify for search/metadata, yt-dlp
to pull audio from YouTube, Demucs for stem separation, and Basic Pitch for tabbing.

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

---

## How to Use

1. **Search** for a song using the Spotify search bar (or skip to step 3)
2. **Click a result** to select it
3. **Choose which stems** you want (vocals, bass, drums, guitar)
4. Click **"Separate & Tab"**
   - If you selected a Spotify track, it'll automatically find and download
     the audio from YouTube (~30 seconds)
   - Then Demucs runs stem separation (~2–5 minutes depending on your computer)
   - Then Basic Pitch generates tabs (~1 minute per stem)
5. **Listen** to each isolated stem and **view the tab** for each part
6. **Download MIDI** files if you want to open them in GarageBand, Logic, etc.

**Or: upload your own file** by dragging & dropping an MP3/WAV/FLAC directly.

---

## Notes

- Processing is done **entirely on your computer** — nothing is uploaded anywhere
- Stem separation quality is best for clearly mixed songs; dense mixes are harder
- Bass tabs tend to be most accurate; distorted guitar is the trickiest
- The "Guitar / Other" stem contains everything that isn't vocals, drums, or bass
- MIDI files can be opened in GarageBand, Logic, Ableton, etc. for further editing

---

## Troubleshooting

**"demucs not found"** — Run `pip install demucs` separately

**"yt-dlp not found"** — Run `pip install yt-dlp` separately

**Processing takes forever** — Demucs is CPU-intensive. A 3-minute song
takes ~5 mins on a modern Mac. If you have a GPU it'll be much faster.

**Spotify search returns an error** — Check that your `.env` file has the
correct credentials and is in the `stem-tab-app` folder.
