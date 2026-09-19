from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from dashboard import SyncJob, reading_profiles
from khan_kids.catalog import CatalogIndex
from khan_kids.curriculum import Activity
from khan_kids.manual_assignments import ManualAssignments, ManualChange, policy_path
from khan_kids.reading_journey import (
    ARCHIVE_HISTORY_FIELDS,
    MILESTONES,
    PHASES,
    build_journey,
    catalog,
)
from khan_kids.records import (
    ACTION_FIELDS,
    ATTEMPT_FIELDS,
    read_attempt_records,
    read_attempt_scores,
)

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 17)


class ReadingJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        archive = json.loads((ROOT / "data/reading-ela-archive.json").read_text())
        for grade in archive["grades"]:
            for lesson in grade["lesson_placements"]:
                for activity in lesson["activities"]:
                    activity["results"] = {}
        (self.root / "data/reading-ela-archive.json").write_text(json.dumps(archive))
        (self.root / "data/letters-lessons.json").symlink_to(ROOT / "data/letters-lessons.json")
        (self.root / "student-records").mkdir()
        self.path = self.root / "student-records/student-a-lesson-attempts.csv"

    def attempts(
        self, values: list[tuple], title: str = "Short Vowel Sound a", variant: str = "Main"
    ) -> None:
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ATTEMPT_FIELDS)
            writer.writeheader()
            for day, score in values:
                writer.writerow(
                    {
                        "student": "Student A",
                        "lesson_title": title,
                        "activity_variant": variant,
                        "attempt_date": day,
                        "score_percent": score,
                    }
                )

    def journey(self, **kwargs) -> dict:
        return build_journey(self.root, "Student A", today=TODAY, **kwargs)

    def activity(self, journey: dict, title="Short Vowel Sound a", variant="Main") -> dict:
        family = next(f for m in journey["milestones"] for f in m["lessons"] if f["title"] == title)
        return next(a for a in family["activities"] if a["variant"] == variant)

    def test_catalog_deduplicates_grade_placements_and_has_both_alphabets(self) -> None:
        self.attempts([])
        journey = self.journey()
        letters = journey["milestones"][:2]
        self.assertEqual([m["total"] for m in letters], [26, 26])
        families = catalog(self.root, 0, 0)
        self.assertEqual(len(families), len({f["title"] for f in families}))
        self.assertEqual(len(journey["milestones"]), 18)
        self.assertGreater(
            len(next(f for f in families if f["title"] == "Short Vowel Sound a")["contexts"]), 1
        )

    def test_assignment_and_mastery_are_separate_per_exact_variant(self):
        self.attempts([("2026-09-17", 100)])
        catalog_index = CatalogIndex(self.root / "data/reading-ela-archive.json")
        main = Activity("Kindergarten", "Short Vowel Sound a", "Main")
        practice = Activity("Kindergarten", "Short Vowel Sound a", "Practice 1")
        policy = ManualAssignments("Student A").changed(ManualChange(main, "assign"))
        policy = policy.changed(ManualChange(practice, "unassign"))
        policy.save(policy_path(self.root, "Student A"))
        report = {
            "queue_count": 1,
            "assigned": [{"title": main.title, "variant": main.variant}],
            "timestamp": "2026-09-17",
        }
        journey = self.journey(report=report)
        assigned = self.activity(journey)
        self.assertEqual(assigned["state"], "mastered")
        self.assertEqual(assigned["assignment_status"], "assigned")
        self.assertTrue(assigned["manual_assignment"])
        self.assertEqual(assigned["grade"], "Kindergarten")
        unassigned = self.activity(journey, variant="Practice 1")
        self.assertEqual(unassigned["state"], "not_assessed")
        self.assertEqual(unassigned["assignment_status"], "unassigned")
        self.assertTrue(unassigned["automatic_assignment_paused"])
        self.assertEqual(self.journey()["assignment_verified_at"], None)
        self.assertEqual(self.activity(self.journey())["assignment_status"], "unknown")
        self.assertEqual(
            self.activity(self.journey(report={"queue_count": 0, "assigned": []}))[
                "assignment_status"
            ],
            "unassigned",
        )
        for milestone in journey["milestones"]:
            for lesson in milestone["lessons"]:
                for activity in lesson["activities"]:
                    self.assertIn(activity["state"], {"mastered", "practicing", "not_assessed"})
                    if activity["grade"]:
                        catalog_index.find_exact(
                            activity["grade"], lesson["title"], activity["variant"]
                        )

    def test_same_day_occurrences_establish_mastery_without_collapse(self) -> None:
        self.attempts([("2026-09-17", 94), ("2026-09-17", 94)])
        journey = self.journey()
        self.assertEqual(journey["weekly_attempts"], 2)
        self.assertEqual(journey["weekly_mastered"], 1)
        self.assertEqual(self.activity(journey)["scores"], [94, 94])
        self.assertEqual(
            read_attempt_scores(self.path, "Student A")[("Short Vowel Sound a", "Main")], (94, 94)
        )

    def snapshot(self, title: str, scores: list[int]) -> None:
        path = self.root / "data/reading-ela-archive.json"
        archive = json.loads(path.read_text())
        for grade in archive["grades"]:
            for lesson in grade["lesson_placements"]:
                if lesson["title"] == title:
                    for activity in lesson["activities"]:
                        if activity["variant"] == "Main":
                            activity["results"]["Student A"] = {
                                "status": "scored",
                                "percent": scores.pop(0) if len(scores) > 1 else scores[0],
                            }
        path.write_text(json.dumps(archive))

    def test_saved_scores_cover_letters_and_other_lessons_without_fake_attempts(self) -> None:
        self.snapshot("Lowercase a", [100])
        self.snapshot("Short Vowel Sound a", [100])
        journey = self.journey()
        self.assertTrue(journey["available"])
        self.assertEqual(journey["mastered_families"], 2)
        self.assertEqual(journey["weekly_attempts"], 0)
        self.assertEqual(journey["weekly_mastered"], 0)
        self.assertEqual(self.activity(journey)["attempts"], [])
        self.assertIsNone(self.activity(journey)["first_mastery_date"])

    def test_summary_scores_cannot_establish_a_consecutive_pair(self) -> None:
        self.snapshot("Short Vowel Sound a", [94, 94])
        self.assertEqual(self.activity(self.journey())["state"], "practicing")

    def test_old_snapshot_prevents_falsely_recent_mastery(self) -> None:
        self.snapshot("Short Vowel Sound a", [100])
        self.attempts([("2026-09-17", 100)])
        self.assertEqual(self.journey()["weekly_mastered"], 0)

    def test_custom_history_does_not_borrow_native_snapshot_scores(self) -> None:
        self.snapshot("Short Vowel Sound a", [100])
        self.attempts([])
        self.assertEqual(self.journey(profile={"attempts": str(self.path)})["mastered_families"], 0)

    def test_mastery_is_monotonic_and_weekly_uses_first_qualification(self) -> None:
        self.attempts([("2026-09-01", 100), ("2026-09-17", 60)])
        journey = self.journey()
        self.assertEqual(self.activity(journey)["state"], "mastered")
        self.assertEqual(self.activity(journey)["first_mastery_score"], 100)
        self.assertEqual(journey["weekly_mastered"], 0)
        self.assertEqual(journey["weekly_attempts"], 1)

    def test_lower_scores_and_single_94_are_not_mastered(self) -> None:
        self.attempts([("2026-09-17", 94)])
        self.assertEqual(self.activity(self.journey())["state"], "practicing")
        self.assertEqual(self.journey()["mastered_families"], 0)

    def test_basic_only_does_not_certify_family_or_reading_level(self) -> None:
        self.attempts([("2026-09-17", 100)], variant="Basic")
        journey = self.journey()
        self.assertEqual(journey["mastered_families"], 0)
        self.assertEqual(journey["reading_level"], "Not assessed")
        self.assertFalse(journey["forecast"]["available"])

    def test_missing_history_is_unknown_not_zero_proficiency(self) -> None:
        journey = self.journey()
        self.assertFalse(journey["available"])
        self.assertEqual(journey["milestones"], [])
        self.assertTrue(journey["warnings"])

    def test_empty_history_does_not_invent_skipped_letter_mastery(self) -> None:
        self.attempts([])
        journey = self.journey()
        self.assertTrue(journey["available"])
        self.assertTrue(all(m["state"] == "not_assessed" for m in journey["milestones"]))

    def test_week_boundary_and_future_attempts_excluded_from_weekly(self) -> None:
        self.attempts(
            [("2026-09-10", 84), ("2026-09-11", 84), ("2026-09-17", 84), ("2026-09-18", 84)]
        )
        self.assertEqual(self.journey()["weekly_attempts"], 2)

    def test_malformed_records_fail_closed_without_returning_paths(self) -> None:
        self.path.write_text("secret_field\nsecret_value\n")
        result = self.journey()
        self.assertFalse(result["available"])
        self.assertNotIn(str(self.path), json.dumps(result))
        self.assertNotIn("secret_value", json.dumps(result))

    def test_score_validation_is_shared_with_native_loader(self) -> None:
        self.attempts([("2026-09-17", 101)])
        with self.assertRaises(ValueError):
            read_attempt_records(self.path, "Student A")
        self.assertFalse(self.journey()["available"])

    def test_durable_mastery_with_missing_history_remains_evidence_not_weekly_gain(self) -> None:
        self.attempts([])
        path = self.root / "student-records/student-a-assignment-actions.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ACTION_FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "student": "Student A",
                    "lesson_title": "Short Vowel Sound a",
                    "activity_variant": "Main",
                    "action": "unchecked",
                    "reason": "mastered: latest attempt is 100%",
                    "result": "saved",
                }
            )
        journey = self.journey()
        self.assertEqual(self.activity(journey)["state"], "mastered")
        self.assertEqual(journey["weekly_mastered"], 0)

    def test_archive_placement_dedup_preserves_occurrences_and_uncertain_years(self) -> None:
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ARCHIVE_HISTORY_FIELDS)
            writer.writeheader()
            for occurrence in (1, 2, 1, 2):
                writer.writerow(
                    {
                        "student": "Student A",
                        "subject": "ela",
                        "lesson_title": "Short Vowel Sound a",
                        "activity_variant": "Main",
                        "normalized_date": "2026-09-17",
                        "date_resolution": "year_not_displayed_by_app",
                        "score_percent": 94,
                        "occurrence": occurrence,
                    }
                )
        journey = self.journey(
            profile={"attempts": str(self.path), "format": "archive", "archived": True}
        )
        self.assertEqual(self.activity(journey)["scores"], [94, 94])
        self.assertEqual(journey["mastered_families"], 1)
        self.assertEqual(journey["weekly_mastered"], 0)
        self.assertEqual(journey["weekly_attempts"], 0)
        self.assertTrue(journey["warnings"])
        self.assertEqual(journey["recommendations"], [])

    def test_custom_engine_does_not_read_native_history(self) -> None:
        job = SyncJob(self.root, self.root / "custom-engine")
        with patch("dashboard.family_journeys") as read:
            result = job.journeys(["Student A"])
        read.assert_not_called()
        self.assertEqual(result["readers"], [])

    def test_profile_config_rejects_public_permissions_and_unregistered_students(self) -> None:
        directory = self.root / "private"
        directory.mkdir()
        path = directory / "dashboard-profiles.local.json"
        path.write_text('{"Student A":{"archived":true}}')
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            reading_profiles(self.root, ["Student A"])
        path.chmod(0o600)
        self.assertTrue(reading_profiles(self.root, ["Student A"])["Student A"]["archived"])
        with self.assertRaises(ValueError):
            reading_profiles(self.root, ["Student B"])

    def test_unknown_variant_is_not_counted_as_mapped_reading_practice(self) -> None:
        self.attempts([("2026-09-17", 100)], variant="Unknown variant")
        result = self.journey()
        self.assertEqual(result["unmapped_attempts"], 1)
        self.assertEqual(result["weekly_attempts"], 0)

    def test_profile_bad_field_types_fail_closed(self) -> None:
        private = self.root / "private"
        private.mkdir()
        path = private / "dashboard-profiles.local.json"
        for bad in ({"format": []}, {"attempts": 1}, {"archived": "yes"}, {"format": "archive"}):
            path.write_text(json.dumps({"Student A": bad}))
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                reading_profiles(self.root, ["Student A"])

    def test_undated_scores_cannot_establish_a_consecutive_pair(self) -> None:
        from khan_kids.reading_journey import _evidence

        attempts = [
            {
                "title": "Example",
                "variant": "Main",
                "date": None,
                "score": 94,
                "date_confidence": "unknown",
            },
            {
                "title": "Example",
                "variant": "Main",
                "date": "2026-09-17",
                "score": 94,
                "date_confidence": "exact",
            },
        ]
        result = _evidence(attempts, False)
        self.assertEqual(result["state"], "practicing")
        self.assertNotIn("two consecutive", result["reason"])

    def archive_history(
        self,
        title: str,
        variant: str = "Main",
        *,
        grade="1st-grade",
        skill="Final -e & Long Vowel Teams",
        score=100,
    ) -> None:
        fields = (*ARCHIVE_HISTORY_FIELDS, "grade", "curriculum_path")
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(
                {
                    "student": "Student A",
                    "subject": "ela",
                    "lesson_title": title,
                    "activity_variant": variant,
                    "normalized_date": "2026-09-17",
                    "date_resolution": "exact",
                    "score_percent": score,
                    "occurrence": 1,
                    "grade": grade,
                    "curriculum_path": "G1: ELA: Reading Foundational Skills: " + skill,
                }
            )

    def direct_inventory(
        self,
        *,
        title="Long U: ui, ue",
        grade="1st-grade",
        skill="Final -e & Long Vowel Teams",
        student="Student A",
    ) -> None:
        fields = ("student", "subject", "grade", "skill_group", "lesson_title", "activity_variant")
        with self.path.with_name("lesson-inventory.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(
                {
                    "student": student,
                    "subject": "ela",
                    "grade": grade,
                    "skill_group": skill,
                    "lesson_title": title,
                    "activity_variant": "Direct",
                }
            )

    def test_exact_catalog_headings_cover_print_spelling_and_supporting_skills(self) -> None:
        self.attempts([])
        journey = self.journey()
        by_id = {m["id"]: m for m in journey["milestones"]}
        for key in ("print", "spelling", "vocabulary", "language", "writing"):
            self.assertGreater(by_id[key]["total"], 0)
            self.assertEqual(by_id[key]["state"], "not_assessed")
        archive = json.loads((self.root / "data/reading-ela-archive.json").read_text())
        heading_ids = {heading: item[0] for item in MILESTONES for heading in item[3]}
        for grade in archive["grades"]:
            for placement in grade["lesson_placements"]:
                key = heading_ids.get(placement["skill_group"])
                if key:
                    self.assertIn(placement["title"], {f["title"] for f in by_id[key]["lessons"]})

    def test_previously_omitted_scored_headings_are_accounted_for(self) -> None:
        families = catalog(self.root, 0, 0)
        headings = {"print", "spelling", "vocabulary", "language", "writing"}
        for milestone_id in headings:
            family = next(f for f in families if milestone_id in f["milestones"] and f["variants"])
            variant = next((v for v in family["variants"] if v != "Basic"), family["variants"][0])
            self.attempts([("2026-09-17", 100)], title=family["title"], variant=variant)
            journey = self.journey()
            self.assertEqual(journey["mapped_attempts"], 1)
            self.assertEqual(journey["unmapped_attempts"], 0)

    def test_phases_are_canonical_and_deduplicate_shared_topics(self) -> None:
        path = self.root / "data/reading-ela-archive.json"
        archive = json.loads(path.read_text())
        placement = next(
            p
            for g in archive["grades"]
            for p in g["lesson_placements"]
            if p["title"] == "Lowercase a"
        )
        copy = dict(placement, skill_group="Print Concepts: Words")
        archive["grades"][0]["lesson_placements"].append(copy)
        path.write_text(json.dumps(archive))
        self.attempts([("2026-09-17", 100)], title="Lowercase a")
        journey = self.journey()
        self.assertEqual([p["id"] for p in journey["phases"]], [p[0] for p in PHASES])
        self.assertEqual(len([p for p in journey["phases"] if not p["supporting"]]), 6)
        milestone_ids = [key for p in journey["phases"] for key in p["milestone_ids"]]
        self.assertEqual(len(milestone_ids), len(set(milestone_ids)))
        self.assertEqual(set(milestone_ids), {m["id"] for m in journey["milestones"]})
        phase = journey["phases"][0]
        items = {
            f["title"]: f
            for m in journey["milestones"]
            if m["id"] in phase["milestone_ids"]
            for f in m["lessons"]
        }
        self.assertEqual(phase["total"], len(items))
        self.assertEqual(phase["mastered"], 1)
        self.assertEqual(phase["weekly_gain"], 1)
        self.assertLess(
            phase["total"],
            sum(m["total"] for m in journey["milestones"] if m["id"] in phase["milestone_ids"]),
        )

    def test_archive_direct_identity_requires_matching_saved_inventory_provenance(self) -> None:
        profile = {"attempts": str(self.path), "format": "archive", "archived": True}
        self.archive_history("Long U: ui, ue", "Direct", score=97)
        self.assertEqual(self.journey(profile=profile)["unmapped_attempts"], 1)
        self.direct_inventory(skill="Unrelated heading")
        self.assertEqual(self.journey(profile=profile)["unmapped_attempts"], 1)
        self.direct_inventory(student="Student B")
        self.assertEqual(self.journey(profile=profile)["unmapped_attempts"], 1)
        self.direct_inventory()
        journey = self.journey(profile=profile)
        self.assertEqual(journey["mapped_attempts"], 1)
        self.assertEqual(journey["unmapped_attempts"], 0)
        activity = self.activity(journey, "Long U: ui, ue", "Direct")
        self.assertEqual(activity["scores"], [97])
        self.assertEqual(activity["state"], "practicing")
        self.assertEqual(
            activity["source_contexts"],
            [{"grade": "1st-grade", "skill": "Final -e & Long Vowel Teams"}],
        )
        self.assertIn("Final -e & Long Vowel Teams", activity["attempts"][0]["source_context"])
        # Local augmentation never leaks into the cached native catalog.
        family = next(f for f in catalog(self.root, 0, 0) if f["title"] == "Long U: ui, ue")
        self.assertNotIn("Direct", family["variants"])
        self.archive_history("Long U: ui, ue", "Direct", skill="Unrelated heading")
        self.assertEqual(self.journey(profile=profile)["unmapped_attempts"], 1)

    def test_archive_direct_does_not_alias_main_or_invent_mastery(self) -> None:
        self.archive_history(
            "Long U: ue, ui", "Direct", grade="2nd-grade", skill="Vowel Sounds", score=100
        )
        self.direct_inventory(title="Long U: ue, ui", grade="2nd-grade", skill="Vowel Sounds")
        journey = self.journey(
            profile={"attempts": str(self.path), "format": "archive", "archived": True}
        )
        self.assertEqual(self.activity(journey, "Long U: ue, ui", "Direct")["state"], "mastered")
        self.assertEqual(self.activity(journey, "Long U: ue, ui", "Main")["state"], "not_assessed")
        self.assertEqual(journey["mastered_families"], 1)
        self.assertEqual(journey["weekly_mastered"], 1)

    def test_archive_unscored_exposure_is_occurrence_deduplicated_and_not_mastery(self) -> None:
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ARCHIVE_HISTORY_FIELDS)
            writer.writeheader()
            for occurrence in (1, 2, 1, 2):
                writer.writerow(
                    {
                        "student": "Student A",
                        "subject": "ela",
                        "lesson_title": "Alphabet book",
                        "activity_variant": "Direct",
                        "normalized_date": "2026-09-17",
                        "date_resolution": "exact",
                        "score_percent": "",
                        "occurrence": occurrence,
                    }
                )
            writer.writerow(
                {
                    "student": "Student A",
                    "subject": "videos",
                    "lesson_title": "Video",
                    "activity_variant": "Direct",
                    "score_percent": "",
                    "occurrence": 1,
                }
            )
        journey = self.journey(
            profile={"attempts": str(self.path), "format": "archive", "archived": True}
        )
        self.assertEqual(journey["unscored_exposures"], 2)
        self.assertEqual(journey["unscored_exposure_titles"], ["Alphabet book"])
        self.assertEqual(journey["mapped_attempts"], 0)
        self.assertEqual(journey["unmapped_attempts"], 0)
        self.assertEqual(journey["mastered_families"], 0)
        self.assertEqual(journey["weekly_attempts"], 0)
        self.assertEqual(journey["weekly_mastered"], 0)
        self.assertEqual(journey["reading_level"], "Not assessed")

    def test_next_category_comes_only_from_native_recommendations(self) -> None:
        self.attempts([])
        result = self.journey(
            report={"recommendations": [{"title": "Short Vowel Sound a", "variant": "Main"}]}
        )
        self.assertEqual(next(m for m in result["milestones"] if m["id"] == "cvc")["state"], "next")
        self.assertEqual(result["milestones"][0]["state"], "not_assessed")
        self.assertEqual(result["current_milestone_id"], "cvc")
        self.assertEqual(
            next(p for p in result["phases"] if p["id"] == "simple-words")["state"], "not_assessed"
        )


if __name__ == "__main__":
    unittest.main()
