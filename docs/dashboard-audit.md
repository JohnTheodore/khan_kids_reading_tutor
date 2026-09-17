# Dashboard quality audit

## Visible sync activity

Sync acknowledges a click before the server responds: the button retains its
action color, and a prominent working inset shows the current phase, a larger
determinate bar and written Read scores / Update lessons / Verify & finish steps.
The original sync icon rotates to indicate activity, not completion. Progress
advances only from backend milestones; neither the bar nor elapsed time is used
to simulate completed work. Reduced motion keeps static activity feedback and
removes bar transitions. Icon motion pauses offscreen/in hidden tabs and stops
on connection loss, failure or completion. On mobile, the elapsed counter yields
space to the current task. Manual edits never display mastery-sync steps.

Synthetic browser coverage checks immediate feedback under a delayed POST,
real stage transitions, partial failure, completion, manual edits, both themes,
320px/mobile layouts, reduced motion, offscreen motion and connection loss.
The working states pass Axe WCAG A/AA checks. Screenshots remain private;
verification does not connect to the tablet or start a real sync.

## Parent assignment controls

Parents can assign/unassign each exact displayed variant from the journey,
recent mastery, recommendations and verified queue. All controls share one
component and one submission path. Mutations use the native launcher, workflow
lock, action journal, immediate save verification and fixed-point verification;
the browser never implements its own tablet automation or mastery policy.

Manual additions are protected extras. They may overflow ten; automatic top-ups
pause at ten or more and resume below ten after mastery/removals. Unassignment
changes only the requested variant and persists an exact-variant exclusion until
the parent assigns it again. Reviewed plans bind the private override digest and
are rebuilt against current preferences and score evidence before application.
Older reviewed plans must be regenerated because the plan schema is now v4.

Assignment state is explicitly last verified, not a live tablet view. No
optimistic saved claims, automatic mutation retry or archived-profile writes.
Controls disable during work. Refresh preserves open milestones, lesson/history
disclosures and letter selection. Read-only archived profiles identify their
permission state. Every exact variant also retains written mastery evidence.

Verification: 214 unit tests and 35 Chromium browser regressions pass. Tests
cover overflow, mastery removal, unassignment persistence, repeated-click/no-op
behavior, two-run fixed points, stale/tampered parent plans, unrelated retained
rows, strict endpoint input/authentication, matching verified worker results,
failed browser requests and archived controls. Synthetic axe checks cover four
widths in both themes, long Unicode text and 200% text size. Read-only checks of
actual saved data at 1280px/light and 390px/dark have zero axe violations, page
overflow or JavaScript errors. Private screenshots were inspected; no screenshots
ship. Impeccable's source detector reports zero findings, the substantial exact
cross-file duplication audit reports zero groups and working-tree privacy checks
report zero findings. No tablet connection or actual assignment mutation was
made during verification; physical save behavior is covered by the existing
engine and simulated regression tests, not a new on-tablet test.

## Letter navigation and qualifying scores

Letter selection previously combined native focus scrolling with scrolling a
large evidence panel using nearest alignment. It now waits for disclosure
layout, focuses the heading without scrolling, and brings only that heading to
a 24px inset if needed. Selected letter and heading retain a clear highlight;
normal motion uses native smooth scroll and a brief color transition, while
reduced motion uses instant positioning and the same static feedback. Pending
frame work is canceled on repeated selection. Regression coverage includes
desktop/mobile, repeated letter selection, viewport placement and reduced motion.

Recent mastery now displays the exact qualifying score from the native evidence
evaluation, never a later repeat score. A regression tests mastery at 94% followed
by a 70% attempt. No assignment changes, tablet connection or real sync.

## Exact mastery variants

Recent mastery is now per activity variant rather than a topic's first mastery:
Basic, Main, Practice 1 and Practice 2 are named explicitly. The activity's exact
first qualifying lesson date determines recency; later variants still appear
when the topic was mastered previously. Repeated milestone placements cannot
duplicate an activity. Unknown/inferred dates remain excluded. Lesson headings,
compact variant rows and alphabet accessible names also name mastered variants.
Aggregate coverage counts still refer to topics, not completion of every rung.
Two added browser regressions cover all four variants, repeated placements,
later-variant growth, unknown dates and the show-all interaction.

## Milestone drill-down polish

The parent-facing view now separates practice to confirm mastery, recorded
mastery, and unrecorded topics. Native saved data identifies 11 practice topics,
11 mastered topics and 61 unrecorded topics in Letters & sounds; unknowns are
not framed as failures. Each practice row exposes its best recorded non-Basic
score and opens compact variant rows; chronological history and capture sources
are secondary disclosures. Alphabet grids use original accessible letter buttons
and checks, not borrowed artwork. Grid selection opens the existing evidence
instance and focuses its summary, without changing assignments.

