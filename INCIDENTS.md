# Incident log

This append-only log records automation events that affected—or could have
affected—the tablet, student data, or confidence in a run. Each incident states
observed facts separately from hypotheses. Corrective actions remain open until
implemented and verified by tests and a live run.

## KKRT-2026-09-11-001 — Khan Kids unstable navigation during mastery sync

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Severity | SEV-3 — repeated automation interruption; no unintended data write observed |
| Status | Resolved / monitoring |
| Detected by | User and automation postcondition failures |
| Affected component | Cold start, parent-view navigation, and score-dialog recovery |

### Summary and impact

Several mastery-sync attempts encountered an inconsistent Khan Kids UI state.
Observed failures included a foreground-launch race, a score dialog that did not
close as expected, and repeated timeouts while navigating to the Assignments
report. One Back action from a score dialog exited Khan Kids instead of returning
to the report. The sync therefore required multiple safe restarts and took much
longer than a normal run.

No child lesson was opened, no roster or account setting was changed, and no
unreviewed assignment write was made during the failed attempts. A later run
completed, recorded the new scores, applied only the reviewed changes, and
verified the exact ten-lesson queue. Temporary pointer-location diagnostics were
disabled afterward, normal screen timeout behavior was restored, and Khan Kids
was left at its profile/login screen.

### Observed facts

1. Some launches did not make Khan Kids the foreground application before the
   launch deadline.
2. One score-detail modal remained present beyond its close postcondition.
3. Multiple parent-view transitions failed to expose the expected Assignments
   report before timeout.
4. Fail-closed checks stopped each attempt instead of continuing with an
   unrecognized screen.
5. During diagnosis, the inspection viewer displayed a scaled preview. Treating
   preview positions as native tablet coordinates briefly led to incorrect
   navigation within the parent interface; the experimental coordinate change
   was reverted before the successful run.

### Root-cause assessment

The direct causes were app/startup settling races and inconsistent Back/modal
behavior in Khan Kids. The exact internal Khan Kids cause is not observable from
ADB. A separate diagnostic error—reasoning from scaled preview coordinates—made
recovery noisier but did not persist into the automation.

### Corrective and preventive actions

| Action | Status |
|---|---|
| Keep all navigation and write postconditions fail-closed | Complete |
| Restore the previously verified native Class Reports coordinate | Complete |
| Verify the final queue exactly after the successful retry | Complete |
| Automatically append an incident for every future failed `khan-mastery-sync` invocation | Complete |
| Redact common device addresses, numeric pairing codes, and local usernames from automatic incident diagnostics | Complete |
| Continue monitoring startup, modal-close, and report-navigation timing for a reproducible failure pattern | Monitoring |

### Operating rule

Every `khan-mastery-sync` invocation that exits through a workflow failure must
append a secret-safe incident record. A failed postcondition is not permission
to guess at the next tap; diagnose the live state and retry from a known parent
entry state.

## KKRT-2026-09-10-001 — Tablet orientation/auto-rotation disruption

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Severity | SEV-3 — recoverable device-setting disruption; no known student-data loss |
| Status | Resolved |
| Detected by | User |
| Affected component | Android UI automation and tablet display settings |

### Summary

While starting a Khan Reading Sync, Khan Kids appeared in portrait orientation
with landscape content letterboxed inside the portrait display. The user also
reported that automatic orientation switching no longer worked and had to be
restored manually in Android Settings. The automation is calibrated for a
2560×1600 landscape UI, so continuing in portrait could have caused incorrect
coordinate taps.

### Impact

- The user had to open Android Settings to restore normal rotation behavior.
- The requested sync was delayed.
- Portrait geometry created a risk of tapping an unintended control.
- No sync assignment mutation began during the observed portrait event, and no
  student-data loss is known.

### Observed facts

1. Android reported `ROTATION_0` and a 1600×2560 portrait application surface
   after Khan Kids settled.
2. Khan Kids displayed a landscape-shaped splash inside that portrait surface.
3. The workflow previously assumed landscape but did not establish or verify it
   before parsing coordinates.
4. A raw coordinate intended for the Dad profile had previously been calculated
   from the wrong geometry and did not open the profile.
5. After the user corrected the setting, Android reported
   `accelerometer_rotation=1` and a 2560×1600 `ROTATION_270` surface.
6. Android subsequently reported `ignoreOrientationRequest=true`. In that mode,
   Khan Kids retained a portrait-sized activity inside the landscape display,
   producing black borders. Setting it to `false` and restarting Khan Kids
   restored a full 2560×1600 application surface.

### Root-cause assessment

