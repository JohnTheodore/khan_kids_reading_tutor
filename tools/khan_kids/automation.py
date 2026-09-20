"""State-checked navigation for score review and mastery assignment changes."""

from __future__ import annotations

import secrets
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .adb import AndroidDevice, AutomationError
from .constants import LEARNING_SEQUENCE, REPORT_GRADE_LABELS
from .reports import (
    DEFAULT_REPORT_LAYOUT,
    AssignmentRow,
    AssignmentSnapshot,
    ReportLayout,
    ScoreHistory,
    is_assignment_report,
    parse_assignment_rows,
    parse_score_history,
)
from .ui import Rect, UiText, find_text, near, node_rect, text_set, visible_nodes
from .vision import CheckboxReading, CheckboxState, read_checkboxes

SCROLL_DURATION_MS = 300
REPORT_BACK_RECT = Rect(38, 38, 171, 171)
SWITCH_USER_RECT = Rect(2259, 12, 2529, 74)
CHILD_PROFILE_RECT = Rect(2365, 13, 2548, 196)
GUARDED_TRANSITION_ATTEMPTS = 3
GUARDED_TRANSITION_TIMEOUT_SECONDS = 12
STABLE_TRANSITION_READS = 2
SCROLL_BOUNDARY_READS = 2
EXPECTED_SCREEN_RECT = Rect(0, 0, 2560, 1600)
SCREEN_EDGE_TOLERANCE_PX = 1


class _PrizeInterruption(RuntimeError):
    """Signal a verified reward prompt during an otherwise guarded transition."""


@dataclass(frozen=True, slots=True)
class ActionResult:
    action: str
    title: str
    variant: str
    result: str


@dataclass(slots=True)
class _ScrollBoundary:
    """Require repeated unchanged reads before treating a report as exhausted."""

    previous: object = None
    unchanged_reads: int = 0

    def reached(self, signature: object) -> bool:
        if signature == self.previous:
            self.unchanged_reads += 1
        else:
            self.unchanged_reads = 0
        self.previous = signature
        return self.unchanged_reads >= SCROLL_BOUNDARY_READS


