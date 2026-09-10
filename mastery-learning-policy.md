# Khan Kids mastery-learning policy for Student A

**Scope:** Reading and phonics inside Khan Academy Kids only. No off-screen
activities are part of this plan.

## The operating cycle

For each skill family, use one rung at a time:

1. Assign **Basic**.
2. Review the score history, not just the latest summary score.
3. If mastered, remove the completed assignment to keep Student A's queue clean.
4. Assign the next available rung: **Main → Practice 1 → Practice 2**.
5. If not mastered, do not promote. Hold the rung; when a reviewed curriculum
   rule provides a narrower Khan Kids corrective, use it before reassessment.

This is a deliberate adaptation of Khan Kids' generic recommendation, which
normally starts with Main and uses Basic for support. It preserves the family's
preferred Basic-first sequence while retaining Khan's intended progression from
Main to Practice 1 to Practice 2.

## Promotion rule

| Evidence in Khan Kids | Status | Action |
|---|---|---|
| **Latest completed attempt is 100%** | Mastered at this rung | Remove it and assign the next rung |
| **90–99% once** | Provisional | Reassess the same rung; promote after a second score of at least 90% |
| **80–89%** | Strong, not yet mastered | Repeat or use a closely matched Khan Kids corrective; do not promote |
| **50–79%** | Developing | Use a prerequisite or Basic corrective in Khan Kids; do not promote |
| **Below 50%** | Significant gap | Step back to a prerequisite Khan Kids lesson; do not promote |

The two-score rule for 90–99% is our conservative operating standard, not a
universal research cutoff. It reduces the chance that one good attempt causes a
premature promotion. A latest score of 100% counts immediately because that is
the family's explicit mastery rule. A later regression supersedes earlier
mastery evidence until the gate is met again. The next rung then acts as a
transfer check in a less-supported or more varied form.

Khan Kids colors 80–100% green and calls that range “strong understanding.” We
use a stricter threshold for promotion because a green score is a broad report
band, not proof that a prerequisite is stable enough to build on.

## What “mastered” means here

- Mastery attaches to a **specific lesson variant**, not permanently to the
  whole reading skill.
- Promotion changes the difficulty or variety; it does not erase history.
- Every attempt remains in `student-records/student-a-lesson-attempts.csv`.
- Every checked or unchecked assignment is recorded in
  `student-records/student-a-assignment-actions.csv` and summarized in the sync
  log.
- If performance drops below 80% on the next rung, hold that rung. A reviewed
  curriculum decision may add a narrower Khan Kids corrective, but the
  software does not invent one automatically.
- Correctives should respond to the actual error area. Repeating an identical
  activity without targeted support is not, by itself, mastery learning.

## Queue composition and stretch lessons

The desired queue contains ten assignments: up to eight mastery-path lessons
and enough curated printed-CVC stretch lessons to fill the remaining positions.
No more than three active lessons may come from the combined short-vowel/CVC-
middle group.

Stretch exposure is diagnostic; it does not waive or mark a core prerequisite
as mastered:

| Stretch evidence | Queue decision |
|---|---|
| No attempt yet | Pin the lesson; do not rotate it |
| 70–89% | Keep the same rung active |
| One 90–99% | Keep it active as provisional mastery evidence |
| Latest score is 100%, or the latest two are both ≥90% | Promote to the next available variant |
| Below 70% | Defer but retain its history and retry eligibility |

A deferred stretch lesson receives another attempt allowance only after a
configured supporting track is mastered. A fallback already active remains
pinned until attempted, preventing immediate remove/re-add oscillation.

## Decision snapshot — September 9, 2026

This table preserves the evidence available on September 9. It is historical;
the append-only attempt and action CSVs are authoritative for later activity.

| Skill | Result | Decision |
|---|---:|---|
| Blend Syllables — Basic | 100% | Mastered; Basic removed; Main assigned September 9 |
| Blend Sounds 1 — Basic | 90%, then 80% | Do not promote; correct and reassess in Khan Kids |
| Blend Sounds 2 — Basic | 85%, then 92% | Provisional; one more score of at least 90% before Main |
| Beginning Sounds 2 — Basic | 83%, then 70% | Do not promote; correct and reassess in Khan Kids |
| Word Families — Basic | 72%, 69%, then 69% | Do not promote; prerequisite correction within Khan Kids |
| Words: End Sound — Basic | 100% | Mastered; Basic removed; Main assigned September 9 |
| Make New Words — Basic | 92% | Provisional; one more score of at least 90% before Main |
| Middle Sound — Basic | 50% | Do not promote; prerequisite correction within Khan Kids |
| Ending Sound — Basic | 58% | Do not promote; prerequisite correction within Khan Kids |

## Current queue — September 10, 2026

The live queue was verified at ten assignments: eight core lessons plus
`Words with f, g, h — Main` and `Words with m & n — Main` in the two initial
stretch positions. Short *e* and short *o* were deferred, not removed from the
curriculum, to enforce the three-active-vowel limit. The exact list and stretch
retry milestones are maintained in [`reading-path.md`](reading-path.md).

## Why this follows mastery learning

Bloom's model keeps the learning goal fixed while allowing time and support to
vary. It uses small sequential units, formative checks, specific corrective
instruction after errors, and reassessment before advancement. Bloom did not
establish one universal percentage cutoff; the standard must be defined for the
subject and learner. Our percentage rules above are therefore a transparent
local policy, while the feedback–corrective–reassessment cycle is the
research-based core.

A meta-analysis of 108 controlled evaluations found positive effects on exam
performance, with larger effects for weaker students, while also finding that
results varied with the procedures, design, and course content. That supports
using the method thoughtfully rather than treating a score threshold as magic.

## Sources

- Benjamin S. Bloom, [*Learning for Mastery* (1968), ERIC ED053419](https://eric.ed.gov/?id=ED053419).
- Kulik, Kulik, and Bangert-Drowns, [“Effectiveness of Mastery Learning Programs: A Meta-Analysis” (1990)](https://doi.org/10.3102/00346543060002265).
- Khan Academy Kids, [Progress reports in the app](https://khankids.zendesk.com/hc/en-us/articles/4403614100109-Progress-reports-in-the-Khan-Academy-Kids-app).
- Khan Academy Kids, [Module 3: Assigning lessons](https://khankids.zendesk.com/hc/en-us/articles/360042194831-Module-3-Assigning-lessons).