The direct cause of the reported auto-rotation setting change is **not proven**.
There is no recorded explicit rotation-setting command before the incident.
Android's `ignoreOrientationRequest=true` compatibility setting is the confirmed
cause of the later letterboxed Khan window. Possible contributors to the
original orientation disruption include Android selecting portrait from the
tablet's physical orientation during launch and coordinate-based interaction
while the display geometry was not validated.

The confirmed systemic cause is that the automation lacked an orientation
precondition and restoration boundary. That allowed an unsupported display
state to reach code that relies on fixed landscape coordinates.

### Immediate response

- Stopped the sync before assignment changes.
- Inspected the settled display rotation and application bounds.
- Confirmed the user's restored auto-rotation and landscape state with
  read-only Android settings queries.

### Corrective and preventive actions

| Action | Status |
|---|---|
| Capture `accelerometer_rotation` and `user_rotation` at workflow entry | Complete |
| Temporarily lock the workflow to 2560×1600 `ROTATION_270` landscape | Complete |
| Make Android honor Khan Kids' landscape request before launching automation | Complete |
| Restore timeout, stay-awake, rotation, and auto-rotation on success or failure | Superseded 2026-09-20: power settings now always permit sleep |
| Reject any UI hierarchy outside the verified 2560×1600 surface (allowing one-pixel UIAutomator edge rounding) before navigation or taps | Complete |
| Add regression coverage for restoration after an exception | Complete |
| Verify the guard and restoration during a live sync | Complete |

### Verification

On 2026-09-10, a complete review-and-apply cycle ran with a 2560×1600
`ROTATION_270` Khan surface. Afterward, auto-rotation was restored to enabled,
the screen timeout returned to 120000 ms, permanent stay-awake returned to off,
and Khan remained full-screen landscape with
`ignoreOrientationRequest=false`.

### Operating rule

Do not issue coordinate taps when the UI hierarchy is not the calibrated
2560×1600 landscape geometry. Rotation changed for a workflow must be captured,
scoped to a restoration context, and restored even when the run fails. Power
settings must always retain a bounded automatic-sleep timeout; they must never
depend on teardown to undo a persistent stay-awake state.

## KKRT-2026-09-10-002 — Add Students near-miss

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Severity | SEV-2 — potential roster mutation; no mutation saved |
| Status | Resolved |
| Detected by | User |
| Affected component | Navigation from the teacher Students screen |

### Summary and impact

While trying to reach Class Reports, the navigator accepted a Students screen
based only on the presence of the roster and used a fixed coordinate for the
next control. In the observed UI state, that coordinate entered the Add
Students flow instead. Its text field gained focus and displayed the keyboard.
Had automation entered text or selected Next, the class roster could have been
modified.

No names were entered, Next was not selected, and no roster change was saved.
Android Back exited the flow, and reopening Khan showed the original Dad,
Student A, and Student B profiles intact.

### Root cause

The Students-page predicate was too broad, and its transition depended on an
unverified coordinate rather than a uniquely identified Class Reports control.
The post-tap destination check detected the failure, but only after the unsafe
navigation had already occurred.

### Corrective and preventive actions

| Action | Status |
|---|---|
| Remove the blind Students-page coordinate transition | Complete |
| Fail closed and require manual navigation when Class Reports is not identifiable by text | Complete |
| Add a regression test proving the ambiguous screen receives no tap | Complete |
| Preserve post-navigation destination checks | Complete |

### Operating rule

Presence of the roster is not sufficient authorization to navigate. A screen
that exposes roster-writing controls must never receive a guessed coordinate;
the automation must identify the intended control semantically or stop.

## KKRT-2026-09-10-003 — Password-reset navigation near-miss

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Severity | SEV-3 — unintended read-only account navigation; no reset requested |
| Status | Resolved after recurrence |
| Detected by | Automation postcondition timeout |
| Affected component | Parent-password submission |

### Summary and impact

During the first live test of cold-start login, automation entered the correct
stored parent password but tapped a hard-coded vertical position that opened
the adjacent Forgot Password flow instead of submitting the form. The
postcondition wait did not observe the teacher roster and aborted the sync.

The screen displayed the account's password-reset information. Automation did
not select Next, send a reset request, change a password, or reach assignment
controls. The flow was backed out safely, and no Khan Kids data was changed.

### Root cause

The password was entered through a semantically validated field, but form
submission still used a coordinate based on an earlier visual estimate. That
coordinate overlapped the nearby Forgot Password control.

### Corrective and preventive actions

