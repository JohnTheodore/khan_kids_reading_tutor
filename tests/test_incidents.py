from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.incidents import append_failed_sync_incident
from reading_workflow import cli


class IncidentTests(unittest.TestCase):
    def test_failed_sync_incident_is_append_only_and_redacts_common_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "INCIDENTS.md"
            path.write_text("# Incident log\n")
            incident_id = append_failed_sync_incident(
                path,
                student="Student A",
                error=RuntimeError(
                    "device 192.0.2.10:45535 code 654321 /home/example-user/private"
                ),
                detected_at=datetime(2026, 9, 11, 16, 2, 3, 456789),
            )
            report = path.read_text()

        self.assertEqual(incident_id, "KKRT-2026-09-11-AUTO-160203-456789")
        self.assertTrue(report.startswith("# Incident log\n"))
        self.assertNotIn("192.0.2.10", report)
        self.assertNotIn("654321", report)
        self.assertNotIn("/home/example-user", report)
        self.assertIn("[redacted device]", report)
        self.assertIn("[redacted numeric secret]", report)

    def test_mastery_sync_failure_automatically_appends_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "INCIDENTS.md"
            with (
                patch("reading_workflow.main", side_effect=RuntimeError("broken app state")),
                patch("reading_workflow.INCIDENT_LOG_PATH", path),
                patch("sys.argv", ["reading_workflow.py", "--sync", "--student", "Student A"]),
                redirect_stderr(StringIO()) as stderr,
                self.assertRaisesRegex(SystemExit, "2"),
            ):
                cli()

            report = path.read_text()

        self.assertIn("Affected student | Student A", report)
        self.assertIn("RuntimeError: broken app state", report)
        self.assertIn("Incident recorded: KKRT-", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
