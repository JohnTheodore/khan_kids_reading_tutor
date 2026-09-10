# Incident log

This append-only log records automation events that affected—or could have
affected—the tablet, student data, or confidence in a run. Each incident states
observed facts separately from hypotheses. Corrective actions remain open until
implemented and verified by tests and a live run.

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