| Action | Status |
|---|---|
| Keep the destination postcondition that stopped the run | Complete |
| Replace the submit coordinate with the uniquely visible `Enter` node bounds | Complete |
| Re-read `Enter` bounds after keyboard-induced dialog movement | Complete |
| Verify cold-start login reaches the teacher roster in a live run | Complete |
| Remove temporary screenshots containing account information | Complete |

### Operating rule

When a unique semantic node exists, use its current live bounds. Do not retain a
coordinate fallback for adjacent account-management controls.

### Recurrence and final verification

The event recurred when a fresh password dialog opened with the keyboard
initially hidden. Although the first correction used the semantic `Enter` node,
it retained bounds captured before typing; opening the keyboard moved the dialog
and made those bounds stale. The final correction re-reads and validates the
dialog after password entry, then taps the current `Enter` bounds. A subsequent
cold-start sync logged in, removed exactly the two reviewed assignments, and
verified the complete desired queue. No password reset was requested in either
event.

## KKRT-2026-09-11-AUTO-161723-038675 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: All Progress lesson not found: 'Words with g & k'`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

### Resolution — 2026-09-11

The interrupted run had safely removed the two newly quarantined assignments
and added `Beginning Sounds 2 — Basic`, leaving nine live assignments. The
unreachable `Words with g & k — Main` proposal was removed from the curated
pool and replaced with `Blend Sounds 1 — Basic`, whose 90% then 80% history
supports additional onset-and-rime practice. A recovery sync added that single
lesson and verified the exact ten-lesson queue. Incident status: **Resolved**.

## KKRT-2026-09-11-002 — Same-day cache concealed new lesson attempts

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Severity | SEV-2 — incomplete score record could affect mastery decisions |
| Status | Resolved |
| Detected by | User observation |
| Affected component | Mastery-sync score-history collection |

### Summary and impact

A mastery sync reused same-day cached score histories when the visible
Assignments-row summary appeared unchanged. It therefore did not open every
colored result control and missed at least one newly displayed attempt. The
user observed that `Words: End Sound — Main` had been completed but was absent
from the saved records.

A subsequent forced live scan recovered two records: `Words: End Sound — Main`
at 68% and `Beginning Sounds 2 — Basic` at 79%. Neither score met the mastery
rule, so no incorrect promotion or assignment removal occurred.

### Root cause

The cache key treated an unchanged lesson title, variant, assignment date, and
visible summary score as proof that the underlying score-dialog history was
unchanged. Khan can add an attempt without changing those visible fields, so
that inference is invalid for mastery decisions.

### Corrective and preventive actions

| Action | Status |
|---|---|
| Disable score-history cache lookup unconditionally for `khan-mastery-sync` | Complete |
| Open every available colored result control in Student A's Assignments column | Complete |
| Record every opened control and its complete displayed history in the run report | Complete |
| Preserve cache use only for review-only workflows, with `--full-score-scan` override | Complete |
| Add regression tests for mastery-sync cache bypass and score-control reporting | Complete |
| Verify behavior live and recover the missing attempt records | Complete |

### Operating rule

Visible assignment-row summaries are discovery controls, not cache validators,
during mastery sync. Every available score control must be opened before
mastery, quarantine, or queue decisions are evaluated.

## KKRT-2026-09-11-003 — Assignment summary contradicted score history

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Severity | SEV-2 — contradictory assessment data could cause an unsupported promotion |
| Status | Open; contained by detailed-history authority rule |
| Detected by | Post-sync visual verification |
| Affected component | Khan Kids Assignments report / mastery-sync result interpretation |

### Summary and impact

After a successful mastery sync, the visible assignment row for
`Blend Sounds 1 — Basic` displayed 100%. The exhaustive scan of its colored
score control returned only two detailed attempts, 80% and 90%, both dated
September 8. A second complete sync reproduced the discrepancy. Direct manual
inspection of that exact control also showed only the 80% and 90% attempts.

The sync treated the detailed attempt history as authoritative, recorded no
new attempt, and did not uncheck or promote the lesson. No assignment was
changed. The tablet was returned to the profile chooser with normal display
settings restored.

### Containment and follow-up

| Action | Status |
|---|---|
| Re-run the exhaustive live score-control scan from a settled profile chooser | Complete |
| Open the exact `Blend Sounds 1 — Basic` control manually and compare its dialog with the row | Complete |
| Refuse to infer a 100% attempt from the row when the detailed dialog does not contain one | Complete |
| Investigate whether Khan's row is stale, aggregated, or uses a different scoring rule | Open |
| Add an explicit row-versus-dialog discrepancy warning to future sync reports | Open |

### Operating rule

