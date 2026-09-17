# Khan Kids Reading Tutor

Give your child a focused learn-to-read path inside Khan Academy Kids.
This tutor keeps ten reading assignments ready, checks scores, and advances
lessons when your child shows mastery. It also recommends which assigned
lessons to try next.

Your child learns in Khan Kids. You run a command on your computer after a
session; the tutor reviews progress and updates the assignments for you.

## How it helps

- **A reading path, not the whole library.** Sound awareness, blending, and
  decoding take priority over completing every ELA lesson.
- **Practice before promotion.** Lessons advance after a latest score of 100%,
  or two consecutive scores of at least 90%.
- **Ten assignments to choose from.** A mix of prerequisite-ready reading work
  and foundational stretch activities keeps the queue manageable.
- **A useful next step.** The report suggests up to three assigned lessons,
  including activities close to mastery.
- **A lasting progress record.** Attempts and assignment changes are retained
  without duplicating unchanged scores on repeat runs.

The [reading path](reading-path.md) assumes basic letter–sound knowledge.
Check it against your child's starting point. Variants progress through
**Basic → Main → Practice 1 → Practice 2**, skipping unavailable rungs.
These are this project's rules, not Khan's default promotion policy.

## What you need

- Khan Academy Kids with a **Class Account**.
- An Android tablet and a Linux computer with Python 3.13, ADB, ImageMagick,
  and `uv`. `scrcpy` is optional for watching the tablet on your computer.
- Comfort with a terminal and Android Developer options.

The current automation is tested on a **Pixel Tablet at 2560×1600 in landscape,
with Khan Kids 9.0.1**. Other devices, app versions, and roster layouts may need
calibration. Watch the first review before allowing assignment changes.
This is a working family project, not a one-click installer for every tablet.

## 1. Set up Khan Kids

### Enable Teacher Tools

Use the account containing your child's existing profile. Enter the Parent
section using the swipe gate, open its account dropdown, and choose
**Convert to a Class Account**. Complete the prompts, then open the teacher's
bear avatar with the teacher password.

For the current automation, the teacher profile's display name must be **`dad`**
(lowercase). Use that name when setting up Teacher Tools; a different teacher
name requires adapting the profile matching in `tools/khan_kids/automation.py`.

