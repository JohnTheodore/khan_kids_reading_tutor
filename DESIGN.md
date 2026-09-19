---
name: Reading Tutor
description: A warm, evidence-led reading journey for parents.
colors:
  canvas: "#fcfaf4"
  surface: "#ffffff"
  soft: "#edf4f7"
  ink: "#203e4b"
  muted: "#526570"
  accent: "#176a96"
  accent-hover: "#105374"
  on-accent: "#ffffff"
  line: "#d9e3e5"
  control-line: "#8096a0"
  focus: "#2755ad"
  warning-surface: "#fff0e7"
  warning-line: "#c78e65"
  warning-ink: "#793717"
  score: "#816015"
  mastered: "#326843"
  removed: "#775392"
  added: "#245daf"
  canvas-dark: "#16242d"
  surface-dark: "#20323d"
  soft-dark: "#293e4b"
  ink-dark: "#e5eff4"
  muted-dark: "#b4c6d0"
  accent-dark: "#97d4f2"
  accent-hover-dark: "#b5e4fa"
  on-accent-dark: "#173b50"
  line-dark: "#415765"
  control-line-dark: "#829ba9"
  focus-dark: "#97bbff"
  warning-surface-dark: "#3a2b23"
  warning-line-dark: "#b88662"
  warning-ink-dark: "#ffcfb0"
  score-dark: "#efd180"
  mastered-dark: "#99d6ac"
  removed-dark: "#d4b4ef"
  added-dark: "#a7c7ff"
typography:
  disclosure-title:
    fontSize: "1.25rem"
  headline:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "2rem"
    fontWeight: 730
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  section-title:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  card-title:
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.35
  body:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontSize: "0.875rem"
    fontWeight: 650
    lineHeight: 1.55
  supporting:
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
  stat:
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.55
rounded:
  meter: "6px"
  control: "10px"
  card: "12px"
  inset: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  control-gap: "12px"
  md: "16px"
  compact-gutter: "20px"
  lg: "24px"
  gutter: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.control}"
    padding: "12px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.accent}"
    rounded: "{rounded.control}"
    padding: "12px 18px"
  reader-select:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 32px 11px 12px"
  mastery-meter:
    backgroundColor: "{colors.line}"
    textColor: "{colors.mastered}"
    rounded: "6px"
  lesson-card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "20px"
  reading-checks:
    backgroundColor: "{colors.soft}"
    rounded: "{rounded.inset}"
    padding: "24px"
---

# Design System: Reading Tutor

## Overview

**Creative North Star: "The Mastery Map"**

A mastery rubric made warm and approachable: ivory space, blue actions, green evidence, and quiet rules help parents read the journey without interpreting automation logs. The character is friendly and original, gently Khan Kids-adjacent without borrowed artwork or composition.

Native disclosures make evidence inspectable in place. Clear system type and restrained semantic colors keep uncertainty as visible as progress; this interface is a reading map, not a certificate of reading ability.

**Key Characteristics:**

- Warm, flat surfaces with quiet structural rules.
- Semantic color paired with written state and evidence.
- Progressive disclosure through native details and summary.
- Original authored SVG icons; no shipping raster imagery.

## Colors

Sky-blue actions and leaf-green mastery sit on warm ivory and cool neutral insets. The frontmatter records the exact light and dark values from the stylesheet; dark variants replace the same semantic roles under `prefers-color-scheme: dark`.

### Primary

- **Sky blue:** actions, links, active reader borders, and practicing states. The hover role strengthens a primary action; on-accent supplies its text.

### Secondary

- **Leaf green:** verified mastery evidence.
- **Golden score:** new score and hold evidence.
- **Plum deferred:** unchecked or deferred assignments.
- **Clear blue added:** added assignments, distinct from the action role.
- **Warm warning:** a surface, stroke, and text trio for connection failures and cautions.

### Neutral

- **Warm ivory canvas:** the page background.
- **Paper surface:** reader controls and lesson cards.
- **Cool soft inset:** selected readers, first recommendations, reading checks, and hover feedback.
- **Deep ink and muted ink:** content hierarchy, not evidence state.
- **Quiet rule and control stroke:** separate content and identify controls respectively.

**The Evidence Color Rule.** Pair semantic color with text or an authored icon; color alone must not carry mastery, uncertainty, or assignment changes.

## Typography

