Now I can see the exact Figma style. This is a very different visual language from your live site — much more restrained and editorial. Let me write a prompt that explicitly anchors to this mockup's DNA.

---

Here's your updated Figma prompt:

---

**You previously designed a landing page for Riffd that I want to use as the design system for every page. Here is a recap of that design language — follow it exactly. Do NOT reference or pull from any live website. These are your design foundations:**

**DESIGN SYSTEM (extracted from your landing page mockup):**

- **Background:** Pure dark — not blue-black, not navy. A flat, deep charcoal-black (#1a1a1a to #111111 range). No colored ambient glows, no gradient washes, no floating blurred shapes. Just clean, dark negative space.
- **Typography:** A clean grotesque sans-serif (appears to be Inter or similar). Extremely large, high-contrast headlines — "Analyze any song." is massive (~72-80px), regular weight (400-500, NOT bold/black). Subtitles are a warm medium gray, ~18px, regular weight, generous line-height. The overall typographic feel is editorial and confident — big type, lots of breathing room.
- **Accent color:** A warm orange/amber (#E8873A or similar). Used sparingly — only on icons and the occasional highlight accent within waveform visualizations. NOT used for gradients, glows, or large fills. It's a subtle signature, not a dominant color.
- **Primary button:** Solid off-white/cream fill (#F5F0E8 or similar) with dark text. Rectangular with slight rounding (~8px). No gradients, no glows, no shadows. Clean and high-contrast against the dark background.
- **Secondary button:** Transparent/ghost with a light border (1px, ~30% white) and white text. Same shape as primary.
- **Cards and containers:** Very subtle — thin 1px borders (~15-20% white opacity) on dark backgrounds. No blur, no glassmorphism, no visible background fills. The borders are doing all the work. Cards feel like wireframe-elegant, not glassy.
- **Waveform/audio visualizations:** Thin vertical lines in gray/white with occasional orange accent lines. Enclosed in bordered containers with the "window chrome" treatment (three dots top-left simulating a macOS window).
- **Info cards (Key, Tempo, Time):** Simple bordered rectangles. Small uppercase label in gray at top, large bold value below in white. No fills, no icons — just type and a border.
- **Feature icons:** Thin stroke-style icons in orange/amber. Small (~20px). Placed above short bold titles with gray descriptions below. Generous vertical spacing between icon → title → description.
- **Spacing philosophy:** Extremely generous. Huge vertical padding between sections (120px+). Content doesn't fill the space — it breathes. The design feels spacious and intentional, like a luxury product page.
- **Nav bar:** "riffd" in lowercase white, left-aligned. Nav links right-aligned: "Features", "Product", "Sign In" in medium gray. Separated from content by a thin horizontal rule. Minimal, no background fill.
- **Overall personality:** This looks like a Linear, Stripe, or Arc-style landing page. Editorial. Monochromatic with one warm accent. Typography-driven. Extremely clean. No visual noise.

---

**Now design the following pages using this exact visual language. Every card, button, input, icon, and container should feel like it belongs on the landing page you already made.**

**Global nav** (every page): "riffd" lowercase left, nav links right (Decompose, Studio, Library, Practice). Thin bottom border. If a page has a "Beta" or "Coming Soon" status, show it as a tiny plain-text label next to the nav link in muted gray — not a colored pill badge.

---

**Page: Decompose** — 3 separate frames.

*Frame 1 — Search:* Centered narrow layout with generous top padding. Large headline "Decompose any song." in the same massive, regular-weight type style as "Analyze any song." from the landing page. Subtitle in warm gray below. A search input — thin bordered rectangle, no background fill, placeholder text "Search any song or artist...", small magnifying glass icon inside left in gray. Below: a thin horizontal rule with the word "or" centered. Below that: a dashed-border upload area, just text "Drop a file" in gray. Further down, a "Recents" section label, then a horizontal row of small song cards (~156px wide): each card is a square image placeholder with a thin border, song name in white below, artist in gray below that. At bottom, a "What Riffd does" row of 4 feature items — each has a small orange stroke icon, a bold white title (Separate stems / Read the structure / Play it back / Learn faster), and a one-line gray description. Same layout as the Isolate/Understand/Play section from the landing page.

*Frame 2 — Processing:* Centered vertically with lots of negative space. A large square album art placeholder (180px, thin border). Song title in large white type below, artist in gray. A row of small info badges — use the same style as the Key/Tempo/Time cards from the landing page but smaller and inline (bordered rectangles with uppercase gray label + white value). Below that, a waveform progress visualization — use the same visual language as the waveform on the landing page (thin vertical lines in a bordered container, with occasional orange accent lines). A gray status line below: "Separating instruments..."

*Frame 3 — Results:* A large bordered container (thin border, no fill). Top section: small album art thumbnail (52px, bordered) next to song title (bold white) and artist (gray), inline. Below: a row of metadata badges — same bordered-rectangle style (Genre, BPM, Key, Progression — uppercase gray labels with white values). A tab bar below: three text labels "Mix | Tab | Lyrics" — active tab in white, inactive in gray, active has a thin underline (white or orange, your choice). **Mix panel:** A transport bar with a play button (could be the off-white/cream filled circle or rectangle with a dark play icon), a stop button (bordered ghost style), a thin horizontal seek bar (gray track, orange or white thumb dot), and a time readout in monospace gray. Below: a vertical stack of channel strips — each row has a stem name in white (e.g., "Vocals", "Bass"), a thin horizontal volume slider (same style as seek bar), a small "M" button and "S" button (thin bordered squares, ~24px). Below the channels: a tools row with "LOOP" label + two small bordered text inputs (start/end times) + a toggle icon, and "KEY" label + minus/plus buttons + value display. **Tab panel:** A bordered container showing blurred monospace guitar tablature behind a centered "Tabs coming soon" label and gray subtitle. **Lyrics panel:** Section labels in uppercase gray ("VERSE", "CHORUS"), lyric lines in medium gray with generous line-height. At the bottom of the whole results card: a "New Song" ghost button (bordered rectangle, white text).

---

**Page: Studio** — Left sidebar + main area. Sidebar: 4 nav items stacked vertically (Chords, Scales, Progressions, Keys). Each is just text — active item in white, others in gray. Maybe a thin left border accent on the active one (orange or white). Main area: a search input at top (same style as Decompose search). Below: a row of filter dropdowns — each is a small bordered rectangle with a label and a chevron. When filters are active, show removable filter tags as small bordered pills with an × icon. Below: a grid of theory cards. Each card is a thin-bordered rectangle containing: name in bold white, a formula line in monospace orange (e.g., "C - E - G"), a short description in gray, and tiny muted labels for difficulty/genre at the bottom.

**Page: Library** — Header: "Library" in the massive headline style + "Coming Soon" as a small muted text label beside it. Subtitle in gray. A grid of song cards — each with a square placeholder image (bordered), song name, artist, album — all at reduced opacity (~60%) to communicate "preview" state. Organized under "Favorites" and "Recommended" section headers.

**Page: Practice** — 2-column grid of 4 module cards. Each card is a bordered rectangle with: a small icon (orange stroke style), a bold title, a paragraph description in gray, a list of features (small gray text, maybe with orange bullets or dashes), a "Coming Soon" muted label, and a dark inset preview box (slightly darker background with a thin border) showing monospace example content in gray/orange (like a chord chart or tab snippet). Modules: Jam Tracks, Scale Trainer, Chord Trainer, Progression Looper.

**Page: About** — Centered layout with massive top padding. "How Riffd works" in the large headline style. A paragraph of gray body text. A 4-column row of pipeline steps — each has a number (in a small bordered circle), a bold title, and gray description. A "Technology" section with 6 small bordered cards (tech name in white, role in orange, description in gray). A "Principles" section with 3 items in a vertical list (bold title + gray description, maybe a small icon or number prefix).

---

**Critical reminders: Do not use colored gradients, glow effects, glassmorphism, or purple/teal anywhere. The palette is: black background, white text, warm gray secondary text, orange accent used sparingly on icons and highlights only, off-white/cream for primary buttons. Borders do all the structural work — keep them thin and subtle. Let the typography and spacing carry the design.**