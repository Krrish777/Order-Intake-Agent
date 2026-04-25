# Order Intake Agent · Hack-O-Relay Deck · Brand Spec
> Captured: 2026-04-23
> Direction: Glacis editorial (cream + teal + muted terracotta)
> Asset sources: 5 user-supplied illustrations; palette sampled from Glacis "How AI Automates Order Intake in Supply Chain" PDF
> Completeness: palette complete; typography locked; illustrations supplied

## 🎯 Core assets

### Supplied illustrations (all in `assets/images/`)
- `01-channels-meme.jpeg` — EDI red-carpet / Portal wasteland / Email chaos (hand-drawn)
- `02-chaos-blueprint.png` — 8-step "blueprint for chaos" workflow (red-zone)
- `03-human-cost.png` — Drowning in papers (envelopes/PDFs/XLS → overloaded human → ERP)
- `04-60sec-workflow.png` — "From Inbox to ERP in Under 60 Seconds" solution workflow
- `05-architecture.png` — Technical architecture (Gmail → Pub/Sub → ADK → Gemini → Firestore)

### Forbidden visuals
- No stock photography (explicitly no businessman-on-laptop, no factory-worker-with-panel)
- No emoji-as-icon
- No AI-generated illustration fill
- No purple gradients, neon blue, or GitHub-dark-mode aesthetic

## 🎨 Palette (from Glacis PDF sampling)

```
--ink            #1A1A1A   body copy
--ink-muted      #5C5C58   secondary text
--teal           #1C5754   primary brand / titles
--teal-deep      #133F3D   architecture dark spread bg
--teal-dim       #2D7572   hover/secondary teal
--cream          #F5EDE1   slide background
--cream-soft     #EFE5D4   panels on cream
--terracotta     #C27B6E   accent / highlights / numbers
--terracotta-dk  #A85A4B   emphasis accent
--rule           #D9CEB9   hairlines / dividers
```

**Palette discipline:** every color comes from `var(--*)`. No hand-typed hex in slide HTML. To add a color, update tokens.css first.

## ✒️ Typography

- **Display:** Fraunces (Google Fonts — variable serif, editorial, high-contrast)
- **Body:** Inter Tight (Google Fonts — geometric sans, tight tracking)
- **Mono (optional, tech slides only):** JetBrains Mono

Why: Fraunces gives the deck magazine-editorial voice rather than tech-blog voice. Pairs with Inter Tight for a restrained, grown-up feel that matches Glacis's aesthetic without copying it. Both free.

## 📐 Grid & composition

- Canvas: 1920×1080
- Outer margin: 120px (generous editorial breathing room)
- Inner grid: 12-column, 32px gutter
- Slide number: bottom-right, tabular, small
- Footer rule: hairline `var(--rule)` above footer metadata

## 🎭 Dark spread (Slide 7 only)

Intentional contrast moment. Background `var(--teal-deep)`, text `var(--cream)`, image on transparent. Acts as a "turn-of-page" in the narrative — from "the problem" half to "the solution" half.

## Signature details

- Hairline rule under slide numbers (tabular figures)
- Small teal square · as section anchor glyph
- Terracotta underline on key numbers
- Section labels in all-caps micro-type, +8% letter-spacing
