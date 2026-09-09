from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.catalog import CatalogIndex


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CatalogIndex(Path("data/reading-ela-archive.json"))

    def test_curriculum_path_disambiguates_repeated_title(self) -> None:
        entry = self.catalog.find(
            "Blend Sounds 1",
            "Basic",
            "A4: ELA: Reading Foundational Skills: Phonological Awareness: Onset & Rime",
        )
        self.assertEqual(entry.grade, "Preschool (Age 4)")
        self.assertIn("Main", entry.variants)


if __name__ == "__main__":
    unittest.main()
