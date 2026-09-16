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