The interface uses the system sans-serif stack in the frontmatter, with rem-based text and a root line-height of 1.55. There is no custom display face. The small `r.` brand mark alone uses Georgia, bold, at 1.875rem; it is not a heading or body typography rule.

Headlines lead at 2rem; section headings at 1.5rem; reader and lesson titles at 1.125rem. Body and milestone titles use 1rem, with supporting evidence and labels at 0.875rem. Statistics use 1.75rem and tabular numbers; dated scores and elapsed time also use tabular numbers. Current focus uses 1.5rem with weight 650.

Introductory and empty-state copy is limited to 65ch; coverage and setup explanations to 75ch. Long names, evidence, and diagnostics wrap rather than escape their containers.

## Layout

Main content and the top bar are centered at a maximum width of 1140px, with 32px horizontal gutters; at 760px and below gutters become 20px. Reader switching uses the persistent dropdown rather than an overview tile grid.

The journey is one full-width milestone column followed by collapsed reading checks, with a 32px gap. At 1000px and below the hero becomes one column. At 760px and below recommendation cards, change groups, and setup checks become single-column; the statistics remain two-column. At 480px and below reader selection and the primary action take the full available width.

At 600px and below, the journey summary becomes one column. Milestone metadata sits under its title in a full-width wrapping row; the short subtitle is hidden while the title, state, and family counts remain. Reading-check padding reduces from 24px to 20px.

The queue keeps a 36rem minimum table width inside a horizontally scrollable, labeled, keyboard-focusable region. Its visible hint explains sideways scrolling and keyboard arrow use. Do not compress the evidence columns into unreadable mobile fragments.

## Elevation & Depth

There are no box shadows. Depth comes from canvas, paper, and soft inset tones, with one-pixel rules and strokes separating evidence. Focus is an outline, not a shadow: 3px in the focus role with a 4px offset.

**The Quiet Structure Rule.** Use rules and tonal insets to organize evidence rather than introducing decorative elevation.

## Shapes

Controls have gently curved corners (10px), reader tiles and lesson cards use 12px, and reading-check insets use 16px. Milestone and queue structures stay open and rule-led rather than becoming nested cards. Authored line icons use currentColor, rounded strokes, and a 1.25rem square; the activity dot is circular.

## Components

### Buttons and reader selection

Primary actions are blue, inline-flex, and weight 650 with 12px × 18px padding. Hover uses the accent-hover role; active press moves down 1px. Secondary actions use paper, a control stroke, and blue text; hover becomes soft and active strengthens the border. Disabled buttons become soft with muted text. Selection is a native select with a control stroke and paper fill. Buttons, select, summary, brand, and navigation links have a minimum height of 44px.

Reader tiles are selectable buttons with a 16px inset, 12px corners, and a control stroke. `aria-pressed` exposes selection; the selected tile uses soft fill and an accent border. Each reader retains their own journey, with no comparative ranking treatment.

### Native disclosures and mastery evidence

The reading map groups milestones into six core phases, followed by a separate
supporting-skills phase for vocabulary, language and writing. Phases remain in
curriculum order, but do not imply a strict prerequisite gate. The current phase
opens initially and carries a written Current focus marker; a View current
practice button opens the containing phase and milestone and focuses its summary.
Phase coverage deduplicates topic placements. Phase and milestone summaries use
the same meter/count component, consistent topics-mastered counts and separate
weekly gains. Native meters have 10px visible tracks. Mobile retains the useful
plain-language examples instead of hiding them.

Alphabet buttons render only the selected letter's evidence beneath the map,
never a duplicate list of all mastered letters. Scored topics needing mastery
evidence remain visible below. Topic mastery and exact activity mastery are
explained separately: Main or another non-Basic qualifying activity counts the
topic once; other variants can still need practice. Supporting skills contribute
to recorded catalog coverage, not a claimed reading-level percentage. Archived
unscored exposure is separate from scored attempts and mastery; inferred dates
never contribute to exact weekly gains.

The selected reader persists across reloads and changes through a compact dropdown, not a family tile grid. Next practice and recent mastery lead, followed by the weekly summary and journey. Static horizontal meters show recorded mastery coverage, never a reading-level percentage. An empty meter is paired with “Not assessed” or no-evidence copy, not a judgment of ability. Milestones use details/summary with written state and a plus/minus indicator. Exact variants and dated evidence sit beneath. Assessment guidance and technical sync details are collapsed. Active sync uses a prominent working inset, written steps and a determinate bar; a separate rotating sync icon signals activity, not completion.