Verification: 194 unit tests and 28 browser regressions pass; the actual two
milestones pass Axe A/AA checks and page-overflow checks on desktop/mobile in
both themes. Native snapshot rows remain undated, never fabricated attempts.
The single Impeccable source detector pass reports no findings. Rendered before
and after captures are ignored under private/. No tablet connection or real sync.

## Selected-reader refinement

Follow-up: saved All Progress scores now contribute to every mapped native
reading lesson. Snapshot evidence remains separate from dated attempts and
never fabricates consecutive attempts or weekly gains. Custom and archived
account histories do not borrow native snapshots. The sync indicator now uses
completed workflow stages, no loop, and reaches full only after successful job
completion; failures retain partial progress. 194 unit tests and 26 browser
tests pass; the final source detector reports no findings.

The selected reader persists in the dropdown; family tiles are removed. Native
recommendations and recent exact-date mastery lead, followed by weekly gains,
an overall coverage meter and milestone meters. Unassessed evidence is named,
not mistaken for zero ability. Sync immediately shows starting feedback, then
an indeterminate indicator, phase and elapsed time; assessment guidance and
administrative details are collapsed. Reduced motion preserves live text.

Verification: 189 unit tests and 26 browser regressions; Axe zero findings at
320/375/768/1280px in both themes with disclosures expanded. Enlarged-text
overflow was found and corrected. No real sync or tablet connection.
The single Impeccable source scan flagged the active-sync animation as a
marquee: a verified false positive, since it contains no scrolling content,
exists only while a requested task runs and has a reduced-motion alternative.
No detector ignores were added. Radius and type advisories were reconciled
with the design documentation. Five-dimension rating remains 18/20 within
the tested Chromium scope; physical assistive technology and other engines
remain untested.

## Reading-journey dashboard audit

September 17, 2026. Scope: family overview, all 13 reading milestones,
seven-day gains, lesson/variant evidence, archived and missing history,
second-grade outlook, and the existing sync/setup/error workflows.
This is a read-only browser and records audit: no tablet connection or real
sync was initiated.

Implementation integrity: the interface expresses a reading journey, not a
generic analytics template. Native mastery decisions, durable action evidence,
lesson identities and recommendation ranking are reused. Analytics never
modify assignments. Coverage counts deduplicate grade placements and variants;
one non-Basic variant supplies evidence for a family, not certification of a
whole skill. Unknown records, Basic-only practice, inferred dates and reading
level remain explicitly distinguished.

| Dimension | Score | Evidence / limitation |
| --- | ---: | --- |
| Accessibility | 3 | Axe A/AA zero in both themes; keyboard disclosure, focus, minimum targets and reduced motion; no physical assistive-technology session |
| Performance | 4 | No production UI dependencies/network assets; lazy evidence DOM, catalog revision cache, lazy logs and reduced polling; local family analytics measured about 29 ms with a roughly 0.7 MB initial payload |
| Responsive design | 3 | Four synthetic widths, two themes, long Unicode content, 200% root text and synthesized touch; actual full 13-stage map at 1280/390px; no physical mobile-device validation |
| Theming | 4 | Sky-blue/leaf-green semantic tokens, warm light surface and corresponding dark tokens; both themes checked |
| Implementation integrity | 4 | Catalog-derived categories, native policy and recommendations, truthful coverage/unknown states, read-only archival guard in UI and server; detector zero, no waivers |
| **Total** | **18/20** | **Excellent within the tested scope; not accessibility certification** |

### Confirmed findings and fixes

| Priority | Finding / impact | Location | Correction |
| --- | --- | --- | --- |
| P1 | An archived reader could be offered a live sync against a different account | Dashboard profiles, setup and POST handler | Owner-private explicit source configuration; archive-only guard in both UI and server; no automatic account switching |
| P1 | Viewed/listened history could be mistaken for scored reading evidence | Archive adapter | Exclude unscored exposure; infer no fluency from viewing a book |
| P1 | Repeated placements and identical attempts could inflate coverage or collapse mastery evidence | Catalog/history adapters | Deduplicate family placements and archive occurrence identities; preserve distinct same-day attempts; reuse chronological native loader and policy |
| P1 | Late-discovered scores or inferred years could overstate weekly growth | Weekly analytics | Use first qualifying lesson date, never discovery date; exclude inferred/unknown dates from exact totals and future dates from evidence |
| P2 | New detail styling collided with existing report text classes | Journey evidence CSS | Separate the new disclosure component without restyling native score text |
| P2 | Mobile milestone metadata became a squeezed multi-line right column | Milestone summary | Full-width status/coverage line beneath the title; no smaller type |
| P2 | Mobile queue columns broke next-step words mid-word | Assigned lesson table | Readable minimum table width inside its named focusable scroll region; visible pointer/keyboard instructions |
| P2 | Enlarged text overflowed in headings and narrow metadata | Typography/layout | Wrappable copy and rem-sized brand mark; 200% root-text regression |