Mastery requires a concrete attempt in the detailed lesson-score dialog and
the durable attempt ledger. A row-level percentage alone must not create an
attempt record or trigger an assignment change when the detailed history
contradicts it.

## KKRT-2026-09-12-AUTO-091635-416033 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for assignments report`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-12-AUTO-165111-890956 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Khan Kids did not become the foreground app`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-12-AUTO-194445-306272 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for profile chooser after switch user`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

### Resolution update — 2026-09-12

**Status: Resolved / monitoring.** The assignment phase was not interrupted.
The structured plan had already been saved as `applied` at 19:44:36, all four
planned assignment changes were recorded as saved, and the exact ten-item final
queue had passed verification before teardown began. The missing performance
section confirms that the exception bypassed the old post-session reporting
path; it does not invalidate the earlier applied outcome.

The teardown implementation had two inadequate assumptions: each unlabeled
Khan control received only one tap, and the profile chooser had only the generic
six-second hierarchy timeout to appear. Khan Kids exposes this interface through
an asynchronously updated React Native accessibility tree, so either a dropped
tap or a delayed tree left the workflow with no safe recovery path. Device logs
show repeated hierarchy reads during the timeout and no application crash or
ANR. The exact transient screen is not recoverable after the fact, so the logs
do not distinguish a dropped tap from delayed accessibility exposure.

The fix now:

- requires two consecutive semantic classifications of each teardown destination;
- allows 12 seconds for each transition;
- re-reads and revalidates the live source screen before retrying a guarded
  **Back** or **Switch User** control, up to three attempts;
- refuses another tap if the UI has moved to an unknown or unexpected screen;
- preserves the applied/review outcome, timing report, and explicit teardown
  error before returning nonzero and creating the automatic incident; and
- covers successful teardown, dropped-tap recovery, missing-control rejection,
  and unexpected-screen fail-closed behavior with automated tests.

Verification completed on 2026-09-12: all 84 repository tests passed, and a
navigation-only live check entered Class Reports, exercised the repaired
teardown, and finished at a semantically verified `profile_chooser`. The live
check did not scan scores or modify assignments.

Evidence: `private/student-a-reading-plan.json`,
`student-records/student-a-assignment-actions.csv`,
`student-records/student-a-reading-sync-log.md`, device logcat for
19:44:36–19:44:45 EDT, and the teardown regression tests in
`tests/test_automation.py`.

## KKRT-2026-09-13-001 — Mastery queue oscillated across unchanged syncs

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Severity | SEV-2 — repeated syncs reversed a verified mastery promotion |
| Status | Resolved / monitoring |
| Detected by | Idempotency audit of consecutive mastery-sync reports |
| Affected component | Attempt persistence, mastery planning, and action audit log |

### Summary and impact

`Beginning Sounds 2 — Basic` was promoted to Main at 17:04, reverted to Basic
at 19:44, and promoted to Main again at 20:03 without another lesson attempt.
The queue remained at ten assignments and each individual run verified its own
desired state, but the desired state itself oscillated between runs.

### Root cause

The live score dialog contained two distinct 94% attempts on the same date.
The attempt ledger used lesson, variant, date, and percentage as a set identity,
so it retained only one 94%. After Basic was unassigned, its live dialog was no
longer available; planning fell back to the incomplete durable history and no
longer considered Basic mastered. Same-day action deduplication also suppressed
some repeated mutation records, obscuring the cycle in the action ledger.

### Corrective and preventive actions

- Reconcile attempt identities as occurrence counts from each complete live
  history; an unchanged rescan appends nothing.
- Treat every successfully saved mastery removal in the existing action ledger
  as durable, monotonic mastery evidence.
- Add a mastery-state digest to reviewed plans so changes invalidate stale plans.
- Timestamp every assignment mutation instead of collapsing repeated same-day
  events.
- Hold a nonblocking operating-system lock for the complete tablet workflow.
- Test the duplicate-score promotion followed by an inactive-predecessor run
  and require the second plan to contain zero actions.

### Verification

The production records form a fixed point. A read-only planner check preserved
the current ten-item queue with zero actions. Two completed live syncs then
preserved that same queue with zero assignment mutations: the first recorded
three legitimate new scores, and the second recorded zero new attempts.
`Beginning Sounds 2 — Main` remained selected throughout. All 88 automated
tests also passed.

## KKRT-2026-09-13-AUTO-093824-722732 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Khan Kids did not become the foreground app`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-13-AUTO-093950-013788 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for assignments report`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-14-AUTO-092714-385487 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Khan Kids did not become the foreground app`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-14-AUTO-092831-151031 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for assignments report`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-14-AUTO-093015-575769 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for lesson variants`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Any completed assignment actions, if present, remain recorded in the student sync log.
- Diagnose the exact device state before retrying.

