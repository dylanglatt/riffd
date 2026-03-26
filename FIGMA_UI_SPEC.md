# Riffd — Complete UI Specification for Figma

> Exhaustive description of every page, component, state, and interaction in the Riffd web app.
> Use this as a prompt/reference to recreate the full UI in Figma.

---

## Brand & Design System

### Product Identity
- **Name:** Riffd (stylized lowercase in logo as "Riffd")
- **Tagline:** "Stems, Chords, and Tabs for Any Song"
- **Domain:** riffdlabs.com
- **Company:** Riffd Labs

### Color Palette
| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#05060a` | Page background (near-black) |
| `--bg-soft` | `#0b0d14` | Slightly lighter background |
| `--surface` | `#11131c` | Card/panel base |
| `--surface-2` | `#171926` | Elevated surface (dropdowns, inputs) |
| `--surface-3` | `#1e2133` | Tertiary surface (placeholders) |
| `--surface-4` | `#262a3e` | Quaternary surface |
| `--accent` | `#7c5cff` | Primary purple accent |
| `--accent-2` | `#5eead4` | Secondary teal/mint accent |
| `--accent-gradient` | `linear-gradient(135deg, #7c5cff, #5eead4)` | Primary gradient (logo, CTAs, play button) |
| `--text-primary` | `#f5f7ff` | Headings, primary text |
| `--text-secondary` | `#9aa0b5` | Body text, descriptions |
| `--text-muted` | `#6b7085` | Labels, captions, subtle text |
| `--red` | `#f87171` | Mute button active, errors |
| `--green` | `#4ade80` | Success states |
| `--yellow` | `#fbbf24` | Solo button active, warnings |

### Typography
- **Font Family:** Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
- **Monospace:** "SF Mono", "Cascadia Code", "Courier New", Consolas, monospace (used for tabs, formulas, code previews)
- **Font Smoothing:** -webkit-font-smoothing: antialiased
- **Headline weight:** 800–900, tight letter-spacing (-0.03em to -0.045em)
- **Body weight:** 400–500
- **Button/label weight:** 600–700

### Border Radii
| Token | Value | Usage |
|-------|-------|-------|
| `--radius` | 16px | Cards, large containers |
| `--radius-sm` | 12px | Buttons, inputs, panels |
| `--radius-xs` | 8px | Small elements, tags |
| Pill | 999px | Badges, chips, filter pills |

### Glassmorphism System
- **Background:** `rgba(17,19,28,0.65)` with `backdrop-filter: blur(24px)`
- **Border:** `rgba(255,255,255,0.06)` — 1px solid
- **Shadow:** `0 8px 32px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.03)`
- Applied to: `.card` class, results shell, and similar containers

