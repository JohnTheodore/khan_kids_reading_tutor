from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.reports import parse_assignment_rows, parse_score_history


def hierarchy(*nodes: tuple[str, str]) -> ET.Element:
    root = ET.Element("hierarchy")
    parent = ET.SubElement(root, "node", bounds="[0,0][2560,1600]", text="")
    for text, bounds in nodes:
        ET.SubElement(parent, "node", text=text, bounds=bounds)
    return root


class ReportTests(unittest.TestCase):
    def test_assignment_row_is_joined_by_vertical_band(self) -> None:
        root = hierarchy(
            ("Class Report: Assignments", "[800,20][1750,90]"),
            ("Words: End Sound", "[235,433][574,474]"),
            ("Today", "[235,476][574,510]"),
            ("Main", "[105,488][192,510]"),
            ("92%", "[575,476][783,510]"),
        )
        rows = parse_assignment_rows(root, "Student A", roster=("Student A", "Student B"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0].title, rows[0].variant, rows[0].score), ("Words: End Sound", "Main", 92)
        )

    def test_assignment_row_tolerates_render_rounding(self) -> None:
        root = hierarchy(
            ("Class Report: Assignments", "[800,20][1750,90]"),
            ("Beginning Sounds 2", "[236,417][575,438]"),
            ("Yesterday", "[236,440][575,474]"),
            ("Basic", "[106,452][193,474]"),
            ("70%", "[576,440][781,476]"),
        )
        rows = parse_assignment_rows(root, "Student A", roster=("Student A",))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].score, 70)

    def test_history_parses_colon_in_title_and_relative_dates(self) -> None:
        root = hierarchy(
            ("Student A's Lesson Scores", "[984,509][1577,573]"),
            ("Words: End Sound: Basic", "[1000,625][1550,675]"),
            (
                "A4: ELA: Reading Foundational Skills:\nPhonological Awareness: Three-Phoneme Words",
                "[984,679][1577,759]",
            ),
            ("Today", "[1089,813][1244,853]"),
            ("100%", "[1412,815][1473,855]"),
            ("Yesterday", "[1089,863][1244,903]"),
            ("80%", "[1412,865][1473,905]"),
        )
        history = parse_score_history(
            root,
            "Student A",
            today=date(2026, 9, 9),
            assigned_display_date="Yesterday",
        )
        self.assertEqual((history.title, history.variant), ("Words: End Sound", "Basic"))
        self.assertEqual(history.assigned_date, date(2026, 9, 8))
        self.assertEqual(
            [(attempt.attempt_date, attempt.score) for attempt in history.attempts_newest_first],
            [(date(2026, 9, 9), 100), (date(2026, 9, 8), 80)],
        )

    def test_history_accepts_app_punctuation_and_spacing(self) -> None:
        root = hierarchy(
            ("Student A's Lesson Scores", "[984,509][1577,573]"),
            ("Blend Sounds 2: Basic", "[1000,625][1550,675]"),
            (
                "A4: ELA: Reading Foundational Skills:\nPhonological Awareness: Three-Phoneme Words",
                "[910,679][1651,759]",
            ),
            ("Tue., Sep.  1", "[1089,863][1265,903]"),
            ("85%", "[1414,865][1473,905]"),
        )
        history = parse_score_history(
            root,
            "Student A",
            today=date(2026, 9, 9),
            assigned_display_date="Yesterday",
        )
        self.assertEqual(history.attempts_newest_first[0].attempt_date, date(2026, 9, 1))


if __name__ == "__main__":
    unittest.main()
