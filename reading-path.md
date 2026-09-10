# Minimum viable Khan Kids reading path

This project's primary outcome is **independent reading**, reached as directly
as Khan Academy Kids permits without advancing past an unmastered prerequisite.
Finishing ELA, collecting lessons, and completing every grade are explicitly
not goals.

The machine-readable source of truth is
[`data/reading-curriculum.json`](data/reading-curriculum.json). This document
explains that path for a person. A change to the selected route belongs in the
JSON and in [`curriculum-decisions.md`](curriculum-decisions.md), so the reason
for adding, removing, or reordering a lesson is never lost.

## What the path means

This is a mastery-gated spine, not a fixed calendar. The numbered order is the
default route for a typical learner who already knows letter-sound
correspondences. A child may have several unlocked tracks at once, but the
assignment queue stays at ten or fewer activities. Each activity follows the
family's `Basic → Main → Practice 1 → Practice 2` rule, skipping variants Khan
does not provide.

A rung advances after either one 100% result or two consecutive results of at
least 90%. Lower results hold the rung or trigger a narrower Khan prerequisite.
They do not cause automatic promotion.

The queue also enforces instructional diversity. All five short-vowel/CVC-middle
tracks share a maximum of three active lessons. Within that group, current
80–99% evidence is preferred, followed by the most advanced mastered sequence
and then curriculum order. A promotion retains the same group slot. When a
complete vowel track frees a slot, the next deferred vowel rotates in. The core
allocation may remain underfilled rather than admit redundant, premature, or
non-reading filler; curated stretch work fills the remaining queue positions.

For the current segment, up to eight positions are reserved for the mastery
spine and at least two for a curated stretch pool of printed CVC lessons. Stretch lessons remain
assigned until attempted. Results of 70% or higher keep the lesson in its slot;
a lower result defers it while preserving the score, and later mastery in a
configured supporting track makes it retry-eligible. This diagnostic exposure
does not mark a prerequisite mastered.

## Entry point

The current route assumes the learner can:

- recognize uppercase and lowercase letters reliably;
- say a letter's common sound after seeing its grapheme; and
- select the corresponding grapheme after hearing its common sound.

Those skills are already established for Student A. A different learner who does
not meet them needs a letter-sound entry segment before this path.

## Selected progression: foundational segment 1

| Order | Khan lesson family | Purpose | Opens after |
|---:|---|---|---|
| 1 | Blend Sounds 2 | Blend an ordered three-phoneme sequence | Entry criteria |
| 2 | Make New Words | Preserve order while manipulating one phoneme | Entry criteria |
| 3 | Words: End Sound | Attend to the final position in a three-phoneme word | Entry criteria |
| 4 | Blend Syllables | Maintain the already-strong larger-unit blending skill | Entry criteria |
| 5 | Short Vowel Sound a | Establish the first controlled CVC middle sound | Entry criteria |
| 6 | Words with a | Transfer short *a* into printed CVC words | Prior row in the same track |
| 7 | Short Vowel Sound i | Add a second controlled middle vowel | Short-*a* track mastered |
| 8 | Words with i | Transfer short *i* into printed CVC words | Prior row in the same track |
| 9 | Short Vowel Sound e | Add short *e* without increasing positional complexity | Entry criteria |
| 10 | Words with e | Transfer short *e* into printed CVC words | Prior row in the same track |
| 11 | Short Vowel Sound o | Add short *o* without increasing positional complexity | Entry criteria |
| 12 | Words with o | Transfer short *o* into printed CVC words | Prior row in the same track |
| 13 | Short Vowel Sound u | Add short *u* without increasing positional complexity | Entry criteria |
| 14 | Words with u | Transfer short *u* into printed CVC words | Prior row in the same track |
| 15 | Words with b, c, d | Use Student A's prior 92% CVC-beginning performance as a near-mastery option | Entry criteria |
| 16 | Words with m & n | Decode comparatively easy continuous final consonants | Short-*a* track mastered |
| 17 | Words with p & s | Generalize CVC endings to a stop and continuous consonant | Words with m & n mastered |
| 18 | Middle Sound | Reassess middle-position analysis after narrow CVC work | All short-vowel and ending tracks mastered |
| 19 | Ending Sound | Reassess final-position analysis after narrow CVC work | Prior row in the same track |
| 20 | First & Last Sound | Coordinate both boundary positions | Prior row in the same track |
| 21 | Isolate All Sounds | Segment an entire ordered phoneme sequence | Prior row in the same track |
| 22 | 1-Syllable Words | Apply the component skills to whole-word decoding | Whole-sequence track mastered |
| 23 | Blends: st | Introduce an adjacent-consonant sequence | One-syllable words mastered |
| 24 | Blends: sp | Generalize the adjacent-consonant sequence | Prior row in the same track |
| 25 | st, sp, sk, sm | Check transfer across several initial blends | Prior row in the same track |

This segment contains 25 deliberately selected lesson families and 83
assignable variants, compared with 2,956 activity placements in the captured
ELA archive. Within a selected family, the current policy advances through each
available variant; mastery controls when that advancement occurs.

## The finish line

Completing this first segment does **not** by itself establish independent
reading. It establishes the foundation needed for the next controlled segment.
Before the project can claim its primary outcome, the path must also validate:

1. the remaining short-vowel CVC patterns;
2. common consonant digraphs and broader blends;
3. silent-*e*, common vowel teams, and r-controlled vowels;
4. longer-word decoding; and
5. accurate reading of short connected text inside Khan Kids.

Those later segments should be added narrowly, using actual Student A evidence
and the archived Khan inventory. They should not become a dump of every phonics
or ELA lesson. The eventual exit criterion is demonstrated decoding of
unfamiliar words plus accurate connected-text reading—not reaching the bottom
of Khan's library.

## How the path is recorded

The project keeps distinct records so plans and evidence are not confused:

| Record | Meaning |
|---|---|
| `data/reading-curriculum.json` | Versioned, reusable route and prerequisite graph for a typical learner |
| `curriculum-decisions.md` | Why lesson families were selected, deferred, removed, or reordered |
| `student-records/student-a-lesson-attempts.csv` | Immutable observed scores and dates |
| `student-records/student-a-assignment-actions.csv` | Immutable checked/unchecked actions and their reasons |
| `student-records/student-a-mastery-state.csv` | Human-readable mastery decisions at specific rungs |
| `student-records/student-a-progress-log.md` | Narrative session summaries and exceptions |
| `student-records/student-a-reading-sync-log.md` | Report from every successful review and apply command |
| `private/student-a-reading-plan.json` | Temporary reviewed proposal for the next queue; never the historical record |

This separation lets another family reuse the generic path without inheriting
Student A's data, while Student A's actual route—including repeats, corrections,
and deliberate skips—remains reconstructable.