### Ambient Background (present on every page)
- Fixed full-viewport layer behind all content
- Dark gradient background: `#06070d → #080a14 → #0a0c16 → #070912`
- Three large, blurred, slowly-animating circular "wash" shapes:
  - Wash 1: 600×500px, purple (#7c5cff), 4% opacity, top-left, 25s pulse animation
  - Wash 2: 500×400px, teal (#5eead4), 3% opacity, mid-right, 30s pulse animation
  - Wash 3: 550×450px, blue (#5c7cff), 3.5% opacity, bottom-left, 22s pulse animation
- Additionally, a radial gradient glow fixed at top-center: purple→teal→transparent at 8%/4% opacity

---

## Global Navigation (present on every page)

**Layout:** Fixed top bar, 56px height, full width
**Background:** `rgba(5,6,10,0.8)` with `backdrop-filter: blur(20px)`
**Border-bottom:** 1px solid `rgba(255,255,255,0.04)`
**Padding:** 0 28px

### Elements (left to right):
1. **Logo** — "Riffd" text in accent gradient (purple→teal), 1.1rem, weight 800, tight letter-spacing. Links to `/` (home).
2. **40px gap**
3. **Nav Links** — horizontal row with 4px gap between items:
   - **Decompose** — with a "Beta" badge (purple-tinted pill)
   - **Studio** — with a "Beta" badge (purple-tinted pill)
   - **Library** — no badge
   - **Practice** — with a "Soon" badge (muted/gray pill)
4. **Spacer** (flex:1, pushes nothing to the right — no right-side elements currently)

### Nav Link Styles:
- Default: muted text color, 0.82rem, weight 500, 8px 16px padding, 8px border-radius
- Hover: slightly brighter text, subtle 3% white background
- Active: primary text color, purple tinted background (`rgba(124,92,255,0.08)`)

### Badge Styles:
- "Beta": purple border/background tint, purple text, 0.54rem uppercase
- "Soon": muted border/background, muted text, 0.54rem uppercase

### Responsive:
- 768px: tighter padding (0 16px), smaller nav link padding/font
- 480px: even tighter, 0.74rem font

---

## Page 1: Home (Landing Page) — `/`

> **Note: Figma has already generated a landing page the user likes. This description is for reference/completeness.**

**Template:** `home.html` extends `base.html`
**Max-width:** 1100px, centered, 40px top padding

### Section 1: Hero
- **Headline:** "See inside **any song.**" — 3.6rem, weight 900. The words "any song." use the accent gradient text effect.
- **Subheadline:** "Break any song into stems, chords, and tabs — powered by AI audio analysis." — 1.12rem, secondary text color, max-width 520px, centered.
- **Two CTAs side by side (centered):**
  - "Decompose a Song" — primary gradient button, 15px 36px padding, white text, purple glow shadow. Links to `/decompose`.
  - "Learn Music Theory" — ghost/secondary button, subtle border, muted text. Links to `/learn`.

### Section 2: How It Works
- Label: "HOW IT WORKS" — centered, 0.7rem uppercase, muted, wide letter-spacing
- 3-column grid (max-width 900px):
  1. **Circle with "1"** → "Choose a song" → "Search by name or drop in an audio file. We handle the rest."
  2. **Circle with "2"** → "AI breaks it apart" → "Stems are separated, notes detected, key and chords analyzed automatically."
  3. **Circle with "3"** → "Play and learn" → "Mix stems, read tabs, loop sections, transpose keys — all in one workspace."
- Circle: 44px, purple tinted background/border, bold number

### Section 3: Feature Cards
- 3-column grid of clickable feature entry-point cards:
  1. **Decompose** — 🎹 icon → "Isolate vocals, guitar, bass, drums, and more from any track. Mix, mute, solo, and loop individual parts." → "Live" tag (teal pill). Links to `/decompose`.
  2. **Learn Theory** — 🎼 icon → "Reference guides for chords, scales, progressions, and keys. Built for guitar and piano players." → "Preview" tag. Links to `/learn`.
  3. **Practice** — 🎸 icon → "Jam tracks, scale drills, chord trainers, and progression loopers to build muscle memory." → "Coming Soon" tag. Links to `/practice`.
- Card style: very subtle background (1.5% white), border, 28px 24px padding, hover lifts up 3px with purple tint

### Section 4: Bottom CTA
- "Ready to hear what's inside your favorite song?" — secondary text
- "Get Started" gradient button → links to `/decompose`

### Footer
- Links: About, Learn, Library (centered, muted, 0.78rem)
- "Riffd by Riffd Labs" — very muted, 0.72rem

---

## Page 2: Decompose — `/decompose`

This is the **core product page** with the most complex UI. It has three mutually exclusive views that transition with a fade-up animation:

### View 1: Search View (default, `#view-search`)

#### Header
- Headline: "Decompose any song." — 2.4rem, weight 900
- Subheadline: "Separate stems, uncover structure, and play along in seconds." — 1.02rem, secondary text
- Decorative: radial glow blob behind the header (purple/teal, blurred)

#### Search Box
- Max-width 560px, centered
- Large input field: 1.05rem font, 18px padding, 48px left padding (for icon), rounded (16px)
- Search magnifying glass icon: positioned inside left of input, muted color, transitions to teal on focus
- Input background: 5% white, border 10% white
- Focus state: purple border, purple glow ring (`0 0 0 4px` + `0 0 40px`), slightly brighter background
- Placeholder: "Search any song or artist..."

#### Search Results Dropdown
- Appears below search input after typing 2+ characters (450ms debounce)
- Vertical list, 4px gap, max-height 400px with thin scrollbar
- Each result row: flex row with:
  - Album art thumbnail (44×44px, 8px border-radius)
  - Track name (0.88rem bold) + artist name (0.78rem muted) stacked
  - Year + duration right-aligned (0.72rem muted)
  - Hover: purple-tinted background, purple border
- Loading state: "Searching..." centered muted text
- Empty state: "No results found." centered muted text
- Rate-limited state: "Search available in Xs..." countdown

#### Upload Area
- Divider: "or" text with horizontal lines on each side, muted, uppercase
- Dashed-border drop zone: "Drop a file" text, muted
- Hover/drag state: purple-tinted border and background
- Hidden file input: accepts .mp3, .wav, .flac, .m4a, .ogg, .aac

#### Recents Section (conditional — only shows if user has history)
- Title: "Recents" — 0.96rem bold
- Horizontal scrolling card row:
  - Cards: 156px wide, flex-shrink:0, scroll-snap-align:start
  - Each card: album art (1:1 aspect ratio, 10px radius), song name (0.82rem bold, truncated), artist (0.72rem muted, truncated)
  - Hover: lifts 6px, scales 1.02, purple shadow glow
  - Art placeholder (no image): gradient surface-3→surface-4 background with music note SVG icon
- Custom scroll track below: 3px tall, subtle background, draggable thumb (0.12 white opacity, purple on hover/drag)
- Supports: wheel-to-horizontal-scroll, click-to-jump on track, drag thumb

#### Capabilities Section
- Title: "What Riffd does" — 0.96rem bold
- 4-column grid of capability cards:
  1. **Separate stems** — layer icon → "Vocals, drums, bass, guitar, keys — isolated from any track."
  2. **Read the structure** — analysis icon → "Key, tempo, chord progression, and harmonic analysis."
  3. **Play it back** — play icon → "Solo any stem, loop sections, transpose to any key."
  4. **Learn faster** — music note icon → "Auto-generated tabs and MIDI for every instrument."
- Each card: 24px 20px padding, icon in 36×36px purple-tinted rounded square, SVG icon in teal, card hovers with purple tint and lift
- Responsive: 2-col at 768px, 1-col at 480px

---

### View 2: Confirm + Processing View (`#view-confirm`)

This view serves **two states**: pre-process (showing the "Dive In" button) and processing (showing the animated waveform).

#### Layout: Centered column, min-height 60vh

#### Album Art Section
- Wrapper: 180×180px, centered
- Pulsing glow ring behind art: radial gradient (purple center → teal mid → transparent), `pulseGlow` animation (2.8s scale 1→1.08, opacity 0.3→0)
- Album art image: 180×180px, 16px radius, drop shadow
- Fallback placeholder: surface-3 background with faint music note SVG

#### Track Info
- **Title:** 1.2rem, weight 800 — song name
- **Subtitle:** 0.88rem muted — "Artist · Year"
- **Chips area:** flex wrap, centered (populated dynamically — genre/key/BPM pills)

#### Pre-Process State (hero-actions visible, hero-loading hidden)
- **"Dive In" button:** large gradient CTA, 14px 44px padding, 0.95rem weight 700, purple glow shadow
- **"Pick another song" link:** ghost text button below, muted, 0.76rem
- **Error message:** red text, hidden by default, appears below buttons

#### Processing State (hero-actions hidden, hero-loading visible)
- **Animated Waveform:**
  - Container: max-width 340px, 40px height, rounded
  - 50 vertical bars, each 3px wide, rounded tops
  - Background layer: 12% opacity bars bouncing with staggered CSS animations (each bar has random min/max heights and durations)
  - Foreground layer: teal-colored bars, width transitions from 0% to ~98% as processing progresses (represents progress percentage)
- **Status Text:** below waveform, 0.84rem muted, cycles through contextual loading messages every 3.2 seconds with a fade transition
  - Messages are stage-aware, grouped by pipeline phase:
    - Download: "Fetching audio source...", "Downloading the track...", etc.
    - Stems: "Separating instruments...", "Splitting the mix into stems...", etc.
    - Tabs: "Detecting pitched notes...", "Converting audio to MIDI events...", etc.
    - Analysis: "Analyzing harmonic content...", "Detecting the tonal center...", etc.
    - Enrich: "Looking up song metadata...", "Searching for lyrics...", etc.
    - Finalize: "Assembling your results...", "Almost there..."

---

### View 3: Results View (`#view-results`)

The main analysis results interface. Wrapped in a glassmorphism `.card` container.

#### Results Header
- **Track Banner:** flex row — album art (52×52px), title (0.96rem bold), subtitle (artist · year, 0.82rem muted)
- **Warning Banner** (conditional): yellow-tinted container, shown if processing had partial failures
- **Metadata Chips:** flex row of pill-shaped chips:
  - Genre chip: "Genre" label (muted) + value (teal) — from Last.fm tags
  - BPM chip: "BPM" or "Est. BPM" label + value — shown if confidence ≥ 0.15
  - Key chip: "Key" label + value (e.g., "A Minor")
  - Progression chip: "Progression" label + value (e.g., "I - IV - V - I") — shown if confidence ≥ 0.50
  - Chip style: purple-tinted background, purple border, pill shape, 0.78rem

#### Results Tab Navigation
- Horizontal tab bar with bottom border
- Three tabs: **Mix** | **Tab** | **Lyrics**
- Active tab: teal text + teal bottom border (2px)
- Inactive: muted text, transparent border
- Hover: slightly brighter text

---

#### Panel: Mix (default active)

Wrapped in a subtle background container (0.8% white).

##### Transport Bar
- **Play/Pause Button:** 48px circle, accent gradient fill, white play/pause SVG icon. Glow shadow. Hover scales 1.07. Active scales 0.94.
- **Stop Button:** 34px circle, surface-3 background, subtle border, square-with-rounded-corners icon
- **Seek Bar:** full-width range slider
  - Track: 6px tall, 5% white background, rounded
  - Thumb: 16px circle, teal, glowing teal shadow, enlarges slightly on hover
- **Time Display:** right-aligned, "0:00 / 3:42" format, 0.74rem muted, tabular-nums

##### Channel Strips
- Vertical stack of per-stem mixer channels, 1px gap
- Each channel is a CSS grid row: `120px | 1fr | 28px | 28px`
  - **Channel Name:** 0.76rem, weight 600, truncated (e.g., "Vocals", "Bass", "Guitar (Acoustic)")
  - **Volume Slider:** thin (4px) range input, teal thumb (12px circle with teal glow)
  - **Volume Label:** 0.64rem muted number (0–100)
  - **Mute Button (M):** 28×24px, 6px radius, 0.62rem bold. Active state: red-tinted background/border/glow
  - **Solo Button (S):** same size. Active state: yellow-tinted background/border/glow
- Muted channel: name becomes faded (35% opacity), volume thumb turns muted gray
- Channel hover: very subtle white background tint

##### Tools Row
- Below channels, separated by 1px border-top, flex row with 20px gap
- **Loop Group:**
  - "LOOP" label (0.66rem uppercase muted)
  - Start time input: 48px wide, dark background, centered text, "0:00"
  - "to" separator text
  - End time input: same style, placeholder "end"
  - Loop toggle button (↻ icon): 26×24px, teal active state with glow
  - Loop hint text: teal, 0.64rem (shows "0:00 – 3:42" when active)
  - Active inputs get teal-tinted border/background
- **Transpose Group:**
  - "KEY" label
  - Minus button (−)
  - Value display: 0.8rem bold teal (e.g., "+2", "-1", "0")
  - Plus button (+)
  - Transposed key name: 0.72rem teal (e.g., "B Minor") — shown when transpose ≠ 0

---

#### Panel: Tab (Coming Soon state)

- **Blurred Preview:** monospace ASCII guitar tablature text, blurred (3px), 35% opacity, behind an overlay
- Preview content shows realistic-looking tab notation with 6 string lines (e|, B|, G|, D|, A|, E|) and fret numbers
- **Overlay (centered on top of blur):**
  - "Tabs coming soon" — 0.9rem bold white
  - "Auto-generated tablature for every instrument" — 0.78rem muted

---

#### Panel: Lyrics

- **Container:** dark background (30% opacity black), border, 16px radius, 28px 32px padding, max-height 65vh with thin scrollbar
- **Section Labels:** (e.g., "[Verse]", "[Chorus]") — 0.7rem, uppercase, purple, wide letter-spacing, 70% opacity
- **Lyric Lines:** 0.88rem, 1.9 line-height, secondary text. Hover: brightens to primary text.
- **Breaks:** 16px spacer between sections
- **Empty State:** "Lyrics not available for this track." — centered, muted

#### New Song Row
- Bottom of results card, border-top, centered
- "New Song" button: ghost style, secondary text, border, hover purple-tint

---

## Page 3: Studio (Learn/Theory) — `/learn`

**Nav label:** "Studio" (with Beta badge)
**Max-width:** 1280px (wider than other pages)

### Header
- "Studio" — 1.8rem, weight 900
- "Chords, scales, progressions, and keys." — 0.88rem muted

### Search Bar
- Max-width 520px, centered, 14px 20px padding (44px left for icon)
- Search icon left-aligned inside
- Placeholder: "Search chords, scales, progressions..."
- Focus: purple border + purple glow ring

### Two-Column Layout: Sidebar + Main

#### Sidebar (180px, sticky at top:96px)
- Vertical nav links:
  1. **Chords** — music note icon (two notes connected)
  2. **Scales** — bar chart icon (ascending bars)
  3. **Progressions** — waveform/zigzag icon
  4. **Keys** — circle with crosshair icon
- Each: 9px 12px padding, 0.82rem, icon 16px at 45% opacity
- Active: primary text, purple-tinted background, icon at 75% opacity
- Responsive (768px): sidebar becomes horizontal scrollable row

#### Main Content Area

##### Filter System (per section)
- **Filter Dropdowns:** horizontal row of dropdown buttons
  - Each button: 6px 12px, 0.72rem, subtle background/border, small chevron icon
  - Active state: teal text, purple border/background
  - Shows count when filters active: "Difficulty (2)"
  - Dropdown menu: dark surface-2 background, border, shadow, max-height 240px scrollable
  - Menu items: checkbox + label, 6px 12px padding
  - Checked state: purple checkbox fill with white checkmark, brighter text
- **Active Filter Pills:** row of removable pill badges below dropdowns
  - Purple-tinted pill, teal text, small × icon
  - Hover: turns red-tinted (indicating removal)
- **"Clear all" button:** muted text, hover turns red
- **Available filters per section:**
  - Chords: Difficulty, Quality (Major/Minor/Dom7/etc.), Genre, Mood
  - Scales: Difficulty, Family (Major/Minor/Pentatonic/etc.), Genre, Mood
  - Progressions: Difficulty, Category, Function (Loop/Cadence/etc.), Genre
  - Keys: Difficulty, Mode (Major/Minor), Brightness, Genre

##### Theory Cards Grid
- `repeat(auto-fill, minmax(260px, 1fr))` — responsive grid, 12px gap
- Each card:
  - 18px padding, 16px radius, very subtle background (1.5% white)
  - **Header row:** name (0.88rem bold) + optional badge pill (e.g., "Essential", "Common")
  - **Formula/notes line:** monospace, 0.72rem, teal, 75% opacity (e.g., "C - E - G" or "1 - 2 - b3 - 4 - 5 - b7")
  - **Description:** 0.74rem muted, 1.5 line-height
  - **Extra info** (varies by section):
    - Keys: "Relative: A Minor · Bright"
    - Progressions: "Loop · 4 chords"
    - Scales: "7 notes · Major"
  - **Tags row:** tiny pills (0.6rem) for difficulty, quality, genre, mood — very subtle styling
  - Hover: purple-tinted background, purple border, lifts 2px with subtle shadow

##### Pagination
- Centered row below grid, 18 items per page
- Number buttons: 32×32px squares, 6px radius
- Active page: purple-tinted, teal text
- Arrow buttons (‹ ›): same style, disabled when at boundary
- Ellipsis for large page counts (7+ pages)
- Empty state: "No matches. Try adjusting filters or search."

### Footer
- "Riffd by Riffd Labs" — centered muted text

---

## Page 4: Library — `/library`

**Status:** Coming Soon (placeholder/preview page)

### Header
- Title row: "Library" + "COMING SOON" purple pill badge (0.6rem uppercase)
- Subtitle: "Save, organize, and revisit your analyses, stems, and practice sessions." — 0.88rem muted
- Note: "We're building this next. Here's a preview of what it will look like." — 0.76rem, 60% opacity

### Section: Favorites (static preview)
- Title: "Favorites" — 0.92rem bold
- Grid: `repeat(auto-fill, minmax(200px, 1fr))`, 14px gap
- 4 song cards (hardcoded preview data: Peg, Dreams, Gravity, Sultans of Swing):
  - "Preview" tag: absolute positioned top-right, dark semi-transparent pill, 0.56rem
  - Album art: full-width, 1:1 aspect ratio, 10px radius, slightly desaturated (85%)
  - Song name: 0.84rem weight 600, truncated
  - Artist: 0.72rem muted, truncated
  - Album: 0.68rem muted, 50% opacity, truncated
  - Cards are at 75% opacity (not fully interactive), hover to 85%
- Responsive: 2-col at 640px

### Section: Recommended for You (static preview)
- Same layout with 4 more cards (Kid Charlemagne, Rosanna, Cliffs of Dover, Sir Duke)

### Footer
- "Riffd by Riffd Labs"

---

## Page 5: Practice — `/practice`

**Status:** Coming Soon (all modules)

### Header
- "Practice" — 1.4rem weight 800
- "Structured tools to build real musicianship. Each module targets a different skill." — 0.88rem muted

### Modules Grid
- 2-column grid, 20px gap
- 4 module cards:

#### 1. Jam Tracks 🎧
- Description: "Play along with backing tracks in any key and style. Drums, bass, and rhythm guitar — you fill in the lead."
- Features list (bullet dots in teal):
  - Choose key, tempo, and style
  - Blues, rock, funk, jazz presets
  - Adjustable tempo from 60-200 BPM
  - Loop any section for focused practice
- Status badge: "Coming Soon" (purple pill)
- Preview box: dark inset container with monospace chord chart (12-bar blues in A)

#### 2. Scale Trainer 🎹
- Description: "Learn and drill scales across the fretboard. Visual patterns, audio playback, and progressive difficulty."
- Features: Major/minor/pentatonic/blues/modes, fretboard visualization, ascending/descending/random, metronome with gradual tempo
- Status: "Coming Soon"
- Preview: A Minor Pentatonic Position 1 tab notation

#### 3. Chord Trainer ♫
- Description: "Build chord vocabulary and smooth transitions. Flash cards, timed challenges, and voicing variations."
- Features: Open/barre/jazz voicings, speed drills, random flash cards, progress tracking
- Status: "Coming Soon"
- Preview: Quick Change Drill (G → C → D → Em at decreasing beat counts)

#### 4. Progression Looper 🔁
- Description: "Loop common chord progressions with a metronome click. Practice strumming, fingerpicking, and improvising over changes."
- Features: I-V-vi-IV/12-bar blues/ii-V-I, any key/tempo, visual chord countdown, export as jam track
- Status: "Coming Soon"
- Preview: I-V-vi-IV in G with bar counter

### Module Card Style:
- 32px 28px padding, 16px radius, subtle background/border
- Icon: 2rem emoji, 16px bottom margin
- Title: 1.15rem weight 800
- Feature list: teal dot prefix, 0.78rem secondary text
- Status badge: pill with either purple (coming) or teal (preview) tint
- Preview box: dark inset (40% black), border, monospace teal text, uppercase label
- Hover: purple-tinted background/border
- Responsive: 1-col at 768px

### Footer
- "Riffd by Riffd Labs"

---

## Page 6: About — `/about`

### Hero
- "How Riffd works" — 2rem, centered
- "Riffd uses AI audio analysis to break songs into individual parts, detect musical properties, and give you the tools to learn and play along. No sheet music required." — 1rem, 1.7 line-height, max-width 560px, centered

### Section: The Pipeline
- 4-column grid of numbered steps:
  1. **Input** — "Search for any song or upload an audio file directly."
  2. **Separate** — "AI isolates vocals, guitar, bass, drums, piano, and other instruments."
  3. **Analyze** — "Detect key, BPM, chord progression, and generate tablature from each stem."
  4. **Explore** — "Mix stems, read tabs, loop sections, transpose, and learn at your own pace."
- Each step: centered card, number in 36px purple circle, title bold, description muted
- Responsive: 2-col at 768px, 1-col at 480px

### Section: Technology
- 3-column grid of tech cards:
  1. **Demucs** — "Stem Separation" (teal role text) — description about Meta's transformer model
  2. **Basic Pitch** — "Note Detection" — Spotify's neural network
  3. **Krumhansl-Schmuckler** — "Key Detection" — pitch-class profiling
  4. **Web Audio API** — "Playback Engine" — browser-native audio
  5. **Diatonic Template Matching** — "Chord Analysis" — windowed pitch-class histograms
  6. **Flask + Vanilla JS** — "Application Stack" — lightweight Python + zero-dependency frontend
- Card style: 20px padding, subtle background/border, name 0.88rem bold, role 0.78rem teal, desc 0.76rem muted
- Responsive: 1-col at 768px

### Section: Principles
- Vertical list (max-width 680px), 3 principle cards:
  1. 🎷 **Built for musicians** — "Every feature is designed for people who play instruments. Not a tech demo — a tool you actually use in your practice room."
  2. 🔎 **Honest about limitations** — "AI analysis isn't perfect. We show confidence levels and suppress low-quality results rather than guessing."
  3. ⚡ **Ship and iterate** — "Core features are live. Tab accuracy, drum detection, and chord analysis are actively improving with every update."
- Each: flex row, emoji icon 1.4rem in 32px column, title bold, description muted

### Footer
- Links: Home, Decompose, Learn
- "Riffd by Riffd Labs"

---

## Shared Components Reference

### Buttons
| Type | Background | Text | Border | Shadow | Hover |
|------|-----------|------|--------|--------|-------|
| Primary | accent gradient | white | none | purple glow (0.25 opacity) | stronger glow (0.4) + lift -1px |
| Secondary | surface-2 | secondary text | 1px border-2 | none | subtle lift |
| Ghost | transparent | muted text | 1px border-2 | none | subtle lift |
| All buttons: 11px 18px padding, 0.88rem, weight 600, 12px radius, disabled at 30% opacity |

### Cards (`.card`)
- Glassmorphism: blurred semi-transparent background, subtle border, deep shadow
- 16px radius

### Album Art Fallbacks
- When image fails to load, replaced with a placeholder div:
  - Background: surface-3 (or gradient surface-3→surface-4)
  - Contains a faint music note SVG icon (20% opacity)
  - Sized to match the original image dimensions

### Footer
- 80px top margin, 1px top border (4% white)
- Optional link row: centered, 28px gap, 0.78rem muted
- Attribution: "Riffd by Riffd Labs" — 0.72rem, 60% opacity

---

## Key Interactions & Animations Summary

| Interaction | Animation |
|-------------|-----------|
| View transitions (Search→Confirm→Results) | fade-in + translateY(8px→0), 0.3s cubic-bezier(0.16,1,0.3,1) |
| Card hover (all types) | translateY(-2px to -6px), scale(1.02), border color change, shadow addition |
| Button hover | translateY(-1px), enhanced shadow |
| Button press | translateY(0) snap back |
| Play button hover | scale(1.07), enhanced glow |
| Play button press | scale(0.94) |
| Processing waveform bars | staggered bounce animation per bar (random heights/durations) |
| Processing glow | 2.8s pulse scale + opacity animation |
| Progress text cycling | 300ms opacity fade out → text swap → fade in |
| Ambient background washes | 22-30s slow scale(1→1.08) pulse |
| Theory section switch | 0.2s fade + translateY(4→0) |
| Search input focus | purple border glow ring animation |
| Seek bar thumb | teal glow enhancement on hover |

---

## Data Model (for populating dynamic content)

### Discovery/History Song Cards
```
{
  id: string (Spotify track ID),
  name: string,
  artist: string,
  year: string,
  image_url: string (album art URL),
  album: string
}
```

### Theory Cards (Chords, Scales, Progressions, Keys)
```
{
  name: string,
  formula: string (e.g., "C - E - G"),
  badge: string (e.g., "Major", "Essential"),
  desc: string,
  difficulty: "Beginner" | "Intermediate" | "Advanced",
  quality/family/category: string (varies by section),
  genres: string[],
  mood: string[],
  context: string[] (e.g., ["Open", "Barre"]),
  // Keys only:
  mode: "Major" | "Minor",
  brightness: string,
  relative: string,
  // Progressions only:
  function: string,
  chords: number
}
```

### Results Metadata Chips
```
Genre: string (from Last.fm tags)
BPM: number (with confidence threshold)
Key: string (e.g., "A Minor", "G Major")
Progression: string (e.g., "I - IV - V - I")
```

### Stem Mixer Channels
```
{
  name: string (raw key like "vocals", "guitar", "bass"),
  label: string (display name like "Lead Vocal", "Guitar (Acoustic)"),
  active: boolean
}
```
