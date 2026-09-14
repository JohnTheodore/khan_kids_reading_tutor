from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.report_archive import (
    assignment_dialog_close_rect,
    infer_dated_display,
    parse_progress_history,
    parse_skills_history,
    progress_history_close_rect,
    report_columns,
    report_rows,
)
from khan_report_archive_crawl import report_matrix


def node(root: ET.Element, text: str, bounds: str) -> None:
    ET.SubElement(
        root,
        "node",
        {"text": text, "bounds": bounds, "visible-to-user": "true"},
    )


class ReportArchiveTests(unittest.TestCase):
    def test_default_matrix_covers_core_books_and_video_bands(self) -> None:
        matrix = report_matrix(
            {
                "preschool-age-2",
                "preschool-age-3",
                "preschool-age-4",
                "kindergarten",
                "1st-grade",
                "2nd-grade",
            },
            ["ela", "math", "logic", "books", "videos"],
        )

        self.assertEqual(len(matrix), 22)
        self.assertIn(("books", "Kindergarten", "all-ages"), matrix)
        self.assertIn(("videos", "Kindergarten", "k-pre-k"), matrix)
        self.assertIn(("videos", "1st Grade", "1st-grade"), matrix)
        self.assertIn(("videos", "2nd Grade", "2nd-grade"), matrix)

    def test_report_columns_are_roster_driven(self) -> None:
        columns = report_columns(("Student C",))

        self.assertEqual(
            (columns[0].student, columns[0].left, columns[0].right), ("Student C", 570, 780)
        )

    def test_report_rows_parse_single_student_without_family_specific_code(self) -> None:
        root = ET.Element("hierarchy")
        node(root, "Title & Author", "[200,700][565,754]")
        node(root, "100%", "[574,700][781,754]")

        rows = report_rows(root, ("Student C",))

        self.assertEqual(rows[0]["results"]["Student C"]["percent"], 100)
        self.assertEqual(rows[0]["result_bounds"]["Student C"], [574, 700, 781, 754])

    def test_date_inference_records_year_uncertainty(self) -> None:
        normalized, method = infer_dated_display("Sun., Jan. 14", captured_on=date(2026, 9, 13))

        self.assertEqual(normalized, "2024-01-14")
        self.assertIn("year_not_displayed", method)

    def test_skills_history_propagates_grouped_display_date_and_duplicates(self) -> None:
        root = ET.Element("hierarchy")
        node(root, "Student C's Skills Scores", "[1021,509][1540,573]")
        node(root, "Title & Author", "[1140,625][1421,675]")
        node(root, "A5: ELA: Reading Foundational Skills", "[999,679][1563,759]")
        node(root, "Sun., Jan. 14", "[947,813][1133,853]")
        node(root, "Practice 1", "[1280,815][1418,855]")
        node(root, "100%", "[1535,815][1614,855]")
        node(root, "Main", "[1281,865][1349,905]")
        node(root, "100%", "[1535,865][1614,905]")
        ET.SubElement(
            root,
            "node",
            {
                "class": "android.view.ViewGroup",
                "text": "",
                "bounds": "[1634,429][1758,553]",
                "visible-to-user": "true",
            },
        )

        history = parse_skills_history(root, "Student C", captured_on=date(2026, 9, 13))

        self.assertEqual(history["lesson_title"], "Title & Author")
        self.assertEqual(len(history["attempts"]), 2)
        self.assertEqual(history["attempts"][1]["display_date"], "Sun., Jan. 14")
        self.assertEqual(history["attempts"][1]["score_percent"], 100)
        self.assertEqual(progress_history_close_rect(root, "Student C").center, (1696, 491))

    def test_lesson_history_preserves_repeated_view_events(self) -> None:
        root = ET.Element("hierarchy")
        node(root, "Student C's Lesson Scores", "[1000,443][1562,507]")
        node(root, "Jada's House", "[1155,559][1407,609]")
        node(root, "A5: BOK: Books:\nReading Adventures", "[1126,613][1435,693]")
        node(root, "Fri., Mar. 15", "[1089,897][1260,937]")
        node(root, "Viewed", "[1368,899][1472,939]")
        node(root, "Fri., Mar. 15", "[1089,947][1260,987]")
        node(root, "Viewed", "[1368,949][1472,989]")

        history = parse_progress_history(root, "Student C", captured_on=date(2026, 9, 13))

        self.assertEqual(len(history["attempts"]), 2)
        self.assertEqual(history["attempts"][0]["variant"], "Direct")
        self.assertEqual(history["attempts"][0]["result"], "Viewed")
        self.assertEqual(history["attempts"][1]["normalized_date"], "2024-03-15")

    def test_assignment_close_requires_verified_dialog(self) -> None:
        root = ET.Element("hierarchy")
        node(root, "Assign\nMakerspace", "[900,100][1600,170]")
        node(root, "Save", "[1920,201][2111,267]")
        ET.SubElement(
            root,
            "node",
            {
                "class": "android.view.ViewGroup",
                "text": "",
                "bounds": "[2197,42][2321,166]",
                "visible-to-user": "true",
            },
        )

        self.assertEqual(assignment_dialog_close_rect(root).center, (2259, 104))

    def test_video_history_allows_absent_curriculum_path(self) -> None:
        root = ET.Element("hierarchy")
        node(root, "Student C's Lesson Scores", "[1000,509][1562,573]")
        node(root, "If You're Happy", "[1137,625][1425,675]")
        node(root, "Mon., Oct. 23", "[1089,744][1291,784]")
        node(root, "Viewed", "[1368,747][1472,787]")

        history = parse_progress_history(root, "Student C", captured_on=date(2026, 9, 13))

        self.assertIsNone(history["curriculum_path"])
        self.assertEqual(history["lesson_title"], "If You're Happy")

    def test_direct_score_uses_percentage_as_attempt_event(self) -> None:
        root = ET.Element("hierarchy")
        node(root, "Student C's Skills Scores", "[1000,509][1562,573]")
        node(root, "Around the House", "[1100,625][1460,675]")
        node(root, "A2: ELA: Language", "[1050,679][1510,759]")
        node(root, "Mon., Oct. 23", "[1000,813][1200,853]")
        node(root, "100%", "[1368,815][1472,855]")

        history = parse_progress_history(root, "Student C", captured_on=date(2026, 9, 13))

        self.assertEqual(history["attempts"][0]["variant"], "Direct")
        self.assertEqual(history["attempts"][0]["score_percent"], 100)


if __name__ == "__main__":
    unittest.main()
