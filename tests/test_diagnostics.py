from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.diagnostics import (
    MAX_DIAGNOSTIC_RUNS,
    DiagnosticRun,
    new_run_id,
    sanitize_diagnostic_text,
    validate_run_id,
)


class FakeDevice:
    def __init__(self, text: str = "Assignments") -> None:
        self.root = ET.fromstring(
            f'<hierarchy><node text="{text}" bounds="[0,0][100,100]" /></hierarchy>'
        )
        self.screenshots = 0

    def hierarchy(self):
        return self.root

    def screenshot(self, destination: Path) -> None:
        self.screenshots += 1
        destination.write_bytes(b"private screenshot")

    def command(self, *args, capture=False):
        return b"device 192.0.2.1:45678 code 123456 /home/person/file"

    def lock_task_mode(self):
        return "pinned"


class DiagnosticRunTests(unittest.TestCase):
    def test_generated_run_id_is_valid_in_every_timezone(self) -> None:
        run_id = new_run_id()
        self.assertEqual(validate_run_id(run_id), run_id)
        self.assertIn("Z-", run_id)
        self.assertNotIn("+", run_id)

    def test_failure_bundle_is_correlated_private_redacted_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = DiagnosticRun(root, "sync-test", student="Student A")
            device = FakeDevice()
            try:
                raise RuntimeError("failed at 192.0.2.2:34567 with 654321")
            except RuntimeError as error:
                run.capture_device_failure(device, error)
                run.record_failure(error, payload={"run_id": "sync-test"})
            run.append_output("x" * 300_000)

            manifest = json.loads((run.path / "run.json").read_text())
            combined = (run.path / "error.txt").read_text() + (
                run.path / "android-logcat.txt"
            ).read_text()

            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(
                json.loads((run.path / "result.json").read_text())["run_id"], "sync-test"
            )
            self.assertTrue((run.path / "failure.xml").exists())
            self.assertTrue((run.path / "failure.png").exists())
            self.assertTrue((run.path / "traceback.txt").exists())
            self.assertEqual(
                json.loads((run.path / "android-state.json").read_text()),
                {"lock_task_mode": "pinned"},
            )
            self.assertNotIn("192.0.2", combined)
            self.assertNotIn("654321", combined)
            self.assertLessEqual((run.path / "output.log").stat().st_size, 250_000)
            self.assertEqual(run.path.stat().st_mode & 0o777, 0o700)
            self.assertTrue(
                all(path.stat().st_mode & 0o777 == 0o600 for path in run.path.iterdir())
            )

    def test_password_dialog_never_captures_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = DiagnosticRun(Path(temporary), "sync-password")
            device = FakeDevice("Enter Password")
            run.capture_device_failure(device, RuntimeError("blocked"))
            self.assertEqual(device.screenshots, 0)
            self.assertFalse((run.path / "failure.xml").exists())
            self.assertFalse((run.path / "failure.png").exists())

    def test_old_run_directories_are_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(MAX_DIAGNOSTIC_RUNS + 3):
                DiagnosticRun(root, f"sync-{index:03d}")
            runs = list((root / "private/sync-runs").iterdir())
            self.assertEqual(len(runs), MAX_DIAGNOSTIC_RUNS)
            self.assertFalse((root / "private/sync-runs/sync-000").exists())

    def test_sanitizer_preserves_context_without_local_secrets(self) -> None:
        result = sanitize_diagnostic_text(
            "ADB 192.0.2.1:45678 pin 123456 at /home/private-user/project"
        )
        self.assertEqual(
            result,
            "ADB [redacted device] pin [redacted numeric secret] at /home/[redacted]/project",
        )


if __name__ == "__main__":
    unittest.main()
