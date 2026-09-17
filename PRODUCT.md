# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Parents and homeschool families reviewing each child's learn-to-read journey
after practice sessions. They need a quick overview and inspectable evidence.

## Product Purpose

Focus Khan Kids practice on reading, maintain a mastery-gated assignment queue,
and explain current skills, weekly growth, and the remaining reading journey.

## Operating Context

A local Python service serves a browser dashboard. Existing Android automation
collects scores and changes assignments when the parent explicitly starts sync.
Parents can also assign/unassign an exact displayed lesson variant through the
same native save-and-verify workflow. Manual extras are protected and may exceed
ten; automatic top-ups pause at ten or more. Manual unassignment persists as an
exact-variant exclusion until the parent assigns it again.
Daily use makes no LLM calls. Family identities and credentials remain private.

## Capabilities and Constraints

Reuse the native mastery evaluator, curriculum, attempt ledger, and recommendation
adapter. Preserve occurrence-aware attempts and account/student separation.
Dashboard analytics are read-only and must not expand or change the sync path.
The current automated assignment path covers a foundational segment, not the
whole journey to second-grade reading. Lesson results do not certify reading level.
Missing, stale, or uncertain evidence must remain explicit. Never infer mastery
of skipped prerequisites. Forecasts are tentative and withheld without history.

## Brand Commitments

Friendly Khan Kids-adjacent warmth throughout the website, with original branding,
icons, typography, and composition; no copied characters or captured artwork.

## Evidence on Hand

Captured ELA and Letters catalogs, standards/skill groups, per-student dated
attempts and durable mastery records, and native sync reports. No validated
grade-level oral fluency assessments are currently available.

## Product Principles

- Show where the child is, what changed this week, and what to practice next.
- Progressive disclosure: overview, milestone, subskill, exact dated evidence.
- Separate app lesson mastery, curriculum coverage, and demonstrated reading.
- No sibling leaderboard, invented metrics, or unjustified completion dates.

## Accessibility & Inclusion

Readable contrast, keyboard access, reduced motion, both color themes, small
screens, enlarged text, and meaningful labels/states. Automated checks are
evidence, not a claim of complete WCAG certification.