Queue, setup, and counting explanations also use native disclosures. Summary hover uses the soft role; all keyboard focus uses the shared visible outline.

Opening a milestone separates scored lessons needing mastery evidence from
mastered topics and topics with no recorded scores. Practice rows show the best
recorded non-Basic score, not an inferred latest score or recommendation. A
single practice topic opens automatically. Variants use compact score rows;
dated history, capture provenance and policy explanations remain in a separate
disclosure. Alphabet milestones additionally use accessible letter buttons,
checks for recorded mastery and an emphasized practice state. Selecting a letter
opens its existing evidence instance and moves keyboard focus to its heading.
Desktop letters form two rows of thirteen; mobile uses fluid 48px-minimum cells.
Letter selection never changes assignments or treats missing evidence as failure.

Exact variant rows, recommendations, recent mastery and the verified queue share
one assignment-control component: written mastery/assignment state and a quiet,
44px-minimum Assign or Unassign button. Parent clicks connect to the native
workflow immediately, but state is never optimistically claimed as saved.
Controls and reader switching disable during work; errors retain previous
verified state and do not automatically retry a mutation. Archived profiles show
read-only state. Assignment status is explicitly the last verified snapshot,
not a live connection. Manual extras are protected; automatic top-ups pause at
ten or more. On narrow screens, variant controls sit beneath score/mastery rows;
wrapping also accommodates enlarged text without escaping the page.

Letter selection emphasizes the selected letter and lesson heading. It focuses
the heading without implicit browser scrolling, waits for disclosure layout,
and scrolls the heading (never the full evidence panel) to a 24px inset only
when it is outside the viewport. Normal motion uses native smooth scrolling and
a brief background transition; reduced motion uses instant scrolling and a
persistent static highlight. Repeated selection cancels pending frame work.

Recent mastery lists exact lesson/activity identities, including Basic, with
each variant's first qualifying exact lesson date. Topics deduplicate across
milestone placements but different variants remain separate. The latest five
appear initially, with an explicit show-all action. Unknown/inferred dates and
snapshot capture dates never qualify as recent lesson dates. Lesson headings,
variant score rows and alphabet accessible names identify which variants have
mastery evidence; aggregate coverage still counts topics, not variants.

Recent mastery also names the final score of the first qualifying attempt
sequence. It does not substitute a later/latest score. The score is secondary
green text beneath the exact activity identity; unknown proof scores are
explicitly unavailable rather than guessed from history.

### Lesson cards and reading checks

Recommendation cards use paper, a quiet stroke, 12px corners, and 20px padding; the first card uses soft fill. Rank and score text precede the lesson title, variant, and a rule-separated goal. The score badge is plain text, not a pill or invented chip system. Reading checks are a soft inset with 16px corners and explanatory supporting text.

### Navigation, feedback, and motion

The top bar uses the original letter mark, restrained supporting brand text, and a plain setup link. Its local/private label hides at 760px and below. Connection activity uses a small dot plus written live status; failures use the warning trio and explicit reconnect/setup actions.

Sync immediately reveals a blue-toned working inset with a larger heading, a
32px original sync icon, a 10px determinate bar and written Read scores / Update
lessons / Verify & finish steps. The busy button retains its blue action color
and exposes aria-busy. Elapsed time remains secondary. Steps follow real backend
phases; the bar advances only at observed milestones and fills only after
successful completion. No visible percentages or looping progress bars are used.
The separate activity icon rotates only during confirmed activity, pauses offscreen
or in a hidden tab, and becomes static with reduced motion. Disconnection and
failure stop activity feedback and retain partial progress. Manual edits share the
working treatment but never display mastery-sync steps. Reduced motion also
removes bar transitions and primary active translation. Forced-colors styles
preserve the indicator, activity dot and button boundaries. Saved All Progress
scores remain undated snapshot evidence, never fabricated weekly gains.

## Do's and Don'ts

### Do:

- Do pair semantic color with written state and evidence.
- Do retain native disclosures, visible keyboard focus, and minimum 44px control heights.
- Do stack mobile milestone metadata below its title and preserve readable queue columns in a scroll region.
- Do keep missing, uncertain, and stale evidence explicit.

### Don't:

- Don't copy Khan characters, artwork, screenshots, or layout.
- Don't replace the original SVG language with mascots or decorative raster imagery.
- Don't imply reading-level certification from lesson mastery or catalog coverage.
- Don't introduce decorative shadows into this flat, rule-led system.
