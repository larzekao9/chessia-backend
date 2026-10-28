---
name: Cyber-Chess Arena
colors:
  surface: '#0d141d'
  surface-dim: '#0d141d'
  surface-bright: '#333a44'
  surface-container-lowest: '#080f18'
  surface-container-low: '#151c26'
  surface-container: '#19202a'
  surface-container-high: '#242a34'
  surface-container-highest: '#2e353f'
  on-surface: '#dce3f0'
  on-surface-variant: '#bacbb9'
  inverse-surface: '#dce3f0'
  inverse-on-surface: '#2a313b'
  outline: '#859585'
  outline-variant: '#3b4a3d'
  surface-tint: '#00e475'
  primary: '#75ff9e'
  on-primary: '#003918'
  primary-container: '#00e676'
  on-primary-container: '#00612e'
  inverse-primary: '#006d35'
  secondary: '#a5e7ff'
  on-secondary: '#003543'
  secondary-container: '#00d2ff'
  on-secondary-container: '#00566a'
  tertiary: '#dce4ff'
  on-tertiary: '#002d6f'
  tertiary-container: '#b3c8ff'
  on-tertiary-container: '#1c50aa'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#62ff96'
  primary-fixed-dim: '#00e475'
  on-primary-fixed: '#00210b'
  on-primary-fixed-variant: '#005226'
  secondary-fixed: '#b6ebff'
  secondary-fixed-dim: '#47d6ff'
  on-secondary-fixed: '#001f28'
  on-secondary-fixed-variant: '#004e60'
  tertiary-fixed: '#d9e2ff'
  tertiary-fixed-dim: '#b0c6ff'
  on-tertiary-fixed: '#001945'
  on-tertiary-fixed-variant: '#00429c'
  background: '#0d141d'
  on-background: '#dce3f0'
  surface-variant: '#2e353f'
  cyber-green-deep: '#007A3D'
  cyber-cyan-deep: '#0A2540'
  navy-surface: '#0A192F'
  navy-surface-hover: '#112240'
  text-primary: '#F8FAFC'
  text-muted: '#8892B0'
  heraldic-red: '#C8102E'
  heraldic-gold: '#E8B21A'
  glow-green: rgba(0, 230, 118, 0.4)
  glow-cyan: rgba(0, 210, 255, 0.45)
typography:
  display-xl:
    fontFamily: Space Grotesk
    fontSize: 56px
    fontWeight: '800'
    lineHeight: 64px
  display-xl-mobile:
    fontFamily: Space Grotesk
    fontSize: 36px
    fontWeight: '800'
    lineHeight: 44px
  headline-lg:
    fontFamily: Space Grotesk
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
  headline-lg-mobile:
    fontFamily: Space Grotesk
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
  headline-md:
    fontFamily: Space Grotesk
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  title-sm:
    fontFamily: Space Grotesk
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Hanken Grotesk
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 22px
  body-sm:
    fontFamily: Hanken Grotesk
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  label-lg:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '700'
    lineHeight: 20px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 14px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1.25rem
  margin: 1.5rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

This design system expresses the intersection of ancient strategic intellect and high-octane artificial intelligence. Developed for the flagship **Torneo de Ajedrez Humano vs. IA** co-hosted by the Asociación Departamental de Ajedrez de Santa Cruz (ADASC) and UAGRM FICCT, it merges institutional Bolivian chess heritage with a futuristic cyberpunk tournament arena.

The target audience spans competitive chess masters, collegiate engineering researchers, robotics hobbyists, and tech enthusiasts. The interface evokes calculating precision, competitive tension, and neon-tinged wonder: sitting across an electrified chessboard facing an autonomous robotic arm.

The design movement combines **High-Contrast Dark Mode** with **Robotic Sci-Fi Brutalism and Tactile Glassmorphism**. Dark navy canvas tones emulate a stadium blackout, pierced by laser-sharp emerald green and electric cyan circuit lines, crisp chamfered containers, tactile mechanical buttons, and glowing digital telemetry.

## Colors

The palette establishes an aggressive visual duel between human organic tactical depth and synthetic computational power:

- **Primary (`#00E676`)**: Neon Cyber Emerald represents human intuition, vitality, and tournament victory. Used for primary CTAs, active status beacons, winning moves, and critical focal points.
- **Secondary (`#00D2FF`)**: Electric Cyber Blue signifies artificial intelligence, robotic telemetry, sensor nodes, and computational analysis lines.
- **Tertiary (`#0D47A1`)**: Deep Cobalt acts as a structural anchor, lending architectural depth to cards, headers, and secondary panel borders.
- **Neutral (`#050B14`)**: Obsidian Void serves as the canvas backdrop, with `#0A192F` providing elevated structural layers.
- **Named Institutional Accents**: `#C8102E` (Cruces Red) and `#E8B21A` (Ducal Gold) pay respect to the official Santa Cruz chess heraldry, reserved exclusively for institutional badges, tournament champion ribbons, and referee credentials.

Contrast ratios consistently exceed WCAG AAA standards for white and cyber-green text on deep navy backdrops.

## Typography

