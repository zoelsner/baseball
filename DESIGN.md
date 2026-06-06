---
name: Sandlot
description: Single-user-first fantasy baseball attention queue for Zach.
colors:
  background: "#efe8dc"
  surface: "#fffaf2"
  surface-muted: "#f1e8da"
  ink: "#0f172a"
  body: "#334155"
  muted: "#64748b"
  hairline: "#e2d7c6"
  hairline-muted: "#eadfce"
  accent: "#df7042"
  accent-soft: "#f8dfce"
  lineup: "#2563eb"
  lineup-soft: "#dbe7fe"
  bench: "#0f9d58"
  bench-soft: "#dcf2e3"
  injured: "#dc2626"
  injured-soft: "#fde2e1"
  empty: "#94a3b8"
  empty-soft: "#eef1f5"
typography:
  display:
    fontFamily: '"Source Serif 4", "Inter", serif'
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 0.96
    letterSpacing: "-0.035em"
  headline:
    fontFamily: '"Source Serif 4", "Inter", serif'
    fontSize: "25px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.045em"
  title:
    fontFamily: '"Source Serif 4", "Inter", serif'
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.2
  body:
    fontFamily: '"Inter", system-ui, -apple-system, sans-serif'
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.45
  label:
    fontFamily: '"Inter", system-ui, -apple-system, sans-serif'
    fontSize: "10.5px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.1em"
  mono:
    fontFamily: '"JetBrains Mono", "Roboto Mono", ui-monospace, monospace'
    fontSize: "14px"
    fontWeight: 700
rounded:
  chip: "999px"
  slot: "6px"
  control: "14px"
  card: "20px"
  major-card: "24px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "18px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "#ffffff"
    rounded: "{rounded.chip}"
    padding: "13px 14px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.body}"
    rounded: "{rounded.chip}"
    padding: "13px 14px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "16px 18px"
  queue-item:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "15px 16px"
---

# Design System: Sandlot

## 1. Overview

**Creative North Star: "The Morning Bench Card"**

Sandlot should feel like a quiet baseball operations card Zach reads before the day starts. It is personal, practical, and specific: what changed, what needs attention, and where to inspect next. The visual system can carry baseball warmth, but it should never hide the operating-tool job under decoration.

The current interface uses a soft cream mobile shell, serif headlines, compact data rows, pill controls, hairline dividers, and clear semantic color. Keep that identity, but tighten it around the Attention Queue. The user should scan from severity to player to reason to next step without first navigating through every fantasy feature.

It explicitly rejects generic fantasy dashboards, AI-first prompt hunting, news firehoses, and feature sprawl. Skipper can explain decisions, but the product surface must reveal important workflows directly.

**Key Characteristics:**

- Mobile-first, single-user-first, and morning-brief compatible.
- Warm baseball identity with restrained product density.
- Queue hierarchy before dashboard summary.
- Deterministic signals before AI explanation.
- Clear manual action boundary for Fantrax decisions.

## 2. Colors

The palette is a warm scorecard surface with high-contrast ink and state colors reserved for meaning.

### Primary