class KhanKidsAutomation:
    def __init__(
        self,
        device: AndroidDevice,
        *,
        student: str,
        roster: tuple[str, ...],
        scratch: Path,
        layout: ReportLayout = DEFAULT_REPORT_LAYOUT,
        parent_password_provider: Callable[[], str] | None = None,
        history_lookup: Callable[[AssignmentRow], ScoreHistory | None] | None = None,
        forbidden_students: tuple[str, ...] = (),
        hierarchy_observer: Callable[[ET.Element], None] | None = None,
        failure_capture: Callable[[Exception], None] | None = None,
    ) -> None:
        if student not in roster:
            raise ValueError(f"Student {student!r} is not in roster {roster!r}")
        self.device = device
        self.student = student
        self.roster = roster
        self.scratch = scratch
        self.layout = layout
        self.parent_password_provider = parent_password_provider
        self.history_lookup = history_lookup
        self.forbidden_students = forbidden_students
        self.hierarchy_observer = hierarchy_observer
        self.failure_capture = failure_capture
        self._assignments_at_top = False
        self.scratch.mkdir(parents=True, exist_ok=True)

    def root(self, name: str = "window") -> ET.Element:
        root = self.device.dump(self.scratch / f"{name}.xml")
        self._validate_screen(root)
        self._observe_hierarchy(root)
        return root

    def live_root(self) -> ET.Element:
        """Inspect state without persisting sensitive transient UI text."""
        root = self.device.hierarchy()
        self._validate_screen(root)
        self._observe_hierarchy(root)
        return root

    def _observe_hierarchy(self, root: ET.Element) -> None:
        if self.hierarchy_observer is not None:
            try:
                self.hierarchy_observer(root)
            except Exception:
                self.device.timing.progress(
                    "Diagnostic capture unavailable; tablet guards remain active"
                )

    @staticmethod
    def _validate_screen(root: ET.Element) -> None:
        screen = _screen_rect(root)
        edge_error = max(
            abs(actual - expected)
            for actual, expected in zip(
                screen.as_list(), EXPECTED_SCREEN_RECT.as_list(), strict=True
            )
        )
        if edge_error > SCREEN_EDGE_TOLERANCE_PX:
            raise AutomationError(
                "Unsupported display orientation or size: "
                f"{screen}; expected landscape [0,0][2560,1600]"
            )

    def ensure_assignments_report(
        self, *, root: ET.Element | None = None, state: str | None = None
    ) -> ET.Element:
        if root is None or state is None:
            root, state = self._wait_for_navigation_state()
        else:
            self._validate_screen(root)
            if self._navigation_state(root) != state:
                raise AutomationError("Supplied navigation state does not match its fresh root")
        if state == "assignments_report":
            return root
        if state == "all_progress_report":
            root = self._tap_navigation_control(
                root,
                source_state=state,
                target_state="assignments_report",
                control=lambda candidate: self._header_rect(candidate, "Assignments"),
                control_name="Assignments tab",
            )
        elif state == "class_reports_menu":
            root = self._tap_navigation_control(
                root,
                source_state=state,
                target_state="assignments_report",
                control=lambda candidate: _unique_visible(candidate, "Class Reports").rect,
                control_name="Class Reports",
            )
        elif state == "teacher_roster":
            root = self._open_class_reports_from_roster(root)
        elif state == "profile_chooser":
            root = self._login_parent(root)
            root = self._open_class_reports_from_roster(root)
        elif state == "password_dialog":
            root = self._submit_parent_password(root)
            root = self._open_class_reports_from_roster(root)
        elif state == "report_tabs":
            root = self._tap_navigation_control(
                root,
                source_state=state,
                target_state="assignments_report",
                control=lambda candidate: _unique_visible(candidate, "Assignments").rect,
                control_name="Assignments tab",
            )
        else:
            raise AssertionError(f"Unhandled navigation state: {state}")
        if not is_assignment_report(root):
            raise AutomationError("Navigation did not reach Class Report: Assignments")
        self._assignments_at_top = True
        return root

    def ready_for_sync(self) -> bool:
        """Reuse only an independently confirmed supported foreground screen."""
        try:
            roots = [self.live_root() for _ in range(2)]
            states = [self._navigation_state(root) for root in roots]
            if states[0] != states[1]:
                raise AutomationError("Startup screen is still changing")
            root = roots[-1]
            state = states[-1]
            if state == "prize_picker":
                root = self.pick_random_prize()
                state = self._navigation_state(root)
            if state == "child_assignments":
                root = self._tap_until_navigation_target(
                    root,
                    source_state=state,
                    target_state="child_home",
                    control_rect=REPORT_BACK_RECT,
                    control_name="Child library back",
                )
                state = "child_home"
            if state == "child_home":
                self._tap_until_navigation_target(
                    root,
                    source_state=state,
                    target_state="profile_chooser",
                    control_rect=CHILD_PROFILE_RECT,
                    control_name="Child profile circle",
                )
                return True
            elif state not in {
                "profile_chooser",
                "teacher_roster",
                "assignments_report",
                "all_progress_report",
            }:
                raise AutomationError("Unrecognized foreground startup screen")
        except AutomationError:
            diagnostic = self._capture_blocked_startup()
            raise AutomationError(
                "Startup blocked; app left open without restarting. "
                f"Inspect the screen before retrying. {diagnostic}"
            ) from None
        return states[0] in {
            "profile_chooser",
            "teacher_roster",
            "assignments_report",
            "all_progress_report",
        }

    def _prize_choices(self, root: ET.Element) -> tuple[Rect, ...]:
        """Recognize the observed three-card reward overlay, not ordinary home UI."""
        texts = text_set(root)
        labels = [item for item in visible_nodes(root) if item.text in self.roster]
        if (
            len(labels) != 1
            or labels[0].rect.left <= 2100
            or labels[0].rect.top >= 180
            or texts
            & {"Assignments", "All Progress", "Enter Password", "Students", "Save", "Sign Out"}
        ):
            return ()
        images = {
            node_rect(node)
            for node in root.iter("node")
            if node.get("class") == "android.widget.ImageView"
        }
        cards = sorted(
            (
                rect
                for rect in images
                if rect is not None
                and 500 <= rect.width <= 550
                and 500 <= rect.height <= 550
                and 500 <= rect.top <= 600
            ),
            key=lambda rect: rect.left,
        )
        expected = (
            Rect(364, 538, 889, 1063),
            Rect(1022, 538, 1547, 1063),
            Rect(1676, 538, 2201, 1063),
        )
        avatars = (
            Rect(551, 983, 701, 1133),
            Rect(1206, 983, 1356, 1133),
            Rect(1864, 983, 2014, 1133),
        )

        def matches(actual: Rect, reference: Rect) -> bool:
            return all(
                abs(a - b) <= 6 for a, b in zip(actual.as_list(), reference.as_list(), strict=True)
            )

        if len(cards) != 3 or not all(
            matches(actual, reference) for actual, reference in zip(cards, expected, strict=True)
        ):
            return ()
        if not all(
            any(rect is not None and matches(rect, avatar) for rect in images) for avatar in avatars
        ):
            return ()
        return tuple(cards)

    def pick_random_prize(self) -> ET.Element:
        """Choose once from a fresh verified overlay; never retry an uncertain award."""
        root = self.live_root()
        choices = self._prize_choices(root)
        if not choices or self._navigation_state(root) != "prize_picker":
            raise AutomationError("Verified three-choice prize picker is not visible")
        if self.student not in text_set(root):
            raise AutomationError("Refusing to collect a prize for a different student")
        selected = secrets.choice(choices)
        self.device.timing.progress("Choosing one of three Khan Kids prizes at random")
        self.device.tap_rect(selected)
        return self._wait_for_stable_root(
            lambda candidate: self._navigation_state(candidate) == "child_home",
            description="child home after random prize selection",
            timeout=20,
            persist=False,
        )

    def _capture_blocked_startup(self) -> str:
        """Retain one owner-private snapshot before any restart could hide a prompt."""
        private = Path(__file__).resolve().parents[2] / "private"
        try:
            private.mkdir(mode=0o700, exist_ok=True)
            destination = Path(tempfile.mkdtemp(prefix="startup-blocked-", dir=private))
        except OSError:
            return "Private diagnostic directory could not be created."
        failures = []
        for name, capture in (
            ("screen.png", self.device.screenshot),
            ("window.xml", self.device.dump),
        ):
            try:
                path = destination / name
                path.touch(mode=0o600)
                capture(path)
            except Exception:
                failures.append(name)
        location = f"private/{destination.name}"
        suffix = f"; capture failed: {', '.join(failures)}" if failures else ""
        return f"Diagnostics: {location}{suffix}."

    def return_to_profile_chooser(self) -> ET.Element:
        """Leave Teacher view through Khan's UI, retrying dropped navigation taps."""
        root, state = self._wait_for_navigation_state()
        if state == "profile_chooser":
            return root
        if state not in {"assignments_report", "all_progress_report", "report_tabs"}:
            raise AutomationError(
                f"Cannot safely leave Teacher view from navigation state {state!r}"
            )

        roster = self._tap_until_navigation_target(
            root,
            source_state=state,
            target_state="teacher_roster",
            control_rect=REPORT_BACK_RECT,
            control_name="Class Report back",
        )
        return self._tap_until_navigation_target(
            roster,
            source_state="teacher_roster",
            target_state="profile_chooser",
            control_rect=SWITCH_USER_RECT,
            control_name="Switch User",
        )

    def _tap_until_navigation_target(
        self,
        root: ET.Element,
        *,
        source_state: str,
        target_state: str,
        control_rect: Rect,
        control_name: str,
    ) -> ET.Element:
        return self._tap_navigation_control(
            root,
            source_state=source_state,
            target_state=target_state,
            control=lambda candidate: _guarded_unlabeled_control(
                candidate, control_rect, control_name
            ),
            control_name=control_name,
        )

    def _tap_navigation_control(
        self,
        root: ET.Element,
        *,
        source_state: str,
        target_state: str,
        control: Callable[[ET.Element], Rect],
        control_name: str,
    ) -> ET.Element:
        return self._tap_until_root_target(
            root,
            source=lambda candidate: self._navigation_state(candidate) == source_state,
            target=lambda candidate: self._navigation_state(candidate) == target_state,
            control=control,
            source_name=source_state,
            target_name=target_state,
            control_name=control_name,
            timeout=GUARDED_TRANSITION_TIMEOUT_SECONDS,
        )

    def _tap_until_root_target(
        self,
        root: ET.Element,
        *,
        source: Callable[[ET.Element], bool],
        target: Callable[[ET.Element], bool],
        control: Callable[[ET.Element], Rect],
        source_name: str,
        target_name: str,
        control_name: str,
        timeout: float,
    ) -> ET.Element:
        """Retry one guarded control only while its validated source remains intact."""
        action_attempt = 0
        prize_handled = False
        prize_allowed = (source_name, target_name) in {
            ("child_assignments", "child_home"),
            ("child_home", "profile_chooser"),
        }
        while action_attempt < GUARDED_TRANSITION_ATTEMPTS:
            if not source(root):
                raise AutomationError(
                    f"{control_name} cannot run from unexpected state while expecting "
                    f"{source_name!r}"
                )
            action_attempt += 1
            self.device.tap_rect(control(root))
            self.device.timing.progress(
                f"Opening {target_name}: {control_name}, attempt {action_attempt}"
            )
            first_wait = (
                min(timeout, 3.0)
                if action_attempt == 1 and source_name == "teacher_roster"
                else timeout
            )

            def target_without_prize(candidate: ET.Element) -> bool:
                if prize_allowed and self._navigation_state(candidate) == "prize_picker":
                    raise _PrizeInterruption
                return target(candidate)

            try:
                return self._wait_for_stable_root(
                    target_without_prize,
                    description=f"{target_name} after {control_name}",
                    timeout=first_wait,
                    persist=False,
                )
            except _PrizeInterruption:
                if prize_handled:
                    raise AutomationError(
                        "A second prize prompt interrupted the same navigation; "
                        "refusing another automatic selection"
                    ) from None
                prize_handled = True
                root = self.pick_random_prize()
                if target(root):
                    return root
                if not source(root):
                    state = self._navigation_state(root)
                    raise AutomationError(
                        f"Prize selection returned an unexpected navigation state {state!r}"
                    ) from None
                # The prompt consumed this navigation action. Resume from the
                # freshly verified child home without charging a retry attempt.
                action_attempt -= 1
                self.device.timing.progress(
                    f"Prize collected; resuming {control_name} from verified child home"
                )
                continue
            except AutomationError:
                root = self.live_root()
            if first_wait < timeout and not source(root) and not target(root):
                # A transition may still be loading. Never tap it; retain the
                # original readiness deadline before declaring an unexpected state.
                return self._wait_for_stable_root(
                    target,
                    description=f"{target_name} still loading after {control_name}",
                    timeout=timeout - first_wait,
                    persist=False,
                )
            if target(root):
                confirmation = self.live_root()
                if target(confirmation):
                    return confirmation
                root = confirmation
            if not source(root):
                state = self._navigation_state(root)
                raise AutomationError(
                    f"{control_name} reached unexpected navigation state {state!r}"
                )
            self.device.timing.progress(f"Source screen still verified; retrying {control_name}")
        raise AutomationError(
            f"{control_name} remained on {source_name!r} after "
            f"{GUARDED_TRANSITION_ATTEMPTS} guarded attempts"
        )

    def _wait_for_stable_root(
        self,
        predicate: Callable[[ET.Element], bool],
        *,
        description: str,
        timeout: float,
        persist: bool,
    ) -> ET.Element:
        matching_reads = 0

        def stable(candidate: ET.Element) -> bool:
            nonlocal matching_reads
            matching_reads = matching_reads + 1 if predicate(candidate) else 0
            return matching_reads >= STABLE_TRANSITION_READS

        return self._wait_for_root(
            stable,
            description=description,
            timeout=timeout,
            persist=persist,
        )

    def _wait_for_navigation_state(
        self,
        *,
        timeout: float = 10,
        stable_reads: int = 2,
    ) -> tuple[ET.Element, str]:
        """Wait for React Native to expose one stable, approved navigation state."""
        if stable_reads < 1:
            raise ValueError("stable_reads must be positive")
        last_state: str | None = None
        matching_reads = 0

        def is_stable(root: ET.Element) -> bool:
            nonlocal last_state, matching_reads
            state = self._navigation_state(root)
            if state is None:
                last_state = None
                matching_reads = 0
                return False
            if state == last_state:
                matching_reads += 1
            else:
                last_state = state
                matching_reads = 1
            required_reads = 1 if state == "assignments_report" else stable_reads
            return matching_reads >= required_reads

        root = self._wait_for_root(
            is_stable,
            description="stable Khan navigation state",
            timeout=timeout,
            persist=False,
        )
        if last_state is None:
            raise AssertionError("Stable navigation predicate returned without a state")
        return root, last_state

    def _navigation_state(self, root: ET.Element) -> str | None:
        """Classify only screens from which navigation is explicitly supported."""
        if is_assignment_report(root):
            return "assignments_report"
        texts = text_set(root)
        unexpected = set(self.forbidden_students) & texts
        if unexpected:
            raise AutomationError(
                f"Account identity guard found forbidden students: {sorted(unexpected)!r}"
            )
        if self._prize_choices(root):
            return "prize_picker"
        # An unfamiliar reward layout can still expose the home name underneath.
        # Never treat large central choice cards as an unobstructed home screen.
        if any(
            node.get("class") == "android.widget.ImageView"
            and (rect := node_rect(node)) is not None
            and 500 <= rect.width <= 550
            and 500 <= rect.height <= 550
            and 500 <= rect.top <= 600
            for node in root.iter("node")
        ):
            return None
        if "Class Report: All Progress" in texts:
            return "all_progress_report"
        if "Class Reports" in texts:
            return "class_reports_menu"
        if "Students" in texts and all(student in texts for student in self.roster):
            return "teacher_roster"
        if self._is_profile_chooser(root):
            return "profile_chooser"
        if "Enter Password" in texts:
            return "password_dialog"
        if "Assignments" in texts and "All Progress" in texts:
            return "report_tabs"
        if {"Assignments", "Lessons assigned to you by dad"}.issubset(texts):
            return "child_assignments"
        labels = [item for item in visible_nodes(root) if item.text in self.roster]
        if (
            len(labels) == 1
            and labels[0].rect.left > 2100
            and labels[0].rect.top < 180
            and not (texts & {"Assignments", "Enter Password", "Students", "Sign Out"})
        ):
            return "child_home"
        return None

    def _is_teacher_roster(self, root: ET.Element) -> bool:
        texts = text_set(root)
        return {"Students", "Add Students", *self.roster}.issubset(texts)

    def _is_profile_chooser(self, root: ET.Element) -> bool:
        texts = text_set(root)
        if not {"dad", *self.roster}.issubset(texts):
            return False
        if "Sign Out" in texts:
            return True
        # Khan sometimes omits Sign Out from accessibility on the real chooser.
        # Require its independently verified avatar-label row instead of accepting
        # any page that merely mentions the parent and students.
        labels = [find_text(root, name) for name in ("dad", *self.roster)]
        if any(len(matches) != 1 for matches in labels):
            return False
        rectangles = [matches[0].rect for matches in labels]
        parent, *children = rectangles
        centers = [rect.center[1] for rect in rectangles]
        return bool(
            children
            and parent.left < min(rect.left for rect in children)
            and min(centers) > _screen_rect(root).bottom * 0.45
            and max(centers) < _screen_rect(root).bottom * 0.8
            and max(centers) - min(centers) <= 60
        )

    def _login_parent(self, root: ET.Element) -> ET.Element:
        parent = _unique_visible(root, "dad")
        self.device.tap_rect(parent.rect)
        password_root = self._wait_for_root(
            lambda candidate: "Enter Password" in text_set(candidate),
            description="parent password dialog",
        )
        return self._submit_parent_password(password_root)

    def _submit_parent_password(self, root: ET.Element) -> ET.Element:
        if self.parent_password_provider is None:
            raise AutomationError("Khan parent password is required to enter Teacher view")
        texts = text_set(root)
        if "Enter Password" not in texts or "Enter" not in texts:
            raise AutomationError("Parent password dialog did not match the expected layout")
        if "Password" not in texts:
            clear = _unique_visible(root, "X")
            self.device.tap_rect(clear.rect)
        self.device.tap(1280, 322)
        self.device.enter_alphanumeric_secret(self.parent_password_provider())
        # Opening the keyboard moves the dialog. Re-read the live bounds so the
        # submit tap cannot land on the adjacent Forgot Password control.
        submitted_root = self.live_root()
        if "Enter Password" not in text_set(submitted_root):
            raise AutomationError("Parent password dialog changed before submission")
        self.device.tap_rect(_unique_visible(submitted_root, "Enter").rect)
        return self._wait_for_root(
            lambda candidate: (
                "Enter Password" not in text_set(candidate)
                and "Students" in text_set(candidate)
                and all(student in text_set(candidate) for student in self.roster)
            ),
            description="teacher roster after login",
            persist=False,
            timeout=8,
        )

    def _open_class_reports_from_roster(self, root: ET.Element) -> ET.Element:
        texts = text_set(root)
        required = {"Students", "Add Students", *self.roster}
        if not required.issubset(texts):
            raise AutomationError("Roster screen did not match safe Class Reports preconditions")
        # This control is visible but absent from Khan's accessibility hierarchy. The coordinate
        # is allowed only after exact geometry and roster-screen predicates have been validated.
        return self._tap_navigation_control(
            root,
            source_state="teacher_roster",
            target_state="assignments_report",
            control=lambda candidate: self._class_reports_rect(candidate),
            control_name="Class Reports roster card",
        )

    def _class_reports_rect(self, root: ET.Element) -> Rect:
        if not self._is_teacher_roster(root):
            raise AutomationError("Roster screen did not match safe Class Reports preconditions")
        return Rect(1279, 458, 1281, 460)

    def _wait_for_root(
        self,
        predicate: Callable[[ET.Element], bool],
        *,
        description: str,
        timeout: float = 6,
        persist: bool = True,
    ) -> ET.Element:
        deadline = time.monotonic() + timeout
        last_root: ET.Element | None = None
        with self.device.timing.span(f"wait.{description}"):
            while time.monotonic() < deadline:
                last_root = (
                    self.root(f"wait-{description.replace(' ', '-')}")
                    if persist
                    else self.live_root()
                )
                if predicate(last_root):
                    return last_root
                time.sleep(0.1)
        raise AutomationError(f"Timed out waiting for {description}")

    def scan_score_histories(self, *, today: date) -> tuple[ScoreHistory, ...]:
        return self.scan_assignments(today=today, include_score_histories=True).histories

    def scan_assignments(self, *, today: date, include_score_histories: bool) -> AssignmentSnapshot:
        """Capture active rows once, optionally including every available score history."""
        with self.device.timing.span("workflow.scan_assignments"):
            return self._scan_assignments(
                today=today, include_score_histories=include_score_histories
            )

    def _scan_assignments(
        self, *, today: date, include_score_histories: bool
    ) -> AssignmentSnapshot:
        root = self._assignment_report_top()
        active_rows: dict[tuple[str, str, str], AssignmentRow] = {}
        histories: dict[tuple[str, str, str], ScoreHistory] = {}
        boundary = _ScrollBoundary()
        for page in range(80):
            rows = self._assignment_rows(root)
            signature = tuple(row.identity for row in rows)
            if boundary.reached(signature):
                break
            for row in rows:
                active_rows[row.identity] = row
                if (
                    not include_score_histories
                    or row.score_rect is None
                    or row.identity in histories
                ):
                    continue
                cached = self.history_lookup(row) if self.history_lookup else None
                if cached is not None:
                    histories[row.identity] = cached
                    continue
                self.device.tap_rect(row.score_rect)
                modal = self._wait_for_root(
                    lambda candidate: any(
                        item.text == f"{self.student}'s Lesson Scores"
                        for item in visible_nodes(candidate)
                    ),
                    description="score dialog",
                )
                history = parse_score_history(
                    modal,
                    self.student,
                    today=today,
                    assigned_display_date=row.assigned_date,
                )
                if (history.title, history.variant) != (row.title, row.variant):
                    raise AutomationError(
                        "Score dialog does not match the assignment row: "
                        f"{history.title}/{history.variant} vs {row.title}/{row.variant}"
                    )
                histories[row.identity] = history
                self.device.timing.progress(f"Read score history: {row.title} — {row.variant}")
                self._close_score_dialog(modal)
            root = self._next_assignment_page(f"assignments-{page + 1:03d}")
        else:
            raise AutomationError("Assignments report did not reach the bottom within 80 pages")
        activities = [(row.title, row.variant) for row in active_rows.values()]
        if len(activities) != len(set(activities)):
            raise AutomationError("Assignments report contains duplicate active lesson variants")
        return AssignmentSnapshot(tuple(active_rows.values()), tuple(histories.values()))

    def inspect_active_assignment(self, title: str, variant: str) -> dict[str, str]:
        """Open and validate an active assignment, then close it without saving."""
        row = self._find_assignment(title, variant)
        self.device.tap(115, row.rect.center[1])
        root = self._wait_for_assignment_dialog(title, variant)
        return self._inspect_open_assignment(title, variant, "probe-active", root=root)

    def inspect_catalog_assignment(
        self, grade: str, title: str, variant: str, *, reset_to_top: bool = True
    ) -> dict[str, str]:
        """Open an All Progress assignment dialog and close it without saving."""
        root = self._open_all_progress()
        root = self._select_grade(grade, root=root)
        root = self._open_report_variant(title, variant, root=root, reset_to_top=reset_to_top)
        return self._inspect_open_assignment(title, variant, "probe-catalog", root=root)

    def unassign(self, title: str, variant: str) -> ActionResult:
        row = self._find_assignment(title, variant)
        return self._unassign_row(row, title, variant)

    def unassign_many(self, assignments: Iterable[tuple[str, str]]) -> Iterator[ActionResult]:
        """Remove several active assignments in one report traversal."""
        requested = tuple(assignments)
        if len(requested) != len(set(requested)):
            raise ValueError("bulk unassignment contains duplicate lesson variants")
        pending = set(requested)
        if not pending:
            return
        root = self._assignment_report_top()
        boundary = _ScrollBoundary()
        for page in range(80):
            rows = self._assignment_rows(root)
            visible_matches = [
                row for row in rows if (row.title, row.variant) in pending and row.rect.top < 1420
            ]
            if visible_matches:
                row = max(visible_matches, key=lambda candidate: candidate.rect.top)
                pending.remove((row.title, row.variant))
                yield self._unassign_row(row, row.title, row.variant)
                if not pending:
                    return
                boundary = _ScrollBoundary()
                root = self.root(f"bulk-unassign-{page:03d}-saved")
                continue
            signature = tuple(row.identity for row in rows)
            if boundary.reached(signature):
                break
            root = self._next_assignment_page(f"bulk-unassign-{page + 1:03d}")
        if pending:
            raise AutomationError(f"Active assignments not found: {sorted(pending)!r}")

    def _unassign_row(self, row: AssignmentRow, title: str, variant: str) -> ActionResult:
        self.device.tap(115, row.rect.center[1])
        root = self._wait_for_assignment_dialog(title, variant)
        self._change_checkbox(root, desired=CheckboxState.UNCHECKED, prefix="unassign")
        self._save_dialog(expected_report="assignments")
        return ActionResult("unchecked", title, variant, "saved")

    def assign(self, grade: str, title: str, variant: str) -> ActionResult:
        """Assign one lesson; callers must perform final queue verification."""
        return next(self.assign_many(((grade, title, variant),)))

    def set_catalog_assignment(
        self,
        grade: str,
        title: str,
        variant: str,
        *,
        assigned: bool,
        reset_to_top: bool = True,
    ) -> tuple[ActionResult | None, dict[str, str]]:
        """Change only the selected student's exact variant, including idempotent removal."""
        root = self._open_all_progress()
        root = self._select_grade(grade, root=root)
        root = self._open_report_variant(title, variant, root=root, reset_to_top=reset_to_top)
        self._validate_assignment_dialog(root, title, variant)
        before = {
            student: reading.state.value
            for student, reading in read_checkboxes(
                self.device, _dialog_student_labels(root, self.roster)
            ).items()
        }
        desired = CheckboxState.CHECKED.value if assigned else CheckboxState.UNCHECKED.value
        if before[self.student] == desired:
            self._inspect_open_assignment(title, variant, "parent-no-op", root=root)
            return None, before
        result, _ = self._save_catalog_assignment(root, title, variant, assigned=assigned)
        return result, before

    def _save_catalog_assignment(
        self, root: ET.Element, title: str, variant: str, *, assigned: bool
    ) -> tuple[ActionResult, ET.Element]:
        self._validate_assignment_dialog(root, title, variant)
        self._change_checkbox(
            root,
            desired=CheckboxState.CHECKED if assigned else CheckboxState.UNCHECKED,
            prefix="assign" if assigned else "unassign",
        )
        after = self._save_dialog(expected_report="all_progress")
        return ActionResult(
            "checked" if assigned else "unchecked",
            title,
            variant,
            "saved; final verification pending",
        ), after

    def assign_many(self, assignments: Iterable[tuple[str, str, str]]) -> Iterator[ActionResult]:
        """Assign catalog-ordered lessons in one All Progress traversal per grade."""
        requested = tuple(assignments)
        if len(requested) != len(set(requested)):
            raise ValueError("bulk assignment contains duplicate lesson variants")
        if not requested:
            return
        root = self._open_all_progress()
        active_grade: str | None = None
        for grade, title, variant in requested:
            if grade != active_grade:
                root = self._select_grade(grade, root=root)
                root = self._scroll_to_top(root=root)
                active_grade = grade
            root = self._open_report_variant(title, variant, root=root, reset_to_top=False)
            result, root = self._save_catalog_assignment(root, title, variant, assigned=True)
            yield result

    def _find_assignment(self, title: str, variant: str) -> AssignmentRow:
        root = self._assignment_report_top()
        boundary = _ScrollBoundary()
        for page in range(80):
            rows = self._assignment_rows(root)
            matches = [row for row in rows if row.title == title and row.variant == variant]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise AutomationError(f"Duplicate active assignment {title!r}/{variant!r}")
            signature = tuple(row.identity for row in rows)
            if boundary.reached(signature):
                break
            root = self._next_assignment_page(f"find-assignment-{page + 1:03d}")
        raise AutomationError(f"Active assignment not found: {title!r}/{variant!r}")

    def _assignment_report_top(self) -> ET.Element:
        root = self._filter_assignments_to_student(self.ensure_assignments_report())
        return root if self._assignments_at_top else self._scroll_to_top(root=root)

    def _assignment_rows(self, root: ET.Element) -> list[AssignmentRow]:
        return parse_assignment_rows(
            root,
            self.student,
            roster=(self.student,),
            layout=self.layout,
        )

    def _next_assignment_page(self, capture_name: str) -> ET.Element:
        self.device.swipe(
            self.layout.safe_scroll_x,
            1380,
            self.layout.safe_scroll_x,
            680,
            SCROLL_DURATION_MS,
        )
        self._assignments_at_top = False
        return self.root(capture_name)

    def _open_all_progress(self) -> ET.Element:
        root, state = self._wait_for_navigation_state()
        if state == "all_progress_report":
            return root
        root = self.ensure_assignments_report(root=root, state=state)
        after = self._tap_navigation_control(
            root,
            source_state="assignments_report",
            target_state="all_progress_report",
            control=lambda candidate: self._header_rect(candidate, "All Progress"),
            control_name="All Progress tab",
        )
        if "Class Report: All Progress" not in text_set(after):
            raise AutomationError("Navigation did not reach Class Report: All Progress")
        self._assignments_at_top = False
        return after

    def ensure_all_progress_report(self) -> ET.Element:
        """Reach All Progress through guarded navigation without changing report data."""
        return self._open_all_progress()

    def select_grade_subject(self, grade: str, subject: str) -> ET.Element:
        """Apply an All Progress grade/subject filter using labeled modal controls."""
        root = self.ensure_all_progress_report()
        current = _filter_value(root, "Subject:")
        self.device.tap(current.rect.right + 38, current.rect.center[1])
        modal = self._wait_for_root(
            lambda candidate: "Select Grade & Subject" in text_set(candidate),
            description="grade and subject selector",
        )
        grade_options = [
            item
            for item in find_text(modal, grade)
            if 600 < item.rect.left < 1200 and item.rect.top > 450
        ]
        subject_options = [
            item
            for item in find_text(modal, subject)
            if item.rect.left > 1200 and item.rect.top > 450
        ]
        if len(grade_options) != 1 or len(subject_options) != 1:
            raise AutomationError(
                f"Grade/subject modal did not uniquely expose {grade!r}/{subject!r}"
            )
        self.device.tap_rect(grade_options[0].rect)
        self.device.tap_rect(subject_options[0].rect)
        modal = self.live_root()
        self.device.tap_rect(_unique_visible(modal, "Done").rect)
        grade_code = {
            "Preschool (Age 2)": "Pre-K.Age2",
            "Preschool (Age 3)": "Pre-K.Age3",
            "Preschool (Age 4)": "Pre-K.Age4",
            "Kindergarten": "K",
            "1st Grade": "Grade1",
            "2nd Grade": "Grade2",
        }[grade]
        subject_code = "ELA" if subject == "English Language Arts" else subject
        if subject == "Books":
            expected = "All Ages : Books"
        elif subject == "Videos" and grade in {
            "Preschool (Age 2)",
            "Preschool (Age 3)",
            "Preschool (Age 4)",
            "Kindergarten",
        }:
            expected = "K & Pre-K : Videos"
        else:
            expected = f"{grade_code} : {subject_code}"
        return self._wait_for_root(
            lambda candidate: (
                "Class Report: All Progress" in text_set(candidate)
                and _filter_value(candidate, "Subject:").text == expected
            ),
            description="grade and subject applied",
            timeout=12,
        )

    def _filter_assignments_to_student(self, root: ET.Element | None = None) -> ET.Element:
        root = root if root is not None else self.root("before-student-filter")
        if self._is_filtered_to_student(root):
            return root
        current = _filter_value(root, "Students:")
        self.device.tap(current.rect.right + 38, current.rect.center[1])
        modal = self._wait_for_root(
            lambda candidate: "Select Students" in text_set(candidate),
            description="student filter dialog",
        )
        if "Select Students" not in text_set(modal):
            raise AutomationError("Student filter dialog did not open")
        labels = _dialog_student_labels(modal, self.roster)
        readings = read_checkboxes(self.device, labels)
        for student, reading in readings.items():
            desired = CheckboxState.CHECKED if student == self.student else CheckboxState.UNCHECKED
            if reading.state is not desired:
                self.device.tap(*reading.center)
        after = self._wait_for_checkbox_states(labels)
        invalid = {
            student: reading.state.value
            for student, reading in after.items()
            if reading.state
            is not (CheckboxState.CHECKED if student == self.student else CheckboxState.UNCHECKED)
        }
        if invalid:
            raise AutomationError(f"Student filter validation failed: {invalid!r}")
        current_modal = self.live_root()
        self.device.tap_rect(_unique_visible(current_modal, "Done").rect)
        filtered = self._wait_for_root(
            self._is_filtered_to_student,
            description="student filter applied",
        )
        if not self._is_filtered_to_student(filtered):
            raise AutomationError(f"Assignments report was not filtered to {self.student!r}")
        self._assignments_at_top = True
        return filtered

    def _is_filtered_to_student(self, root: ET.Element) -> bool:
        if not is_assignment_report(root):
            return False
        texts = text_set(root)
        return self.student in texts and all(
            other == self.student or other not in texts for other in self.roster
        )

    def _wait_for_checkbox_states(
        self, labels: dict[str, Rect], *, timeout: float = 4
    ) -> dict[str, CheckboxReading]:
        deadline = time.monotonic() + timeout
        readings: dict[str, CheckboxReading] = {}
        while time.monotonic() < deadline:
            readings = read_checkboxes(self.device, labels)
            if all(
                reading.state
                is (CheckboxState.CHECKED if student == self.student else CheckboxState.UNCHECKED)
                for student, reading in readings.items()
            ):
                return readings
            time.sleep(0.1)
        return readings

    def _select_grade(self, grade: str, *, root: ET.Element | None = None) -> ET.Element:
        root = root if root is not None else self.root("before-grade")
        expected = _report_grade_label(grade)
        subject = _filter_value(root, "Subject:")
        if subject.text == expected:
            return root
        self.device.tap(subject.rect.right + 38, subject.rect.center[1])
        modal = self._wait_for_root(
            lambda candidate: grade in text_set(candidate) and "Done" in text_set(candidate),
            description="grade selector",
        )
        grade_node = _unique_visible(modal, grade)
        self.device.tap_rect(grade_node.rect)
        self.device.tap_rect(_unique_visible(modal, "Done").rect)
        after = self._wait_for_root(
            lambda candidate: (
                "Class Report: All Progress" in text_set(candidate)
                and _filter_value(candidate, "Subject:").text == expected
            ),
            description="grade applied",
        )
        if _filter_value(after, "Subject:").text != expected:
            raise AutomationError(f"Grade selection did not produce {expected!r}")
        return after

    def _open_report_variant(
        self,
        title: str,
        variant: str,
        *,
        root: ET.Element | None = None,
        reset_to_top: bool = True,
    ) -> ET.Element:
        if reset_to_top:
            root = self._scroll_to_top()
        elif root is None:
            root = self.root("before-find-lesson")
        boundary = _ScrollBoundary()
        for page in range(200):
            assert root is not None
            nodes = visible_nodes(root)
            matches = [item for item in nodes if item.text == title and near(item.rect.left, 200)]
            if len(matches) > 1:
                raise AutomationError(f"Multiple visible All Progress rows named {title!r}")
            if matches:
                lesson = matches[0]
                if lesson.rect.top > 1250:
                    self.device.swipe(1200, 1380, 1200, 900, SCROLL_DURATION_MS)
                    root = self.root(f"find-lesson-{page:03d}-repositioned")
                    continue
                variants = _variants_below(nodes, lesson)
                if variant not in {item.text for item in variants}:
                    root = self._tap_until_root_target(
                        root,
                        source=lambda candidate: self._collapsed_lesson_visible(
                            candidate, title, variant
                        ),
                        target=lambda candidate: self._variant_visible(candidate, title, variant),
                        control=lambda candidate: self._visible_lesson_rect(candidate, title),
                        source_name=f"collapsed All Progress row {title!r}",
                        target_name=f"lesson variant {title!r}/{variant!r}",
                        control_name=f"expand lesson {title!r}",
                        timeout=GUARDED_TRANSITION_TIMEOUT_SECONDS,
                    )
                    nodes = visible_nodes(root)
                    lesson = next(
                        item for item in nodes if item.text == title and near(item.rect.left, 200)
                    )
                    variants = _variants_below(nodes, lesson)
                target = [item for item in variants if item.text == variant]
                if len(target) == 1:
                    self.device.tap_rect(target[0].rect)
                    return self._wait_for_assignment_dialog(title, variant)
                if variants:
                    raise AutomationError(
                        f"Variant {variant!r} is unavailable for visible lesson {title!r}"
                    )
            signature = tuple(
                (item.text, item.rect)
                for item in nodes
                if item.rect.left <= 564 and item.rect.top >= 385
            )
            if boundary.reached(signature):
                break
            self.device.swipe(1200, 1380, 1200, 680, SCROLL_DURATION_MS)
            root = self.root(f"find-lesson-{page + 1:03d}")
        raise AutomationError(f"All Progress lesson not found: {title!r}")

    def _visible_lesson_rect(self, root: ET.Element, title: str) -> Rect:
        matches = [
            item for item in visible_nodes(root) if item.text == title and near(item.rect.left, 200)
        ]
        if len(matches) != 1:
            raise AutomationError(f"Expected one visible All Progress row named {title!r}")
        return matches[0].rect

    def _variant_visible(self, root: ET.Element, title: str, variant: str) -> bool:
        if "Class Report: All Progress" not in text_set(root):
            return False
        nodes = visible_nodes(root)
        lessons = [item for item in nodes if item.text == title and near(item.rect.left, 200)]
        if len(lessons) != 1:
            raise AutomationError(f"Expected one visible All Progress row named {title!r}")
        return variant in {item.text for item in _variants_below(nodes, lessons[0])}

    def _collapsed_lesson_visible(self, root: ET.Element, title: str, variant: str) -> bool:
        try:
            return not self._variant_visible(root, title, variant)
        except AutomationError:
            return False

    def _validate_assignment_dialog(
        self, root: ET.Element, expected_title: str, expected_variant: str
    ) -> None:
        titles, variants = self._assignment_dialog_identity(root)
        if titles != [expected_title] or variants != [expected_variant]:
            raise AutomationError(
                "Assignment dialog mismatch: "
                f"expected {expected_title!r}/{expected_variant!r}, got {titles!r}/{variants!r}"
            )
        _dialog_student_labels(root, self.roster)
        save = [item for item in find_text(root, "Save") if item.rect.top < 350]
        if len(save) != 1:
            raise AutomationError(f"Expected one assignment Save button, found {len(save)}")

    @staticmethod
    def _assignment_dialog_identity(root: ET.Element) -> tuple[list[str], list[str]]:
        """Read only the modal header and preview, excluding the report behind it."""
        nodes = visible_nodes(root)
        titles = [
            item.text.removeprefix("Assign\n") for item in nodes if item.text.startswith("Assign\n")
        ]
        variants = [
            item.text
            for item in nodes
            if item.text in LEARNING_SEQUENCE and item.rect.left < 600 and item.rect.top < 400
        ]
        return titles, variants

    def _assignment_dialog_controls_complete(self, root: ET.Element) -> bool:
        """Distinguish a still-rendering dialog from duplicated unsafe controls."""
        complete = True
        for student in self.roster:
            count = len([item for item in find_text(root, student) if item.rect.top > 400])
            if count > 1:
                raise AutomationError(f"Expected one dialog label for {student!r}, found {count}")
            complete = complete and count == 1
        save_count = len([item for item in find_text(root, "Save") if item.rect.top < 350])
        if save_count > 1:
            raise AutomationError(f"Expected one assignment Save button, found {save_count}")
        return complete and save_count == 1

    def discard_open_assignment_dialog(self, expected_title: str) -> bool:
        """Dismiss one exact unsaved dialog so read-only recovery can inspect the queue."""
        root = self.live_root()
        titles, _variants = self._assignment_dialog_identity(root)
        if not titles:
            return False
        if titles != [expected_title]:
            raise AutomationError(
                "Refusing to dismiss an unexpected assignment dialog: "
                f"expected {expected_title!r}, got {titles!r}"
            )
        self.device.command("shell", "input", "keyevent", "KEYCODE_BACK")
        self._wait_for_stable_root(
            lambda candidate: (
                not self._assignment_dialog_identity(candidate)[0]
                and "Class Report: All Progress" in text_set(candidate)
            ),
            description="unsaved assignment discarded",
            timeout=GUARDED_TRANSITION_TIMEOUT_SECONDS,
            persist=True,
        )
        return True

    def _change_checkbox(self, root: ET.Element, *, desired: CheckboxState, prefix: str) -> None:
        labels = _dialog_student_labels(root, self.roster)
        before = read_checkboxes(self.device, labels)
        target = before[self.student]
        expected_before = (
            CheckboxState.UNCHECKED if desired is CheckboxState.CHECKED else CheckboxState.CHECKED
        )
        if target.state is not expected_before:
            raise AutomationError(
                f"Refusing checkbox change: {self.student} is {target.state.value}, "
                f"expected {expected_before.value}"
            )
        self.device.tap(*target.center)
        deadline = time.monotonic() + 4
        while True:
            after = read_checkboxes(self.device, labels)
            if after[self.student].state is desired or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if after[self.student].state is not desired:
            raise AutomationError(f"Checkbox for {self.student} did not become {desired.value}")
        changed_others = [
            student
            for student in self.roster
            if student != self.student and before[student].state is not after[student].state
        ]
        if changed_others:
            raise AutomationError(f"Non-target checkbox changed: {changed_others!r}")

    def _inspect_open_assignment(
        self,
        title: str,
        variant: str,
        prefix: str,
        *,
        root: ET.Element | None = None,
    ) -> dict[str, str]:
        root = root or self.root(f"{prefix}-dialog")
        self._validate_assignment_dialog(root, title, variant)
        labels = _dialog_student_labels(root, self.roster)
        states = {
            student: reading.state.value
            for student, reading in read_checkboxes(self.device, labels).items()
        }
        screen = _screen_rect(root)
        self.device.tap(int(screen.right * 0.883), int(screen.bottom * 0.065))
        after = self._wait_for_root(
            lambda candidate: (
                not any(item.text.startswith("Assign\n") for item in visible_nodes(candidate))
            ),
            description="assignment dialog closed",
        )
        if any(item.text.startswith("Assign\n") for item in visible_nodes(after)):
            raise AutomationError("Assignment dialog remained open after read-only probe")
        return states

    def _save_dialog(self, *, expected_report: str) -> ET.Element:
        root = self.root("before-save")
        save = [item for item in find_text(root, "Save") if item.rect.top < 350]
        if len(save) != 1:
            raise AutomationError(f"Expected one assignment Save button, found {len(save)}")
        self.device.tap_rect(save[0].rect)

        def returned_to_report(candidate: ET.Element) -> bool:
            if any(item.text.startswith("Assign\n") for item in visible_nodes(candidate)):
                return False
            if expected_report == "assignments":
                return is_assignment_report(candidate)
            if expected_report == "all_progress":
                return "Class Report: All Progress" in text_set(candidate)
            raise ValueError(f"unknown report type: {expected_report}")

        after = self._wait_for_stable_root(
            returned_to_report,
            description="assignment saved",
            timeout=GUARDED_TRANSITION_TIMEOUT_SECONDS,
            persist=True,
        )
        if not returned_to_report(after):
            raise AutomationError("Assignment dialog did not return to the expected report")
        return after

    def _close_score_dialog(self, root: ET.Element) -> None:
        title = f"{self.student}'s Lesson Scores"
        self._tap_until_root_target(
            root,
            source=lambda candidate: title in text_set(candidate),
            target=lambda candidate: (
                is_assignment_report(candidate) and title not in text_set(candidate)
            ),
            control=lambda candidate: Rect(
                int(_screen_rect(candidate).right * 0.66),
                int(_screen_rect(candidate).bottom * 0.30),
                int(_screen_rect(candidate).right * 0.66) + 1,
                int(_screen_rect(candidate).bottom * 0.30) + 1,
            ),
            source_name="score_dialog",
            target_name="assignments_report",
            control_name="score dialog close",
            timeout=6,
        )

    def _scroll_to_top(self, *, root: ET.Element | None = None) -> ET.Element:
        root = self.device.scroll_to_top(self.layout.safe_scroll_x, root=root)
        self._validate_screen(root)
        self._assignments_at_top = True
        return root

    def _wait_for_assignment_dialog(self, expected_title: str, expected_variant: str) -> ET.Element:
        """Wait for two complete renders; React Native exposes the header first."""

        def complete(candidate: ET.Element) -> bool:
            titles, variants = self._assignment_dialog_identity(candidate)
            if not titles:
                return False
            if titles != [expected_title] or (variants and variants != [expected_variant]):
                raise AutomationError(
                    "Assignment dialog mismatch: "
                    f"expected {expected_title!r}/{expected_variant!r}, "
                    f"got {titles!r}/{variants!r}"
                )
            if variants != [expected_variant]:
                return False
            if not self._assignment_dialog_controls_complete(candidate):
                return False
            self._validate_assignment_dialog(candidate, expected_title, expected_variant)
            return True

        return self._wait_for_stable_root(
            complete,
            description=f"complete assignment dialog for {expected_title} — {expected_variant}",
            timeout=GUARDED_TRANSITION_TIMEOUT_SECONDS,
            persist=True,
        )

    @staticmethod
    def _header_rect(root: ET.Element, label: str) -> Rect:
        candidates = [item for item in find_text(root, label) if item.rect.top < 200]
        if len(candidates) != 1:
            raise AutomationError(f"Expected one top {label!r} tab, found {len(candidates)}")
        return candidates[0].rect


