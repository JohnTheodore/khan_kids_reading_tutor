# Khan Kids Reading Tutor

Tools, curriculum data, and Android automation for turning the Khan Academy
Kids teacher interface into a mastery-gated reading path and durable progress
record.

This family project helps Student A reach independent reading while preserving
mastery at prerequisite steps; it is not an ELA-completion project or an official
Khan Academy product. The Android teacher UI supplies the lesson inventory and
scores. A shared Python workflow maintains ten assignments, verifies changes,
and records attempts and decisions durably.

See [`reading-path.md`](reading-path.md) for the current route and
[`curriculum-decisions.md`](curriculum-decisions.md) for its rationale.

> [!WARNING]
> This repository is public. Student names have been replaced with aliases, but
> dated scores, learning trajectories, and captured accessibility XML remain.
> These records are pseudonymized, not fully anonymous. Keep your own captures
> and real-name mapping private. The project's MIT license does not automatically
> cover Khan's captured materials; see [privacy and licensing](#privacy-safety-and-limitations).

## Contents

- [Quick start](#quick-start)
- [What we accomplished](#what-we-accomplished)
- [Repository outputs](#repository-outputs)
- [How the system works](#how-the-system-works)
- [Convert Khan Kids to a Class Account](#convert-khan-kids-to-a-class-account)
- [Set up an Ubuntu control computer](#set-up-an-ubuntu-control-computer)
- [Connect the Android device](#connect-the-android-device)
- [Open Khan Kids safely](#open-khan-kids-safely)
- [Capture your own lesson data](#capture-your-own-lesson-data)
- [Analyze a child's progress](#analyze-a-childs-progress)
- [Assign lessons safely](#assign-lessons-safely)
- [Run the mastery workflow](#run-the-mastery-workflow)
- [Test the tools](#test-the-tools)
- [Privacy, safety, and limitations](#privacy-safety-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Project status and license](#project-status-and-license)

## Quick start

The automation is calibrated for a Pixel Tablet at 2560×1600 in landscape,
running Khan Kids 9.0.1. Validate other layouts before permitting assignment changes.

1. [Set up the host tools](#set-up-an-ubuntu-control-computer) and run `uv sync --frozen`.
2. [Configure private identities and credentials](#private-student-identities).
3. [Pair and configure the tablet](#connect-the-android-device). After a tablet
   reboot, physically unlock it and re-enable Wireless debugging.
4. Preview with `./khan-reading-sync --student 'Student A'`, then run
   `./khan-mastery-sync` to apply and verify the mastery queue for the configured student.

The default commands discover the configured tablet; an IP address is normally
unnecessary. Cloning this repository does not configure your device, credentials,
or student mapping. Do not use the archived family records as your own baseline.

## What we accomplished

The September 8, 2026 capture used Khan Academy Kids Android 9.0.1
(`versionCode 123`) on a Pixel Tablet in landscape orientation.

- Indexed the complete **Letters** teacher-library tab:
  - 51 overlapping screen captures
  - 103 unique lesson titles
  - 392 assignable cards after counting Basic, Main, Practice 1, and Practice 2
- Captured **Class Reports → All Progress → ELA** across all six levels:
  - Preschool (Age 2), Preschool (Age 3), Preschool (Age 4), Kindergarten,
    1st Grade, and 2nd Grade
  - 1,339 lesson placements
  - 2,956 assignable activity placements after expanding variants
  - 653 globally unique title strings
- Preserved each child's displayed result for every activity placement:
  percentage, `Viewed`, aggregate count, or blank/not attempted
- Identified lessons most directly related to sound order, blending,
  segmentation, sound position, CVC decoding, syllables, and consonant blends
- Assigned one Basic-or-Main activity from each of 16 selected categories to
  Student A only as the initial diagnostic batch
- Replaced that initial batch with a continuously reconciled ten-item queue:
  eight mastery-path positions and at least two rotating printed-CVC stretch
  positions
- Added automatic wake/unlock/login/navigation, state-checked assignment
  changes, exact post-write verification, same-day score caching, and
  secret-safe performance profiling

The 2,956 figure is the number of assignable activity placements in the full
ELA report, **not** the number of lessons either child completed. The same
lesson title may appear in multiple grade reports, and each title may contain
several variants.

## Repository outputs

| Path | Purpose |
|---|---|
| [`letters-lessons.md`](letters-lessons.md) | Human-readable Letters catalog with inferred teaching targets |
| [`data/letters-lessons.json`](data/letters-lessons.json) | Structured Letters catalog |
| [`reading-ela-archive.md`](reading-ela-archive.md) | Complete ELA hierarchy with both children's displayed results |
| [`data/reading-ela-archive.json`](data/reading-ela-archive.json) | Structured full ELA archive |
| [`data/reading-curriculum.json`](data/reading-curriculum.json) | Validated mastery-gated Khan-only reading sequence and prerequisites |
| [`reading-path.md`](reading-path.md) | Human-readable minimum path, entry point, stopping rule, and record model |
| [`curriculum-decisions.md`](curriculum-decisions.md) | Append-only rationale for selecting, deferring, or reordering lesson families |
| [`INCIDENTS.md`](INCIDENTS.md) | Append-only operational incident record and corrective actions |
| [`performance-audit-2026-09-10.md`](performance-audit-2026-09-10.md) | Baseline bottleneck analysis, implemented optimizations, and measured speedups |
| [`performance-update-2026-09-16.md`](performance-update-2026-09-16.md) | Later profiling results and startup/navigation improvements |
| [`reading-ela-performance.csv`](reading-ela-performance.csv) | One row per assignable activity, suitable for a spreadsheet or analysis |
| [`ordering-related-lessons.md`](ordering-related-lessons.md) | Reading-order analysis and proposed instructional sequence |
| [`student-a-next-reading-lessons-science-of-reading.md`](student-a-next-reading-lessons-science-of-reading.md) | Research-backed, Khan-only next-lesson sequence tailored to Student A's scores |
| [`ordering-assignments-2026-09-08.md`](ordering-assignments-2026-09-08.md) | Exact 16-activity assignment record |
| [`mastery-learning-policy.md`](mastery-learning-policy.md) | Evidence-based, family-specific promotion policy |
| [`student-records/`](student-records/) | Attempt history, mastery state, progress notes, and assignment-action audit log |
| [`khan-kids-school-dashboard-deep-dive.md`](khan-kids-school-dashboard-deep-dive.md) | Research on free Class Accounts versus the paid school web dashboard |
| [`report-source.md`](report-source.md) | Claim-to-source ledger for the dashboard research |
| `data/raw/` | Original teacher-library UI XML and manifests |
| `data/raw-verified/` | Re-captured and verified Letters UI XML evidence |
| `data/raw-reports/ela/` | All Progress UI XML and per-grade manifests |
| `tools/` | Shared Android automation package, crawlers, builders, and mastery CLI |

### Sources of truth

To keep current state separate from historical evidence:

- `data/reading-curriculum.json` defines the assignable path, prerequisites,
  queue limits, diversity groups, and stretch pool.
- `reading-path.md` is the current human-readable curriculum and queue summary.
- `mastery-learning-policy.md` defines promotion and reassessment rules.
- `curriculum-decisions.md` is an append-only rationale log; later entries
  supersede earlier operational decisions.
- the attempt and assignment-action CSVs under `student-records/` are the
  permanent event records; `student-records/student-a-reading-sync-log.md` is
  the generated run history.
- dated research and assignment documents preserve their original snapshots;
  they are not instructions for the current queue.

Image checks create temporary screenshots under ignored `private/`, with
owner-only permissions and exception-safe cleanup. Crawlers no longer retain
page screenshots, and no captured images are included in the repository. The
retained XML is sufficient to rebuild the current catalogs and score archive.

Blank result cells are retained as `not_attempted`; they are never treated as a
score of zero. Lesson descriptions marked “inferred” are interpretations of
the visible title and standard, not prose supplied by Khan Kids.

## How the system works

```text
Khan Kids Class Account on Android
        │
        ├── Teacher library: Letters / Reading
        │       └── UI hierarchy XML
        │               └── Letters JSON and Markdown catalog
        │
        └── Students → Class Reports → All Progress → ELA
                └── UI hierarchy XML
                        └── ELA JSON, Markdown, and performance CSV
                                └── mastery and diversity planner
                                        ├── eight core positions
                                        ├── rotating CVC stretch pool
                                        └── checked/unchecked assignment diff
```

[`scrcpy`](https://github.com/Genymobile/scrcpy) mirrors the Android display so
the operator can watch every interaction. Android Debug Bridge (`adb`) supplies
touch gestures, screenshots, and accessibility-tree dumps. The Python scripts
deduplicate overlapping captures and preserve the report hierarchy.

The scripts use the visible on-device teacher UI, not an unofficial Khan API or
app modification. UI-based capture still requires consideration of Khan's terms;
using the visible interface is not itself permission to redistribute its data.

## Convert Khan Kids to a Class Account

Class mode is what exposes the full Pre-K–2 teacher library, individual lesson
assignment, standards search, and Class Reports.

### Convert an existing family account

Use the account that already contains the child profiles whose history you want
to preserve.

1. Update Khan Academy Kids to the latest version.
2. Open the user-selection screen and tap **For Parents**.
3. Complete the on-screen swipe gate to enter the Parent/Grown-Ups section.
4. Open the account dropdown.
5. Choose **Teacher: Convert to a Class Account**.
6. Create a password if prompted and enter the teacher name.
7. On the user-selection screen, tap the teacher's bear avatar and enter the
   password to open Teacher Tools.

Khan's current instructions say existing profiles and progress carry over. The
often-mentioned “tap ten times” trick is **not required for account conversion**
in the documented flow; use the **For Parents** swipe gate and account dropdown.

Khan does not document a self-service conversion back to a Parent Account. If
reversibility matters, contact Khan Kids support first or test with a separate
account.

### Create a new class account instead

On first launch, choose **At School → Teacher**, create or sign in to the teacher
account, and add the student profiles. A new account will not automatically
contain progress from a different family account.

Official references:

- [Convert a Parent Account to a Class Account](https://khankids.zendesk.com/hc/en-us/articles/360042944391-FAQ-How-do-I-convert-my-account-to-a-Class-Account)
- [Set up a Class Account](https://khankids.zendesk.com/hc/en-us/articles/360042193551-Module-1-Setting-up-a-Class-Account)
- [Teacher Tools overview](https://khankids.zendesk.com/hc/en-us/articles/360041862972-All-about-Teacher-Tools-in-Khan-Academy-Kids)
- [Assigning lessons](https://khankids.zendesk.com/hc/en-us/articles/360042194831-Module-3-Assigning-lessons)

### Class mode is not the paid web dashboard

The free conversion enables Teacher Tools **inside the mobile app**. It does not
grant access to <https://dashboard.khanacademykids.org/>. Khan currently limits
that browser dashboard to provisioned school and district partners. See the
[dashboard deep dive](khan-kids-school-dashboard-deep-dive.md) for details.

## Set up an Ubuntu control computer

The tested host was Ubuntu/Linux with:

- Python 3.13.7
- `uv` with the repository's locked Python dependencies
- `uiautomator2` 3.7.0 for persistent, low-latency hierarchy reads
- Android Debug Bridge 34.0.5
- scrcpy 4.1
- ImageMagick 7.1.2 (`magick`)

Install ADB and ImageMagick:

```bash
sudo apt update
sudo apt install adb imagemagick
```

Install `uv` using its
[official instructions](https://docs.astral.sh/uv/getting-started/installation/),
then create the locked environment:

```bash
uv sync --frozen
```

Install a current scrcpy release using its
[official Linux instructions](https://github.com/Genymobile/scrcpy/blob/master/doc/linux.md).
If your Ubuntu release provides a sufficiently recent package, this may be as
simple as:

```bash
sudo apt install scrcpy
```

Verify the tools:

```bash
adb version
scrcpy --version
magick -version
python3 --version
uv --version
```

Only the Linux control computer needs these tools. Nothing has to be configured
on a second Mac or PC.

## Connect the Android device

### Enable wireless debugging

On Android:

1. Open **Settings → About tablet** and tap **Build number** seven times to
   enable Developer options.
2. Open **Settings → System → Developer options**.
3. Enable **Wireless debugging**.
4. Confirm that the tablet and Ubuntu computer are on the same local network.

Menu names vary slightly by Android manufacturer.

### Pair, then connect

In **Wireless debugging**, choose **Pair device with pairing code**. Android
shows a temporary IP address and pairing port plus a six-digit code:

```bash
adb pair 192.168.1.50:37123
# Enter the six-digit code when prompted.
```

Return to the main Wireless debugging page and use its separate IP address and
debugging port:

```bash
adb connect 192.168.1.50:42817
adb devices
```

The pairing port and connection port are normally different, and both can
change. Use the values currently displayed on the tablet rather than copying
the examples above.

### Restart-safe automatic connection

This repository can discover its configured Pixel Tablet after the router,
Mac, OrbStack VM, or ADB server restarts. It asks the macOS host to resolve the
tablet's changing mDNS endpoint, connects from Linux with TLS, and verifies the
stable hardware serial and model before any UI automation.

The owner-private, Git-ignored configuration is:

```json
{
  "student": "Student A",
  "hardware_serial": "DEVICE_SERIAL_FROM_ADB",
  "model": "Pixel Tablet"
}
```

Save it as `private/tablet-device.local.json` with mode `0600`. Install current
SDK Platform Tools under `private/android-sdk/platform-tools`; ADB 37.0.0 or
newer is required for Android 17 Wi-Fi 2.0. The wrappers prefer that private
copy without replacing the operating system's ADB package.

On the tablet, enable **Wireless debugging** and select **Always allow on this
network** only for the trusted home Wi-Fi. Pair the Linux account once. The
pairing survives ordinary restarts unless the paired workstation is forgotten,
ADB authorizations are revoked, the private ADB key is lost, or the tablet is
reset.

On this Pixel Tablet build, a cold tablet boot turns the Wireless debugging
master switch off even though the paired-host and trusted-network records
survive. Recovery therefore requires a physical PIN unlock and manually turning
Wireless debugging on again. A tested Direct Boot helper could not override the
platform behavior and was removed. Do not remove the lock screen to bypass that
security boundary. If unattended recovery from a tablet reboot or total power
loss is mandatory, use a powered USB-C data connection to an always-on host and
test pre-unlock USB ADB on the exact tablet build.

Connect and verify without supplying an address:

```bash
./khan-tablet-connect
./khan-kids-open
./khan-mastery-sync
```

An explicit `--serial IP:PORT` remains available as a diagnostic override. The
automatic resolver ignores offline transports and refuses zero, multiple, or
identity-mismatched results.

The optional user timer warms the connection after VM startup and every five
minutes. The command wrappers still verify independently at the start of every
run:

```bash
mkdir -p ~/.config/systemd/user
ln -s "$PWD/systemd/khan-tablet-reconnect.service" ~/.config/systemd/user/
ln -s "$PWD/systemd/khan-tablet-reconnect.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now khan-tablet-reconnect.timer
```

The timer does not wake the screen or open Khan Kids. It only ensures the
encrypted ADB transport is discoverable and identity-verified.

Mirror the tablet:

```bash
scrcpy --serial 192.168.1.50:42817 --stay-awake
```

The tablet UI remains visible locally and in the scrcpy window. A person can
watch the crawler scroll and stop it with `Ctrl+C` if it leaves the expected
screen.

For manual diagnostics, set the current connected serial once:

```bash
KHAN_SERIAL='192.168.1.50:42817'
adb -s "$KHAN_SERIAL" get-state
```

## Open Khan Kids safely

The state-aware launcher handles an awake or sleeping tablet, an already-open
app, another foreground app, and an Android PIN lock:

```bash
./khan-kids-open --serial "$KHAN_SERIAL"
```

It reads the PIN only when Android is locked. The PIN is never passed as a
command-line argument or written to a report. It makes one unlock attempt,
verifies that the lock is gone, opens Khan Kids if necessary, and verifies that
Khan Kids is foreground.

Local credentials live in the Git-ignored `.secrets.json` file:

```json
{
  "android_pin": "...",
  "khan_parent_password": "..."
}
```

The file must have `0600` permissions. The launcher rejects symlinks,
non-regular files, and files accessible by the group or other users. It loads
`.secrets.json` automatically when present, or accepts another location with
`--secrets-file`. It never prints either value. After a tablet reboot, Android
may not restore wireless ADB until the first manual unlock; the launcher cannot
bypass that platform restriction. An ignored file remains available after a
Codex `/clear` or restart, but it is not included in clones or backups of Git.

### Prevent sleep during a long capture

`scrcpy --stay-awake` is the first choice. For an unplugged tablet or a long
session, record the current timeout and temporarily extend it:

```bash
KHAN_TIMEOUT_BEFORE="$(adb -s "$KHAN_SERIAL" shell settings get system screen_off_timeout | tr -d '\r')"
printf 'Original timeout: %s ms\n' "$KHAN_TIMEOUT_BEFORE"

adb -s "$KHAN_SERIAL" shell settings put system screen_off_timeout 2147483647
adb -s "$KHAN_SERIAL" shell svc power stayon true
```

Restore the original value when finished, in the same shell where the variable
was recorded:

```bash
adb -s "$KHAN_SERIAL" shell settings put system screen_off_timeout "$KHAN_TIMEOUT_BEFORE"
adb -s "$KHAN_SERIAL" shell svc power stayon false
```

We used this method during the long report crawl and restored the original
two-minute timeout afterward.

## Capture your own lesson data

> [!IMPORTANT]
> The current interaction coordinates are calibrated for a **Pixel Tablet at
> 2560×1600 in landscape orientation** and Khan Kids 9.0.1. App redesigns,
> different resolutions, display scaling, or different roster sizes can move
> controls. Validate a short run while watching scrcpy before any full crawl.

Create a private output area that will not overwrite this project's evidence:

```bash
mkdir -p private/my-family
```

### Capture the Letters library

Enter the teacher view and leave the app on its main Library screen. Then run:

```bash
python3 tools/khan_catalog_crawl.py \
  --serial "$KHAN_SERIAL" \
  --tab Letters \
  --output private/my-family/raw-library
```

Build the structured and readable catalogs:

```bash
python3 tools/build_letters_catalog.py \
  private/my-family/raw-library/letters/all \
  --json private/my-family/letters-lessons.json \
  --markdown private/my-family/letters-lessons.md
```

The library crawler scrolls in a blank area outside the lesson cards and never
intentionally opens or assigns a card. Captures overlap so the builder can
deduplicate rows without losing lessons at page boundaries.

### Optionally capture the Reading library

The generic library crawler also supports the Reading tab and its grade filter:

```bash
python3 tools/khan_catalog_crawl.py \
  --serial "$KHAN_SERIAL" \
  --tab Reading \
  --grade 'Preschool (Age 4)' \
  --grade 'Kindergarten' \
  --output private/my-family/raw-library
```

This project ultimately used **All Progress** rather than the Reading tab for
the full archive because All Progress provides the complete hierarchy and each
student's result in one place.

### Capture a complete, read-only progress archive

The report crawler enters **Teacher view → Students → Class Reports → All
Progress** through state-checked navigation. By default it covers every report
Khan exposes: all six grades for English Language Arts, Math, and Logic+, the
grade-independent **All Ages** Books report, and three Videos bands (**K &
Pre-K**, **Grade1**, and **Grade2**)—22 reports total.

Use account-specific, ignored configuration and secrets files. The roster is
explicit, and forbidden-student guards prevent captures from silently crossing
between family accounts:

```bash
serial="$(tools/run-tablet-workflow connect \
  --config private/student-c-tablet.local.json)"

tools/run-python tools/khan_report_archive_crawl.py \
  --serial "$serial" \
  --student 'Student C' \
  --forbid-student 'Student A' \
  --forbid-student 'Student B' \
  --secrets-file private/student-c-secrets.local.json \
  --output private/student-c/archive-pass-1.local \
  --capture-histories
```

The crawler only changes report filters, expands report rows, opens
score-history dialogs, and closes them. It never changes an assignment
checkbox and never presses Save.
Restrict a pilot to one combination with, for example:

```bash
tools/run-python tools/khan_report_archive_crawl.py \
  --serial "$serial" \
  --student 'Student C' \
  --forbid-student 'Student A' \
  --forbid-student 'Student B' \
  --secrets-file private/student-c-secrets.local.json \
  --grade kindergarten \
  --subject ela \
  --output private/student-c/archive-pilot.local
```

Valid grade slugs are `preschool-age-2`, `preschool-age-3`,
`preschool-age-4`, `kindergarten`, `1st-grade`, and `2nd-grade`. Subject slugs
are `ela`, `math`, `logic`, `books`, and `videos`.

The parser is roster-driven; do not create family-specific copies of crawler or
parser code. Build private normalized records with:

```bash
tools/run-python tools/build_reading_report_archive.py \
  private/student-c/archive-pass-1.local \
  --student 'Student C' \
  --output private/student-c/records.local
```

For a two-pass capture, use the validation pass for the freshest inventory and
the history-bearing pass for detailed score dialogs:

```bash
tools/run-python tools/build_reading_report_archive.py \
  private/student-c/archive-pass-2.local \
  --history-source private/student-c/archive-pass-1.local \
  --student 'Student C' \
  --output private/student-c/records.local
```

This produces a full lesson/activity inventory, occurrence-aware dated-attempt
CSV, discrepancy CSV, capture manifest, readable history, and completeness
report. Khan Kids displays weekday/month/day in historical dialogs but omits
the year. Both the raw displayed date and a clearly labeled, weekday-checked
year inference are retained. A second crawl should be compared with the first
before declaring the archive stable because Khan's own results can appear late.

## Analyze a child's progress

The CSV is the easiest entry point for a spreadsheet, SQL engine, dataframe, or
LLM. Its columns are:

```text
grade, domain, skill_group, lesson_title, activity_variant,
student result columns, title-level aggregate result columns
```

Recommended interpretation:

- A percentage is a scored attempt reported by Khan Kids.
- `Viewed` means the report records exposure without a numeric score.
- An empty cell means no displayed result; it is **unknown/not attempted**, not
  failure and not `0%`.
- A fraction such as `1/3` is a title-level completion count, not a percentage.
- Duplicate titles in different grade reports should retain their grade and
  hierarchy rather than being merged blindly.

For this family, the reading-order review searched titles and skill groups for:

- syllable blending;
- onset-and-rime and three-phoneme blending;
- word families;
- beginning, middle, ending, and first/last sound position;
- phoneme isolation and manipulation;
- CVC middle and ending sounds;
- one- and two-syllable words; and
- consonant blends.

The resulting rationale and suggested progression are in
[`ordering-related-lessons.md`](ordering-related-lessons.md). Treat this as an
instructional hypothesis to review with the child's teacher or reading
specialist, not a diagnostic assessment.

## Assign lessons safely

Khan Kids supports assignment from either the teacher Library or:

```text
Students → Class Reports → All Progress → expand a lesson → tap a variant
```

The assignment dialog can contain students who were assigned the same activity
on older dates. Preserve those existing checkbox states. Select only the target
child for today's assignment, verify every checkbox visually, and only then tap
**Save**.

Khan labels variants as Main, Practice 1, Practice 2, and Basic. Khan's general
guidance starts students with Main and uses Basic for extra support. This
project's mastery path instead begins with **Basic when present**, then advances
through Main, Practice 1, and Practice 2. Only one rung from a lesson family is
active at a time.

The original 16-lesson diagnostic batch is preserved in
[`ordering-assignments-2026-09-08.md`](ordering-assignments-2026-09-08.md).
It is historical evidence, not the current desired queue. Current assignments
and every checked or unchecked activity are recorded under `student-records/`.

The repository includes a state-checked desired-queue workflow. It combines the
mastery policy with the Khan-only prerequisite graph in
[`data/reading-curriculum.json`](data/reading-curriculum.json). Routine score
capture, advancement, corrective selection, assignment cleanup, verification,
and logging are deterministic and require no screenshot interpretation by an
LLM.

## Run the mastery workflow

Use the locked environment and private configuration described above. The shared
workflow can wake/unlock the tablet, enter the parent password, review scores,
and navigate Teacher Tools. It reuses an already-open supported teacher screen
only after two matching live reads; unknown screens and open modals retain the
cold-start route. It does not navigate a child's lesson view.

### Review and apply

Preview without changing assignments:

```bash
./khan-reading-sync --student 'Student A'
```

The protected `.secrets.json` is loaded automatically when needed. Override it
with `--secrets-file /secure/local/khan-secrets.json`, or specify the current
transport with `--serial "$KHAN_SERIAL"` for diagnostics.

Review writes a proposed plan, not assignment changes. Apply that exact plan with:

```bash
./khan-reading-sync \
  --student 'Student A' \
  --apply-plan private/student-a-reading-plan.json
```

Apply mode repeats the live scan and refuses stale assignment, score, catalog,
or curriculum snapshots. After an interruption, generate a fresh review plan;
reconciliation proposes only the remaining difference.

For the usual one-session sync, the private device configuration supplies the
student and device:

```bash
./khan-mastery-sync
```

This scans, plans, applies, and verifies in one session. A matching queue is a
verified no-op, without a redundant apply scan. `khan-mastery-sync` adds `--sync`
to the shared reading workflow; it contains no separate implementation.

### Queue policy

The planner:

- evaluates `Basic → Main → Practice 1 → Practice 2` using the
  [mastery policy](mastery-learning-policy.md);
- reserves up to eight positions for prerequisite-ready mastery-path lessons;
- normally limits similar lesson groups to three active choices, relaxing that
  diversity limit only when needed to reach ten approved assignments;
- fills remaining positions from the curated foundational-reading stretch pool,
  including vetted Beginning Sounds 1 and Rhyming maintenance reserves;
- pins unattempted stretch families, defers below-70% results, and permits a
  retry only after supporting mastery evidence changes;
- excludes active student-specific family quarantines and refills from the same
  approved curriculum; and
- requires exactly ten eligible desired lessons before changing assignments,
  otherwise reporting a review-required shortfall and withholding changes.

Ten assignments is a successful-sync invariant, not a guarantee during a
capacity-limited remove/add transition or while the device is unavailable.
See [the current reading path](reading-path.md) for queue and stretch details.

Quarantines live in `student-records/<student>-lesson-quarantines.csv`. A new
fourth-or-later attempt below 70% starts a 14-day family quarantine. Expired rows
remain as history and are not restarted without another new attempt.
An `eligible_date` of 2026-10-11 excludes the exact family through 2026-10-10.

### Scores, idempotency, and verification

Mastery sync opens every available colored score control in the target student's
Assignments column and records the full displayed history. It never relies on
the same-day cache. Review-only runs can reuse an unchanged history; use
`--full-score-scan` to disable that review cache.

Khan displays dates and percentages but no stable attempt ID or timestamp.
Occurrence-aware persistence preserves two identical same-day scores as two
attempts without appending them again on an unchanged scan. Blank results are
never interpreted as zero. Durable mastery cannot regress simply because its
predecessor is no longer assigned or its live dialog disappears.

Queue reconciliation is idempotent for unchanged available evidence. Repeated
runs still append reports and timings. Khan can reveal delayed scores even when
no lesson was played between runs; new evidence can legitimately change the queue.
An operating-system lock prevents concurrent workflows from opening the app or
mutating the same local records.

Before changing assignments, the workflow atomically journals the desired state
and planned actions. It adds replacements first when capacity permits; at full
capacity it alternates one removal with one addition. Every Save is independently
verified against the complete live queue and checkpointed. Success requires a
second fixed-point scan matching the complete desired set. Promotions are
reported as applied only after their removal and replacement were saved and verified.

### Navigation and failure recovery

Startup requires matching lock-state and foreground reads and allows at most
one PIN attempt. Shared transition guards recheck the source before retrying a
dropped tap, stop on unexpected screens, and require two destination reads.
Normal teardown uses the app's Back and Switch User controls to verify the
profile chooser without stopping or relaunching Khan Kids.

The command restores the prior screen timeout, plugged-in stay-awake setting,
and rotation mode on success or failure. Landscape locking uses the atomic
`wm user-rotation lock 3` operation; separate writes to `accelerometer_rotation`
and `user_rotation` can briefly expose a stale portrait angle. Cleanup restores
exactly `free` or the original `lock N` mode.

Workflow failures append a distinct, secret-safe entry to [INCIDENTS.md](INCIDENTS.md)
and return a nonzero status. An incident never authorizes continuing from an
unrecognized screen. If teardown fails after the data phase, the saved outcome
and full report remain authoritative; the incident identifies that later failure.
Interruption reports distinguish saved from verified operations and include the
last recoverable queue, missing/unexpected assignments, and duration.

### Reports and next-lesson suggestions

The default readable report includes:

- a prominent changes-since-last-sync summary and newly observed scores;
- opened score controls and their complete displayed histories;
- mastery found, assignments unchecked, and assignments added;
- reasons and score evidence for every change;
- the complete resulting queue, core/stretch roles, and mastery status; and
- active quarantines, verification outcome, and duration.

Review labels changes as proposed. Sync labels them as applied only after
verification. Symbols preserve meaning without terminal color: 🟢 mastered,
🔵 added, 🟡 new score/hold, 🟣 unchecked/deferred, and ⚪ unchanged.
Color respects `NO_COLOR`; override with `--color always` or `--color never`.

Successful syncs also suggest up to three **Do next** lessons from the verified
queue: provisional mastery, strong recent scores (80–89%), unattempted activities
in curriculum order, then lower-scoring practice. Each includes score evidence
and the result needed for mastery. Suggestions never change assignments and
are withheld for review-only or interrupted runs.

Add `--json` for structured output, including `next_lesson_recommendations`.
Default paths are student-derived; use `--attempts`, `--actions`, `--report`,
`--plan`, and `--max-actions` to override them. Run
`./khan-reading-sync --help` for all options. Wrappers work from other directories
when invoked by absolute path.

### Progress and profiling

Milestones, completed history reads, and verified actions stream to stderr,
with a ten-second heartbeat during longer work. With `--json`, stdout remains
machine-readable. A heartbeat is not evidence that an assignment was saved.
Performance payloads contain per-step timings and `max_silent_seconds`, measured
from workflow startup after device discovery.

Record an optional private cProfile file:

```bash
KHAN_PROFILE_OUTPUT="$PWD/private/mastery-sync.prof" ./khan-mastery-sync
./tools/run-python -m pstats private/mastery-sync.prof
```

The production backend uses uncompressed persistent hierarchy reads. Fresh roots
are reused only before gestures; post-Save and fixed-point verification always
read live state. Use `--ui-backend legacy-adb` only as a diagnostic fallback.
See [the baseline audit](performance-audit-2026-09-10.md) and
[the later update](performance-update-2026-09-16.md) for measured results.

## Test the tools

The automated suite covers mastery, report/date parsing, catalog lookup,
duplicate-safe records, workflow planning, graphical checkbox recognition,
privacy gates, and private screenshot cleanup:

```bash
uv run --frozen python -m compileall -q tools tests
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ruff check .
uv run --frozen ruff format --check .
./tools/run-python tools/audit_code_duplication.py
```

ImageMagick is required for the checkbox test. The GitHub Actions workflow in
`.github/workflows/tests.yml` installs it and runs lint, formatting, and tests on
pushes and pull requests, alongside the dedicated privacy job. The duplication
audit detects substantial exact cross-file blocks and function bodies; it does
not prove that all semantic duplication is absent. Live UI validation still
requires the calibrated tablet and is separate from these checks.

## Privacy, safety, and limitations

### Private student identities

Published records use Student A, Student B, and Student C. Save real tablet names
only in Git-ignored `private/student-aliases.local.json`, for example:

```json
{
  "REAL_DISPLAY_NAME": "Student A",
  "REAL_SECOND_DISPLAY_NAME": "Student B"
}
```

Replace the placeholders with the exact names displayed on your tablet. Include
every child on the account roster; aliases must have the form `Student A`.
Protect the file:

```bash
chmod 600 private/student-aliases.local.json
```

The local tablet configuration may retain the real default student name.
Normal sync and explicit `--student` inputs resolve through the private map;
quote aliases containing spaces in shell commands.

UI hierarchy capture fails before device I/O if the mapping is missing or empty.
Unmapped student inputs are rejected. Accessibility attributes and text are
anonymized before parsing or saving XML; records, reports, and incident student
identities use aliases. Supported score and student-selection views reject
unknown identity labels, but other UI text still depends on complete mappings.
The map is not a general-purpose detector of unknown people's names.

Public aliases remain usable for offline analysis without a tablet mapping.
Do not overwrite this project's archived records with another child's data;
choose explicit private output paths for your own work.

### Commit, push, and CI gates

For maintainers with access to this repository's GitHub secrets, configure the
local/CI fingerprints and install the hooks on each clone:

```bash
./tools/run-python tools/configure_student_privacy.py
git config core.hooksPath .githooks
```

The configuration command targets `JohnTheodore/khan_kids_reading_tutor`.
Fork maintainers must adapt that target to their repository. It updates
`KHAN_PRIVACY_KEY` and `KHAN_PRIVACY_FINGERPRINTS` and saves an ignored,
owner-private backup. Only keyed fingerprints and their key are supplied to CI,
not plaintext student names. Rerun it after changing the private mapping;
local hooks reject stale configuration.

- The pre-commit hook scans staged contents and filenames.
- The pre-push hook scans every commit reachable from outgoing refs, including
  commit metadata and files renamed or removed in later commits.
- The dedicated CI `privacy` job scans full checked-out history and fails if
  secrets are missing, invalid, or mismatched.
- Image captures are rejected by extension and binary signature, including
  renamed images and forced Git additions.

Run either audit manually:

```bash
./tools/run-python tools/audit_student_privacy.py
./tools/run-python tools/audit_student_privacy.py --history
```

The index audit excludes unstaged/untracked changes; stage intended changes
first. Reports print violation counts, not private names. Hooks are bypassable,
and CI detects leaks after upload, not before. The scanner detects configured
names, not every possible identity in arbitrary prose or images.

Fork pull requests do not receive these secrets and fail closed. Do not use
`pull_request_target` to expose secrets to untrusted code.
CI jobs alone do not enforce a merge gate. This repository is now public, so
[GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
is available without the private-repository plan upgrade. Require the `privacy`
and `test` checks to enforce merge protection; those requirements are not
currently configured.

### Screenshots and credentials

No captured screenshots are committed or needed as repository fixtures.
Checkbox/expansion checks use temporary screenshots under ignored `private/`,
with owner-only permissions and exception-safe cleanup. Crawlers no longer
retain page screenshots. Direct screenshot calls outside `private/` are
refused. Images are not anonymized; abrupt process termination may leave private
temporary files that need manual cleanup. Tests generate synthetic images at runtime.

Keep credentials, device addresses, pairing codes, and your own captures out of
public commits. The optional Android PIN and Khan password belong only in the
protected local secrets file described [above](#open-khan-kids-safely).
Pair only on a trusted network; never forward tablet or host ADB ports to the
internet. Obtain any consent required before processing other children's data.

### Published records and third-party material

Aliases do not make dated scores and learning trajectories fully anonymous.
This public repository intentionally retains its pseudonymized records and
captured XML/catalog data; publication is not a declaration that all capture
reuse is permitted. Khan's [Kids terms](https://www.khanacademy.org/kids/terms-of-service)
include content-redistribution and scraping/copying restrictions (§§8–9).
Review applicable rights and permissions separately from the project's MIT license.

Deleting a file does not remove it from Git history. History rewrites also do
not guarantee erasure from hosting caches, backups, or other people's clones.
Before publishing sensitive history, verify old commit IDs are unavailable and
use the host's sensitive-data removal process when needed.

### Technical limitations

- Coordinates and column bounds are device-, orientation-, roster-, and
  app-version-specific. Unit tests cannot validate a future layout.
- Khan provides no multi-assignment transaction. Checkpointing and alternating
  removals/replacements limit interruption exposure, but a failure can still
  leave an unmatched pair. Generate a fresh review to reconcile live state.
- The UI hierarchy omits some graphical text and checkbox states; narrow image
  checks remain necessary. Routine automation is procedural Python, not LLM vision.
- Lesson targets may be inferred from titles and standards, not Khan-authored
  descriptions. All Progress can repeat the same content across grades.
- Khan can expose scores late. A capture is a point-in-time archive, not a
  continuously synchronized database or diagnostic assessment.
- This project is not affiliated with or endorsed by Khan Academy.

Stop immediately if the visible screen differs from the expected state.
Recalibrate after a layout change before resuming any assignment operation.

## Troubleshooting

### `Pairing unsuccessful`

- Confirm both devices are on the same non-guest Wi-Fi network.
- Generate a new pairing code; codes and pairing ports expire.
- Run `adb pair` with the address in the pairing-code dialog.
- Run `adb connect` afterward with the **different** address on the main
  Wireless debugging screen.
- If client isolation or a VPN blocks local traffic, disable it or use USB.

### The device is paired but absent from `adb devices`

Pairing authorizes the computer but does not always establish the active debug
connection. Run:

```bash
adb connect TABLET_IP:DEBUG_PORT
adb devices
```

### The tablet dims or sleeps

Use `scrcpy --stay-awake`, keep the tablet powered, or temporarily change the
timeout using the commands above. Always restore the original setting.

### The crawler reports an unexpected screen

- Stop it with `Ctrl+C`.
- Confirm `adb shell wm user-rotation` reports `free` after the workflow, or
  the same `lock N` state that existed before it.
- Confirm `adb shell dumpsys window displays` reports `mRotation=3` for the
  calibrated 2560×1600 landscape direction.
- Restore landscape orientation and the expected starting screen if either
  check differs.
- Confirm Khan Kids has not changed its layout or labels.
- Compare the current screenshot dimensions with `2560x1600`.
- Recalibrate constants in the relevant crawler before retrying.

### The report parser assigns scores to the wrong child

Do not use the generated output. The parser's student names or score-column
bounds do not match the current roster layout. Update both and rebuild from the
unchanged raw captures.

## Project status and license

This is an active family research and automation project, calibrated to one
tablet/app layout rather than a general-purpose supported product. Dated captures,
research, and assignment documents preserve historical evidence; use the
[sources of truth](#sources-of-truth) for current policy.

Original source code and project documentation are licensed under
[MIT](LICENSE). This does not grant rights to Khan's trademarks, app assets, or
other third-party material, or override privacy rights in children's records.
Review captured material separately before redistributing it.
