# Khan Kids Reading Tutor

Tools, curriculum data, and Android automation for turning the Khan Academy
Kids teacher interface into a mastery-gated reading path and durable progress
record.

The project's primary goal is to help Student A reach independent reading as
directly as possible while preserving mastery at the prerequisite steps. It is
not an ELA-completion project. The reusable route is documented in
[`reading-path.md`](reading-path.md), and every material route change is
explained in [`curriculum-decisions.md`](curriculum-decisions.md).

This repository documents a family project, not an official Khan Academy
product. It converts the visible teacher UI into a reproducible workflow: index
the lesson library, capture scores, choose a small science-of-reading-aligned
queue, apply mastery promotions, maintain instructional diversity, and preserve
an append-only record of every decision.

> [!WARNING]
> This snapshot contains children's names, performance data, and Android
> accessibility dumps. **Do not publish or fork it publicly as-is.**
> Create a private copy, replace the names in the parser, and keep personal
> captures out of version control.

## Contents

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
  permanent event records; `student-a-reading-sync-log.md` is the generated run
  history.
- dated research and assignment documents preserve their original snapshots;
  they are not instructions for the current queue.

The crawlers create PNG screenshots locally for live validation, but captured
PNGs are ignored by Git and are not included in the repository. The retained
XML is sufficient to rebuild the current catalogs and score archive.

Blank result cells are retained as `not_attempted`; they are never treated as a
score of zero. Lesson descriptions marked “inferred” are interpretations of
the visible title and standard, not prose supplied by Khan Kids.

## How the system works

```text
Khan Kids Class Account on Android
        │
        ├── Teacher library: Letters / Reading
        │       └── screenshots + UI hierarchy XML
        │               └── Letters JSON and Markdown catalog
        │
        └── Students → Class Reports → All Progress → ELA
                └── screenshots + UI hierarchy XML
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

No unofficial Khan API, account scraping endpoint, or app modification is used.
The scripts operate the same on-device teacher UI that a person can see.

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

```bash
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
  --student Student C \
  --forbid-student Student A \
  --forbid-student Student B \
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
  --student Student C \
  --forbid-student Student A \
  --forbid-student Student B \
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
  --student Student C \
  --output private/student-c/records.local
```

For a two-pass capture, use the validation pass for the freshest inventory and
the history-bearing pass for detailed score dialogs:

```bash
tools/run-python tools/build_reading_report_archive.py \
  private/student-c/archive-pass-2.local \
  --history-source private/student-c/archive-pass-1.local \
  --student Student C \
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

Create the locked Python environment as described above. Its persistent UI
transport is much faster than invoking Android's legacy hierarchy dumper for
every screen.

The command can wake the tablet, unlock Android, start a fresh Khan Kids app
session, select the parent profile, enter the parent password, and safely
navigate from the teacher roster to Class Reports. Mastery sync deliberately
does not inspect or navigate the child lesson view: its fresh start establishes
the profile chooser as the only supported entry route to the parent/teacher
workflow.

Android can briefly report the lock screen, launcher, or notification shade
while waking and launching Khan Kids. Startup waits for two matching lock-state
reads, makes at most one PIN attempt, launches through ActivityManager's
wait-capable mode, and requires Khan Kids to hold foreground focus for two
reads. The React Native accessibility tree must then expose an approved semantic
screen before navigation. Unknown states still time out without tapping.

On successful completion, mastery sync stays inside the running Khan Kids app:
it uses the app's own **Back** control to return from Class Reports to Teacher
Tools, selects **Switch User**, and verifies the profile chooser. It does not
stop or relaunch Khan Kids during teardown. Both image-backed controls are used
only after their surrounding screen and exact bounds have been validated. Each
transition must produce two consecutive reads of the expected destination. One
shared transition guard covers forward report navigation, lesson expansion,
assignment Save, and teardown. If Khan drops a tap or its accessibility tree is
late, the guard re-reads the source screen and retries up to three times,
allowing 12 seconds per attempt. It stops immediately if any unexpected screen
appears.

Run a read-only review with:

```bash
./khan-reading-sync --serial "$KHAN_SERIAL" --student Student A
```

