from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import LOCK_TASK_NONE, AndroidDevice, AutomationError
from khan_kids.cancellation import FileCancellationToken, WorkflowCancelled
from khan_kids.preflight import (
    assert_tablet_preflight,
    connectivity_is_validated,
    tablet_health,
)


class TabletPreflightTests(unittest.TestCase):
    def test_active_default_network_must_have_internet_and_validation(self) -> None:
        online = """
        Active default network: 103
        NetworkAgentInfo{ network{102} nc{INTERNET&VALIDATED}}
        NetworkAgentInfo{ network{103} nc{WIFI&INTERNET&VALIDATED}}
        """
        offline = """
        Active default network: 103
        NetworkAgentInfo{ network{102} nc{INTERNET&VALIDATED}}
        NetworkAgentInfo{ network{103} nc{WIFI&INTERNET}}
        """
        self.assertTrue(connectivity_is_validated(online))
        self.assertFalse(connectivity_is_validated(offline))
        self.assertFalse(connectivity_is_validated("Active default network: none"))

    def test_preflight_stops_before_app_navigation_when_offline(self) -> None:
        device = Mock(spec=AndroidDevice)
        device.is_locked.return_value = False
        device.ensure_screen_unpinned.return_value = False
        device.command.return_value = b"Active default network: none"
        with self.assertRaisesRegex(AutomationError, "no validated Internet"):
            assert_tablet_preflight(device)
        device.assert_connected.assert_called_once_with()
        self.assertEqual(device.command.call_count, 1)

    def test_health_reports_each_read_only_gate(self) -> None:
        device = Mock(spec=AndroidDevice)
        device.is_locked.return_value = False
        device.ensure_screen_unpinned.return_value = False
        device.command.side_effect = [
            b"Active default network: 7\nNetworkAgentInfo{ network{7} nc{INTERNET&VALIDATED}}",
            b"package:/data/app/base.apk\n",
        ]
        result = tablet_health(device)
        self.assertTrue(result["ready"])
        self.assertEqual(
            [check["id"] for check in result["checks"]],
            ["adb", "unlocked", "screen_pinning", "internet", "app"],
        )

    def test_dashboard_health_treats_sleep_as_automatic_unlock_ready(self) -> None:
        device = Mock(spec=AndroidDevice)
        device.is_locked.return_value = True
        device.ensure_screen_unpinned.return_value = False
        device.command.side_effect = [
            b"Active default network: 7\nNetworkAgentInfo{ network{7} nc{INTERNET&VALIDATED}}",
            b"package:/data/app/base.apk\n",
        ]

        result = tablet_health(device, allow_automatic_unlock=True)

        self.assertTrue(result["ready"])
        self.assertTrue(result["automatic_unlock_required"])
        self.assertEqual(result["checks"][1]["label"], "Tablet sleeping · automatic unlock ready")

    def test_screen_pinning_is_repaired_before_network_and_app_checks(self) -> None:
        device = Mock(spec=AndroidDevice)
        device.is_locked.return_value = False
        device.ensure_screen_unpinned.return_value = True
        device.command.side_effect = [
            b"Active default network: 7\nNetworkAgentInfo{ network{7} nc{INTERNET&VALIDATED}}",
            b"package:/data/app/base.apk\n",
            b"Active default network: 7\nNetworkAgentInfo{ network{7} nc{INTERNET&VALIDATED}}",
            b"package:/data/app/base.apk\n",
        ]

        assert_tablet_preflight(device)
        health = tablet_health(device)

        self.assertTrue(health["ready"])
        self.assertTrue(health["screen_pinning_recovered"])
        self.assertEqual(health["lock_task_mode"], LOCK_TASK_NONE)
        self.assertEqual(health["checks"][2]["id"], "screen_pinning")

    def test_failed_automatic_unpin_stops_before_network_or_app_navigation(self) -> None:
        device = Mock(spec=AndroidDevice)
        device.is_locked.return_value = False
        device.ensure_screen_unpinned.side_effect = AutomationError("automatic unpin failed")

        with self.assertRaisesRegex(AutomationError, "automatic unpin failed"):
            assert_tablet_preflight(device)
        device.command.assert_not_called()

    def test_durable_cancellation_blocks_normal_commands_but_allows_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stop"
            token = FileCancellationToken(path)
            device = AndroidDevice("synthetic", cancellation_check=token.check)
            path.write_text("stop\n")
            with self.assertRaises(WorkflowCancelled):
                device.command("get-state")
            with patch("khan_kids.adb.run_command", return_value=b"device") as command:
                with device.cleanup_mode():
                    self.assertEqual(device.command("get-state", capture=True), b"device")
                command.assert_called_once()


if __name__ == "__main__":
    unittest.main()
