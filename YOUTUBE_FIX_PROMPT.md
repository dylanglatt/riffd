# Claude Code Prompt: Fix YouTube Full-Track Downloads on Render

Copy this entire prompt into Claude Code when YouTube downloads break again.

---

## Context

This is Riffd (riffdlabs.com), a music stem separation app deployed on Render. The YouTube download pipeline in `downloader.py` uses yt-dlp to download full songs. It frequently breaks because:

1. YouTube rolls out new signature challenges requiring a JavaScript runtime (Node.js/EJS)
2. YouTube requires PO (Proof of Origin) tokens for certain player clients
3. Render's datacenter IPs get blocked by YouTube
4. We use a Decodo/SmartProxy residential proxy (already configured via `YT_PROXY_URL` env var)
5. We have a Piped API fallback that uses public instances which go down frequently

## Current Architecture

Download waterfall in `downloader.py` → `resolve_audio()`:
1. `download_audio_from_youtube()` → yt-dlp with cookies + proxy
2. `_download_via_piped()` → Piped API (proxied YouTube, bypasses IP blocks)
3. Upload prompt (if both fail)

## What to do

### Step 1: Check Render logs for the exact error

Look at the Render logs for lines containing `[downloader]`, `YOUTUBE FAILED`, `yt-dlp failed`, `piped`, and `AUDIO SOURCE SELECTED`. The error message tells you exactly what's broken.

### Step 2: Common errors and fixes

**Error: "Signature solving failed: Ensure you have a supported JavaScript runtime"**
- yt-dlp needs Node.js to solve YouTube's obfuscated signatures
- Check if Node.js is available on Render: look for `[downloader] JS runtime: node vXX` in startup logs
- If missing: Render's Python runtime should include Node.js. If not, add to build command: `curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs && pip install -r requirements.txt`
- Or: ensure `yt-dlp-ejs` package is in requirements.txt (provides embedded JS)

**Error: "requires a GVS PO Token which was not provided"**
- YouTube now requires Proof of Origin tokens for certain player clients
- Option A: Switch to player clients that don't need PO tokens (e.g., `android`)
- Option B: Generate a PO token and set `YT_PO_TOKEN` env var on Render
  - To generate: install `yt-dlp-get-pot` browser extension, visit youtube.com, extract the token
  - Token format for env var: `web.gvs+TOKEN_VALUE_HERE`
- Option C: Install `bgutil-ytdlp-pot-provider` package (in requirements.txt) which auto-generates tokens

**Error: "Only images are available for download"**
- All audio formats were blocked. This means signature solving AND PO tokens both failed.
- Fix: ensure both JS runtime AND proper player_client are configured
- In `_run_ytdlp_with_binary()`, the `--extractor-args` line controls player clients
- Try: `youtube:player_client=android,ios,web` (android usually works without PO tokens)

**Error: "Piped instances failed" / all returning 5xx**
- Public Piped instances are unreliable and go down frequently
- The code dynamically fetches instances from `piped-instances.kavin.rocks`
- If the registry itself is down, it falls back to a hardcoded list
- Check if any Piped instances are alive: `curl https://pipedapi.kavin.rocks/search?q=test`
- You can add more instances to the `FALLBACK` list in `_get_piped_instances()`

**Error: proxy-related (502 Bad Gateway, tunnel failed)**
- The Decodo residential proxy may be down or out of bandwidth
- Check proxy dashboard at decodo.com — current plan is 3GB
- Proxy URL format: `http://USERNAME:PASSWORD@gate.decodo.com:PORT`
- Set/update via `YT_PROXY_URL` env var on Render

### Step 3: If yt-dlp is fundamentally broken (YouTube changed too much)

1. Update yt-dlp to latest: change `requirements.txt` to `yt-dlp>=2026.1` (or whatever year it is)
2. Force Render rebuild: Render → Manual Deploy → Clear Build Cache & Deploy
3. Check yt-dlp GitHub releases for known YouTube breakage: https://github.com/yt-dlp/yt-dlp/releases
4. If yt-dlp is behind on fixes, try nightly: `pip install --pre yt-dlp`

### Step 4: Nuclear option — if nothing works

If YouTube extraction is completely broken from server-side:
1. The upload flow works — users can upload their own audio files
2. Consider adding a client-side download option (browser extension or bookmarklet)
3. Consider using a paid YouTube extraction API service
4. Self-host a cobalt instance on a VPS with residential IP

## Key files

- `downloader.py` — All download logic, yt-dlp config, Piped fallback
- `app.py` — Download endpoint (`download_track()`), prefetch logic
- `templates/decompose.html` — Frontend stem separation flow, upload UI
- `requirements.txt` — yt-dlp version, yt-dlp-ejs, bgutil-ytdlp-pot-provider

## Key env vars on Render

- `YT_PROXY_URL` — Residential proxy URL (Decodo/SmartProxy)
- `YT_PO_TOKEN` — YouTube PO token (format: `web.gvs+TOKEN`)
- `YT_COOKIES_B64` — Base64 cookies (legacy, prefer Secret Files)
- Secret File: `/etc/secrets/cookies.txt` — YouTube cookies in Netscape format

## Testing

After any fix, test by searching for a song on riffdlabs.com/decompose and clicking the stem separation button. Check Render logs for:
- `[job xxx] AUDIO SOURCE SELECTED: youtube` = SUCCESS
- `[job xxx] ⚠️ yt-dlp FAILED` = yt-dlp broken
- `[piped] ✅ SUCCESS` = Piped fallback worked
- `upload_required` = everything failed