When Android may be PIN-locked, the default `.secrets.json` is used
automatically. To select another protected file explicitly:

```bash
./khan-reading-sync \
  --serial "$KHAN_SERIAL" \
  --student Student A \
  --secrets-file /secure/local/khan-secrets.json
```

The default review:

- filters Assignments to the named child;
- reads each changed or uncached scored activity's full history and safely
  reuses an exact same-day cache match;
- appends newly observed attempts without duplicating prior rows;
- evaluates `Basic → Main → Practice 1 → Practice 2` using the documented
  mastery policy;
- reserves up to eight positions for mastery-path lessons whose prerequisites
  are complete;
- normally limits configured groups of similar lessons, currently short-vowel/
  CVC-middle work, to three active choices, but admits the next approved choice
  when needed to reach the ten-assignment target;
- fills the remaining positions from a curated foundational-reading stretch pool,
  including vetted Beginning Sounds 1 and Rhyming maintenance reserves;
- requires exactly ten eligible desired lessons before changing any assignment;
  otherwise reports the shortfall and leaves the live queue untouched;
- pins every active stretch family until its first attempt, preserves a
  below-70% result as deferred, and permits a later retry only after supporting
  mastery evidence changes;
- excludes any student-specific lesson family with an active dated quarantine,
  then refills the queue from the same approved reading curriculum;
- automatically starts a 14-day family quarantine when a newly captured fourth
  or later attempt leaves that lesson variant below 70%; an expired quarantine
  is not restarted without another new attempt;
- computes the exact difference between the live and desired queues;
- writes a compact reviewed plan to `private/student-a-reading-plan.json`; and
- appends a human-readable run report to
  `student-records/student-a-reading-sync-log.md`.

Temporary family quarantines live in
`student-records/<student>-lesson-quarantines.csv`. Each row records a start
date, the first date the family may be considered again, and the evidence-based
reason. The end date is exclusive: a row with `eligible_date` 2026-10-11 is
excluded through 2026-10-10. Matching is by exact lesson-family title, so all
available variants of that family are withheld without affecting similarly
named families. Expired rows remain as history and stop affecting plans
automatically.

Screenshots used to distinguish checked from unchecked boxes live only in a
temporary directory and are deleted when the command exits. The optional
Android PIN and Khan parent password may be kept in the owner-private,
Git-ignored `.secrets.json` described above. They are loaded lazily, never
written to reports, and never passed as complete command-line arguments.
The command temporarily prevents sleep and restores the tablet's prior screen
timeout, plugged-in stay-awake setting, and rotation mode on success or
failure. Orientation locking uses Android WindowManager's single
`wm user-rotation lock 3` operation. Do not replace it with consecutive writes
to `accelerometer_rotation` and `user_rotation`: when auto-rotate is enabled,
the stored fallback angle may still be portrait, and disabling auto-rotate
first visibly flashes that stale angle before the landscape write arrives.
The workflow reads `wm user-rotation` before starting and restores exactly
`free` or the prior `lock N` mode during cleanup.

Successful sync reports include an advisory **Do next** section ranking up to
three lessons from the verified assignments: provisional mastery first, then
strong recent scores (80–89%), then unattempted activities in curriculum order,
then lower-scoring practice. Each shows its latest score, reason, and the result
needed for mastery, using the existing mastery evaluator. Recommendations do
not change assignments and are withheld for review-only or interrupted runs.
The structured result stores them in `next_lesson_recommendations`.

For the usual one-command operation, run without an address or student; the
private device configuration supplies both:

```bash
./khan-mastery-sync
```

The workflow streams phase milestones, completed score-history reads, and
verified assignment actions to stderr with flushing, plus a ten-second
heartbeat during longer operations. JSON stdout remains machine-readable.
Performance payloads include `max_silent_seconds`, measured from workflow
startup (device discovery precedes this reporter). Progress never issues device
commands or exposes credentials or raw UI text. A heartbeat is not evidence
that an assignment was saved; only verified action messages establish that.

