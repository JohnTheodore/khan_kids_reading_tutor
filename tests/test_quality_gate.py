from __future__ import annotations

import os
import unittest
from pathlib import Path


class QualityGateTests(unittest.TestCase):
    def test_ci_and_pre_push_share_the_utc_quality_gate(self) -> None:
        gate_path = Path("tools/check-before-push")
        gate = gate_path.read_text()
        hook = Path(".githooks/pre-push").read_text()
        workflow = Path(".github/workflows/tests.yml").read_text()

        self.assertTrue(os.access(gate_path, os.X_OK))
        self.assertIn("export TZ=UTC", gate)
        self.assertIn("-m unittest discover -s tests", gate)
        self.assertIn("tools/check_dashboard_browser.py", gate)
        self.assertIn("tools/audit_code_duplication.py", gate)
        self.assertIn("exec ./tools/check-before-push", hook)
        self.assertIn("audit_student_privacy.py --outgoing", hook)
        self.assertEqual(workflow.count("run: ./tools/check-before-push"), 1)


if __name__ == "__main__":
    unittest.main()