No grade-level percentage or completion date is claimed. The dashboard
withholds a forecast because the current automated route is a foundational
segment, not a validated complete second-grade reading route. Decoding,
oral fluency and comprehension prompts are not a scored assessment.

### Verification

- 189 unit tests and 23 browser regressions pass, including archived sync
  rejection, uncertainty, monotonic evidence, occurrence persistence, unknown
  variants and mobile table keyboard scrolling.
- Axe Core 4.10.3 reports zero A/AA findings at four widths in both themes with
  synthetic evidence/queue/setup expanded. The actual complete map also has
  zero findings at 1280/390px in both themes and no JavaScript errors.
- Impeccable's one finishing source scan reports `[]`; no ignores were added.
- Ruff, JavaScript syntax, Prettier, working-tree privacy checks and the
  substantial exact cross-file Python duplication audit pass.
- A fresh delegated reviewer, using Impeccable's shipped degraded reviewer
  contract, identified the two mobile readability corrections above. The
  same reviewer inspected all eight overwritten recaptures and scored both
  corrections **resolved**, with disposition **ship**. That verdict covers the
  two fixes; it is not a second full-surface review or accessibility certification.

Actual captures stay under ignored `.impeccable/review/`; synthetic captures
stay under ignored `private/`. Neither screenshots nor local profile paths are
published. Safari, Firefox, physical assistive technology and physical touch
remain untested. This is a complete five-dimension audit of the available
implementation evidence, not proof that all possible UX issues are absent.

## Earlier dashboard baseline audit

Audited September 17, 2026 with Impeccable's audit, hardening, Operate-mode,
and polish guidance; its bundled detector; headless Chromium via Playwright;
and Axe Core 4.10.3. Scope: the local browser dashboard, including first run,
saved results, running jobs, proposed changes, failures, and multiple readers.
No tablet connection or real sync was initiated during this audit.

## Result

Implementation integrity: the daily task is now the primary surface, not a
marketing hero. The interface continues to use the native engine's report,
mastery evidence, and recommendation ranking; it adds no second policy engine.
All confirmed findings below were addressed. Impeccable's final source scan
reports zero findings; Axe reports zero A/AA violations in the tested layouts.

These are Impeccable-style quality judgments, not accessibility certification:

| Dimension | Before | After | Evidence / limitation |
| --- | ---: | ---: | --- |
| Accessibility | 2 | 3 | Contrast, semantics, focus, keyboard flow, reduced motion; no physical assistive-technology session |
| Performance | 3 | 4 | Dependency-free UI, lazy diagnostics, reduced polling, stable DOM updates |
| Responsive design | 2 | 3 | Four widths, 200% text, long Unicode content, synthesized touch; no physical mobile device |
| Theming | 1 | 4 | Semantic colors and OS-selected light/dark themes, both checked with Axe |
| Implementation integrity | 2 | 3 | Native evidence preserved, explicit proposals, reader-isolated results, clean detector |
| **Total** | **10/20** | **17/20** | **Good; validation limits remain explicit** |

## Findings and fixes

Locations identify components rather than unstable line numbers. P1 means a
major trust/accessibility problem; P2 means a usability problem; P3 is polish.

