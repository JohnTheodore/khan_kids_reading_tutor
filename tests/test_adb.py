from __future__ import annotations

import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import (
    AUTO_SLEEP_TIMEOUT_MS,
    LOCK_TASK_LOCKED,
    LOCK_TASK_NONE,
    LOCK_TASK_PINNED,
    LOCK_TASK_UNKNOWN,
    AndroidDevice,
    AutomationError,
    HomeHandoffError,
    parse_lock_task_mode,
    run_command,
)


class AndroidDeviceTests(unittest.TestCase):
    def test_lock_task_state_parser_is_explicit_and_fails_unknown_closed(self) -> None:
        self.assertEqual(parse_lock_task_mode("mLockTaskModeState=NONE"), LOCK_TASK_NONE)
        self.assertEqual(
            parse_lock_task_mode("mLockTaskModeState=LOCK_TASK_MODE_PINNED"),
            LOCK_TASK_PINNED,
        )
        self.assertEqual(parse_lock_task_mode("mLockTaskModeState=LOCKED"), LOCK_TASK_LOCKED)
        self.assertEqual(parse_lock_task_mode("changed format"), LOCK_TASK_UNKNOWN)

    def test_android_home_requires_two_stable_non_app_reads(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        with (
            patch.object(
                device, "command", side_effect=[b"mLockTaskModeState=NONE", b""]
            ) as command,
            patch.object(
                device,
                "foreground_package",
                side_effect=[
                    "org.khankids.android",
                    "com.android.systemui",
                    "launcher",
                    "launcher",
                ],
            ),
            patch("khan_kids.adb.time.sleep"),
        ):
            self.assertEqual(device.return_to_android_home("org.khankids.android"), "launcher")
        self.assertEqual(
            command.call_args_list[-1].args,
            ("shell", "input", "keyevent", "KEYCODE_HOME"),
        )

    def test_android_home_unpins_before_sending_home(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        with (
            patch.object(
                device,
                "lock_task_mode",
                side_effect=[LOCK_TASK_PINNED, LOCK_TASK_NONE],
            ),
            patch.object(device, "foreground_package", side_effect=["launcher", "launcher"]),
            patch.object(device, "command") as command,
            patch("khan_kids.adb.time.sleep"),
        ):
            self.assertEqual(device.return_to_android_home("org.khankids.android"), "launcher")
        self.assertEqual(
            [call.args for call in command.call_args_list],
            [
                ("shell", "am", "task", "lock", "stop"),
                ("shell", "input", "keyevent", "KEYCODE_HOME"),
            ],
        )

    def test_unpin_requires_verified_none_and_never_touches_managed_mode(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        with (
            patch.object(device, "lock_task_mode", return_value=LOCK_TASK_PINNED),
            patch.object(device, "command") as command,
            patch("khan_kids.adb.time.monotonic", side_effect=[0, 0, 4]),
            patch("khan_kids.adb.time.sleep"),
            self.assertRaisesRegex(AutomationError, "remained active"),
        ):
            device.ensure_screen_unpinned()
        command.assert_called_once_with("shell", "am", "task", "lock", "stop")

        with (
            patch.object(device, "command") as command,
            self.assertRaisesRegex(AutomationError, "managed lock-task"),
        ):
            device.ensure_screen_unpinned(initial_mode=LOCK_TASK_LOCKED)
        command.assert_not_called()

    def test_android_home_fails_when_khan_remains_foreground(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        with (
            patch.object(device, "command", return_value=b""),
            patch.object(device, "lock_task_mode", return_value=LOCK_TASK_NONE),
            patch.object(device, "foreground_package", return_value="org.khankids.android"),
            patch("khan_kids.adb.time.monotonic", side_effect=[0, 0, 6]),
            patch("khan_kids.adb.time.sleep"),
            self.assertRaisesRegex(HomeHandoffError, "did not leave"),
        ):
            device.return_to_android_home("org.khankids.android")

    def test_app_session_goes_home_on_success(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(device, "awake_session", return_value=nullcontext()),
            patch.object(device, "return_to_android_home") as home,
            device.app_session("org.khankids.android"),
        ):
            pass
        home.assert_called_once_with("org.khankids.android")

    def test_app_session_goes_home_on_failure(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(device, "awake_session", return_value=nullcontext()),
            patch.object(device, "return_to_android_home") as home,
            self.assertRaisesRegex(RuntimeError, "operation failed"),
            device.app_session("org.khankids.android"),
        ):
            raise RuntimeError("operation failed")
        home.assert_called_once_with("org.khankids.android")

    def test_app_session_captures_failure_before_home_cleanup(self) -> None:
        device = AndroidDevice("test-device")
        events = []
        with (
            patch.object(device, "awake_session", return_value=nullcontext()),
            patch.object(
                device,
                "return_to_android_home",
                side_effect=lambda _package: events.append("home"),
            ),
            self.assertRaisesRegex(RuntimeError, "operation failed"),
            device.app_session(
                "org.khankids.android",
                on_error=lambda _error: events.append("diagnostic"),
            ),
        ):
            raise RuntimeError("operation failed")
        self.assertEqual(events, ["diagnostic", "home"])

    def test_app_session_preserves_primary_failure_when_home_also_fails(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(device, "awake_session", return_value=nullcontext()),
            patch.object(
                device,
                "return_to_android_home",
                side_effect=AutomationError("home blocked"),
            ),
            self.assertRaisesRegex(RuntimeError, "operation failed") as raised,
            device.app_session("org.khankids.android"),
        ):
            raise RuntimeError("operation failed")
        self.assertIn("Home cleanup also failed", " ".join(raised.exception.__notes__))

    def test_wake_skips_command_and_sleep_when_already_awake(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(device, "command", return_value=b"mWakefulness=Awake") as command,
            patch("khan_kids.adb.time.sleep") as sleep,
        ):
            device.wake()
        self.assertEqual(command.call_count, 1)
        sleep.assert_not_called()

    def test_unknown_wake_state_retains_wakeup_and_settling(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(device, "command", return_value=b"unknown") as command,
            patch("khan_kids.adb.time.sleep") as sleep,
        ):
            device.wake()
        self.assertEqual(command.call_args.args, ("shell", "input", "keyevent", "KEYCODE_WAKEUP"))
        sleep.assert_called_once_with(device.settle_seconds)

    def test_existing_landscape_lock_skips_rotation_settle_only(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(device, "ensure_auto_sleep"),
            patch.object(
                device,
                "_rotation_restore_command",
                return_value=("shell", "wm", "user-rotation", "lock", "3"),
            ),
            patch.object(device, "wake"),
            patch.object(device, "command"),
            patch("khan_kids.adb.time.sleep") as sleep,
            device.awake_session(),
        ):
            pass
        sleep.assert_not_called()

    def test_scroll_to_top_reuses_supplied_root_but_reads_after_gesture(self) -> None:
        device = AndroidDevice("test-device")
        root = ET.fromstring('<hierarchy><node text="top" bounds="[0,0][100,100]"/></hierarchy>')
        with (
            patch.object(device, "hierarchy", return_value=root) as hierarchy,
            patch.object(device, "swipe") as swipe,
        ):
            self.assertIs(device.scroll_to_top(1200, root=root), root)
        hierarchy.assert_called_once_with()
        swipe.assert_called_once()

    def _run_awake_session(
        self, rotation_state: bytes, *, fail: bool = False
    ) -> tuple[AndroidDevice, object]:
        device = AndroidDevice("test-device")
        command_patch = patch.object(device, "command")
        with command_patch as command, patch("khan_kids.adb.time.sleep"):
            command.side_effect = [b"", b"", rotation_state, b"mWakefulness=Awake", *([b""] * 10)]
            if fail:
                with self.assertRaisesRegex(RuntimeError, "test failure"), device.awake_session():
                    raise RuntimeError("test failure")
            else:
                with device.awake_session():
                    pass
        return device, command

    def test_failed_command_becomes_concise_automation_error(self) -> None:
        with patch("khan_kids.adb.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(
                1, ["adb", "get-state"], stderr=b"device offline\n"
            )

            with self.assertRaisesRegex(AutomationError, "device offline"):
                run_command(("adb", "get-state"), capture=True)

    def test_awake_session_atomically_locks_landscape_and_restores_auto_rotation(self) -> None:
        _device, command = self._run_awake_session(b"free\n", fail=True)

        calls = [call.args for call in command.call_args_list]
        landscape_lock = ("shell", "wm", "user-rotation", "lock", "3")
        compatibility_override = (
            "shell",
            "wm",
            "set-ignore-orientation-request",
            "false",
        )
        self.assertIn(landscape_lock, calls)
        self.assertIn(compatibility_override, calls)
        self.assertLess(calls.index(landscape_lock), calls.index(compatibility_override))
        self.assertEqual(
            command.call_args_list[-3].args,
            ("shell", "wm", "user-rotation", "free"),
        )
        self.assertEqual(
            command.call_args_list[-2].args,
            (
                "shell",
                "settings",
                "put",
                "system",
                "screen_off_timeout",
                AUTO_SLEEP_TIMEOUT_MS,
            ),
        )
        self.assertEqual(
            command.call_args_list[-1].args,
            ("shell", "settings", "put", "global", "stay_on_while_plugged_in", "0"),
        )

    def test_awake_session_restores_prior_fixed_rotation(self) -> None:
        _device, command = self._run_awake_session(b"lock 1\n")

        self.assertIn(
            ("shell", "wm", "user-rotation", "lock", "1"),
            [call.args for call in command.call_args_list],
        )

    def test_awake_session_rejects_unrecognized_rotation_state(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(
                device,
                "command",
                side_effect=[b"", b"", b"unexpected\n"],
            ),
            self.assertRaisesRegex(AutomationError, "Unexpected Android user-rotation"),
            device.awake_session(),
        ):
            pass

    def test_auto_sleep_attempts_both_safety_settings_after_one_failure(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(
                device,
                "_set_setting",
                side_effect=[AutomationError("timeout write failed"), None],
            ) as setting,
            self.assertRaisesRegex(AutomationError, "automatic tablet sleep"),
        ):
            device.ensure_auto_sleep()
        self.assertEqual(setting.call_count, 2)

    def test_automation_never_enables_persistent_stay_awake(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools/khan_kids/adb.py").read_text()
        self.assertNotIn('"stayon", "true"', source)
        self.assertNotIn('"2147483647"', source)

    def test_scroll_to_top_stops_when_visible_ui_repeats(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        middle = ET.Element("hierarchy")
        top = ET.Element("hierarchy")
        with (
            patch.object(device, "hierarchy", side_effect=[middle, top, top]) as hierarchy,
            patch.object(
                device, "_window_signature", side_effect=[(("middle",),), (("top",),), (("top",),)]
            ) as signature,
            patch.object(device, "swipe") as swipe,
            patch("khan_kids.adb.time.sleep"),
        ):
            result = device.scroll_to_top(1200)

        self.assertEqual(signature.call_count, 3)
        self.assertEqual(hierarchy.call_count, 3)
        self.assertEqual(swipe.call_count, 2)
        self.assertIs(result, top)

    def test_unlock_sends_pin_as_individual_key_events(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        with (
            patch.object(device, "command") as command,
            patch.object(device, "swipe") as swipe,
            patch("khan_kids.adb.time.sleep"),
        ):
            device.unlock_with_pin("1357")

        swipe.assert_called_once()
        calls = [call.args for call in command.call_args_list]
        self.assertEqual(
            calls,
            [
                ("shell", "input", "keyevent", "KEYCODE_1"),
                ("shell", "input", "keyevent", "KEYCODE_3"),
                ("shell", "input", "keyevent", "KEYCODE_5"),
                ("shell", "input", "keyevent", "KEYCODE_7"),
                ("shell", "input", "keyevent", "KEYCODE_ENTER"),
            ],
        )
        self.assertFalse(any("1357" in argument for call in calls for argument in call))

    def test_secret_entry_preserves_case_without_exposing_complete_secret(self) -> None:
        device = AndroidDevice("test-device")
        with patch("khan_kids.adb.run_command") as command:
            device.enter_alphanumeric_secret("Ab2")
        command.assert_called_once_with(
            ("adb", "-s", "test-device", "shell"),
            input_data=b"set -e\ninput keycombination KEYCODE_SHIFT_LEFT KEYCODE_A\n"
            b"input keyevent KEYCODE_B\ninput keyevent KEYCODE_2\nexit\n",
        )
        self.assertNotIn("Ab2", str(command.call_args.args))

    def test_secret_entry_failure_is_sanitized_and_not_retried(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch("khan_kids.adb.run_command", side_effect=AutomationError("sensitive")) as run,
            self.assertRaisesRegex(AutomationError, "refusing to retry") as error,
        ):
            device.enter_alphanumeric_secret("Ab2")
        run.assert_called_once()
        self.assertNotIn("sensitive", str(error.exception))

    def test_secret_entry_rejects_non_alphanumeric_text(self) -> None:
        device = AndroidDevice("test-device")

        with self.assertRaisesRegex(AutomationError, "unsupported"):
            device.enter_alphanumeric_secret("not safe!")

    def test_lock_state_reads_window_markers(self) -> None:
        device = AndroidDevice("test-device")
        with patch.object(
            device,
            "command",
            return_value=b"mShowingDream=false mDreamingLockscreen=true\n",
        ):
            self.assertTrue(device.is_locked())

    def test_foreground_package_parses_current_focus(self) -> None:
        device = AndroidDevice("test-device")
        state = b"mCurrentFocus=Window{abc u0 org.khankids.android/.MainActivity}\n"
        with patch.object(device, "command", return_value=state):
            self.assertEqual(device.foreground_package(), "org.khankids.android")

    def test_start_activity_waits_for_activity_manager_transition(self) -> None:
        device = AndroidDevice("test-device", settle_seconds=0)
        with patch.object(device, "command") as command:
            device.start_activity("example/.MainActivity")

        command.assert_called_once_with("shell", "am", "start", "-W", "-n", "example/.MainActivity")


if __name__ == "__main__":
    unittest.main()
