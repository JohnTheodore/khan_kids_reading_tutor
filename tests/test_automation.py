from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AutomationError
from khan_kids.automation import (
    REPORT_BACK_RECT,
    SWITCH_USER_RECT,
    ActionResult,
    KhanKidsAutomation,
)
from khan_kids.reports import AssignmentRow
from khan_kids.ui import Rect


class AutomationTests(unittest.TestCase):
    def test_roster_only_students_screen_is_not_tapped_by_coordinate(self) -> None:
        device, automation = _automation()
        root = ET.Element("hierarchy")
        parent = ET.SubElement(root, "node", bounds="[0,0][2560,1600]", text="")
        for text in ("Students", "Student A", "Student B"):
            ET.SubElement(parent, "node", bounds="[100,100][300,160]", text=text)
        device.hierarchy.return_value = root

        with self.assertRaisesRegex(AutomationError, "safe Class Reports preconditions"):
            automation.ensure_assignments_report()

        device.tap.assert_not_called()

    def test_exact_roster_screen_opens_class_reports_at_guarded_coordinate(self) -> None:
        device, automation = _automation()
        root = ET.Element("hierarchy")
        parent = ET.SubElement(root, "node", bounds="[0,0][2560,1600]", text="")
        for text in ("Students", "Add Students", "Student A", "Student B"):
            ET.SubElement(parent, "node", bounds="[100,100][300,160]", text=text)
        device.hierarchy.return_value = root
        report = ET.Element("hierarchy")
        report_parent = ET.SubElement(
            report, "node", bounds="[0,0][2560,1600]", text="Class Report: Assignments"
        )
        for text in ("Assignments", "All Progress", "Students:", "All"):
            ET.SubElement(report_parent, "node", bounds="[100,100][300,160]", text=text)
        device.dump.return_value = report

        automation.ensure_assignments_report()

        device.tap.assert_called_once_with(1280, 459)

    def test_navigation_waits_for_two_stable_profile_chooser_reads(self) -> None:
        device, automation = _automation()
        blank = _screen_with_text()
        incomplete = _screen_with_text(("dad", Rect(100, 100, 300, 160)))
        chooser = _chooser_screen()
        device.hierarchy.side_effect = (blank, incomplete, chooser, chooser)

        with patch("khan_kids.automation.time.sleep"):
            root, state = automation._wait_for_navigation_state()

        self.assertIs(root, chooser)
        self.assertEqual(state, "profile_chooser")
        self.assertEqual(device.hierarchy.call_count, 4)

    def test_stable_assignments_report_returns_after_one_read(self) -> None:
        device, automation = _automation()
        report = _screen_with_text(
            ("Class Report: Assignments", Rect(100, 100, 600, 160)),
            ("Assignments", Rect(100, 200, 300, 260)),
            ("All Progress", Rect(400, 200, 600, 260)),
            ("Students:", Rect(700, 200, 900, 260)),
            ("All", Rect(1000, 200, 1100, 260)),
        )
        device.hierarchy.return_value = report

        root, state = automation._wait_for_navigation_state()

        self.assertIs(root, report)
        self.assertEqual(state, "assignments_report")
        device.hierarchy.assert_called_once_with()

    def test_navigation_state_change_resets_stability(self) -> None:
        device, automation = _automation()
        chooser = _chooser_screen()
        password = _screen_with_text(
            ("Enter Password", Rect(900, 100, 1600, 200)),
            ("Enter", Rect(1100, 440, 1400, 510)),
        )
        device.hierarchy.side_effect = (chooser, password, password)

        with patch("khan_kids.automation.time.sleep"):
            _root, state = automation._wait_for_navigation_state()

        self.assertEqual(state, "password_dialog")
        self.assertEqual(device.hierarchy.call_count, 3)

    def test_unknown_navigation_state_times_out_without_tapping(self) -> None:
        device, automation = _automation()
        device.hierarchy.return_value = _screen_with_text(
            ("Unknown screen", Rect(100, 100, 300, 160))
        )

        with (
            patch("khan_kids.automation.time.monotonic", side_effect=(0, 0, 11)),
            patch("khan_kids.automation.time.sleep"),
            self.assertRaisesRegex(AutomationError, "stable Khan navigation state"),
        ):
            automation._wait_for_navigation_state()

        device.tap.assert_not_called()
        device.tap_rect.assert_not_called()

    def test_portrait_ui_is_rejected_before_navigation(self) -> None:
        device = _device()
        hierarchy = ET.Element("hierarchy")
        ET.SubElement(hierarchy, "node", bounds="[0,0][1600,2560]", text="")
        device.dump.return_value = hierarchy
        automation = KhanKidsAutomation(
            device,
            student="Student A",
            roster=("Student A", "Student B"),
            scratch=Path("/tmp/not-used"),
        )

        with self.assertRaisesRegex(AutomationError, "expected landscape"):
            automation.root()

    def test_password_submit_uses_bounds_refreshed_after_keyboard_opens(self) -> None:
        device = _device()
        automation = KhanKidsAutomation(
            device,
            student="Student A",
            roster=("Student A", "Student B"),
            scratch=Path("/tmp/not-used"),
            parent_password_provider=lambda: "example123",
        )
        initial = _screen_with_text(
            ("Enter Password", Rect(900, 300, 1600, 400)),
            ("Password", Rect(900, 500, 1600, 600)),
            ("Enter", Rect(1100, 700, 1400, 780)),
        )
        shifted_enter = Rect(1100, 440, 1400, 510)
        shifted = _screen_with_text(
            ("Enter Password", Rect(900, 100, 1600, 200)),
            ("Enter", shifted_enter),
        )
        roster = _screen_with_text(
            ("Students", Rect(100, 100, 300, 160)),
            ("Student A", Rect(100, 200, 300, 260)),
            ("Student B", Rect(100, 300, 300, 360)),
        )
        automation.live_root = Mock(return_value=shifted)
        automation._wait_for_root = Mock(return_value=roster)

        automation._submit_parent_password(initial)

        device.enter_alphanumeric_secret.assert_called_once_with("example123")
        device.tap_rect.assert_called_once_with(shifted_enter)

    def test_return_to_profile_chooser_uses_in_app_back_and_switch_user(self) -> None:
        device, automation = _automation()
        report = _screen_with_text(
            ("Class Report: Assignments", Rect(600, 20, 1900, 120)),
            ("Assignments", Rect(900, 130, 1200, 190)),
            ("All Progress", Rect(1250, 130, 1550, 190)),
        )
        _add_control(report, REPORT_BACK_RECT)
        roster = _roster_screen()
        _add_control(roster, SWITCH_USER_RECT)
        chooser = _screen_with_text(
            ("dad", Rect(400, 500, 700, 800)),
            ("Student A", Rect(900, 500, 1200, 800)),
            ("Student B", Rect(1400, 500, 1700, 800)),
            ("Sign Out", Rect(2200, 1400, 2500, 1550)),
        )
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_navigation_target = Mock(side_effect=(roster, chooser))

        result = automation.return_to_profile_chooser()

        self.assertIs(result, chooser)
        self.assertEqual(
            device.tap_rect.call_args_list,
            [call(REPORT_BACK_RECT), call(SWITCH_USER_RECT)],
        )

    def test_return_to_profile_chooser_is_no_op_when_already_there(self) -> None:
        device, automation = _automation()
        chooser = _screen_with_text()
        automation._wait_for_navigation_state = Mock(return_value=(chooser, "profile_chooser"))

        self.assertIs(automation.return_to_profile_chooser(), chooser)
        device.tap_rect.assert_not_called()

    def test_return_to_profile_chooser_rejects_missing_switch_user_control(self) -> None:
        device, automation = _automation()
        report = _screen_with_text()
        _add_control(report, REPORT_BACK_RECT)
        roster = _roster_screen()
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_navigation_target = Mock(return_value=roster)

        with self.assertRaisesRegex(AutomationError, "Switch User"):
            automation.return_to_profile_chooser()

        device.tap_rect.assert_called_once_with(REPORT_BACK_RECT)

    def test_return_to_profile_chooser_retries_switch_user_from_fresh_roster(self) -> None:
        device, automation = _automation()
        report = _screen_with_text()
        _add_control(report, REPORT_BACK_RECT)
        first_roster = _roster_screen()
        _add_control(first_roster, SWITCH_USER_RECT)
        fresh_roster = _roster_screen()
        _add_control(fresh_roster, SWITCH_USER_RECT)
        chooser = _screen_with_text()
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_navigation_target = Mock(
            side_effect=(
                first_roster,
                AutomationError("transition timeout"),
                chooser,
            )
        )
        automation.live_root = Mock(return_value=fresh_roster)

        self.assertIs(automation.return_to_profile_chooser(), chooser)
        self.assertEqual(
            device.tap_rect.call_args_list,
            [call(REPORT_BACK_RECT), call(SWITCH_USER_RECT), call(SWITCH_USER_RECT)],
        )

    def test_return_to_profile_chooser_fails_closed_on_unexpected_screen(self) -> None:
        device, automation = _automation()
        report = _screen_with_text()
        _add_control(report, REPORT_BACK_RECT)
        unexpected = _screen_with_text()
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_navigation_target = Mock(
            side_effect=AutomationError("transition timeout")
        )
        automation.live_root = Mock(return_value=unexpected)

        with self.assertRaisesRegex(AutomationError, "unexpected navigation state None"):
            automation.return_to_profile_chooser()

        device.tap_rect.assert_called_once_with(REPORT_BACK_RECT)

    def test_bulk_unassignment_uses_one_traversal_and_bottom_first(self) -> None:
        device, automation = _automation()
        upper = _row("Upper", 500)
        lower = _row("Lower", 900)
        automation.ensure_assignments_report = Mock()
        automation._filter_assignments_to_student = Mock()
        automation._scroll_to_top = Mock()
        automation.root = Mock(return_value=ET.Element("hierarchy"))
        automation._unassign_row = Mock(
            side_effect=lambda _row, title, variant: ActionResult(
                "unchecked", title, variant, "saved"
            )
        )

        with patch("khan_kids.automation.parse_assignment_rows", return_value=[upper, lower]):
            results = tuple(automation.unassign_many((("Upper", "Basic"), ("Lower", "Basic"))))

        self.assertEqual([result.title for result in results], ["Lower", "Upper"])
        automation.ensure_assignments_report.assert_called_once_with()
        automation._scroll_to_top.assert_called_once_with()
        device.swipe.assert_not_called()

    def test_bulk_assignment_reuses_one_all_progress_pass_for_a_grade(self) -> None:
        device, automation = _automation()
        progress = ET.Element("progress")
        top = ET.Element("top")
        first_dialog = ET.Element("first-dialog")
        first_saved = ET.Element("first-saved")
        second_dialog = ET.Element("second-dialog")
        second_saved = ET.Element("second-saved")
        automation._open_all_progress = Mock(return_value=progress)
        automation._select_grade = Mock(return_value=progress)
        automation._scroll_to_top = Mock(return_value=top)
        automation._open_report_variant = Mock(side_effect=(first_dialog, second_dialog))
        automation._validate_assignment_dialog = Mock()
        automation._change_checkbox = Mock()
        automation._save_dialog = Mock(side_effect=(first_saved, second_saved))

        results = tuple(
            automation.assign_many(
                (
                    ("Preschool (Age 4)", "First", "Basic"),
                    ("Preschool (Age 4)", "Second", "Main"),
                )
            )
        )

        self.assertEqual([result.title for result in results], ["First", "Second"])
        automation._open_all_progress.assert_called_once_with()
        automation._select_grade.assert_called_once_with("Preschool (Age 4)", root=progress)
        automation._scroll_to_top.assert_called_once_with()
        self.assertEqual(
            automation._open_report_variant.call_args_list,
            [
                call("First", "Basic", root=top, reset_to_top=False),
                call("Second", "Main", root=first_saved, reset_to_top=False),
            ],
        )


