from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.automation import ActionResult, KhanKidsAutomation
from khan_kids.reports import AssignmentRow
from khan_kids.ui import Rect


class AutomationTests(unittest.TestCase):
    def test_bulk_unassignment_uses_one_traversal_and_bottom_first(self) -> None:
        device = Mock()
        automation = KhanKidsAutomation(
            device,
            student="Student A",
            roster=("Student A", "Student B"),
            scratch=Path("/tmp/not-used"),
        )
        upper = _row("Upper", 500)
        lower = _row("Lower", 900)
        automation.ensure_assignments_report = Mock()
        automation._filter_assignments_to_student = Mock()
        automation._scroll_to_top = Mock()
        automation.root = Mock(return_value=ET.Element("hierarchy"))
        automation._unassign_row = Mock(
            side_effect=lambda _row, title, variant: ActionResult(
                "unchecked", title, variant, "saved"
            )
        )

        with patch("khan_kids.automation.parse_assignment_rows", return_value=[upper, lower]):
            results = tuple(automation.unassign_many((("Upper", "Basic"), ("Lower", "Basic"))))

        self.assertEqual([result.title for result in results], ["Lower", "Upper"])
        automation.ensure_assignments_report.assert_called_once_with()
        automation._scroll_to_top.assert_called_once_with()
        device.swipe.assert_not_called()


def _row(title: str, top: int) -> AssignmentRow:
    return AssignmentRow(
        title=title,
        variant="Basic",
        assigned_date="Today",
        rect=Rect(0, top, 700, top + 80),
        score=None,
        score_rect=None,
    )


if __name__ == "__main__":
    unittest.main()
