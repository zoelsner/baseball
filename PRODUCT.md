# Product

## Register

product

## Users

Sandlot is designed first as a single-user personal operating tool for Zach. It is based on his fantasy baseball pain points, morning routine, Fantrax league context, and the specific moments where missed information costs real roster value.

The product should still be shaped so it can eventually support other fantasy baseball managers. Future users should benefit from the patterns proven in Zach's use case, but the current design should not dilute itself into a generic SaaS surface before the personal workflow is correct.

Primary usage context: Zach checks the app through a morning brief, before lineup lock, or when something changes with a rostered player. He wants the app to tell him what needs attention without making him browse Fantrax, player news, matchup data, waiver boards, and league rosters manually.

## Product Purpose

Sandlot turns Fantrax league data and baseball context into a small attention queue for a fantasy baseball team.

The core job is not to be a general dashboard. The core job is to answer: what changed, what needs attention, what should I inspect next, and why should I trust that read?

The app is recommend-first. The product UI surfaces decisions clearly enough that Zach can act on them; it does not fire Fantrax writes itself. Execution exists only through a token-gated machine API (`POST /api/actions`) used by Zach's external agent, and every action requires Zach's explicit per-action confirmation (e.g., a Telegram yes) before it runs. Trade accepts are out of scope for execution entirely — trades stay a manual, human activity.

Success means Zach can read the morning brief or open Today and quickly know:

- whether a high-value player picked up an injury/status flag
- whether a starter is not playing, not pitching, or otherwise risky before lock
- whether the waiver board has a replacement worth reviewing
- whether a league/team/trade context needs inspection
- what is real data, what is a deterministic recommendation, and what is AI explanation

## Brand Personality

Calm, sharp, grounded.

Sandlot should feel like a trusted bench coach plus a quiet operations board: baseball-literate, practical, and direct. It should use plain language, cite concrete data, avoid hype, and distinguish clearly between current facts and recommendations.

Skipper is a helper and explainer, not the hidden command center. Important workflows must exist as visible product surfaces before Skipper can discuss them.

## Anti-references

- Generic fantasy sports dashboards that show every metric but do not say what needs attention.
- AI chat apps where the user must ask the right prompt before important information appears.
- News firehoses, rumor feeds, and injury blurbs without roster-specific consequence.
- SaaS-style feature sprawl where every idea becomes a top-level tab.
- Autonomous automation that executes Fantrax moves without explicit per-action human confirmation.
- Decorative baseball nostalgia that makes the interface less scannable.
- Future-user abstraction that weakens Zach's current morning workflow.

## Design Principles

1. Attention first.
   Today is an attention queue, not a dashboard. Injury/status changes, lineup risks, and high-value decision points outrank general roster information.

2. Zach first, extensible later.
   Build from Zach's actual league, cadence, and pain points. Keep architecture and language flexible enough for future users, but do not generalize before the personal workflow works.

3. Deterministic before AI.
   Python/Fantrax/MLB data should produce the core queue and rankings. AI can explain, summarize, and help with context, but it should not be the only way important information appears.

4. Recommend first; execute only on explicit confirmation.
   Sandlot suggests, compares, and explains. Fantrax writes happen only through
   the token-gated actions API after Zach confirms the specific action — never
   autonomously, and never hidden behind an ambiguous boundary.

5. Fewer primary surfaces.
   Bottom navigation should reflect durable workflows: Today, Roster, Adds, League, and Skipper. Trade belongs inside League context until it earns a separate primary surface.

6. The Town can consume the queue.
   The attention queue should eventually be readable by Zach's broader personal operating system, including morning brief updates in The Town. Sandlot owns baseball logic; The Town owns cross-life aggregation.

## Accessibility & Inclusion

Target WCAG AA for text contrast and interactive controls. Do not rely on color alone for injury, warning, success, or lineup state. Pair severity color with labels, ordering, and concise reason text.

The app is used on mobile in a quick-read context, so tap targets should be comfortable, text should remain readable without zooming, and the queue should be scannable under time pressure.

Motion should be minimal and state-driven. Respect reduced-motion preferences. Loading and error states should explain whether data is missing, stale, refreshing, or unavailable.