- **Infield Clay** (#df7042): Primary accent for current selection, queue emphasis, and warm warning states.
- **Clay Wash** (#f8dfce): Soft accent background for selected or advisory surfaces.

### Secondary

- **Lineup Blue** (#2563eb): Active lineup state. Use only for roster status, not decoration.
- **Bench Green** (#0f9d58): Bench, healthy, success, or safe state.
- **Injury Red** (#dc2626): Injury, out, failed, or high-severity attention.

### Neutral

- **Morning Scorecard** (#efe8dc): App background.
- **Card Stock** (#fffaf2): Main content surface.
- **Dugout Layer** (#f1e8da): Secondary controls, segmented backgrounds, and subtle chips.
- **Ink** (#0f172a): Primary text and primary actions.
- **Slate Body** (#334155): Body copy and secondary button text.
- **Muted Slate** (#64748b): Metadata and low-emphasis labels.
- **Scorecard Rule** (#e2d7c6): Main borders.
- **Soft Rule** (#eadfce): Internal dividers.

### Named Rules

**The Severity Rarity Rule.** Red belongs to urgent roster consequence: injury, out, failure, or status change. Do not use red for generic negative styling.

**The Accent Discipline Rule.** Infield Clay is a guide color, not decoration. It should point to current context, primary action, or advisory state.

## 3. Typography

**Display Font:** Source Serif 4 with Inter fallback.
**Body Font:** Inter with system-ui fallback.
**Label/Mono Font:** JetBrains Mono with Roboto Mono and ui-monospace fallbacks.

**Character:** The serif gives Sandlot a baseball-card cadence; Inter keeps controls and rows trustworthy. Mono is for scores, FP/G, ranks, and snapshot facts.

### Hierarchy

- **Display** (700, 34px, 0.96): Today headline and major page identity. Use sparingly inside the mobile shell.
- **Headline** (800, 25px, 1): Matchup scores, queue totals, and high-emphasis numbers.
- **Title** (700, 20px, 1.2): Card titles, player sheet sections, and page subheads.
- **Body** (600, 13px, 1.45): Explanations, queue reasons, card copy, and empty states.
- **Label** (800, 10.5px, 0.1em): Short section labels and state labels. Avoid long all-caps phrases.
- **Mono** (700, 14px): Numeric stats, slots, ranks, and score lines.

### Named Rules

**The Product Serif Rule.** Serif type can lead pages and cards, but labels, buttons, rows, inputs, and data stay in Inter or mono.

## 4. Elevation

Sandlot is flat by default. Depth comes from tonal layers, borders, internal dividers, and spacing. Shadows are reserved for the desktop phone frame and occasional active state; content cards should not look like floating marketing panels.

### Shadow Vocabulary

- **Desktop Phone Frame** (`0 30px 80px rgba(0,0,0,0.18), 0 0 0 10px #1a1a1a, 0 0 0 11px #2a2a2a`): Used only by the desktop preview frame.
- **Selected Segment** (`0 1px 2px rgba(26,26,26,0.06)`): Used only for active segmented controls.

### Named Rules

**The Flat Queue Rule.** Queue items should feel ordered, not floating. Use severity, position, and reason text instead of shadow drama.

## 5. Components

### Buttons

- **Shape:** Fully rounded pill (`999px`) for primary commands and paired bottom actions.
- **Primary:** Ink background, white text, 13 to 15px bold label, 13 to 15px vertical padding.
- **Secondary:** Card Stock background, Slate Body text, Scorecard Rule border.
- **Hover / Focus:** Keep changes quiet and stateful. Add visible focus rings in implementation, but do not introduce decorative glow.

### Chips

- **Style:** Soft tonal backgrounds with bold 10.5 to 11px text.
- **State:** Injury chips use Injury Red on Injury Soft. Lineup chips use Lineup Blue on Lineup Soft. Bench chips use Bench Green on Bench Soft.
- **Use:** Chips support a row reason; they do not replace the reason sentence.

### Cards / Containers

- **Corner Style:** 20px for ordinary cards, 24px for major summary cards.
- **Background:** Card Stock for primary content, Dugout Layer for controls and inactive pills.
- **Shadow Strategy:** Flat by default. Use borders and tonal hierarchy.
- **Border:** 1px Scorecard Rule for card boundaries, Soft Rule for internal dividers.
- **Internal Padding:** 16 to 18px for cards, 14 to 16px for queue rows.

### Inputs / Fields

- **Style:** Card Stock background, 1px Scorecard Rule border, 14px radius, 14 to 16px padding.
- **Focus:** Clear border or outline state. Do not rely on placeholder contrast.
- **Error / Disabled:** Pair color with text. Disabled controls should reduce opacity but keep labels readable.

### Navigation

- **Bottom Nav:** Primary mobile navigation should stay to five items: Today, Roster, Adds, League, Skipper.
- **Trade Placement:** Trade grading and scouting live under League until they earn a separate durable workflow.
- **Active State:** Use Ink for active icon/text, Muted Slate for inactive. Avoid state colors in nav unless communicating a real alert count.

### Attention Queue

The queue is the signature component. It should list items by consequence, not category.

- **Order:** Injury/status changes, active starter out or not playing, pitcher not starting, high-impact waiver replacement, league/trade inspection.
- **Row Shape:** Player avatar, player name, roster slot/team, concise reason, severity chip, and a clear inspection action.
- **Copy:** Say what changed and why it matters. Example: "Aaron Judge changed to DTD since the last refresh. Inspect OF replacement before lock."
- **Future Integration:** Queue output should be structured so The Town can read it for Zach's morning brief.

## 6. Do's and Don'ts

### Do:

- **Do** make Today the Attention Queue, not a generic roster-health dashboard.
- **Do** show important player status changes without requiring a Skipper prompt.
- **Do** keep Skipper available as explanation and Q&A, not as the only entry point to core workflows.
- **Do** use deterministic data and cached recommendations before AI-generated prose.
- **Do** keep bottom navigation to Today, Roster, Adds, League, and Skipper.
- **Do** preserve the manual Fantrax boundary in action copy.
- **Do** use labels and reason text alongside color for every state.

### Don't:

- **Don't** add a top-level tab for every fantasy feature.
- **Don't** hide trade workflows behind Skipper prompts. Put them in League context.
- **Don't** use injury status alone to recommend dropping a high-value IL player without a richer current-news signal.
- **Don't** turn the app into a news feed. Every alert must connect to Zach's roster.
- **Don't** use side-stripe borders greater than 1px as card decoration.
- **Don't** use decorative motion, glass effects, gradient text, or marketing-style hero metrics.
- **Don't** generalize for future users before Zach's current workflow is working.