Fresh UI roots are passed through navigation and scrolling helpers rather
than immediately read again. They are never used as a substitute for fresh
post-gesture or post-Save reads. Full mastery score scans, every post-Save queue
verification, and the final fixed-point scan remain mandatory.

Run `./tools/run-python tools/audit_code_duplication.py` to check production
and test Python files for substantial exact cross-file repeated blocks and
function bodies. This is a regression aid, not a proof that all semantic
duplication is absent; shared policy and transport code still require review.

This scans and plans once, then applies and verifies any changes in the same
device session. If the queue already matches the mastery plan, it records a
verified no-op and stops without a redundant apply scan. An eight- or nine-item
desired plan is never applied or called a verified no-op. The ten-item rule is
a successful-sync invariant, not a promise that Khan Kids will display ten
during a capacity-limited remove/add transition or while the tablet is
unavailable. If safe eligible lessons are exhausted, the report is
review-required and any changes are withheld. Every run writes
secret-safe per-step timing data. A mastery sync always opens every available
colored score control in Student A's Assignments column and records the complete
displayed history; it never trusts the same-day cache. Review-only runs may
reuse score histories when the visible lesson, variant, assignment date, and
score are unchanged; use `--full-score-scan` to disable that review cache too.
Use `--ui-backend legacy-adb` only as a diagnostic fallback. The current queue and
stretch policy are described in [`reading-path.md`](reading-path.md).

Queue reconciliation is idempotent: a verified mastery action is durable and
cannot regress merely because its predecessor is no longer assigned and its
live score dialog is unavailable. Complete live histories are reconciled by
occurrence count, so two identical scores on the same date remain two attempts
without being appended again on the next scan. The assignment-action ledger
records each actual mutation with a unique timestamp. An operating-system lock
allows only one reading workflow to use the tablet and its local records at a
time; a concurrent invocation fails before opening the app.

Every workflow failure from `khan-mastery-sync` also appends a distinct,
secret-safe entry to [`INCIDENTS.md`](INCIDENTS.md) before returning a nonzero
status. Automatic diagnostics redact common device-address, pairing-code, and
local-home-path forms. A failure remains fail-closed: the incident is a record
for diagnosis, not permission to continue from an unrecognized screen. If the
data phase completed before teardown failed, its saved outcome and full terminal
report remain authoritative; the error and incident explicitly identify the
later teardown failure, and timing data is still persisted.

Before changing the queue, the workflow atomically writes an operation journal
containing the desired-state fingerprint and every planned action. It adds a
replacement before removing its predecessor whenever capacity permits. At a
full queue it alternates one removal with one addition, so an interruption can
leave at most one unmatched removal instead of applying every removal first.
After every Save it captures the complete live queue, verifies the individual
change, and checkpoints the journal. Success requires a second, independent
fixed-point scan matching the complete desired set. Performance reports expose
each mutation-and-verification phase separately.

Every successful run ends with a readable terminal report containing:

- a visually dominant “Changes Since Last Sync” summary;
- newly observed scores and the relevant score history;
- every score control opened and its complete displayed attempt history;
- lessons that met the mastery rule;
- assignments unchecked and the evidence-based reason for each removal;
- assignments added and why each is the appropriate next rung or stretch item;
- all lessons in the resulting queue, with core/stretch role and mastery
  status; and
- verification outcome and total duration.

The report also names every active quarantine, its last excluded date, its
reconsideration date, and its reason.

Review-only output labels changes as proposed and not yet applied. Sync output
labels changes as applied only after exact post-write verification. For scripts
that consume the older compact payload, add `--json`.

Interactive terminals color new scores yellow, mastery green, removals magenta,
additions blue, and unchanged queue rows dim. Symbols preserve the same meaning
when color is unavailable. Color defaults to `auto`, respects the `NO_COLOR`
environment variable, and can be controlled explicitly with
`--color always` or `--color never`.

Review the JSON plan. Apply that exact plan with:

```bash
./khan-reading-sync \
  --serial "$KHAN_SERIAL" \
  --student Student A \
  --apply-plan private/student-a-reading-plan.json
```

