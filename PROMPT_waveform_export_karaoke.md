# Claude Code Prompt: Real Waveform + Stem Export + Karaoke Mode

## Three independent features. None interact with each other. All build on existing code.

---

## Part 1: Real Waveform from Audio Buffer

### Problem
`_buildPlayerWaveform()` in `decompose.html` generates random bar heights (`Math.random() * 36`). The actual audio buffer is already decoded and available in `stemBuffers`. We should draw bars proportional to the real audio amplitude.

### File: `templates/decompose.html`

### What to change

Replace the bar generation loop inside `_buildPlayerWaveform()` (around line 2060). The current code is:

```javascript
function _buildPlayerWaveform() {
  const bg = document.getElementById('player-waveform-bg');
  const fg = document.getElementById('player-waveform-fg-inner');
  if (!bg) return;
  const wrap = document.getElementById('player-waveform-wrap');
  const w = (wrap && wrap.offsetWidth > 0) ? wrap.offsetWidth : 600;
  const barCount = Math.floor(w / 4);
  let bgHtml = '', fgHtml = '';
  for (let i = 0; i < barCount; i++) {
    const h = 6 + Math.random() * 36;
    const style = `height:${h}px`;
    bgHtml += `<div class="player-wv-bar" style="${style}"></div>`;
    fgHtml += `<div class="player-wv-bar" style="${style}"></div>`;
  }
  bg.innerHTML = bgHtml;
  fg.innerHTML = fgHtml;
```

Replace with this approach:

```javascript
function _buildPlayerWaveform() {
  const bg = document.getElementById('player-waveform-bg');
  const fg = document.getElementById('player-waveform-fg-inner');
  if (!bg) return;
  const wrap = document.getElementById('player-waveform-wrap');
  const w = (wrap && wrap.offsetWidth > 0) ? wrap.offsetWidth : 600;
  const barCount = Math.floor(w / 4);

  // Get amplitude data from the first available audio buffer
  let amplitudes = null;
  const bufferKeys = Object.keys(stemBuffers);
  if (bufferKeys.length > 0) {
    // If we have a "preview" buffer use that, otherwise merge all stem buffers
    const primaryKey = stemBuffers['preview'] ? 'preview' : bufferKeys[0];
    const buf = stemBuffers[primaryKey];
    if (buf && buf.getChannelData) {
      const raw = buf.getChannelData(0); // left channel
      const samplesPerBar = Math.floor(raw.length / barCount);
      amplitudes = new Float32Array(barCount);
      for (let i = 0; i < barCount; i++) {
        const start = i * samplesPerBar;
        const end = Math.min(start + samplesPerBar, raw.length);
        let sum = 0;
        for (let j = start; j < end; j++) {
          sum += Math.abs(raw[j]);
        }
        amplitudes[i] = sum / (end - start); // average absolute amplitude
      }
    }
  }

  // Normalize amplitudes to 0-1 range
  let maxAmp = 0;
  if (amplitudes) {
    for (let i = 0; i < amplitudes.length; i++) {
      if (amplitudes[i] > maxAmp) maxAmp = amplitudes[i];
    }
  }

  let bgHtml = '', fgHtml = '';
  for (let i = 0; i < barCount; i++) {
    let h;
    if (amplitudes && maxAmp > 0) {
      h = 4 + (amplitudes[i] / maxAmp) * 38; // 4px min, 42px max
    } else {
      h = 6 + Math.random() * 36; // fallback to random if no buffer yet
    }
    const style = `height:${h}px`;
    bgHtml += `<div class="player-wv-bar" style="${style}"></div>`;
    fgHtml += `<div class="player-wv-bar" style="${style}"></div>`;
  }
  bg.innerHTML = bgHtml;
  fg.innerHTML = fgHtml;
```

**Keep the rest of the function exactly as-is** — the touch/mouse scrubbing code that follows the bar generation must not change.

### What NOT to change
- The scrubbing/seek logic below the bar generation
- The CSS for `.player-wv-bar` — it already handles the bar styling
- The places where `_buildPlayerWaveform()` is called — it's already called whenever audio loads (line ~863, ~1981, ~2057)

---

## Part 2: Stem Export (Download Buttons)

### Two changes needed: one backend route, one frontend button.

### File: `app.py`

