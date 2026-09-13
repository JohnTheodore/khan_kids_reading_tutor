from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AutomationError
from khan_kids.workflow_lock import exclusive_workflow_lock


class WorkflowLockTests(unittest.TestCase):
    def test_second_workflow_fails_while_first_holds_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow.lock"
            with (
                exclusive_workflow_lock(path),
                self.assertRaisesRegex(AutomationError, "already running"),
                exclusive_workflow_lock(path),
            ):
                self.fail("second workflow unexpectedly acquired the lock")


if __name__ == "__main__":
    unittest.main()
