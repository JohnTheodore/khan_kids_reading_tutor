# Khan Mastery Sync Performance Audit

**Date:** September 10, 2026  
**Audience:** Maintainer of `khan_kids_reading_tutor`  
**Scope:** One live, profiled no-change sync for Student A on a Pixel Tablet over
wireless ADB, followed by implementation and repeat benchmarks.

> **Status:** The initial 157.31-second measurement is the baseline. All eight
> recommendations were subsequently implemented; the measured results are in
> [Implementation benchmark](#implementation-benchmark).

## Executive conclusion

The successful two-phase sync took **157.31 seconds**:

- read-only review: **61.93 seconds**;
- apply and exact verification: **95.38 seconds**; and
- assignment changes: **zero**.

The dominant problem is not Python, JSON, XML parsing, computer vision, or
mastery planning. In the review profile, **70.1%** of time was blocked waiting
for subprocess/device responses and **29.5%** was in explicit sleeps. Apply was
similar: **72.9%** blocked and **26.7%** sleeping. Together those categories
accounted for about **99.6%** of each run.

The first optimization should be a verified no-op fast path: when review has
already proved that the live queue equals the desired queue and there are no
actions, do not run apply. That would have reduced this sync from 157.31 to
61.93 seconds, a **60.6% reduction**, without skipping a write verification
because there were no writes.

The largest architectural gain after that is to stop invoking the legacy
`uiautomator dump` command for every observation. Ten hierarchy dumps consumed
27.92 seconds during review; sixteen consumed 45.07 seconds during apply. A
persistent UI Automator or `UiAutomation` session can query the active
accessibility tree and wait for specific conditions without repeatedly
connecting, idling, serializing XML, and transferring a file.

## Sync result

- New attempt records: **0**
- Mastered this run: **none**
- Unchecked: **none**
- Promoted: **none**
- Added: **none**
- Final desired and live queue size: **10**
- Result: **applied and exactly verified**

The review and apply reports were appended to
`student-records/student-a-reading-sync-log.md`. No assignment checkbox was
changed.

## Measurement method and limitations

The workflow was run through Python's deterministic profiler, with independent
nanosecond wall-clock timestamps around each process. The profile recorded
function call counts and cumulative/self time. No production instrumentation
or source code was added for the measurement.

Two preliminary attempts failed closed before data collection because the app
started outside the teacher report: one took 14.48 seconds and one took 10.42
seconds. These are excluded from the 157.31-second successful-sync total. They
show a separate startup-navigation gap, discussed below.

This is one physical-device sample over wireless ADB. It is sufficient to
identify order-of-magnitude bottlenecks, but not to establish p50/p95 latency.
There were no assignment changes, so the cost of finding a lesson in All
Progress, changing a checkbox, saving, and verifying that individual action
was not measured in this run. `cProfile` adds some overhead, but only about
150,000 calls were observed and the workflow was almost entirely blocked or
sleeping; a repeated unprofiled benchmark is still required before claiming a
production speedup.

## Timing breakdown

### Review phase — 61.93 seconds

| Operation | Calls | Cumulative time | Interpretation |
|---|---:|---:|---|
| Full assignment/score scan | 1 | 53.94 s | Main critical path |
| ADB subprocess/device waits | 61 | 43.52 s | Overlaps the UI operations below |
| UI hierarchy dump and pull | 10 | 27.92 s | 2.79 s per observation |
| Explicit sleeps | 27 | 18.24 s | 0.68 s average |
| Select and verify one student | 1 | 15.75 s | Modal, two screenshots, taps, waits |
| Scroll-to-top verification | 1 | 7.12 s | Two hierarchy probes plus one swipe |
| Awake-session setup/restoration | 1 session | 5.95 s | Settings reads/writes and waits |
| Close four score dialogs | 4 | 4.96 s | About 1.24 s each |
| Safe launcher check | 1 | 1.91 s | Wake, lock, foreground checks |
| Two screenshots | 2 | 1.22 s | Needed for checkbox-state vision |
| Checkbox vision | 4 reads | 0.41 s | Not a bottleneck |
| Parse ten XML trees | 10 | 0.025 s | Not a bottleneck |
| Build mastery queue | 1 | 0.0004 s | Negligible |
| Build/write plan and report | 1 | under 0.012 s | Negligible |

### Apply phase — 95.38 seconds

| Operation | Calls | Cumulative time | Interpretation |
|---|---:|---:|---|
| Assignment scans | 2 | 87.39 s | Full stale-plan scan plus final scan |
| ADB subprocess/device waits | 87 | 69.76 s | Dominant blocked time |
| UI hierarchy dump and pull | 16 | 45.07 s | 2.82 s per observation |
| Initial full history scan | 1 | about 54.86 s | Revalidates reviewed fingerprint |
| Final row-only verification | 1 | about 32.53 s | Runs even with zero actions |
| Select and verify one student | 2 | 30.91 s | Repeated in both scans |
| Explicit sleeps | 54 | 25.47 s | Fixed delay rather than condition wait |
| Scroll-to-top verification | 2 | 14.07 s | Four hierarchy probes total |
| Awake-session setup/restoration | 1 session | 6.05 s | Fixed per invocation |
| Close four score dialogs | 4 | 4.96 s | Repeats review's score inspection |
| Four screenshots | 4 | 2.53 s | Student-filter verification repeated |
| Parse sixteen XML trees | 16 | 0.038 s | Not a bottleneck |
| Validate/write/report | 1 | under 0.006 s | Negligible |

Cumulative rows overlap: for example, hierarchy-dump time is included in ADB
wait time and scan time. The self-time profile avoids double counting: review
spent 43.38 seconds polling subprocesses and 18.24 seconds sleeping; apply
spent 69.51 seconds polling and 25.47 seconds sleeping.

## Why hierarchy observation is slow

Each `root()` call currently starts `adb shell uiautomator dump`, writes XML on
the tablet, then runs a second ADB command to pull it. The AOSP implementation
connects a `UiAutomation` bridge and calls `waitForIdle(1000, 10000)` before it
reads the root and serializes the hierarchy. That means it seeks a full second
without accessibility events on every dump, with a possible ten-second global
timeout. This directly explains a substantial part of the observed 2.8-second
average. [AOSP `DumpCommand`](https://android.googlesource.com/platform/frameworks/uiautomator/+/17fac436d78f6ac642386a245fb4fdb7243a91a4/cmds/uiautomator/src/com/android/commands/uiautomator/DumpCommand.java)

Android's current UI Automator guidance favors predicate-based element lookup
with built-in waits. It says a blanket `waitForStable()` is usually unnecessary
because element lookup already has a timeout; stability waits should be used
when the complete UI genuinely must stabilize. This maps well to our workflow:
after a tap, wait for the expected dialog/title/filter value instead of always
sleeping and then performing a full dump. [Modern UI Automator guidance](https://developer.android.com/training/testing/other-components/ui-automator)

The lower-level `UiAutomation` API exposes `getRootInActiveWindow()`, input
injection, and shell execution through one connected automation session. A
persistent on-device runner can therefore preserve the current black-box model
without modifying Khan Kids. [Android `UiAutomation` API](https://developer.android.com/reference/android/app/UiAutomation)

Starting a persistent ADB shell alone is not the main answer. ADB already uses
a long-running server that manages device connections, and local process-launch
self time was only 0.03–0.04 seconds in these runs. Batching simple settings
commands may save a little latency, but it cannot address the repeated
UI-Automation idle waits. [Android Debug Bridge architecture](https://developer.android.com/tools/adb)

## Other avoidable work

### Zero-action apply

Apply always performs a full history scan and a final row scan. That is useful
when a human-reviewed plan may be stale and writes will occur. It provides no
additional write safety when `actions` is empty: review already captured the
live rows and histories, built the desired queue, and proved the set difference
was empty.

### Fixed settling sleeps

The code has one-, two-, and four-second waits after taps, saves, filter changes,
and navigation. These make slow devices reliable, but every successful fast
transition still pays the full delay. The correct replacement is bounded
condition polling: return immediately when the expected postcondition appears,
retain a conservative timeout, and capture diagnostics on timeout.

### Repeated student-filter work

Student filtering consumed 15.75 seconds in review and 30.91 seconds in apply.
The apply run selected and visually verified the filter twice. The second scan
should reuse a validated filter when the report has not been left, or cheaply
verify its displayed value before reopening the modal. The two screenshots and
computer-vision reads are not computationally expensive; opening the modal,
dumping state, transferring screenshots, and waiting are expensive.

### Scroll-boundary detection

Each scroll-to-top operation dumps the hierarchy before and after a gesture to
look for an unchanged signature. That safe fix stopped the former repeated
"screen shaking," but costs about seven seconds even when one gesture reaches
the top. A persistent UI session can use a known first-row postcondition or
`UiScrollable`/predicate-based scrolling while retaining an explicit boundary
assertion.

### Score-history dialogs

Four scored assignments required four dialog opens, hierarchy observations,
and closes. The local parsing took milliseconds; UI round trips took seconds.
A visible-row fingerprint could avoid reopening unchanged histories, but that
optimization has a mastery-safety caveat: a new attempt can have the same score
as the prior attempt. Khan's report and the current record model already cannot
distinguish two same-day attempts with identical scores. Any cache must include
periodic full reconciliation and must be proven not to change promotion
decisions on archived fixtures.

### Incomplete cold-start navigation

The launcher reliably wakes, unlocks, and opens Khan Kids, but the sync still
requires a logged-in teacher report. The two preliminary runs correctly failed
instead of guessing from the profile chooser and Students page. A complete
state machine should recognize and safely traverse profile chooser → parent
password → Students → Class Reports → Assignments. It must combine multiple
screen predicates with a postcondition after every tap; a naked coordinate tap
would recreate the Add Students incident risk.

## Recommended implementation plan

### Phase 0 — Build permanent, secret-safe measurements

Add structured spans using a monotonic clock around:

1. connection and awake setup;
2. unlock and app launch;
3. teacher/report navigation;
4. filter inspection/change;
5. top-boundary reset;
6. each hierarchy observation and screenshot;
7. each score-dialog open/read/close;
8. pure parsing, record merge, mastery evaluation, and plan writing;
9. each uncheck/add/save; and
10. final verification and device-setting restoration.

Write aggregate durations and counts to the existing sync report, but never
command arguments, PIN digits, password fields, raw UI text, or secret paths.
Capture failure-stage timing as well as success timing. Establish p50 and p95
from at least ten runs in four scenarios: warm no-op, cold PIN-locked no-op, one
new score/no promotion, and one real promotion.

**Acceptance:** timing instrumentation changes no UI behavior, adds under 1%
wall time, and passes an automated redaction test containing canary secrets.

### Phase 1 — Add the zero-action fast path

When review produces an empty action list, finalize it as a verified no-op and
do not request or execute apply. Keep the full desired queue and mastery holds
in the report. If the two-command review contract must remain, apply should
recognize an empty reviewed plan and exit before connecting to the tablet.

**Measured upper-bound benefit today:** save 95.38 seconds; 157.31 → 61.93
seconds.

**Safety invariant:** this shortcut is legal only with zero writes. Never skip
post-write verification when any checkbox changed.

### Phase 2 — Replace sleeps with postcondition waits

Create one shared bounded-wait primitive rather than custom polling loops. Each
action should declare its expected state transition, such as:

- lock screen disappears;
- Khan package becomes foreground;
- score dialog title matches the selected row;
- dialog disappears;
- student filter displays the target student;
- first assignment row is visible; or
- saved assignment appears with the expected checkbox state.

Poll quickly at first, back off modestly, and retain the existing generous
timeout. On timeout, capture one hierarchy and screenshot for diagnosis. Do not
globally lower timeouts or remove assertions.

**Target to validate:** remove 10–18 seconds from a typical review while
preserving or improving reliability.

### Phase 3 — Remove redundant report setup

Represent the current screen, selected student, scroll position, and report tab
as validated session state. Invalidate that state after any navigation whose
postcondition is uncertain. Within one invocation:

- filter to Student A once;
- retain that filter across score dialogs;
- avoid reopening the filter for final verification;
- reset to top using a direct first-row assertion; and
- reuse the most recent root when no UI event occurred between consumers.

**Target to validate:** reduce filter/top-reset work from roughly 22.9 seconds
per full scan to below 5 seconds.

### Phase 4 — Use one persistent UI Automator session

Prototype a small on-device instrumentation runner using stable UI Automator
APIs or lower-level `UiAutomation`. Keep one session alive for the complete
sync. Query nodes directly, use selectors and condition waits, and return only
the structured fields needed by the Python planner. Do not serialize and pull a
complete XML tree for every question.

Android currently recommends its newer UI Automator 2.4 API, but the published
artifact is still labeled alpha and under development. Benchmark it, but do not
make the repository depend on an alpha API without a deliberate compatibility
decision. A stable UI Automator 2.3 runner or carefully scoped `UiAutomation`
bridge is the conservative alternative.

Preserve Python as the single source of truth for curriculum, mastery, records,
plan validation, and reporting. The runner should be a transport/UI adapter,
not a second planner; this avoids duplicated policy logic.

**Target to validate:** cut hierarchy-observation time by at least 70%. Based on
this run, that is worth about 20 seconds in review and 32 seconds in apply.

### Phase 5 — Join review and apply when explicitly requested

Offer two modes:

- `review`: read-only, creates a human-reviewable plan;
- `sync`: scans once, builds the deterministic plan, enforces the action cap,
  applies it in the same live session, and performs exact post-write
  verification.

The existing separate apply command must retain a fresh stale-plan check because
the tablet can change between invocations. The joined command can safely reuse
its in-memory pre-action snapshot because no review gap exists. Print and log
the intended actions before the first write, and retain interruption-safe
per-action logging.

**Expected benefit when changes exist:** avoid the approximately 55-second
second full-history scan. Final post-write verification remains mandatory.

### Phase 6 — Add safe cold-start navigation

Extend the state machine for the known starting screens. For every transition,
require a conjunction of distinctive labels, expected 2560×1600 landscape
geometry, a target region separated from destructive controls, and a verified
destination. Parent credentials must be read lazily from `.secrets.json`, sent
without a complete password command-line argument, and never included in UI
dumps or screenshots retained after login.

**Acceptance:** fixture tests for every known screen, wrong-screen fault
injection, one-attempt credential policy, no interaction with Add Students or
Delete Student, and a live watched test from sleep through Assignments.

### Phase 7 — Consider incremental score-history reads last

Store a per-assignment observation fingerprint and inspect full score history
only when the visible row changes, the last full audit is old, or mastery could
change. Run a scheduled full reconciliation. Before enabling this optimization,
replay all archived attempts through both algorithms and prove identical
mastery decisions, desired queues, and action lists.

This is deliberately last because a missed attempt can affect promotion. UI
transport improvements offer large gains without weakening mastery evidence.

## Performance targets

These are engineering targets to benchmark, not guarantees:

| Scenario | Current | Near-term target | Longer-term target |
|---|---:|---:|---:|
| Warm no-op sync | 157.31 s | ≤62 s via no-op exit | 20–35 s |
| Review only | 61.93 s | 40–50 s | 20–35 s |
| Sync with changes | Not measured | Benchmark first | 40–70 s plus lesson-search cost |
| Pure planning/reporting | <0.02 s | No work needed | No work needed |

The longer-term targets assume conditional waits, one filter operation, fewer
scroll probes, and a persistent UI session. They must be accepted only after
p50/p95 measurements and safety-fault tests.

## What not to optimize

- Do not rewrite mastery evaluation or JSON/CSV handling for speed; they take
  milliseconds.
- Do not remove pre-write validation, checkbox-state verification, the
  non-target-student guard, action limits, interruption logging, or final
  post-write verification.
- Do not use blind coordinate taps to save hierarchy checks.
- Do not access Khan's private storage, reverse-engineer an unofficial API, or
  root the tablet merely for performance.
- Do not add parallel UI interactions; a single screen is inherently serial
  and concurrency would create races.

## Recommended order of work

1. Permanent timing spans and benchmark scenarios.
2. Zero-action fast exit.
3. Conditional waits.
4. Filter, root, and scroll-state reuse.
5. Safe cold-start navigation.
6. Persistent UI Automator proof of concept.
7. Joined one-command sync.
8. Incremental history cache only after equivalence proof.

This ordering delivers the largest low-risk win first, preserves the fail-closed
behavior that protected the roster today, and keeps curriculum/mastery logic in
one implementation.

## Source ledger

| Claim | Source | Publisher | Accessed |
|---|---|---|---|
| Legacy dump connects UiAutomation and waits for a 1-second idle window, bounded by 10 seconds | [`DumpCommand.java`](https://android.googlesource.com/platform/frameworks/uiautomator/+/17fac436d78f6ac642386a245fb4fdb7243a91a4/cmds/uiautomator/src/com/android/commands/uiautomator/DumpCommand.java) | Android Open Source Project | 2026-09-10 |
| Predicate element lookup has built-in waits; blanket stability waits are usually unnecessary | [Write automated tests with UI Automator](https://developer.android.com/training/testing/other-components/ui-automator) | Android Developers | 2026-09-10 |
| UiAutomation can retrieve the active root, inject input, and execute shell commands | [`UiAutomation` API](https://developer.android.com/reference/android/app/UiAutomation) | Android Developers | 2026-09-10 |
| ADB is a client/server system with a persistent server managing device connections | [Android Debug Bridge](https://developer.android.com/tools/adb) | Android Developers | 2026-09-10 |

## Implementation benchmark

All eight recommendations were implemented and validated on 2026-09-10. Both
post-change runs observed the same ten-lesson desired/live queue and therefore
used the new verified no-op path; no assignment was checked or unchecked.

| Scenario | Before | After | Reduction | Speedup |
|---|---:|---:|---:|---:|
| Complete no-op sync, cold cache | 157.313 s | 22.793 s | 85.5% | 6.90× |
| Complete no-op sync, warm cache | 157.313 s | 13.338 s | 91.5% | 11.79× |
| Review-equivalent work, warm cache | 61.930 s | 13.338 s | 78.5% | 4.64× |

The cold run read four full score dialogs in 13.035 seconds of scan time. The
warm run reused all four unchanged same-day histories and reduced scan time to
4.287 seconds. Its remaining wall time was primarily Android setting capture
and restoration, launch-state checks, persistent-backend initialization, two
scroll gestures, and five hierarchy reads. Operational timings are now appended
to the student's sync log on every successful run without command arguments,
screen text, credentials, or screenshots.

The cache was enabled only after tests established that an unchanged cached
history round-trips exactly and produces the same normalized attempt records as
the live history. A changed visible score or calendar day forces a live dialog
read, and `--full-score-scan` provides an explicit reconciliation path.
