from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AutomationError
from khan_kids.constants import KHAN_KIDS_ACTIVITY, KHAN_KIDS_PACKAGE
from khan_kids.launcher import ensure_khan_kids_open, read_local_secrets


class FakeDevice:
    def __init__(self, *, locked: bool, foreground: str | None) -> None:
        self.locked = locked
        self.foreground = foreground
        self.calls: list[object] = []

    def wake(self) -> None:
        self.calls.append("wake")

    def is_locked(self) -> bool:
        self.calls.append("is_locked")
        return self.locked

    def unlock_with_pin(self, pin: str) -> None:
        self.calls.append(("unlock", pin))
        self.locked = False

    def foreground_package(self) -> str | None:
        self.calls.append("foreground")
        return self.foreground

    def start_activity(self, component: str) -> None:
        self.calls.append(("start", component))
        self.foreground = KHAN_KIDS_PACKAGE

    def force_stop(self, package: str) -> None:
        self.calls.append(("force_stop", package))
        self.foreground = None


class LauncherTests(unittest.TestCase):
    def test_blocked_reuse_never_force_stops_or_launches(self) -> None:
        device = FakeDevice(locked=False, foreground=KHAN_KIDS_PACKAGE)

        def blocked() -> bool:
            raise AutomationError("Startup blocked; preserve prompt")

        with self.assertRaisesRegex(AutomationError, "preserve prompt"):
            ensure_khan_kids_open(device, fresh_start=True, reuse_ready=blocked)
        self.assertNotIn(("force_stop", KHAN_KIDS_PACKAGE), device.calls)
        self.assertNotIn(("start", KHAN_KIDS_ACTIVITY), device.calls)

    def test_ready_foreground_can_be_reused_without_force_stop(self) -> None:
        device = FakeDevice(locked=False, foreground=KHAN_KIDS_PACKAGE)
        result = ensure_khan_kids_open(device, fresh_start=True, reuse_ready=lambda: True)
        self.assertFalse(result.launched)
        self.assertNotIn(("force_stop", KHAN_KIDS_PACKAGE), device.calls)

    def test_unready_foreground_retains_cold_start(self) -> None:
        device = FakeDevice(locked=False, foreground=KHAN_KIDS_PACKAGE)
        result = ensure_khan_kids_open(device, fresh_start=True, reuse_ready=lambda: False)
        self.assertTrue(result.launched)

    def test_already_open_device_is_not_unlocked_or_relaunched(self) -> None:
        device = FakeDevice(locked=False, foreground=KHAN_KIDS_PACKAGE)

        result = ensure_khan_kids_open(device)

        self.assertFalse(result.unlocked)
        self.assertFalse(result.launched)
        self.assertNotIn(("start", KHAN_KIDS_ACTIVITY), device.calls)

    def test_locked_device_is_unlocked_once_and_launched(self) -> None:
        device = FakeDevice(locked=True, foreground="com.android.systemui")

        result = ensure_khan_kids_open(device, pin_provider=lambda: "1234")

        self.assertTrue(result.unlocked)
        self.assertTrue(result.launched)
        self.assertEqual(device.calls.count(("unlock", "1234")), 1)
        self.assertIn(("start", KHAN_KIDS_ACTIVITY), device.calls)

    def test_locked_device_without_provider_fails_before_launch(self) -> None:
        device = FakeDevice(locked=True, foreground="com.android.systemui")

        with self.assertRaisesRegex(AutomationError, "PIN provider"):
            ensure_khan_kids_open(device)

        self.assertNotIn(("start", KHAN_KIDS_ACTIVITY), device.calls)

    def test_fresh_start_restarts_even_when_khan_kids_is_foreground(self) -> None:
        device = FakeDevice(locked=False, foreground=KHAN_KIDS_PACKAGE)

        result = ensure_khan_kids_open(device, fresh_start=True)

        self.assertTrue(result.launched)
        self.assertIn(("force_stop", KHAN_KIDS_PACKAGE), device.calls)
        self.assertIn(("start", KHAN_KIDS_ACTIVITY), device.calls)

    def test_transient_keyguard_and_delayed_foreground_are_waited_out(self) -> None:
        device = Mock()
        device.is_locked.side_effect = (False, True, True, False, False)
        device.foreground_package.side_effect = (
            "com.android.systemui",
            "com.android.launcher",
            KHAN_KIDS_PACKAGE,
            KHAN_KIDS_PACKAGE,
        )

        with patch("khan_kids.launcher.time.sleep"):
            result = ensure_khan_kids_open(device, pin_provider=lambda: "1234")

        self.assertTrue(result.unlocked)
        device.unlock_with_pin.assert_called_once_with("1234")
        device.start_activity.assert_called_once_with(KHAN_KIDS_ACTIVITY)

    def test_secrets_file_must_be_owner_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".secrets.json"
            path.write_text('{"android_pin":"1234","khan_parent_password":"test"}\n')
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

            with self.assertRaisesRegex(AutomationError, "0600"):
                read_local_secrets(path)

    def test_owner_private_secrets_file_is_read_without_repr_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".secrets.json"
            path.write_text('{"android_pin":"1234","khan_parent_password":"test"}\n')
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)

            secrets = read_local_secrets(path)

            self.assertEqual(secrets.android_pin, "1234")
            self.assertEqual(secrets.khan_parent_password, "test")
            self.assertNotIn("1234", repr(secrets))
            self.assertNotIn("test", repr(secrets))


if __name__ == "__main__":
    unittest.main()
