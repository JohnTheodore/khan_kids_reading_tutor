from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AndroidDevice


class AndroidDeviceTests(unittest.TestCase):
    def test_awake_session_restores_settings_after_failure(self) -> None:
        device = AndroidDevice("test-device")
        with patch.object(device, "command") as command:
            command.side_effect = [b"120000\n", b"0\n", b"", b"", b"", b"", b""]
            with self.assertRaisesRegex(RuntimeError, "test failure"), device.awake_session():
                raise RuntimeError("test failure")

        self.assertEqual(
            command.call_args_list[-2].args,
            ("shell", "settings", "put", "system", "screen_off_timeout", "120000"),
        )
        self.assertEqual(
            command.call_args_list[-1].args,
            ("shell", "settings", "put", "global", "stay_on_while_plugged_in", "0"),
        )


if __name__ == "__main__":
    unittest.main()