Khan documents that existing profiles and progress carry over. Follow its
[conversion instructions](https://khankids.zendesk.com/hc/en-us/articles/360042944391-FAQ-How-do-I-convert-my-account-to-a-Class-Account),
or [create a new Class Account](https://khankids.zendesk.com/hc/en-us/articles/360042193551-Module-1-Setting-up-a-Class-Account).
The free in-app Teacher Tools are sufficient; you do not need the paid school dashboard.

### Strongly recommended: assignments-first access

Enable **Show students Assignments first** so your child works on the selected
lessons before exploring other areas of Khan Kids:

1. Open the teacher's bear avatar and enter the teacher password.
2. Open **Teacher Settings** in the top-left corner.
3. Turn **Show students Assignments first** **ON**.
4. Check the child's profile: other areas should be unavailable while assignments
   are incomplete.

After all assignments are complete, Khan unlocks the other areas. This is an
assignments-first gate, not a permanent assignments-only lock or our score-based
mastery rule. See [Khan's setting instructions](https://khankids.zendesk.com/hc/en-us/articles/17031195532059--NEW-Manage-student-access-to-areas-of-the-app).

Set this manually once during onboarding. The tutor does not enable or verify
it yet. Automated setup needs verified Settings controls, an already-ON no-op,
and a fresh persistence check. Create/Videos restrictions are separate choices.

## 2. Install the computer tools

On Ubuntu:

```bash
sudo apt update
sudo apt install git adb imagemagick
git clone https://github.com/JohnTheodore/khan_kids_reading_tutor.git
cd khan_kids_reading_tutor
```

Install `uv` using its [official instructions](https://docs.astral.sh/uv/getting-started/installation/),
then install the locked dependencies:

```bash
uv sync --frozen
```

For an optional live view, install a current
[scrcpy release](https://github.com/Genymobile/scrcpy/blob/master/doc/linux.md).

## 3. Connect the tablet

**USB:** Enable USB debugging under Android Developer options, connect a
data-capable cable, and approve the computer on the tablet. Check `adb devices`.

**Wireless:**

1. Under **Settings → About tablet**, tap **Build number** seven times.
2. Open **System → Developer options → Wireless debugging** and turn it on.
3. Put the tablet and computer on the same trusted network.
4. Choose **Pair device with pairing code**, then pair using that dialog's address:

```bash
adb pair TABLET_IP:PAIRING_PORT
# Enter the pairing code when prompted.
```

Connect using the separate address on the main Wireless debugging page:

```bash
adb connect TABLET_IP:DEBUG_PORT
adb devices
```

Ports can change. After a cold reboot on the tested Pixel Tablet, physically
unlock it and re-enable Wireless debugging; pairing usually survives, but the
switch does not stay on.

Set the current connected transport—either the USB serial or wireless `IP:port`
shown by `adb devices`—and get the device's identity:

```bash
KHAN_SERIAL='YOUR_CONNECTED_ADB_SERIAL'
adb -s "$KHAN_SERIAL" shell getprop ro.serialno
adb -s "$KHAN_SERIAL" shell getprop ro.product.model
```

Save those returned values in the configuration below. The resolver recognizes
an already-connected matching device. Automatic wireless rediscovery also
supports the original Linux-in-OrbStack setup through macOS mDNS; on other hosts,
use USB or reconnect with `adb connect` when the wireless port changes.

If Android 17 Wi-Fi debugging needs newer ADB, install current SDK Platform Tools
under `private/android-sdk/platform-tools`; wrappers prefer that private copy.
See [connection details](tablet-persistent-adb-research.md) for reboot limitations.

## 4. Give your family its own configuration

From the repository directory:

```bash
mkdir -p private/my-family
```

Create `private/student-aliases.local.json`. Replace the placeholder with the
child's exact name in Khan Kids:

```json
{
  "YOUR_CHILD_DISPLAY_NAME": "Student A"
}
```

Include every child on the account, adding `Student B`, `Student C`, and so on.
Aliases keep real names out of exported records.

Create `private/tablet-device.local.json` using the hardware serial and model
returned by the commands above:

```json
{
  "student": "Student A",
  "hardware_serial": "HARDWARE_SERIAL_FROM_GETPROP",
  "model": "MODEL_FROM_GETPROP"
}
```

Create `.secrets.json` in the repository root:

```json
{
  "android_pin": "YOUR_TABLET_PIN",
  "khan_parent_password": "YOUR_TEACHER_PASSWORD"
}
```

The PIN is needed only when the tablet is locked. Preserve password capitalization.
Automatic password entry currently supports ASCII letters and digits; passwords
containing symbols need a keyboard-entry adaptation before unattended login.
Protect the files:

```bash
chmod 600 .secrets.json private/student-aliases.local.json private/tablet-device.local.json
```

### Match the catalog to your roster

Reuse the lesson inventory, but give your account its own catalog copy:

```bash
cp data/reading-ela-archive.json private/my-family/catalog.json
```

In that copy, edit the top-level `students` list to match your account's aliases,
in the order shown in Class Reports. For one child:

```json
"students": ["Student A"]
```

The tutor uses the catalog for lesson locations and variants. Scores come from
your tablet and your own attempt/action files—not the example family's archived
result fields. A different roster layout still needs a watched pilot review.

### Save a one-command launcher

Keep your records separate from the example family records included here.
Save this as `private/my-family/sync.local.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
family_dir="$repo_dir/private/my-family"
case "${1:-sync}" in
  review)
    workflow=khan-reading-sync
    plan_args=(--plan "$family_dir/reading-plan.json")
    ;;
  sync)
    workflow=khan-mastery-sync
    plan_args=()
    ;;
  *)
    printf 'Usage: %s [review|sync]\n' "$0" >&2
    exit 2
    ;;
esac
exec "$repo_dir/$workflow" \
  --student 'Student A' \
  --catalog "$family_dir/catalog.json" \
  --attempts "$family_dir/lesson-attempts.csv" \
  --actions "$family_dir/assignment-actions.csv" \
  --report "$family_dir/reading-sync-log.md" \
  --quarantines "$family_dir/lesson-quarantines.csv" \
  --history-cache "$family_dir/score-history-cache.json" \
  "${plan_args[@]}"
```

Make it executable:

```bash
chmod 700 private/my-family/sync.local.sh
```

This launcher only supplies your paths to the existing workflow; it is not a
second tutor implementation. Change its alias when targeting another child.

## 5. Preview, then start using it

Watch the tablet directly or mirror it:

```bash
scrcpy --serial "$KHAN_SERIAL" --stay-awake
```

Start with a review:

```bash
./private/my-family/sync.local.sh review
```

Review opens the teacher reports, reads scores, and saves a proposed plan.
It does not change assignment checkboxes, although it records newly observed
attempts. Check the child, lesson names, score evidence, and proposed removals—
the planner reconciles the existing queue, not just new assignments.

When the preview looks right, run:

```bash
./private/my-family/sync.local.sh
```

Sync reads current state again, applies the desired queue, and verifies it.
Every change is checked after Save; a second complete queue scan must match
before success is reported. The app normally returns to the profile chooser.

Run that same command after learning sessions. For the original configured
family, `./khan-mastery-sync` alone remains the usual command; the launcher
above keeps a new family's records separate.

## Reading the report

Start with **Changes since last sync** and **Do next**:

- 🟡 **New score/hold:** a newly captured attempt or a lesson needing more practice.
- 🟢 **Mastered:** score history meets the promotion rule.
- 🟣 **Unchecked/deferred:** a lesson was removed or set aside, with the reason.
- 🔵 **Added:** the next variant or another eligible reading lesson.
- ⚪ **Unchanged:** no new relevant evidence or assignment changes.

The report includes score evidence, the final queue, and duration. **Do next**
ranks up to three already-assigned lessons; it does not add extra assignments.
Use it to pick a near-mastery lesson to finish or a new activity to introduce.

Look for a verified outcome. A review-required result describes proposed
changes, not applied ones. Failed runs distinguish saved from verified actions.

## Common questions

**Will another sync change things if my child hasn't done a lesson?**

Not when available evidence and the queue are unchanged. Reports/timings still
append. Khan can reveal delayed scores later, so runs may legitimately see new
evidence even without a lesson between them.

**Why ten assignments?**

It provides a manageable mix of core and stretch work. Ten is enforced on
successful completion, not during transitions or interrupted runs. If ten safe
choices aren't available, the tutor explains why and withholds assignment changes.

**My child already completed lots of lessons. Will they all be imported?**

Routine sync reads histories available in Assignments, not every past lesson.
Review the starting route before applying it. A complete archive is possible
with `tools/khan_report_archive_crawl.py`; importing it into the sync ledger
requires matching its record format.

**The tablet rebooted or disappeared.**

Physically unlock it, re-enable Wireless debugging, and reconnect using the
current address. USB avoids port changes. Never disable the lock screen or expose
ADB to the internet to make access easier.

**The tutor stopped on an unexpected screen.**

Check landscape orientation, the account, and app layout before retrying.
Do not keep tapping guessed coordinates. Failures are recorded in `INCIDENTS.md`;
generate a fresh review after an interrupted assignment change.

**Can I customize the path?**

Start with [reading-path.md](reading-path.md), edit your own copy of
`data/reading-curriculum.json`, and pass `--curriculum` through your launcher.
Keep prerequisites and mastery gates intact. The [curriculum decisions](curriculum-decisions.md)
explain why the current lessons were selected.

## More detail

- [Reading path](reading-path.md): skill progression, prerequisites, and stopping rule.
- [Mastery policy](mastery-learning-policy.md): promotion, retries, and quarantines.
- [Lesson catalog](letters-lessons.md) and [ELA archive](reading-ela-archive.md): inventory.
- [Next-lesson research](student-a-next-reading-lessons-science-of-reading.md): the original family's reasoning.
- [Connection research](tablet-persistent-adb-research.md): access and reboot limitations.
- [Performance notes](performance-update-2026-09-16.md): profiling and measured improvements.

For options, run `./khan-reading-sync --help`. Developers can check the code with:

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ruff check .
uv run --frozen ruff format --check .
./tools/run-python tools/audit_code_duplication.py
```

## Your family's data and licensing

Keep your configuration, credentials, and records under ignored `private/`
(the root `.secrets.json` is also ignored). Image checks use private temporary
screenshots; no captured screenshots need to be committed. The included example
records use aliases but retain dated scores and learning trajectories.

Original code and documentation are [MIT licensed](LICENSE). Khan's trademarks,
app assets, and captured materials have separate rights; see
[Khan Kids' terms](https://www.khanacademy.org/kids/terms-of-service) before
redistributing captures. This project is not affiliated with Khan Academy.