Add a new route right after the existing `/api/audio/<job_id>/<stem_name>` route (after line ~1184):

```python
@app.route("/api/download_stem/<job_id>/<stem_name>")
def download_stem_audio(job_id, stem_name):
    """Download a separated stem as an audio file (with Content-Disposition: attachment)."""
    stems_dir = OUTPUT_DIR / job_id / "stems"
    wav_path = stems_dir / f"{stem_name}.wav"
    mp3_path = stems_dir / f"{stem_name}.mp3"
    if wav_path.exists():
        return send_from_directory(str(stems_dir), f"{stem_name}.wav", as_attachment=True)
    elif mp3_path.exists():
        return send_from_directory(str(stems_dir), f"{stem_name}.mp3", as_attachment=True)
    return jsonify({"error": "Stem file not found"}), 404
```

This follows the exact same pattern as the existing `/api/download_midi/<job_id>/<stem_name>` route at line 1187.

### File: `templates/decompose.html`

**In `_addStemChannel()` (around line 839):**

Add a download button after the solo button in the channel innerHTML. Find this line:

```javascript
channel.innerHTML = `<div class="channel-name">${esc(label)}</div><div class="channel-volume"><input type="range" class="vol-slider" min="0" max="100" value="100" oninput="setVolume('${stemName}',this.value)" id="vol-${stemName}"/><span class="vol-label" id="vol-label-${stemName}">100</span></div><button class="ch-btn" id="mute-${stemName}" onclick="toggleMute('${stemName}')">M</button><button class="ch-btn" id="solo-${stemName}" onclick="toggleSolo('${stemName}')">S</button>`;
```

Replace with (adds a download button at the end):

```javascript
channel.innerHTML = `<div class="channel-name">${esc(label)}</div><div class="channel-volume"><input type="range" class="vol-slider" min="0" max="100" value="100" oninput="setVolume('${stemName}',this.value)" id="vol-${stemName}"/><span class="vol-label" id="vol-label-${stemName}">100</span></div><button class="ch-btn" id="mute-${stemName}" onclick="toggleMute('${stemName}')">M</button><button class="ch-btn" id="solo-${stemName}" onclick="toggleSolo('${stemName}')">S</button><button class="ch-btn stem-dl-btn" onclick="_downloadStem('${stemName}')" title="Download stem"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>`;
```

**Also in `loadStemAudio()` (around line 2033) — this renders channels for cached deep analysis:**

Find the channel rendering loop. Inside the `for(const n of names)` loop (around line 2038), the channels are created. Find where the channel HTML is constructed and add the same download button after the solo button. The exact code looks like:

```javascript
for(const n of names){
    stemVolumes[n]=1;stemMuted[n]=false;stemSoloed[n]=false;
    const g=audioCtx.createGain();g.connect(audioCtx.destination);stemGains[n]=g;
```

There should also be channel DOM creation in this path. Search for where `mixer-channels` innerHTML is built for the full results view (in `renderResults()` around line 1786). Look for the code that builds channel HTML for each stem and add the download button there too. The pattern is the same — add the download button SVG after the solo button.

**Add the download function** right after `_addStemChannel()`:

```javascript
function _downloadStem(stemName) {
  if (!currentJobId) return;
  const a = document.createElement('a');
  a.href = `/api/download_stem/${currentJobId}/${stemName}`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
```

**Update the `.channel` CSS grid** to accommodate the new button. Find the `.channel` CSS rule (around line 218):

```css
.channel { display:grid; grid-template-columns:120px 1fr auto auto; ... }
```

Change to add one more `auto` column:

```css
.channel { display:grid; grid-template-columns:120px 1fr auto auto auto; ... }
```

**Add CSS for the download button** — add this near the existing `.ch-btn` styles:

```css
.stem-dl-btn { opacity:0.4; transition:opacity 0.15s; }
.stem-dl-btn:hover { opacity:0.8; }
```

---

## Part 3: Karaoke Mode

### A toggle button that mutes the vocal stem and keeps everything else.

### File: `templates/decompose.html`

**Add a karaoke state variable** near the other state variables (around line 633-650):

```javascript
let _karaokeActive = false;
```

**Add the toggle function** near the other audio control functions (after `toggleSolo` around line 2193):