def _unique_visible(root: ET.Element, text: str) -> UiText:
    matches = [item for item in find_text(root, text) if item.rect.top < 1600]
    if len(matches) != 1:
        raise AutomationError(f"Expected one visible {text!r} node, found {len(matches)}")
    return matches[0]


def _guarded_unlabeled_control(root: ET.Element, bounds: Rect, description: str) -> Rect:
    """Resolve one image-backed control only after its screen has been identified."""
    matches = [
        node
        for node in root.iter()
        if node.attrib.get("class") == "android.view.ViewGroup" and node_rect(node) == bounds
    ]
    if len(matches) != 1:
        raise AutomationError(
            f"Expected one {description} control at {bounds}, found {len(matches)}"
        )
    return bounds


def _dialog_student_labels(root: ET.Element, roster: Iterable[str]) -> dict[str, Rect]:
    """Return student labels inside a modal, excluding report headers behind it."""
    labels: dict[str, Rect] = {}
    for student in roster:
        matches = [item for item in find_text(root, student) if item.rect.top > 400]
        if len(matches) != 1:
            raise AutomationError(
                f"Expected one dialog label for {student!r}, found {len(matches)}"
            )
        labels[student] = matches[0].rect
    return labels


def _filter_value(root: ET.Element, label_text: str) -> UiText:
    label = _unique_visible(root, label_text)
    matches = [
        item
        for item in visible_nodes(root)
        if item.rect.left > label.rect.right
        and item.rect.right < 800
        and abs(item.rect.center[1] - label.rect.center[1]) < 30
    ]
    if len(matches) != 1:
        raise AutomationError(
            f"Expected one value beside {label_text!r}, found {[item.text for item in matches]!r}"
        )
    return matches[0]


def _screen_rect(root: ET.Element) -> Rect:
    candidates = [
        rect
        for node in root.iter("node")
        if (rect := node_rect(node)) is not None and rect.left == 0 and rect.top == 0
    ]
    if candidates:
        return max(candidates, key=lambda rect: (rect.width * rect.height, rect.width, rect.height))
    raise AutomationError("UI hierarchy has no screen-sized root node")


def _variants_below(nodes: Iterable[UiText], lesson: UiText) -> list[UiText]:
    variants: list[UiText] = []
    for item in sorted(nodes, key=lambda candidate: candidate.rect.top):
        if item.rect.top <= lesson.rect.bottom:
            continue
        if item.rect.left <= 202:
            break
        if near(item.rect.left, 239) and item.text in {
            "Basic",
            "Main",
            "Practice 1",
            "Practice 2",
        }:
            variants.append(item)
    return variants


def _report_grade_label(grade: str) -> str:
    return REPORT_GRADE_LABELS[grade]