Apply mode first repeats the live scan. It refuses to act if the assignments,
scores, catalog, or curriculum differ from the reviewed snapshot. It also
validates that the actions produce the desired queue, that every addition is in
the approved curriculum, and that the queue remains within its configured
limit. Each successful Save is logged and journaled immediately, independently
verified against the live queue, and followed by fixed-point verification. If a
run is interrupted, generate a new review plan; live-state reconciliation
proposes only the remaining difference. The interruption report records saved
versus verified operations, the last recoverable live queue, missing and
unexpected assignments, and duration. Promotions are reported as applied only
when both their removal and replacement addition were actually saved.

The default output paths are derived from the student name. Use `--attempts`,
`--actions`, `--report`, `--plan`, and `--max-actions` to customize the run.
`khan-mastery-sync` is the one-session sync alias for the same shared workflow;
it adds `--sync` and contains no separate implementation. Run
`./khan-reading-sync --help` for all options. Both wrappers resolve the
repository location first, so they can be invoked by absolute path from another
working directory.

Khan's report provides dates and percentages but no attempt timestamp or
stable attempt ID. The workflow therefore reconciles the multiplicity of each
lesson/variant/date/percentage tuple against the complete live history. Two
identical displayed attempts are stored as two occurrences, while rescanning
that unchanged pair adds nothing. The command never interprets a blank result
as zero.

## Test the tools

The automated test suite covers mastery decisions, date and report parsing,
catalog lookup, duplicate-safe records, workflow planning, and graphical
checkbox recognition:

```bash
uv run --frozen python -m compileall -q tools tests
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

ImageMagick is required for the checkbox test. The GitHub Actions workflow in
`.github/workflows/tests.yml` installs it and runs all four checks on pushes and
pull requests. Live UI validation still requires the calibrated Android tablet;
unit tests cannot guarantee compatibility with a future Khan Kids redesign.

## Privacy, safety, and limitations

### Protect children's data

- Keep `private/`, raw screenshots, XML dumps, reports, email addresses,
  passwords, device addresses, and pairing codes out of public commits.
- Never put a Khan Kids password or wireless-debugging pairing code in a script.
- Pair only on a trusted local network. For persistent access, approve only the
  dedicated home network and never expose the tablet's ADB port or the host ADB
  server port through router forwarding.
- Review screenshots as well as text files; images can expose names and scores.
- Obtain any consent required before processing data for children other than
  your own.

A suitable local `.gitignore` for a new private analysis area is:

```gitignore
private/
*.local.csv
*.local.json
*.local.xml
*.local.png
```

This does not retroactively remove files already committed. Use Git history
rewriting or start a clean repository if personal data has entered history.

### Technical limitations

- Coordinates and column bounds are device-, orientation-, roster-, and
  app-version-specific.
- Khan Kids does not provide a multi-assignment transaction. The workflow limits
  exposure by checkpointing and verifying every Save and alternating removals
  with replacements; a process or device failure can still interrupt one pair.
  Generate a fresh review to reconcile that final difference from live state.
- Android's UI hierarchy omits some graphical text and does not expose reliable
  checkbox state for every React Native control.
- The library does not provide prose descriptions for most lessons. Some targets
  in the generated catalog are explicitly inferred from titles and standards.
- The All Progress report can repeat content across grades.
- Khan Kids updates may invalidate the navigation constants without warning.
- Captures are a point-in-time archive, not a continuously synchronized mirror.
- This project is not affiliated with or endorsed by Khan Academy.

Stop a crawler immediately if the visible screen differs from the expected
Library or Class Report. The capture scripts are designed to avoid lesson cards,
but no coordinate-driven UI automation can guarantee safety after a layout
change.

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

This is an active family research and automation project. The captured catalog
is a point-in-time snapshot, while the mastery workflow and student records are
updated as new attempts occur. The tools remain a calibrated reference
implementation rather than a stable general-purpose end-user application.

The original source code and project documentation are available under the
[MIT License](LICENSE). That license does not grant rights to Khan Academy's
names, trademarks, app assets, or other third-party material, and it does not
override privacy rights in the captured children's data. Remove private and
third-party capture artifacts before redistributing a copy.
