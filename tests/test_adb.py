from __future__ import annotations

import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AndroidDevice, AutomationError, run_command


class AndroidDeviceTests(unittest.TestCase):
    def _run_awake_session(
        self, rotation_state: bytes, *, fail: bool = False
    ) -> tuple[AndroidDevice, object]:
        device = AndroidDevice("test-device")
        command_patch = patch.object(device, "command")
        with command_patch as command, patch("khan_kids.adb.time.sleep"):
            command.side_effect = [b"120000\n", b"0\n", rotation_state, *([b""] * 10)]
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
            ("shell", "settings", "put", "system", "screen_off_timeout", "120000"),
        )
        self.assertEqual(
            command.call_args_list[-2].args,
            ("shell", "settings", "put", "global", "stay_on_while_plugged_in", "0"),
        )
        self.assertEqual(
            command.call_args_list[-1].args,
            ("shell", "wm", "user-rotation", "free"),
        )

    def test_awake_session_restores_prior_fixed_rotation(self) -> None:
        _device, command = self._run_awake_session(b"lock 1\n")

        self.assertEqual(
            command.call_args_list[-1].args,
            ("shell", "wm", "user-rotation", "lock", "1"),
        )

    def test_awake_session_rejects_unrecognized_rotation_state(self) -> None:
        device = AndroidDevice("test-device")
        with (
            patch.object(
                device,
                "command",
                side_effect=[b"120000\n", b"0\n", b"unexpected\n"],
            ),
            self.assertRaisesRegex(AutomationError, "Unexpected Android user-rotation"),
            device.awake_session(),
        ):
            pass

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


if __name__ == "__main__":
    unittest.main()