### Resolution update — 2026-09-14

**Status: Resolved in code / monitoring live behavior.** This update also covers
`KKRT-2026-09-14-AUTO-092714-385487` and
`KKRT-2026-09-14-AUTO-092831-151031`, which were earlier stages of the same
mastery-sync attempt sequence.

The failures shared one missing resilience mechanism: startup and forward UI
transitions did not use the guarded, stable-state retry behavior already used
by teardown. The final attempt then exposed a second safety weakness: the
workflow removed all four mastered assignments before adding replacements, so
a dropped lesson-expansion transition interrupted the run after four saved
removals and only one saved addition.

The corrective implementation now:

- requires stable lock-state and foreground readings and uses Android's
  wait-capable activity launch while preserving the one-PIN-attempt limit;
- routes forward report navigation, All Progress navigation, lesson expansion,
  assignment Save, and teardown through one shared guarded transition system;
- retries only while a freshly read source screen still satisfies its exact
  safety predicates and fails closed on any unexpected state;
- writes a desired-state operation journal before the first mutation and
  checkpoints every saved and independently verified action;
- adds before removing when capacity permits and otherwise alternates one
  removal with one replacement, limiting an interrupted full-queue update to
  one unmatched removal;
- verifies the complete live queue after every Save and requires a second exact
  fixed-point scan before reporting success;
- attempts a bounded read-only live-queue recovery after interruption and
  reports the exact saved actions, missing assignments, queue count, and
  duration; and
- reports a promotion as applied only when both the removal and its replacement
  addition were actually saved.

Automated verification completed on 2026-09-14: all 105 repository tests pass,
including delayed foreground, transient keyguard, dropped roster tap, dropped
lesson expansion, and a fault injected after a saved removal from a full queue.
A strict production-source duplication scan found no duplicated blocks. Live
behavior remains under monitoring until the next requested mastery sync.

### Queue restoration update — 2026-09-15

The teacher Assignments report was subsequently observed with seven active
lessons after the interrupted September 14 update; the child view was also
confirmed at seven. This queue remains **operationally unresolved** despite
the September 14 code fix. A September 15 read-only review captured four new
scores and proposed eight desired lessons under the old planner, but did not
change any assignments.

The planner now reuses the existing stretch eligibility rules for two vetted
oral-phoneme maintenance reserves, Beginning Sounds 1 and Rhyming, and its
September 15 score-state regression produces an idempotent ten-item desired
queue. The apply boundary rejects any plan that is not exactly ten, and review
reports an explicit shortfall without mutation. A live sync on September 15
repaired seven observed assignments to ten and verified every saved action and
the final fixed point. The immediately following sync observed ten, proposed
no changes, and verified a ten-item no-op. **Queue restoration is resolved**;
continue monitoring future runs for an exhausted eligible pool or Khan-side
assignment drift.

## KKRT-2026-09-16-AUTO-090155-234973 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`RuntimeError: Tablet discovery failed before workflow startup: macOS discovered 0 wireless ADB services for the configured tablet; expected exactly one`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- No assignment changes were attempted.

### Root cause and correction

This was not an orientation change. The failing live UIAutomator hierarchy
reported a transient `2559×1600` root; the automatic failure capture immediately
afterward reported the correct `2560×1600` landscape root. Android remained in
the expected landscape rotation. The prior exact-equality check therefore treated
one pixel of accessibility-boundary rounding as a dangerous display change.

Screen validation now selects the largest top-left root candidate instead of the
first one, preventing a status-bar-sized node from masquerading as the display.
It accepts at most one pixel of error on an expected screen edge, which is too
small to affect guarded tap regions. Portrait, split-screen and any geometry two
or more pixels outside the tested surface still fail closed. Regression tests
cover the captured `2559×1600` case, the one-pixel boundary, portrait rejection
and competing status-bar/full-screen roots.

## KKRT-2026-09-16-AUTO-155144-441822 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Root cause corrected 2026-09-22; live verification pending |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for score dialog closed`

### Resolution update

The score-modal close path previously performed one tap and one wait, unlike
guarded navigation. It now reuses the existing bounded transition guard: retry
only while the target student's score dialog remains visible, and stop on an
unexpected screen. The next full live sync read all five available score
histories and verified the unchanged ten-item queue. No assignment mutation
occurred during the failed sample or the recovery run. Status: resolved in
code and verified live; monitor future modal-close behavior. This failure does
not establish whether warm startup caused the Khan-side close interruption.

