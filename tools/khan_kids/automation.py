"""State-checked navigation for score review and mastery assignment changes."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .adb import AndroidDevice, AutomationError
from .reports import (
    DEFAULT_REPORT_LAYOUT,
    AssignmentRow,
    ReportLayout,
    ScoreHistory,
    is_assignment_report,
    parse_assignment_rows,
    parse_score_history,
)
from .ui import Rect, UiText, find_text, near, node_rect, text_set, visible_nodes
from .vision import CheckboxState, read_checkbox


@dataclass(frozen=True, slots=True)
class ActionResult:
    action: str
    title: str
    variant: str
    result: str


class KhanKidsAutomation:
    def __init__(
        self,
        device: AndroidDevice,
        *,
        student: str,
        roster: tuple[str, ...],
        scratch: Path,
        layout: ReportLayout = DEFAULT_REPORT_LAYOUT,
    ) -> None:
        if student not in roster:
            raise ValueError(f"Student {student!r} is not in roster {roster!r}")
        self.device = device
        self.student = student
        self.roster = roster
        self.scratch = scratch
        self.layout = layout
        self.scratch.mkdir(parents=True, exist_ok=True)

    def root(self, name: str = "window") -> ET.Element:
        return self.device.dump(self.scratch / f"{name}.xml")

    def ensure_assignments_report(self) -> ET.Element:
        root = self.root("before-assignments")
        if is_assignment_report(root):
            return root
        texts = text_set(root)
        if "Class Report: All Progress" in texts:
            self._tap_header(root, "Assignments")
        elif "Class Reports" in texts:
            self.device.tap_rect(_unique_visible(root, "Class Reports").rect, settle=4)
        elif "Students" in texts and all(student in texts for student in self.roster):
            screen = _screen_rect(root)
            self.device.tap(int(screen.right * 0.5), int(screen.bottom * 0.295), settle=4)
        else:
            raise AutomationError(
                "Open the logged-in Teacher view or Class Reports before running automation"
            )
        root = self.root("assignments-report")
        if not is_assignment_report(root):
            raise AutomationError("Navigation did not reach Class Report: Assignments")
        return root

    def scan_score_histories(self, *, today: date) -> tuple[ScoreHistory, ...]:
        self.ensure_assignments_report()
        self._filter_assignments_to_student()
        self._scroll_to_top()
        histories: dict[tuple[str, str, str], ScoreHistory] = {}
        prior_signature: tuple[tuple[str, str, str], ...] | None = None
        for page in range(80):
            root = self.root(f"assignments-{page:03d}")
            rows = parse_assignment_rows(
                root, self.student, roster=(self.student,), layout=self.layout
            )
            signature = tuple(row.identity for row in rows)
            if signature == prior_signature:
                break
            for row in rows:
                if row.score_rect is None or row.identity in histories:
                    continue
                self.device.tap_rect(row.score_rect, settle=1)
                modal = self.root(f"history-{page:03d}-{len(histories):03d}")
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
                self._close_score_dialog(modal)
            prior_signature = signature
            self.device.swipe(
                self.layout.safe_scroll_x,
                1380,
                self.layout.safe_scroll_x,
                680,
                settle=1,
            )
        else:
            raise AutomationError("Assignments report did not reach the bottom within 80 pages")
        return tuple(histories.values())

    def inspect_active_assignment(self, title: str, variant: str) -> dict[str, str]:
        """Open and validate an active assignment, then close it without saving."""
        row = self._find_assignment(title, variant)
        self.device.tap(115, row.rect.center[1], settle=1)
        return self._inspect_open_assignment(title, variant, "probe-active")

    def inspect_catalog_assignment(self, grade: str, title: str, variant: str) -> dict[str, str]:
        """Open an All Progress assignment dialog and close it without saving."""
        self._open_all_progress()
        self._select_grade(grade)
        self._open_report_variant(title, variant)
        return self._inspect_open_assignment(title, variant, "probe-catalog")

    def unassign(self, title: str, variant: str) -> ActionResult:
        row = self._find_assignment(title, variant)
        self.device.tap(115, row.rect.center[1], settle=1)
        root = self.root("unassign-dialog")
        self._validate_assignment_dialog(root, title, variant)
        self._change_checkbox(root, desired=CheckboxState.UNCHECKED, prefix="unassign")
        self._save_dialog()
        return ActionResult("unchecked", title, variant, "saved")

    def assign(self, grade: str, title: str, variant: str) -> ActionResult:
        self._open_all_progress()
        self._select_grade(grade)
        self._open_report_variant(title, variant)
        root = self.root("assign-dialog")
        self._validate_assignment_dialog(root, title, variant)
        self._change_checkbox(root, desired=CheckboxState.CHECKED, prefix="assign")
        self._save_dialog()
        verified = self._find_assignment(title, variant)
        if verified.title != title or verified.variant != variant:
            raise AutomationError(f"Post-save verification failed for {title!r}/{variant!r}")
        return ActionResult("checked", title, variant, "saved and verified in Assignments")

    def _find_assignment(self, title: str, variant: str) -> AssignmentRow:
        self.ensure_assignments_report()
        self._filter_assignments_to_student()
        self._scroll_to_top()
        prior_signature: tuple[tuple[str, str, str], ...] | None = None
        for page in range(80):
            rows = parse_assignment_rows(
                self.root(f"find-assignment-{page:03d}"),
                self.student,
                roster=(self.student,),
                layout=self.layout,
            )
            matches = [row for row in rows if row.title == title and row.variant == variant]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise AutomationError(f"Duplicate active assignment {title!r}/{variant!r}")
            signature = tuple(row.identity for row in rows)
            if signature == prior_signature:
                break
            prior_signature = signature
            self.device.swipe(
                self.layout.safe_scroll_x, 1380, self.layout.safe_scroll_x, 680, settle=1
            )
        raise AutomationError(f"Active assignment not found: {title!r}/{variant!r}")

    def _open_all_progress(self) -> None:
        root = self.ensure_assignments_report()
        self._tap_header(root, "All Progress")
        after = self.root("all-progress")
        if "Class Report: All Progress" not in text_set(after):
            raise AutomationError("Navigation did not reach Class Report: All Progress")

    def _filter_assignments_to_student(self) -> None:
        root = self.root("before-student-filter")
        current = _filter_value(root, "Students:")
        self.device.tap(current.rect.right + 38, current.rect.center[1], settle=1)
        modal = self.root("student-filter")
        if "Select Students" not in text_set(modal):
            raise AutomationError("Student filter dialog did not open")
        labels = _dialog_student_labels(modal, self.roster)
        before_path = self.scratch / "student-filter-before.png"
        self.device.screenshot(before_path)
        readings = {student: read_checkbox(before_path, label) for student, label in labels.items()}
        for student, reading in readings.items():
            desired = CheckboxState.CHECKED if student == self.student else CheckboxState.UNCHECKED
            if reading.state is not desired:
                self.device.tap(*reading.center, settle=0.5)
        after_path = self.scratch / "student-filter-after.png"
        self.device.screenshot(after_path)
        after = {student: read_checkbox(after_path, label) for student, label in labels.items()}
        invalid = {
            student: reading.state.value
            for student, reading in after.items()
            if reading.state
            is not (CheckboxState.CHECKED if student == self.student else CheckboxState.UNCHECKED)
        }
        if invalid:
            raise AutomationError(f"Student filter validation failed: {invalid!r}")
        self.device.tap_rect(_unique_visible(modal, "Done").rect, settle=4)
        filtered = self.root("filtered-assignments")
        if _filter_value(filtered, "Students:").text == "All":
            raise AutomationError(f"Assignments report was not filtered to {self.student!r}")

    def _select_grade(self, grade: str) -> None:
        root = self.root("before-grade")
        expected = _report_grade_label(grade)
        subject = _filter_value(root, "Subject:")
        if subject.text == expected:
            return
        self.device.tap(subject.rect.right + 38, subject.rect.center[1], settle=1)
        modal = self.root("grade-selector")
        grade_node = _unique_visible(modal, grade)
        self.device.tap_rect(grade_node.rect)
        self.device.tap_rect(_unique_visible(modal, "Done").rect, settle=4)
        after = self.root("selected-grade")
        if _filter_value(after, "Subject:").text != expected:
            raise AutomationError(f"Grade selection did not produce {expected!r}")

    def _open_report_variant(self, title: str, variant: str) -> None:
        self._scroll_to_top()
        prior_signature: tuple[tuple[str, Rect], ...] | None = None
        for page in range(200):
            root = self.root(f"find-lesson-{page:03d}")
            nodes = visible_nodes(root)
            matches = [item for item in nodes if item.text == title and near(item.rect.left, 200)]
            if len(matches) > 1:
                raise AutomationError(f"Multiple visible All Progress rows named {title!r}")
            if matches:
                lesson = matches[0]
                if lesson.rect.top > 1250:
                    self.device.swipe(1200, 1380, 1200, 900, settle=1)
                    continue
                variants = _variants_below(nodes, lesson)
                if variant not in {item.text for item in variants}:
                    self.device.tap_rect(lesson.rect, settle=1)
                    root = self.root(f"expanded-{page:03d}")
                    nodes = visible_nodes(root)
                    lesson = next(
                        item for item in nodes if item.text == title and near(item.rect.left, 200)
                    )
                    variants = _variants_below(nodes, lesson)
                target = [item for item in variants if item.text == variant]
                if len(target) == 1:
                    self.device.tap_rect(target[0].rect, settle=1)
                    return
                if variants:
                    raise AutomationError(
                        f"Variant {variant!r} is unavailable for visible lesson {title!r}"
                    )
            signature = tuple(
                (item.text, item.rect)
                for item in nodes
                if item.rect.left <= 564 and item.rect.top >= 385
            )
            if signature == prior_signature:
                break
            prior_signature = signature
            self.device.swipe(1200, 1380, 1200, 680, settle=1)
        raise AutomationError(f"All Progress lesson not found: {title!r}")

    def _validate_assignment_dialog(
        self, root: ET.Element, expected_title: str, expected_variant: str
    ) -> None:
        assign_title = next(
            (
                item.text.removeprefix("Assign\n")
                for item in visible_nodes(root)
                if item.text.startswith("Assign\n")
            ),
            None,
        )
        variants = [
            item.text
            for item in visible_nodes(root)
            if item.text in {"Basic", "Main", "Practice 1", "Practice 2"}
            and item.rect.left < 600
            and item.rect.top < 400
        ]
        if assign_title != expected_title or variants != [expected_variant]:
            raise AutomationError(
                "Assignment dialog mismatch: "
                f"expected {expected_title!r}/{expected_variant!r}, got {assign_title!r}/{variants!r}"
            )
        _dialog_student_labels(root, self.roster)

    def _change_checkbox(self, root: ET.Element, *, desired: CheckboxState, prefix: str) -> None:
        labels = _dialog_student_labels(root, self.roster)
        before_path = self.scratch / f"{prefix}-before.png"
        self.device.screenshot(before_path)
        before = {student: read_checkbox(before_path, label) for student, label in labels.items()}
        target = before[self.student]
        expected_before = (
            CheckboxState.UNCHECKED if desired is CheckboxState.CHECKED else CheckboxState.CHECKED
        )
        if target.state is not expected_before:
            raise AutomationError(
                f"Refusing checkbox change: {self.student} is {target.state.value}, "
                f"expected {expected_before.value}"
            )
        self.device.tap(*target.center, settle=1)
        after_path = self.scratch / f"{prefix}-after.png"
        self.device.screenshot(after_path)
        after = {student: read_checkbox(after_path, label) for student, label in labels.items()}
        if after[self.student].state is not desired:
            raise AutomationError(f"Checkbox for {self.student} did not become {desired.value}")
        changed_others = [
            student
            for student in self.roster
            if student != self.student and before[student].state is not after[student].state
        ]
        if changed_others:
            raise AutomationError(f"Non-target checkbox changed: {changed_others!r}")

    def _inspect_open_assignment(self, title: str, variant: str, prefix: str) -> dict[str, str]:
        root = self.root(f"{prefix}-dialog")
        self._validate_assignment_dialog(root, title, variant)
        labels = _dialog_student_labels(root, self.roster)
        screenshot = self.scratch / f"{prefix}.png"
        self.device.screenshot(screenshot)
        states = {
            student: read_checkbox(screenshot, label).state.value
            for student, label in labels.items()
        }
        screen = _screen_rect(root)
        self.device.tap(int(screen.right * 0.883), int(screen.bottom * 0.065), settle=2)
        after = self.root(f"{prefix}-closed")
        if any(item.text.startswith("Assign\n") for item in visible_nodes(after)):
            raise AutomationError("Assignment dialog remained open after read-only probe")
        return states

    def _save_dialog(self) -> None:
        root = self.root("before-save")
        save = [item for item in find_text(root, "Save") if item.rect.top < 350]
        if len(save) != 1:
            raise AutomationError(f"Expected one assignment Save button, found {len(save)}")
        self.device.tap_rect(save[0].rect, settle=4)
        if any(item.text.startswith("Assign\n") for item in visible_nodes(self.root("after-save"))):
            raise AutomationError("Assignment dialog remained open after Save")

    def _close_score_dialog(self, root: ET.Element) -> None:
        screen = _screen_rect(root)
        self.device.tap(int(screen.right * 0.66), int(screen.bottom * 0.30), settle=1)

    def _scroll_to_top(self) -> None:
        self.device.scroll_to_top(self.layout.safe_scroll_x)

    def _tap_header(self, root: ET.Element, label: str) -> None:
        candidates = [item for item in find_text(root, label) if item.rect.top < 200]
        if len(candidates) != 1:
            raise AutomationError(f"Expected one top {label!r} tab, found {len(candidates)}")
        self.device.tap_rect(candidates[0].rect, settle=4)


def _unique_visible(root: ET.Element, text: str) -> UiText:
    matches = [item for item in find_text(root, text) if item.rect.top < 1600]
    if len(matches) != 1:
        raise AutomationError(f"Expected one visible {text!r} node, found {len(matches)}")
    return matches[0]


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
        and item.rect.right < 620
        and abs(item.rect.center[1] - label.rect.center[1]) < 30
    ]
    if len(matches) != 1:
        raise AutomationError(
            f"Expected one value beside {label_text!r}, found {[item.text for item in matches]!r}"
        )
    return matches[0]


def _screen_rect(root: ET.Element) -> Rect:
    for node in root.iter("node"):
        rect = node_rect(node)
        if rect and rect.left == 0 and rect.top == 0:
            return rect
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
    return {
        "Preschool (Age 2)": "Pre-K.Age2 : ELA",
        "Preschool (Age 3)": "Pre-K.Age3 : ELA",
        "Preschool (Age 4)": "Pre-K.Age4 : ELA",
        "Kindergarten": "K : ELA",
        "1st Grade": "Grade1 : ELA",
        "2nd Grade": "Grade2 : ELA",
    }[grade]