def _row(title: str, top: int) -> AssignmentRow:
    return AssignmentRow(
        title=title,
        variant="Basic",
        assigned_date="Today",
        rect=Rect(0, top, 700, top + 80),
        score=None,
        score_rect=None,
    )


def _device() -> Mock:
    device = Mock()
    device.timing.span.return_value = nullcontext()
    return device


def _automation() -> tuple[Mock, KhanKidsAutomation]:
    device = _device()
    automation = KhanKidsAutomation(
        device,
        student="Student A",
        roster=("Student A", "Student B"),
        scratch=Path("/tmp/not-used"),
    )
    return device, automation


def _screen_with_text(*items: tuple[str, Rect]) -> ET.Element:
    root = ET.Element("hierarchy")
    parent = ET.SubElement(root, "node", bounds="[0,0][2560,1600]", text="")
    for text, rect in items:
        ET.SubElement(
            parent,
            "node",
            bounds=f"[{rect.left},{rect.top}][{rect.right},{rect.bottom}]",
            text=text,
        )
    return root


def _roster_screen() -> ET.Element:
    return _screen_with_text(
        ("Students", Rect(300, 300, 700, 400)),
        ("Add Students", Rect(1900, 400, 2300, 500)),
        ("Student A", Rect(300, 500, 600, 600)),
        ("Student B", Rect(1000, 500, 1300, 600)),
    )


def _chooser_screen() -> ET.Element:
    return _screen_with_text(
        ("dad", Rect(100, 100, 300, 160)),
        ("Student A", Rect(400, 100, 600, 160)),
        ("Student B", Rect(700, 100, 900, 160)),
        ("Sign Out", Rect(2200, 1400, 2500, 1550)),
    )


def _add_control(root: ET.Element, rect: Rect) -> None:
    parent = next(iter(root))
    ET.SubElement(
        parent,
        "node",
        bounds=f"[{rect.left},{rect.top}][{rect.right},{rect.bottom}]",
        text="",
        **{"class": "android.view.ViewGroup"},
    )


if __name__ == "__main__":
    unittest.main()
