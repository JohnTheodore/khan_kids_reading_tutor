from __future__ import annotations

import sys
import tempfile
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
    def test_prize_overlay_is_not_mistaken_for_child_home(self):
        _, automation = _automation()
        root = _prize_screen()
        self.assertEqual(automation._navigation_state(root), "prize_picker")
        self.assertEqual(len(automation._prize_choices(root)), 3)
        root.remove(list(root)[-1])
        self.assertFalse(automation._prize_choices(root))
        self.assertIsNone(automation._navigation_state(root))

    def test_unobstructed_child_home_is_not_a_prize_picker(self):
        _, automation = _automation()
        root = _screen_with_text(("Student A", Rect(2169, 42, 2356, 166)))
        self.assertFalse(automation._prize_choices(root))
        self.assertEqual(automation._navigation_state(root), "child_home")

    def test_random_prize_uses_one_fresh_choice_and_verifies_home(self):
        device, automation = _automation()
        root = _prize_screen()
        home = _screen_with_text(("Student A", Rect(2169, 42, 2356, 166)))
        automation.live_root = Mock(return_value=root)
        automation._wait_for_stable_root = Mock(return_value=home)
        choice = automation._prize_choices(root)[1]
        with patch("khan_kids.automation.secrets.choice", return_value=choice) as random_choice:
            self.assertIs(automation.pick_random_prize(), home)
        random_choice.assert_called_once_with(automation._prize_choices(root))
        device.tap_rect.assert_called_once_with(choice)
        predicate = automation._wait_for_stable_root.call_args.args[0]
        self.assertTrue(predicate(home))
        self.assertFalse(predicate(root))

    def test_prize_timeout_does_not_repeat_an_uncertain_award(self):
        device, automation = _automation()
        automation.live_root = Mock(return_value=_prize_screen())
        automation._wait_for_stable_root = Mock(side_effect=AutomationError("timeout"))
        with self.assertRaisesRegex(AutomationError, "timeout"):
            automation.pick_random_prize()
        device.tap_rect.assert_called_once()

    def test_prize_refuses_wrong_child_or_stale_screen(self):
        for root in (_prize_screen("Student B"), _screen_with_text()):
            with self.subTest(root=root):
                device, automation = _automation()
                automation.live_root = Mock(return_value=root)
                with self.assertRaises(AutomationError):
                    automation.pick_random_prize()
                device.tap_rect.assert_not_called()

    def test_prize_startup_continues_without_restarting(self):
        device, automation = _automation()
        automation.live_root = Mock(return_value=_prize_screen())
        home = _screen_with_text(("Student A", Rect(2169, 42, 2356, 166)))
        automation.pick_random_prize = Mock(return_value=home)
        automation._tap_until_navigation_target = Mock(return_value=_chooser_screen())
        self.assertTrue(automation.ready_for_sync())
        automation.pick_random_prize.assert_called_once()
        self.assertEqual(
            automation._tap_until_navigation_target.call_args.kwargs["target_state"],
            "profile_chooser",
        )
        device.force_stop.assert_not_called()

    def test_profile_chooser_without_sign_out_requires_aligned_avatar_labels(self):
        _, automation = _automation()
        chooser = _screen_with_text(
            ("dad", Rect(611, 981, 677, 1026)),
            ("Student A", Rect(1218, 981, 1350, 1026)),
            ("Student B", Rect(1862, 981, 1985, 1026)),
        )
        self.assertEqual(automation._navigation_state(chooser), "profile_chooser")
        unrelated = _screen_with_text(
            ("dad", Rect(611, 100, 677, 150)),
            ("Student A", Rect(1218, 981, 1350, 1026)),
            ("Student B", Rect(1862, 981, 1985, 1026)),
        )
        self.assertIsNone(automation._navigation_state(unrelated))

    def test_debug_observer_failure_cannot_interrupt_navigation(self):
        device, automation = _automation()
        device.hierarchy.return_value = _assignment_screen()
        automation.hierarchy_observer = Mock(side_effect=OSError("diagnostic storage unavailable"))
        self.assertIs(automation.live_root(), device.hierarchy.return_value)

    def test_blocked_startup_captures_are_private_and_capture_failure_does_not_escape(self) -> None:
        device, automation = _automation()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            device.dump.side_effect = AutomationError("sensitive details")
            with patch("khan_kids.automation.tempfile.mkdtemp", return_value=directory):
                message = automation._capture_blocked_startup()
            device.screenshot.assert_called_once_with(destination / "screen.png")
            device.dump.assert_called_once_with(destination / "window.xml")
            for name in ("screen.png", "window.xml"):
                self.assertEqual((destination / name).stat().st_mode & 0o777, 0o600)
            self.assertIn("capture failed: window.xml", message)
            self.assertNotIn("sensitive details", message)

    def test_early_roster_check_waits_without_retapping_loading_screen(self) -> None:
        device, automation = _automation()
        roster = _roster_screen()
        report = _assignment_screen()
        automation._wait_for_navigation_state = Mock(return_value=(roster, "teacher_roster"))
        automation._wait_for_stable_root = Mock(side_effect=(AutomationError("loading"), report))
        automation.live_root = Mock(return_value=_screen_with_text())
        self.assertIs(automation.ensure_assignments_report(), report)
        device.tap_rect.assert_called_once()
        self.assertEqual(
            [item.kwargs["timeout"] for item in automation._wait_for_stable_root.call_args_list],
            [3.0, 9.0],
        )

    def test_score_close_does_not_retry_from_unexpected_screen(self) -> None:
        device, automation = _automation()
        dialog = _screen_with_text(("Student A's Lesson Scores", Rect(100, 100, 600, 160)))
        automation._wait_for_stable_root = Mock(side_effect=AutomationError("timeout"))
        automation.live_root = Mock(return_value=_screen_with_text())
        with self.assertRaisesRegex(AutomationError, "unexpected navigation"):
            automation._close_score_dialog(dialog)
        device.tap_rect.assert_called_once()

    def test_score_close_retries_dropped_tap_only_from_same_dialog(self) -> None:
        device, automation = _automation()
        dialog = _screen_with_text(("Student A's Lesson Scores", Rect(100, 100, 600, 160)))
        report = _assignment_screen()
        automation._wait_for_stable_root = Mock(side_effect=(AutomationError("dropped"), report))
        automation.live_root = Mock(return_value=dialog)
        automation._close_score_dialog(dialog)
        self.assertEqual(device.tap_rect.call_count, 2)

    def test_warm_reuse_requires_two_matching_supported_screens(self) -> None:
        _, automation = _automation()
        automation._capture_blocked_startup = Mock(return_value="Diagnostics saved privately.")
        automation.live_root = Mock(side_effect=(_chooser_screen(), _chooser_screen()))
        self.assertTrue(automation.ready_for_sync())
        automation.live_root = Mock(side_effect=(_chooser_screen(), _assignment_screen()))
        with self.assertRaisesRegex(AutomationError, "without restarting"):
            automation.ready_for_sync()
        automation.live_root = Mock(return_value=_screen_with_text())
        with self.assertRaisesRegex(AutomationError, "without restarting"):
            automation.ready_for_sync()

    def test_supplied_navigation_root_cannot_bypass_state_validation(self) -> None:
        device, automation = _automation()
        with self.assertRaisesRegex(AutomationError, "does not match"):
            automation.ensure_assignments_report(root=_assignment_screen(), state="teacher_roster")
        device.tap_rect.assert_not_called()

    def test_child_assignments_reuses_guarded_navigation_to_chooser(self) -> None:
        _, automation = _automation()
        library = _screen_with_text(
            ("Assignments", Rect(353, 443, 754, 521)),
            ("Lessons assigned to you by dad", Rect(353, 532, 791, 572)),
        )
        home = _screen_with_text(("Student A", Rect(2169, 42, 2356, 166)))
        automation.live_root = Mock(return_value=library)
        automation._tap_navigation_control = Mock(side_effect=(home, _chooser_screen()))
        self.assertTrue(automation.ready_for_sync())
        calls = automation._tap_navigation_control.call_args_list
        self.assertEqual(
            [c.kwargs["target_state"] for c in calls], ["child_home", "profile_chooser"]
        )

    def test_child_home_skips_back_and_navigation_failure_preserves_prompt(self) -> None:
        _, automation = _automation()
        automation._capture_blocked_startup = Mock(return_value="Diagnostics saved privately.")
        home = _screen_with_text(("Student A", Rect(2169, 42, 2356, 166)))
        automation.live_root = Mock(return_value=home)
        automation._tap_navigation_control = Mock(return_value=_chooser_screen())
        self.assertTrue(automation.ready_for_sync())
        automation._tap_navigation_control.assert_called_once()
        automation._tap_navigation_control.side_effect = AutomationError("unexpected screen")
        with self.assertRaisesRegex(AutomationError, "without restarting"):
            automation.ready_for_sync()
        automation._capture_blocked_startup.assert_called_once()

    def test_child_home_does_not_match_arbitrary_student_label(self) -> None:
        _, automation = _automation()
        automation._capture_blocked_startup = Mock(return_value="Diagnostics saved privately.")
        automation.live_root = Mock(
            return_value=_screen_with_text(("Student A", Rect(100, 100, 500, 160)))
        )
        automation._tap_navigation_control = Mock()
        with self.assertRaisesRegex(AutomationError, "without restarting"):
            automation.ready_for_sync()
        automation._tap_navigation_control.assert_not_called()

    def test_child_navigation_requires_exact_image_control_before_tapping(self) -> None:
        device, automation = _automation()
        automation._capture_blocked_startup = Mock(return_value="Diagnostics saved privately.")
        for screen in (
            _screen_with_text(("Student A", Rect(2169, 42, 2356, 166))),
            _screen_with_text(
                ("Assignments", Rect(353, 443, 754, 521)),
                ("Lessons assigned to you by dad", Rect(353, 532, 791, 572)),
            ),
        ):
            automation.live_root = Mock(return_value=screen)
            with self.assertRaisesRegex(AutomationError, "without restarting"):
                automation.ready_for_sync()
        device.tap_rect.assert_not_called()

    def test_all_progress_navigation_reuses_fresh_assignments_state(self) -> None:
        _, automation = _automation()
        report = _assignment_screen()
        progress = _screen_with_text(("Class Report: All Progress", Rect(100, 100, 600, 160)))
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._tap_navigation_control = Mock(return_value=progress)
        self.assertIs(automation.ensure_all_progress_report(), progress)
        automation._wait_for_navigation_state.assert_called_once_with()

    def test_all_progress_navigation_does_not_switch_away_when_already_there(self) -> None:
        _, automation = _automation()
        progress = _screen_with_text(("Class Report: All Progress", Rect(100, 100, 600, 160)))
        automation._wait_for_navigation_state = Mock(return_value=(progress, "all_progress_report"))
        automation._tap_navigation_control = Mock()
        self.assertIs(automation.ensure_all_progress_report(), progress)
        automation._tap_navigation_control.assert_not_called()

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
        root = _roster_screen()
        report = _assignment_screen()
        device.hierarchy.side_effect = (root, root, report, report)

        automation.ensure_assignments_report()

        device.tap_rect.assert_called_once_with(Rect(1279, 458, 1281, 460))

    def test_roster_navigation_retries_one_dropped_tap_from_fresh_state(self) -> None:
        device, automation = _automation()
        roster = _roster_screen()
        fresh_roster = _roster_screen()
        report = _assignment_screen()
        automation._wait_for_navigation_state = Mock(return_value=(roster, "teacher_roster"))
        automation._wait_for_stable_root = Mock(
            side_effect=(AutomationError("dropped tap"), report)
        )
        automation.live_root = Mock(return_value=fresh_roster)

        self.assertIs(automation.ensure_assignments_report(), report)
        self.assertEqual(device.tap_rect.call_count, 2)
        self.assertEqual(
            [item.kwargs["timeout"] for item in automation._wait_for_stable_root.call_args_list],
            [3.0, 12],
        )

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
        report = _assignment_screen()
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
        report = _assignment_screen()
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
        automation._wait_for_stable_root = Mock(side_effect=(roster, chooser))

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
        report = _assignment_screen()
        _add_control(report, REPORT_BACK_RECT)
        roster = _roster_screen()
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_stable_root = Mock(return_value=roster)

        with self.assertRaisesRegex(AutomationError, "Switch User"):
            automation.return_to_profile_chooser()

        device.tap_rect.assert_called_once_with(REPORT_BACK_RECT)

    def test_return_to_profile_chooser_retries_switch_user_from_fresh_roster(self) -> None:
        device, automation = _automation()
        report = _assignment_screen()
        _add_control(report, REPORT_BACK_RECT)
        first_roster = _roster_screen()
        _add_control(first_roster, SWITCH_USER_RECT)
        fresh_roster = _roster_screen()
        _add_control(fresh_roster, SWITCH_USER_RECT)
        chooser = _screen_with_text()
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_stable_root = Mock(
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
        report = _assignment_screen()
        _add_control(report, REPORT_BACK_RECT)
        unexpected = _screen_with_text()
        automation._wait_for_navigation_state = Mock(return_value=(report, "assignments_report"))
        automation._wait_for_stable_root = Mock(side_effect=AutomationError("transition timeout"))
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
        automation._scroll_to_top.assert_called_once_with(
            root=automation._filter_assignments_to_student.return_value
        )
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
        automation._scroll_to_top.assert_called_once_with(root=progress)
        self.assertEqual(
            automation._open_report_variant.call_args_list,
            [
                call("First", "Basic", root=top, reset_to_top=False),
                call("Second", "Main", root=first_saved, reset_to_top=False),
            ],
        )

    def test_catalog_probe_reuses_current_report_root_without_reset(self):
        _, automation = _automation()
        progress, dialog = ET.Element("progress"), ET.Element("dialog")
        automation._open_all_progress = Mock(return_value=progress)
        automation._select_grade = Mock(return_value=progress)
        automation._open_report_variant = Mock(return_value=dialog)
        automation._inspect_open_assignment = Mock(return_value={"Student A": "checked"})
        self.assertEqual(
            automation.inspect_catalog_assignment(
                "Kindergarten", "Lowercase l", "Main", reset_to_top=False
            ),
            {"Student A": "checked"},
        )
        automation._select_grade.assert_called_once_with("Kindergarten", root=progress)
        automation._open_report_variant.assert_called_once_with(
            "Lowercase l", "Main", root=progress, reset_to_top=False
        )

    def test_in_place_variant_lookup_never_scrolls_to_top(self):
        device, automation = _automation()
        expanded = _screen_with_text(
            ("Class Report: All Progress", Rect(600, 20, 1900, 120)),
            ("Blend Sounds 2", Rect(200, 500, 900, 580)),
            ("Practice 2", Rect(239, 600, 800, 680)),
        )
        dialog = ET.Element("dialog")
        automation._scroll_to_top = Mock()
        automation._wait_for_assignment_dialog = Mock(return_value=dialog)
        self.assertIs(
            automation._open_report_variant(
                "Blend Sounds 2", "Practice 2", root=expanded, reset_to_top=False
            ),
            dialog,
        )
        automation._scroll_to_top.assert_not_called()
        device.swipe.assert_not_called()
        device.tap_rect.assert_called_once_with(Rect(239, 600, 800, 680))

    def test_lesson_expansion_retries_a_dropped_tap_from_fresh_row(self) -> None:
        device, automation = _automation()
        collapsed = _screen_with_text(
            ("Class Report: All Progress", Rect(600, 20, 1900, 120)),
            ("Blend Sounds 2", Rect(200, 500, 900, 580)),
        )
        refreshed = _screen_with_text(
            ("Class Report: All Progress", Rect(600, 20, 1900, 120)),
            ("Blend Sounds 2", Rect(200, 500, 900, 580)),
        )
        expanded = _screen_with_text(
            ("Class Report: All Progress", Rect(600, 20, 1900, 120)),
            ("Blend Sounds 2", Rect(200, 500, 900, 580)),
            ("Practice 2", Rect(239, 600, 800, 680)),
        )
        dialog = ET.Element("dialog")
        automation._wait_for_stable_root = Mock(
            side_effect=(AutomationError("dropped tap"), expanded)
        )
        automation.live_root = Mock(return_value=refreshed)
        automation._wait_for_assignment_dialog = Mock(return_value=dialog)

        result = automation._open_report_variant(
            "Blend Sounds 2", "Practice 2", root=collapsed, reset_to_top=False
        )

        self.assertIs(result, dialog)
        self.assertEqual(
            device.tap_rect.call_args_list,
            [
                call(Rect(200, 500, 900, 580)),
                call(Rect(200, 500, 900, 580)),
                call(Rect(239, 600, 800, 680)),
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


def _prize_screen(student: str = "Student A") -> ET.Element:
    root = _screen_with_text((student, Rect(2169, 42, 2356, 166)))
    for bounds in (
        "[364,538][889,1063]",
        "[1022,538][1547,1063]",
        "[1676,538][2201,1063]",
        "[551,983][701,1133]",
        "[1206,983][1356,1133]",
        "[1864,983][2014,1133]",
    ):
        ET.SubElement(root, "node", bounds=bounds, **{"class": "android.widget.ImageView"})
    return root


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


def _assignment_screen() -> ET.Element:
    return _screen_with_text(
        ("Class Report: Assignments", Rect(600, 20, 1900, 120)),
        ("Assignments", Rect(900, 130, 1200, 190)),
        ("All Progress", Rect(1250, 130, 1550, 190)),
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
