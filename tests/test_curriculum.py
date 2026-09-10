from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.catalog import CatalogIndex
from khan_kids.curriculum import ReadingCurriculum


class CurriculumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CatalogIndex(Path("data/reading-ela-archive.json"))

    def test_every_configured_activity_exists_in_the_archive(self) -> None:
        curriculum = ReadingCurriculum.load(Path("data/reading-curriculum.json"), self.catalog)

        self.assertEqual(curriculum.path_id, "minimum-viable-reading-path-v1")
        self.assertIn("independent decoding", curriculum.objective)
        self.assertTrue(curriculum.entry_criteria)
        self.assertTrue(curriculum.segment_exit_criteria)
        self.assertEqual(curriculum.queue_limit, 10)
        self.assertEqual(len(curriculum.diversity_groups), 1)
        self.assertEqual(curriculum.diversity_groups[0].max_active, 3)
        self.assertEqual(curriculum.stretch_slots, 2)
        self.assertEqual(len(curriculum.stretch_pool), 5)
        self.assertEqual(
            curriculum.stretch_pool[0].activities[0].key, ("Words with f, g, h", "Main")
        )
        self.assertEqual(len(curriculum.tracks), 15)
        self.assertEqual(sum(len(track.activities) for track in curriculum.tracks), 83)

    def test_dependency_cycles_are_rejected(self) -> None:
        payload = json.loads(Path("data/reading-curriculum.json").read_text())
        payload["tracks"][0]["requires"] = [payload["tracks"][0]["id"]]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "curriculum.json"
            path.write_text(json.dumps(payload))

            with self.assertRaisesRegex(ValueError, "cannot require itself"):
                ReadingCurriculum.load(path, self.catalog)

    def test_path_metadata_is_required(self) -> None:
        payload = json.loads(Path("data/reading-curriculum.json").read_text())
        del payload["objective"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "curriculum.json"
            path.write_text(json.dumps(payload))

            with self.assertRaisesRegex(ValueError, "objective"):
                ReadingCurriculum.load(path, self.catalog)

    def test_duplicate_activity_configuration_is_rejected(self) -> None:
        payload = json.loads(Path("data/reading-curriculum.json").read_text())
        payload["tracks"][0]["lesson_groups"].append(payload["tracks"][0]["lesson_groups"][0])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "curriculum.json"
            path.write_text(json.dumps(payload))

            with self.assertRaisesRegex(ValueError, "duplicated"):
                ReadingCurriculum.load(path, self.catalog)

    def test_track_cannot_belong_to_multiple_diversity_groups(self) -> None:
        payload = json.loads(Path("data/reading-curriculum.json").read_text())
        payload["diversity_groups"].append(
            {"id": "overlap", "max_active": 2, "track_ids": ["short_a_cvc_middle"]}
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "curriculum.json"
            path.write_text(json.dumps(payload))

            with self.assertRaisesRegex(ValueError, "multiple diversity groups"):
                ReadingCurriculum.load(path, self.catalog)


if __name__ == "__main__":
    unittest.main()