### Root-cause correction — 2026-09-22

A later recurrence retained complete failure evidence and disproved the dropped-tap
diagnosis. The close path used one fixed screen-relative point rather than the
dialog's live X-button bounds. Retrying therefore repeated the same wrong point
when a taller score history shifted the centered modal upward. The bounded retry
remains useful for genuinely dropped input, but it was not the underlying fix.
The corrected implementation resolves the unique close control from every fresh
dialog hierarchy before each guarded attempt.

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-17-AUTO-135010-081070 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: All Progress lesson not found: 'Lowercase l'`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-17-AUTO-150801-225386 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Startup blocked; app left open without restarting. Inspect the screen before retrying. Diagnostics: private/startup-blocked-5j295_nv.`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-17-AUTO-150849-972298 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: All Progress tab reached unexpected navigation state 'profile_chooser'`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-18-AUTO-145108-483356 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-18 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Startup blocked; app left open without restarting. Inspect the screen before retrying. Diagnostics: private/startup-blocked-ri52s7xk.`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

### Diagnosis and prevention

The saved startup screen shows a three-card prize overlay. Its accessibility
hierarchy exposes the child's home label beneath the overlay but no labeled
prize buttons. Startup previously classified it as child home and attempted
profile navigation through the overlay, then correctly stopped instead of
restarting past the unsupported screen.

Startup now recognizes the verified card-and-avatar layout before child home,
checks the selected child, chooses one live card at random, and waits for stable
child home before continuing. It makes only one prize tap: an uncertain result
stops rather than risking another award. Unfamiliar central-card layouts cannot
fall through to child-home navigation. Regression tests cover recognition,
ordinary home, wrong-child and stale-screen refusal, single-tap timeout behavior,
and continued startup without restarting. Captured artwork remains private;
tests use synthetic hierarchy fixtures.
## KKRT-2026-09-19-UX-001 — System volume unavailable after tablet automation

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Severity | SEV-3 — tablet usability impaired after an otherwise bounded workflow |
| Status | Resolved |
| Detected by | Parent report |
| Affected surface | Android system controls after Khan Kids automation |

### Findings

The automation did not send volume keys or modify audio, mute, or Do Not Disturb
settings. A later live inspection reproduced the unsafe handoff: Do Not Disturb
was off and screen pinning was inactive, but the fullscreen Khan Kids activity
was foregrounded with its media player active. Returning to verified Android Home
paused that player and released foreground system-key focus.

### Prevention

All mastery-sync, manual-assignment, and archive-crawl tablet sessions now press
Android Home after in-app logout or after failure diagnostics. The handoff is
accepted only after two stable foreground reads outside Khan Kids; it never
changes the user's volume level and never force-stops Khan Kids. Cleanup failure
is reported, while a primary workflow failure remains primary and carries the
Home failure as a diagnostic note. Tests cover success, workflow failure,
simultaneous cleanup failure, transient System UI, and Khan Kids remaining
foreground.

The follow-up correction also parks Khan Kids at verified Android Home after each
successful direct-assignment batch. The authenticated parent session remains warm
for 60 seconds in the backend and resumes automatically for another request, but
the idle window no longer leaves the fullscreen app or its media player in control
of the physical tablet. Regression coverage verifies parking before the warm state
is announced and resuming without a force-stop or second backend session.

## KKRT-2026-09-19-AUTO-133832-444346 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260919T173801103781Z-9c644f6c` |

### Observed failure

`AutomationError: Unsupported display orientation or size: Rect(left=0, top=0, right=2560, bottom=72); expected landscape [0,0][2560,1600]`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-19-AUTO-134206-666694 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260919T173843467508Z-57cf1ef5` |

### Observed failure

`AutomationError: Command failed: adb -s [redacted device]: adb: device offline`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-19-AUTO-140438-670350 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Resolved |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260919T180259817078Z-026a23bc` |

### Observed failure

`AutomationError: All Progress lesson not found: 'Words with e'`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: unchecked Beginning Sound p — Practice 2; unchecked Short Vowel Sound u — Practice 1.
- Live queue after interruption: unavailable.
- A later verified run added the missing replacement and restored the target of
  10 assignments.

### Root cause and correction

- A single unchanged report page after a swipe was treated as the end of the
  catalog. A transiently ineffective or not-yet-rendered swipe could therefore
  produce a false “lesson not found” result.
- The workflow removed mastered assignments before confirming their
  replacements, allowing this lookup failure to leave the queue short.
