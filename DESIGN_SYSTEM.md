# SHOWRUNNER UI/UX DESIGN SYSTEM (HACKATHON DEMO SPEC)

## 1. Aesthetic Direction & Visual Identity
- **Atmosphere:** Dark-first, cinematic event aesthetic. Deep obsidian surfaces with subtle glassmorphism and radiant electric accents.
- **Surface Elevation:**
  - Base Canvas: `oklch(0.12 0.012 260)` (Rich obsidian slate, never raw `#000000`)
  - Elevated Cards: `oklch(0.16 0.018 260)` with a 1px border `oklch(1 0 0 / 8%)`
  - Floating Overlays / Modals: `backdrop-blur-xl bg-slate-950/70 border border-white/10`
  - Warm Lighting: Ambient colored glows behind active media (`drop-shadow(0 0 24px rgba(99, 102, 241, 0.15))`)

## 2. Color Tokens
- **Canvas / Backgrounds:** `oklch(0.12 0.012 260)` (Primary), `oklch(0.15 0.015 260)` (Secondary)
- **Primary Accent (AI & Match Actions):** Electric Indigo `oklch(0.64 0.24 275)`
- **Live / Stream Status:** Emerald Glow `oklch(0.72 0.20 150)`
- **Media Highlight / Reels Accent:** Sunset Orange `oklch(0.68 0.21 40)`
- **Typography:**
  - Primary (Headings/Body): `oklch(0.96 0.005 260)`
  - Secondary (Metadata/Captions): `oklch(0.65 0.02 260)`
  - Tertiary (Dividers/Placeholders): `oklch(0.35 0.02 260)`

## 3. Typography & Numerical Layout
- **Font Stack:** Geist Sans, Inter, or System Apple SF Pro.
- **Rhythm & Sizing:**
  - Hero / Screen Titles: 28px–36px (`font-weight: 700`, `letter-spacing: -0.02em`)
  - Card Titles / Modal Headers: 18px–20px (`font-weight: 600`, `letter-spacing: -0.01em`)
  - Body Text: 14px–15px (`line-height: 1.5`)
  - Badges & Tags: 11px–12px (`font-weight: 600`, uppercase, `letter-spacing: +0.06em`)
- **Tabular Figures:** Always apply `font-variant-numeric: tabular-nums` on photo counts, timers, match percentages, and timestamps.

## 4. Spacing, Geometry & Ergonomics
- **8pt Soft Grid:** All margins, paddings, and button heights must be multiples of 4 or 8.
- **Squircle Curvature:**
  - Outer Containers & Modals: `rounded-3xl` (24px)
  - Media Cards & Video Tiles: `rounded-2xl` (16px)
  - Action Chips & Pill Buttons: `rounded-full`
- **Tap Targets:** Minimum 44px height for all mobile touch targets.

## 5. Motion, Springs & Feedback
- **Spring Physics:** Use Framer Motion springs `{ stiffness: 350, damping: 28, mass: 0.8 }` for drawers, card taps, and modal entrances. Avoid stiff linear transitions.
- **Tap Physics:** Buttons and media cards scale down on press (`active:scale-95` or `whileTap={{ scale: 0.97 }}`).
- **Micro-Haptics:** Fire `navigator.vibrate?.([15])` on actions like matching a selfie, filtering media, or opening the lightbox.