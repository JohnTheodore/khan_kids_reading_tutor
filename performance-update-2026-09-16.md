# Sync progress and safe navigation cleanup — September 16, 2026

The preceding promotion run took 85.408 seconds, including 68 hierarchy reads
(51.125 seconds). Its initial score review took 26.487 seconds and its
add-and-verify phase took 23.329 seconds. Nested timing spans overlap.

Implemented changes:

- Flush workflow milestones, score-history completions, and verified actions to
  stderr. Emit a ten-second heartbeat without making device calls.
- Record the longest output gap as `performance.max_silent_seconds`.
- Reuse freshly read navigation roots and scrolling inputs; do not navigate
  from All Progress to Assignments merely to return to All Progress.
- Share assignment-row parsing and page traversal rather than duplicate them.
- Add a repeatable exact cross-file Python duplication audit and regression test.

A full-score, read-only live review verified the existing ten-item queue and
captured no new attempts. It took 43.210 seconds with 31 hierarchy reads
(19.054 seconds), and the longest output gap was 10.005 seconds. Initial score
review took 23.0 seconds. No assignment was added or removed for benchmarking.

This is not an apples-to-apples speed comparison with the promotion baseline:
the baseline included two assignment mutations and four queue scans, while
the new review performed no mutations. Promotion-path performance remains to
be measured during a naturally required promotion; do not manufacture one.
The previous no-change run took 39.741 seconds, so this sample does not
establish a wall-clock improvement for no-change runs. It does establish
continuous progress delivery and a verified unchanged queue.

The repository-wide review retained explicit safety boundaries: fresh mastery
histories, graphical checkbox checks, guarded navigation retries, an independent
queue scan after every Save, and final fixed-point verification. Refactoring
was limited to observed duplication and redundant transport work; there was no
wholesale rewrite of stable curriculum, persistence, or account behavior.

## Later profiling and startup optimization

A fresh mastery-sync baseline read five score histories and verified ten
unchanged assignments in 45.695 seconds. Score review took 26.476 seconds,
40 hierarchy reads took 21.619 seconds, startup took 8.817 seconds, and initial
screen stabilization reached 5.506 seconds. The hierarchy timing overlaps the
other phases.

Implemented guarded warm reuse: two matching supported foreground screens may
avoid the forced restart; unknown or modal screens retain cold startup.
Teacher-roster navigation may recheck after three seconds rather than twelve,
but retries only from a verified unchanged source and preserves the original
remaining loading wait without tapping an unknown screen. Progress now names
each guarded navigation attempt.

One comparison interrupted on the existing single-tap score-close path before
any assignment mutation. Incident KKRT-2026-09-16-AUTO-155144-441822 records it.
Modal closing was moved to the shared guarded transition mechanism, preserving
independent target confirmation and rejecting unexpected screens. The failure
cannot be conclusively attributed to warm startup.

The recovery comparison succeeded in 33.857 seconds, but its starting screen
was not comparable. A subsequent normal profile-chooser-start run read the
same five histories, verified ten unchanged assignments, and took 41.886
seconds: an observed 8.3% improvement over the baseline. Startup took 3.5
seconds; score review took 27.3 seconds. This is a small live sample, not a
guaranteed speedup. No scores were cached and no assignment mutation was
manufactured for benchmarking. Score review remains the dominant cost.

## Conditional wake waits and compression experiment

Wake now checks Android power state and skips the wake keyevent and its settle
sleep only when explicitly awake. Existing `lock 3` user rotation skips its
settle sleep; changed modes retain it. All prior setting restoration and
screen/lock verification remain in place. The extra power query has a cost,
so the net speedup must still be measured in complete comparable runs.

A read-only paired hierarchy test on the current tablet screen produced 7,917
bytes and ten control-signature nodes in both modes. Uncompressed took 0.184
seconds; compressed took 0.306 seconds. This single-screen sample demonstrated
no payload reduction or speed benefit. Compression therefore remains off in
production; an optional adapter pilot compares full and compressed control
signatures, including unlabeled leaves, and falls back on mismatch. Whole-app
compressed compatibility is not established by this sample.