- All report-scanning paths now require repeated unchanged reads before
  declaring the bottom of a list.
- Queue reconciliation now adds and verifies replacements before removing old
  assignments. If an add fails, the existing full queue is preserved.
- Failure evidence is captured before recovery navigation changes the screen.
  Regression coverage verifies the scroll retry, add-first ordering, preserved
  queue, and capture-before-reconciliation behavior.

## KKRT-2026-09-19-AUTO-152442-953643 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Prevented in code; current tablet cleanup pending |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260919T192356173635Z-2e0fddf2` |

### Observed failure

`AutomationError: Sync outcome was saved, but Android Home could not be verified: Android Home did not leave org.khankids.android; the tablet may still be in fullscreen`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- The saved result verified 10 live assignments and recorded four new scores;
  no assignment action was needed.

### Root cause and prevention

Android screen pinning was active. System UI logged repeated `ScreenPinningNotify`
events and the instruction “To unpin this app, swipe up & hold,” so Android
intentionally rejected Home while Khan Kids remained on the correctly logged-out
profile chooser.

Sync and dashboard preflight now read Android lock-task state before navigating
Khan Kids and stop with an unpin instruction when app pinning is active. Unknown
and managed lock-task states also fail closed. Diagnostics record the state
directly. A saved sync with only this cleanup failure is presented as “progress
saved,” and its dedicated cleanup action checks that pinning is off, returns to
Android Home, and marks cleanup recovered without replaying the sync.

## KKRT-2026-09-20-001 — Tablet automatic sleep disabled after interruption

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Severity | SEV-2 — unattended battery drain |
| Status | Resolved in code; next tablet session repairs existing settings |
| Detected by | Tablet found with an empty battery the next morning |

### Root cause

The automation temporarily wrote an effectively infinite Android screen timeout
and enabled “stay awake while plugged in,” then relied on Python teardown to
restore the prior values. A hard process interruption cannot run teardown. Worse,
a later session captured those already-unsafe values as its “prior” state and
restored them again. Diagnostic logs from the preceding run showed restoration
writes of `screen_off_timeout=2147483647` and
`stay_on_while_plugged_in=15`, confirming that the unsafe values had become the
saved baseline.

### Prevention

- Automation no longer enables either persistent stay-awake mechanism.
- Tablet sessions enforce a two-minute timeout and disable plugged-in stay-awake
  at both entry and exit; startup therefore repairs settings left by old builds.
- Both safety writes are attempted even if one fails.
- A repository regression test rejects reintroduction of the old infinite
  timeout or persistent stay-awake command.
- The README no longer recommends `scrcpy --stay-awake`.
- A stale cleanup-only dashboard result no longer disables a fresh sync. The new
  sync still performs its complete connection and app-pinning preflight first.

## KKRT-2026-09-20-AUTO-104532-457307 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Resolved in code; live verification pending |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260920T144505927790Z-978d7fd8` |

### Observed failure

`AutomationError: Startup blocked; app left open without restarting. Inspect the screen before retrying. Diagnostics: private/startup-blocked-3hakgg6u.`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

### Root cause and correction

Khan Kids displayed its verified three-choice prize prompt asynchronously after
the child profile circle had been tapped. Existing handling recognized the same
prompt only when it was already visible during the initial startup reads. The
guarded transition therefore saw neither its expected child-home source nor the
profile chooser target and correctly stopped without selecting anything.

Child-side guarded navigation now treats that exact, student-verified prize
layout as a bounded interruption. It selects one prize with the existing random,
at-most-once routine, waits for a stable child home, and resumes the interrupted
navigation from fresh state. A second prompt, a different student's prompt, an
uncertain selection, or any unrecognized layout still fails closed. Regression
coverage reproduces the delayed prompt captured by this incident.

## KKRT-2026-09-20-AUTO-151233-048204 — Parent assignment interruption

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Resolved preventively |
| Detected by | Automated parent-assignment failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Assignment dialog mismatch: expected 'Words on Signs 2'/'Practice 2', got 'Words on Signs 2'/[]`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- `Words on Signs 1 — Practice 2` was saved and its checkbox was locally
  verified, but final live-queue verification was unavailable.
- `Words on Signs 2 — Practice 2` was not saved.
- Recovery did not replay either action.

### Root cause and correction

Khan Kids renders its React Native assignment modal in stages. The exact lesson
heading appeared before the variant preview; the automation treated the heading
alone as a complete dialog and immediately rejected the temporarily empty variant.
Its read-only recovery then tried to scan the queue while that modal was still
open, so it could not promote the earlier locally verified save into a completed
result.

Assignment dialogs now require two complete consecutive reads containing one
exact title, the expected variant in the modal preview, every configured student
control and one Save button. A correct title with incomplete controls remains a
bounded loading state; a wrong title or populated wrong variant still fails
immediately. Before read-only batch reconciliation, the automation dismisses only
the exact current unsaved dialog, never taps Save, and then scans the live queue
once. Regression coverage reproduces the delayed render, true mismatch and safe
dismissal paths.

## KKRT-2026-09-20-AUTO-154226-447912 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Resolved preventively |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260920T194203986641Z-cea0d1f2` |