The typography system operates on three deliberate typographic poles:

1. **Display & Headlines (`Space Grotesk`)**: Technical, futuristic, and commanding. Used in all tournament banners, round titles, countdowns, and match standings. Headers leverage uppercase letter-tracking (`0.04em` to `0.08em`) to mirror aerospace telemetry.
2. **Body Content (`Hanken Grotesk`)**: Clean, hyper-legible neo-grotesque for dense tournament rules, match analysis paragraphs, player biographies, and institutional documentation.
3. **Labels & Data Telemetry (`JetBrains Mono`)**: Monospaced mechanical data representation for chess notations (e.g., `1. e4 e5 2. Nf3 Nc6`), clocks, robot positional coordinates (DOF / Gripper status), ELO ratings, and schedule timestamps.

## Layout & Spacing

The interface employs a responsive **12-column cyber-grid** engineered for dense tactical streams, live camera feeds, move notation logs, and participant registration.

- **Desktop (≥1200px)**: 12-column layout with 24px gutters, fixed container max-width of 1320px, and 32px screen edge margin. Supports asymmetric split-view (Chessboard arena on 7 cols, AI telemetry & notation on 5 cols).
- **Tablet (768px - 1199px)**: 8-column layout with 16px gutters and 24px margins. Move records collapse below the active board viewport.
- **Mobile (<768px)**: 4-column layout with 12px gutters and 16px side margins. High-priority interactive modules stack into swipeable tabs (Board vs. Clock vs. AI Analysis).

Spacing relies on a strict 8px harmonic scale (`space-xs` = 4px, `space-sm` = 8px, `space-md` = 16px, `space-lg` = 24px, `space-xl` = 40px) ensuring consistent mathematical rhythm across dense tournament panels.

## Elevation & Depth

Depth is established through **neon-lit sci-fi layering** rather than natural sunlight shadows:

- **Level 0 (Canvas Void)**: `#050B14` with a subtle SVG circuit grid overlay (1px lines at 4% cyan opacity).
- **Level 1 (Dock & Panels)**: `#0A192F` with a 1px border of `rgba(0, 210, 255, 0.15)` and soft backdrop blur (`12px`).
- **Level 2 (Active Cards & Floating Widgets)**: `#112240` framed by dual accent borders: 1px continuous cyan border plus directional top-left emerald highlight accents (`#00E676`).
- **Glow & Atmospheric Bloom**: Key CTA elements and active AI move indicators project diffuse neon glows:
  - Primary Active Button: `0 0 20px rgba(0, 230, 118, 0.45), 0 4px 12px rgba(0, 0, 0, 0.6)`
  - Alert / Robot In-Motion Indicator: `0 0 16px rgba(0, 210, 255, 0.5)`

## Shapes

The shape architecture relies on a **Soft-Chiseled Industrial profile** (`roundedness: 1`). Curvature is kept low (4px default, 8px on large cards) to evoke military grade terminals, robotic casing panels, and precision electronics. 

Key interactive focal cards incorporate subtle 45-degree chamfered top-right corners or pill pills strictly reserved for tournament status badges (`LIVE`, `AI THINKING`, `CHECKMATE`).

## Components

### Buttons
- **Primary Cyber CTA (`REGÍSTRATE Y JUEGA`)**: Solid neon green `#00E676` background, pitch navy `#050B14` bold text in Space Grotesk. Features a full rounded-pill or 4px chamfered edge, accompanied by an animated forward chevron `>`. Includes a neon glow shadow (`0 0 18px rgba(0, 230, 118, 0.4)`).
- **Secondary AI Action**: Ghost button with 1.5px `#00D2FF` electric cyan border, transparent navy fill, cyan text, and a hover transition that triggers a light cyan gradient flood (`rgba(0, 210, 255, 0.15)`).
- **Tertiary Tactical Action**: Dark `#112240` fill with `#8892B0` muted text, expanding to `#F8FAFC` on hover.

### Cards & Panels
- Constructed from dark navy `#0A192F` glass-backed tiles.
- Border: 1px `#0D47A1` structural border accented by glowing corner brackets or dual-tone top border stripes (7px emerald line spaced 8px from an electric blue line, paying homage to the ADASC institutional seal).

### Status Chips & Telemetry Badges
- Set in `JetBrains Mono` uppercase with high tracking.
- Features glowing status pips: pulsating green for "HUMANO EN TURNO", cyan scanline for "BRAZO DOBOT PENSANDO", and crimson red for "TIEMPO AGOTÁNDOSE".

### Input Fields & Search Bars
- Background `#050B14`, inset 1px border `rgba(0, 210, 255, 0.3)`.
- When focused, border pulses to `#00E676` with a faint interior neon wash. Monospaced placeholders for registration codes and player FIDE/Elo ranks.

### Chessboard & Notation Log
- **Board Squares**: Deep charcoal navy `#0A192F` for dark squares, cybernetic slate `#1A2F4C` for light squares. Move destination targets glow with radial cyan rings.
- **Move Log**: Two-column monospaced list highlighting the latest ply with a high-contrast `#00E676` badge.