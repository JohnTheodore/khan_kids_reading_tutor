from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.device_discovery import (
    DeviceConfig,
    DeviceDiscoveryError,
    is_network_endpoint,
    parse_adb_devices,
    parse_browse_instances,
    parse_host_ipv4,
    parse_service_lookup,
    resolve_device,
)


class DeviceDiscoveryTests(unittest.TestCase):
    def test_loads_private_device_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "device.json"
            path.write_text(
                json.dumps(
                    {
                        "student": "Student A",
                        "hardware_serial": "TABLET123",
                        "model": "Pixel Tablet",
                    }
                )
            )
            self.assertEqual(
                DeviceConfig.load(path),
                DeviceConfig("Student A", "TABLET123", "Pixel Tablet"),
            )

    def test_rejects_invalid_hardware_serial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "device.json"
            path.write_text(
                json.dumps(
                    {"student": "Student A", "hardware_serial": "bad value", "model": "Pixel"}
                )
            )
            with self.assertRaisesRegex(DeviceDiscoveryError, "unsupported"):
                DeviceConfig.load(path)

    def test_parses_online_and_offline_adb_devices(self) -> None:
        output = "List of devices attached\nold:123 offline\nnew:456 device product:test\n"
        self.assertEqual(parse_adb_devices(output), [("old:123", "offline"), ("new:456", "device")])

    def test_recognizes_only_ip_port_network_endpoints(self) -> None:
        self.assertTrue(is_network_endpoint("192.0.2.10:41234"))
        self.assertTrue(is_network_endpoint("[2001:db8::10]:41234"))
        self.assertFalse(is_network_endpoint("emulator-5554"))
        self.assertFalse(is_network_endpoint("USBDEVICE123"))

    def test_selects_only_matching_tls_service(self) -> None:
        output = """
12:00 Add 2 14 local. _adb-tls-connect._tcp. adb-OTHER-abcd
12:00 Add 2 14 local. _adb-tls-connect._tcp. adb-TABLET123-efgh
"""
        self.assertEqual(parse_browse_instances(output, "TABLET123"), ["adb-TABLET123-efgh"])

    def test_parses_service_hostname_and_port(self) -> None:
        output = (
            "adb-TABLET123-efgh._adb-tls-connect._tcp.local. "
            "can be reached at Android_EXAMPLE.local.:41234 (interface 14)\n"
        )
        self.assertEqual(parse_service_lookup(output), ("Android_EXAMPLE.local", 41234))

    def test_requires_one_ipv4_address(self) -> None:
        self.assertEqual(
            parse_host_ipv4("name: tablet.local\nip_address: 192.0.2.10\n"), "192.0.2.10"
        )
        with self.assertRaisesRegex(DeviceDiscoveryError, "expected exactly one"):
            parse_host_ipv4("name: tablet.local\n")

    @patch("khan_kids.device_discovery._run")
    @patch("khan_kids.device_discovery._verify_device", return_value=True)
    @patch("khan_kids.device_discovery._discover_endpoint", return_value="192.0.2.10:41234")
    @patch("khan_kids.device_discovery._already_connected", return_value=None)
    @patch("khan_kids.device_discovery.ensure_current_adb_server")
    def test_evicts_stale_endpoint_before_reconnect(
        self,
        ensure_server,
        already_connected,
        discover_endpoint,
        verify_device,
        run,
    ) -> None:
        config = DeviceConfig("Student A", "TABLET123", "Pixel Tablet")
        self.assertEqual(resolve_device(config), "192.0.2.10:41234")
        run.assert_any_call(["adb", "disconnect", "192.0.2.10:41234"], allowed_codes=(0, 1))
        run.assert_any_call(["adb", "connect", "192.0.2.10:41234"], timeout=15)
        ensure_server.assert_called_once_with()
        already_connected.assert_called_once_with(config)
        discover_endpoint.assert_called_once_with(config)
        verify_device.assert_called_once_with("192.0.2.10:41234", config)


if __name__ == "__main__":
    unittest.main()