### Observed failure

`AutomationError: Unsupported display orientation or size: Rect(left=0, top=0, right=2559, bottom=1600); expected landscape [0,0][2560,1600]`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- No assignment changes were attempted.

### Root cause and correction

This was not an orientation change. The failing live UIAutomator hierarchy
reported a transient `2559×1600` root; the automatic failure capture immediately
afterward reported the correct `2560×1600` landscape root. Android remained in
the expected landscape rotation. The prior exact-equality check therefore treated
one pixel of accessibility-boundary rounding as a dangerous display change.

Screen validation now selects the largest top-left root candidate instead of the
first one, preventing a status-bar-sized node from masquerading as the display.
It accepts at most one pixel of error on an expected screen edge, which is too
small to affect guarded tap regions. Portrait, split-screen and any geometry two
or more pixels outside the tested surface still fail closed. Regression tests
cover the captured `2559×1600` case, the one-pixel boundary, portrait rejection
and competing status-bar/full-screen roots.

## KKRT-2026-09-20-AUTO-162728-288891 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Open |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |

### Observed failure

`AutomationError: Timed out waiting for stable Khan navigation state`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

## KKRT-2026-09-21-AUTO-085057-039332 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Resolved in code; live verification pending |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260921T125042992502Z-ed552538` |

### Observed failure

`AutomationError: Startup blocked; app left open without restarting. Inspect the screen before retrying. Diagnostics: private/startup-blocked-v25qs9om.`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

### Root cause

The preserved screen is Khan Kids' child Library on its Assignments tab, scrolled
below the page introduction. In this state Khan's accessibility hierarchy omits
the visible `Library` and `Assignments` artwork and exposes only lesson variants,
lesson titles and date headings. The startup classifier recognizes this route
only when the top-of-page subtitle `Lessons assigned to you by dad` is exposed.
Because that subtitle had scrolled offscreen, the otherwise valid child-library
state was classified as unknown. The safety guard correctly avoided both a blind
Back tap and an app restart, but the sync could not proceed.

### Correction

Startup now recognizes the child Library from five stable, package-scoped chrome
anchors instead of scroll-dependent lesson text. It still requires two stable
reads, validated landscape geometry and the exact guarded Back control. After
Back, the existing child-home identity guard must succeed before the profile
chooser can open; mismatches continue to fail closed.

Synthetic regression coverage models the captured scrolled state and rejects
both incomplete chrome and the same geometry from another package. The complete
Library → child home → profile chooser path is covered. Unknown startup failures
now report a secret-safe package result and matched-anchor count before retaining
the private screenshot and hierarchy.

## KKRT-2026-09-22-AUTO-150831-447379 — Mastery sync interruption

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Severity | SEV-3 — automation interruption; review required before retry |
| Status | Resolved in code; live verification pending |
| Detected by | Automated mastery-sync failure handler |
| Affected student | Student A |
| Diagnostic run | `sync-20260922T190702167364Z-2c59cebe` |

### Observed failure

`AutomationError: score dialog close remained on 'score_dialog' after 3 guarded attempts`

### Automatic response

- The invocation stopped with a nonzero exit status.
- The normal workflow safety guards remained in force.
- Completed assignment actions: none recorded.
- Live queue after interruption: unavailable.
- Diagnose the exact device state before retrying.

### Root cause and correction

The first five, shorter score histories closed successfully because their centered
dialogs placed the X button over the legacy fixed tap at `(1689, 480)`. The
`Letters & Words — Practice 2` history contained nine attempts, making the modal
taller and moving its X button to `[1634,338][1758,462]`, centered at
`(1696, 400)`. The fixed tap landed below the button, and all three retries
repeated that same invalid point.

The close path now reuses the existing image-backed history-close resolver. Each
guarded attempt validates the target student's score dialog, requires exactly one
plausible close control, and taps its current live bounds. Regression tests cover
the tall failure geometry, a lower short-dialog geometry, movement between retry
attempts, missing controls and unexpected navigation states. No coordinate
fallback remains.