| Priority | Finding and impact | Location | Fix / Impeccable action |
| --- | --- | --- | --- |
| P1 | Highlighted recommendation's secondary text failed contrast; parents could miss the variant and practice guidance | `style.css`, first recommendation | Contrasting semantic text colors in both themes; harden/polish (WCAG 1.4.3) |
| P1 | Any fragment, including `#setup`, replaced the access token on reload, breaking authentication | `app.js`, initialization | Accept only the launch token's shape; preserve anchors and existing session token; harden |
| P1 | Late reader-history requests could overwrite the selected reader's results | `app.js`, `latest()` | Sequence requests and check the selected alias before rendering; harden |
| P1 | A different reader's finished/failed job was described as the current reader's outcome | `app.js`, `status()` | Scope outcomes/errors to their alias; identify the actual reader during running jobs; clarify/harden |
| P1 | An incomplete worker result could produce an exception after the UI had already announced completion | `app.js`, report rendering | Validate structured reports before announcing an outcome; preserve prior results and show an actionable error; harden |
| P2 | Requests had no timeout, leaving the page indefinitely stuck after a hanging response | `app.js`, `api()` | Ten-second request timeout; warn that a sync may still be running; never automatically resubmit; harden |
| P2 | Offline, unreadable-response, and expired-access errors lacked useful recovery | Error banner, `api()` | Direct reconnect control, specific launch-link guidance for access failures, no polling after rejected authentication; clarify/harden |
| P2 | A failed status request prevented saved results from loading | `app.js`, initialization | Load the saved check-in independently of status; retain known results during interruptions; harden |
| P2 | Reader selection reset on reload | `app.js`, setup/reader selection | Retain the alias in session storage; keep actual display names local; harden |
| P2 | Navigation targets were below 44px tall | `style.css`, header links | Enlarge targets and maintain visible keyboard focus; adapt/polish. 44px is the chosen quality target, not a claim about every WCAG AA criterion |
| P2 | Pixel-sized text did not respect enlarged root text; numerous labels were unnecessarily small | `style.css`, typography | Rem-based type; 16px primary content and 14px secondary text at the default root; adapt/typeset (WCAG 1.4.4) |
| P2 | Oversized introduction pushed results to about 669px on desktop and 740px at 375px width | Header/hero | Compact task-first heading and controls; remove decorative routine card; layout/distill |
| P2 | One-second idle polling repeatedly transferred the whole diagnostic log | `app.js` polling, `dashboard.py` status API | One-second active / ten-second idle / thirty-second hidden-tab polling; request raw output only with open diagnostics; optimize |
| P2 | Light-only, hard-coded component colors lacked a consistent theme system | `style.css` | Shared semantic tokens, automatic OS-selected dark theme, meaningful forced-colors fallbacks; extract/harden |
| P2 | Deferred lesson families were absent from the browser summary | `index.html`, `app.js` | Show the engine's recorded reason and eligible-again date without inventing variant or score evidence; clarify |
| P2 | No visible non-JavaScript recovery instruction | `index.html` | `noscript` instruction to enable JavaScript or use the existing command-line sync; harden |
| P3 | Eyebrows, crushed headline tracking, tiny brand text, and hard offset decoration added noise | Header/hero and typography | Remove generated kickers and decorative book; consistent type and authored SVG icons; distill/polish |
| P3 | Table headings inherited browser-centered styling, misaligning row labels | `style.css`, queue table | Inherit the table's start alignment; add a score-history caption and focusable scroll region; polish |

Proposed assignment changes remain explicitly labeled **Will be**. The final
queue count is shown only when supplied by the native report. Failure during
teardown remains visible even when the queue was verified. Empty score evidence
is labeled **No scores recorded**, not asserted to mean the child never tried
the lesson. Addition evidence names the prerequisite variant it came from.

## Detector interpretation

The initial source scan returned 11 findings: generated kickers, tiny text,
tracking, and an accent border. The book's thick border was decorative rather
than a functional card accent; uppercase label tracking was also less serious
than the detector's generic body-text warning. Both structures were unnecessary
here and removed rather than waived. No detector ignores were added.

Positive practices retained: local-only authenticated API, strict origin and
host checks, no secret configuration exposed to the browser, safe text-based
DOM rendering, no external font/CDN dependencies, native controls, collapsed
completed setup, and the existing engine's concurrency protection.

## Reproduce and prevent regressions

See the README's browser-check commands. `tools/check_dashboard_browser.py`
now contains 23 browser regressions and serves synthetic reports through the real
dashboard HTTP handler. A fake job replaces the tablet workflow completely.
The suite covers 1280, 768, 375, and 320px widths in both themes, expanded queue
and troubleshooting regions, long Unicode names/titles, 200% root text,
keyboard focus and activation, synthesized touch, and reduced motion.

It also checks reader-request races, reload/anchor authentication, reader
selection persistence, proposed versus applied changes, teardown failures,
incomplete results, malformed API responses, absent access tokens, offline
recovery, request timeouts, lost sync responses, deferred families, lazy
diagnostics, idle polling, and startup without triggering any sync. CI runs
the suite, and unit tests separately verify the compact status API.

Optional screenshots from `KHAN_BROWSER_SCREENSHOTS=1` stay under ignored
`private/`. They are not published. Impeccable is a development skill, not a
production dependency; daily use still makes no LLM calls.

## Validation limits

Browser evidence is headless Chromium with emulated viewports and synthesized
touch, not a physical phone or tablet. Enlarged text is tested by doubling the
root font size, not every browser's zoom implementation. This pass did not test
Safari, Firefox, a physical screen reader, or Windows high-contrast mode.
Automated Axe and Impeccable scans cannot prove full WCAG conformance or find
every UX issue. Initial skill context loading failed because the downloaded
launcher lacked executable permission; project context was inspected directly,
and launcher permission was repaired before running the detector.