```javascript
function toggleKaraoke() {
  _karaokeActive = !_karaokeActive;
  const btn = document.getElementById('karaoke-btn');
  if (btn) btn.classList.toggle('karaoke-active', _karaokeActive);

  // Find vocal stem(s) — could be "vocals", "Vocals", "vocal", etc.
  const vocalKeys = Object.keys(stemGains).filter(k =>
    k.toLowerCase().includes('vocal')
  );

  for (const k of vocalKeys) {
    stemMuted[k] = _karaokeActive;
    const muteBtn = document.getElementById(`mute-${k}`);
    const chEl = document.getElementById(`ch-${k}`);
    if (muteBtn) muteBtn.classList.toggle('mute-active', _karaokeActive);
    if (chEl) chEl.classList.toggle('muted', _karaokeActive);
  }

  updateGains();
}
```

**Add the karaoke button to the mix tools bar.** Find the `.mix-tools` div (around line 581-584):

```html
<div class="mix-tools">
  <div class="tool-grp"><span class="tool-lbl">Loop</span>...
  <div class="tool-grp"><span class="tool-lbl">Key</span>...
</div>
```

Add a third tool group after the Key group, before the closing `</div>` of `.mix-tools`:

```html
<div class="tool-grp"><button class="tool-btn karaoke-btn" id="karaoke-btn" onclick="toggleKaraoke()" title="Karaoke mode — mute vocals"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg><span class="tool-lbl" style="margin-left:6px">Karaoke</span></button></div>
```

**Add CSS for the karaoke button.** Add near the other tool button styles:

```css
.karaoke-btn { display:flex; align-items:center; gap:4px; padding:4px 10px; border:1px solid rgba(255,255,255,0.06); background:transparent; color:rgba(245,245,245,0.5); cursor:pointer; transition:all 0.15s; border-radius:0; }
.karaoke-btn:hover { color:rgba(245,245,245,0.8); border-color:rgba(255,255,255,0.12); }
.karaoke-btn.karaoke-active { color:#D4691F; border-color:rgba(212,105,31,0.4); background:rgba(212,105,31,0.08); }
```

**Reset karaoke state in `resetApp()`** (around line 2203). Add `_karaokeActive=false;` to the reset chain.

**Reset karaoke state in `selectTrack()`** (around line 988). Add `_karaokeActive=false;` to the state reset.

---

## Verification Checklist

After all changes:

1. **Waveform**: `_buildPlayerWaveform` should reference `stemBuffers` and call `getChannelData(0)`. Grep for `Math.random` in `_buildPlayerWaveform` — it should only appear in the fallback path (when no buffer is available).

2. **Stem export**: Grep for `/api/download_stem/` in `app.py` — should exist as a route. Grep for `_downloadStem` in `decompose.html` — should exist as a function. Grep for `stem-dl-btn` in `decompose.html` — should appear in `_addStemChannel` and CSS.

3. **Karaoke**: Grep for `toggleKaraoke` in `decompose.html` — should exist as a function. Grep for `karaoke-btn` — should appear in HTML and CSS. Grep for `_karaokeActive` — should appear in variable declaration, `toggleKaraoke()`, `resetApp()`, and `selectTrack()`.

4. **No regressions**: The existing `playAll()`, `updateGains()`, `toggleMute()`, `toggleSolo()` functions must not be modified. The scrubbing code inside `_buildPlayerWaveform()` must not be modified.

---

## Files Modified (summary)

| File | What Changed | Why |
|------|-------------|-----|
| `templates/decompose.html` | `_buildPlayerWaveform()` reads real amplitude from audio buffer | Real waveform instead of random bars |
| `templates/decompose.html` | Download button added to `_addStemChannel()` channel HTML | Stem export |
| `templates/decompose.html` | New `_downloadStem()` function | Triggers browser download of stem file |
| `templates/decompose.html` | `.channel` grid updated to 5 columns | Accommodate download button |
| `templates/decompose.html` | New `toggleKaraoke()` function + karaoke button in mix tools | Karaoke mode |
| `templates/decompose.html` | `_karaokeActive` state + reset in `resetApp()`/`selectTrack()` | Karaoke state management |
| `app.py` | New `/api/download_stem/<job_id>/<stem_name>` route | Serve stem files as downloads |
