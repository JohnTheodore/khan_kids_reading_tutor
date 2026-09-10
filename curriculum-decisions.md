# Curriculum decision log

This append-only log records material changes to the reusable Khan Kids reading
path. The curriculum JSON says *what* the software may assign; this file says
*why*. Student-specific results and actions remain under `student-records/`.

## 2026-09-09 — Establish foundational segment 1

**Decision:** Adopt `minimum-viable-reading-path-v1`, an 18-lesson-family path
from ordered three-phoneme work through early one-syllable decoding and initial
consonant blends.

**Goal:** Move a learner who already has secure bidirectional letter-sound
knowledge toward independent reading quickly, without requiring completion of
the full ELA catalog and without relaxing the mastery gates.

**Included:** Three-phoneme blending, phoneme manipulation, final-sound work,
short-*a* and short-*i* CVC middle work, selected CVC endings, complete
phoneme-position analysis, one-syllable words, and the smallest initial-blend
sequence available in the captured Khan catalog.

**Deferred:** Repeated alphabet work, rhyming, narrative sequencing, broad
onset-rime drilling, two-syllable words, advanced vowel patterns, grammar,
comprehension topics, and general edutainment. A deferred lesson can be added
later as a targeted corrective or as part of a reviewed later decoding segment;
it is not silently admitted to the active queue.

**Important limitation:** This first segment is a foundation, not yet a valid
claim of independent reading. Later decisions must add a minimal route through
the remaining vowel patterns, digraphs, longer words, and connected-text
accuracy. The stopping rule will be demonstrated reading ability, not catalog
completion.

## 2026-09-10 — Expand the daily choice set to ten

**Decision:** Increase the active queue ceiling from five to ten and add five
low-inference-gap entry tracks: short *e*, short *i*, short *o*, short *u*, and
`Words with b, c, d`.

**Reason:** Student A has secure bidirectional letter-sound knowledge and needs a
larger set of appropriate choices. The four vowel entries preserve a simple
sound-recognition task before their corresponding printed CVC work.
`Words with b, c, d — Main` is especially well placed because Student A's archived
result is 92%, making it a near-mastery reassessment rather than a conceptual
jump.

**Guardrail:** Harder whole-sequence lessons remain locked until all five
short-vowel tracks and the selected CVC-ending tracks are mastered. Expanding
choice therefore does not unlock the previously deferred first-grade tasks.

## 2026-09-10 — Cap simultaneous short-vowel work at three tracks

**Decision:** Treat the five short-vowel/CVC-middle tracks as one instructional
diversity group with at most three active lessons. Preserve mastery progression
inside every vowel track, but rotate deferred vowels in only as active vowel
tracks finish.

**Reason:** Five nearly identical short-vowel activities overconcentrate the
choice set and introduce unnecessary simultaneous contrasts. A smaller,
cumulative set better supports attention, retrieval, and the low-inferential-gap
transition from a vowel sound to that vowel in printed CVC words.

**Current application:** Retain short *a* and short *u* because both have recent
89% evidence, plus short *i* as the next curriculum-ordered diagnostic. Defer
short *e* and short *o*. Do not backfill their slots with grammar, read-aloud
stories, repeated low-yield activities, or prerequisite-locked phonics. Until a
sound replacement becomes eligible, eight assignments is the correct queue.

## 2026-09-10 — Reserve two rotating stretch slots

**Decision:** Maintain eight mastery-path slots and two stretch slots so the
learner has ten choices. Start with `Words with f, g, h — Main` and
`Words with m & n — Main`. All stretch-pool families are print-linked CVC work;
grammar, passive stories, and unrelated ELA remain ineligible.

**Persistence rule:** Once assigned, a stretch family is pinned until it has at
least one score. A score of 70% or higher keeps it active under the ordinary
mastery progression. A score below 70% removes it from the active queue but
preserves its attempt and marks it deferred, rather than rejecting it from the
curriculum.

**Retry rule:** A below-70% family cannot return immediately. It earns another
attempt allowance only after mastery of a configured supporting track. A
fallback stretch already on screen is likewise pinned until attempted, so a
newly re-eligible lesson cannot displace an untried one.

**Curated fallback order:** `Words with b & d`, `Words with m, n, p`, then
`Words with g & k`. These retain the printed CVC structure and vary beginning
and ending position without introducing blends, multisyllable words, grammar,
or connected-text demands prematurely.
