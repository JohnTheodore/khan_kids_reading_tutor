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
| Restore timeout, stay-awake, rotation, and auto-rotation on success or failure | Complete |
| Reject any UI hierarchy that is not exactly 2560×1600 before navigation or taps | Complete |
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
2560×1600 landscape geometry. Device settings changed for a workflow must be
captured first, scoped to a restoration context, and restored even when the run
fails.

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
