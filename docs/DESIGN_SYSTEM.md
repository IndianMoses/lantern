# Design system

Lantern uses the ClaimMate design language: a 60 / 30 / 10 split of white
content surfaces, grey structure and brown identity. Every colour, radius and
type size is a CSS custom property declared once in `frontend/src/styles.css`;
components never carry literal colours. Status colours are reserved for status
and never used for decoration. There are no gradients, no blue, purple or teal.

## Tokens

| Token | Value | Use |
|---|---|---|
| `--brand-900` | `#3E2A1E` | primary actions, active navigation, the mark, the login hero |
| `--brand-700` | `#5A3E2C` | hover states, links, the flame in the mark |
| `--brand-100` | `#F1EAE3` | selected rows, active chips |
| `--brand-50` | `#F6F2EE` | citation chips, subtle brand surfaces |
| `--brand-border` | `#E3D7CD` | borders on brand surfaces |
| `--brand-on-dark` | `#E3D7CD` | text and the mark's frame on `--brand-900` |
| `--surface` | `#FFFFFF` | cards, panels, inputs (the 60) |
| `--surface-subtle` | `#F5F4F2` | code, muted blocks |
| `--bg-page` / `--bg-sidebar` | `#EBEAE8` / `#E2E0DD` | page and sidebar backgrounds (the 30) |
| `--grey-100` to `--grey-300`, `--border`, `--border-strong` | `#F1F0EE` to `#D6D3CF` | structure |
| `--text-primary` / `--text-body` / `--text-secondary` / `--text-tertiary` | `#1F1A17` / `#3A3532` / `#5E5853` / `#6E6863` | type hierarchy (all pass 4.5:1 on white) |
| `--success-*`, `--warning-*`, `--danger-*` | green, amber and red triplets (bg, border, text) | statuses only: verified, warning, failed |

Legacy variable names used by earlier components (`--bg`, `--bg-elev`,
`--ink`, `--accent` …) are aliases that resolve to the tokens above, so no
component holds a second palette.

## Geometry and motion

- Radii: cards 12 px, controls 8 px, badges and chips 6 px.
- Content width 1120 px; sidebar 236 px; touch targets at least 44 px on
  phones (buttons grow to 36 px minimum height under 900 px).
- Motion 160 ms, disabled under `prefers-reduced-motion`.
- At most three cards side by side; all multi column grids collapse to one
  column under 900 px, and tables marked `stack-sm` become cards under 640 px.

## Typography

System font stack (`-apple-system, BlinkMacSystemFont, "SF Pro Text",
"Helvetica Neue", system-ui, "PingFang SC", "Hiragino Sans GB", "Microsoft
YaHei", "Noto Sans CJK SC", sans-serif`), no web font download. Scale: body
14 / 21, small 12.5, h1 26, h2 18, h3 15, tile values 26 semibold. Numbers use
tabular figures wherever they are compared (`font-variant-numeric:
tabular-nums` on tiles, tables, timelines). Chinese text uses the same scale
and falls to PingFang / YaHei / Noto Sans CJK through the stack.

## The mark

The brief asked for the existing Lantern logo to be recoloured to the tokens
without changing its shape. **The repository had no logo asset.** The previous
build drew a plain orange rounded tile with CSS (`.brand .mark`, a gradient
square with a lighter inset) and no lantern shape at all. So an inline SVG
lantern mark was created (`frontend/src/components/Logo.tsx`):

- 32 × 40 viewBox; handle arc, cap, body frame with rounded corners, white
  glass panel with a neutral mullion, a small flame, two step base.
- Colours come from the tokens: frame `--brand-900` (or `--brand-on-dark` on
  dark backgrounds), glass white, flame `--brand-700` (white on dark), detail
  `--grey-300`.
- It is used in the sidebar lockup, the login hero, the favicon
  (`public/favicon.svg`) and the PWA icons (`public/icons/`, rendered by
  `scripts/make_icons.py` from the same geometry).

This is therefore a **new mark in the token colours, not a recoloured
original**. If ClaimMate has an official logo, replace the paths in
`LanternMark` (keep the component's props so the lockups do not change) and
re-run `scripts/make_icons.py` for the icons.

## Navigation and layout rules

- Overview → subsection navigation goes through router links or `navigate()`
  with real routes (`/gaps/:analysisId`, `/claims/:id`, `/insurer/claims/:id`),
  so URL, content and the active state in the sidebar change together;
  `ScrollToTop` resets scroll on route change and `document.title` follows
  the page.
- Under 900 px the sidebar becomes a bottom navigation with the surface's
  primary items and a "More" sheet for the rest; the header keeps the
  language toggle and notifications.
- Language: one authoritative locale held by `LangProvider`, sent as `X-Lang`
  on every request; pages refetch on change so server generated labels switch
  with the interface.

## Checking a page

Every captured page is checked for horizontal overflow at 1280 px and 390 px
(`scripts/screenshots.mjs`, `scripts/screens_gaps.mjs`,
`scripts/screens_bank.mjs`), and the Playwright console is watched for
errors. Screenshots live in `evidence/screens/`.
