from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.checkin_history import checkin_history, record_checkin


def payload(run_id: str, *, status: str = "no_op", completed: str = "2026-09-20T10:00:00-04:00"):
    return {
        "run_id": run_id,
        "student": "Student A",
        "status": status,
        "generated_at": "2026-09-20T09:59:00-04:00",
        "verified_at": completed,
        "new_attempts": [
            {
                "title": "Short Vowel Sound a",
                "variant": "Practice 1",
                "score": 100,
                "attempt_date": "2026-09-19",
                "captured_at": completed,
            }
        ],
        "actions": [],
        "observed_assignments": [],
        "desired_assignments": [],
    }


class CheckinHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_records_delayed_score_without_rewriting_khan_attempt_date(self):
        record_checkin(self.root, payload("sync-1"))
        event = checkin_history(self.root, "Student A")["events"][0]
        self.assertEqual(event["completed_at"], "2026-09-20T10:00:00-04:00")
        self.assertEqual(event["report"]["new_scores"][0]["attempt_date"], "2026-09-19")
        self.assertEqual(
            event["report"]["new_scores"][0]["captured_at"], "2026-09-20T10:00:00-04:00"
        )

    def test_same_run_is_deduplicated_and_newest_checkin_is_first(self):
        record_checkin(self.root, payload("sync-1"))
        record_checkin(self.root, payload("sync-1", status="interrupted"))
        record_checkin(
            self.root,
            payload("sync-2", completed="2026-09-20T11:00:00-04:00"),
        )
        events = checkin_history(self.root, "Student A")["events"]
        self.assertEqual([event["run_id"] for event in events], ["sync-2", "sync-1"])
        self.assertEqual(events[1]["report"]["status"], "interrupted")

    def test_manual_event_kind_is_explicit(self):
        item = payload("manual-1")
        item["manual_change"] = {
            "action": "assign",
            "title": "Lowercase l",
            "variant": "Main",
        }
        self.assertEqual(record_checkin(self.root, item)["kind"], "manual_assignment")

    def test_backfill_uses_only_structured_results_and_deduplicates(self):
        for directory in ("a", "b"):
            target = self.root / "private/sync-runs" / directory
            target.mkdir(parents=True)
            (target / "result.json").write_text(json.dumps(payload("sync-old")))
        result = checkin_history(self.root, "Student A")
        self.assertEqual(len(result["events"]), 1)
        self.assertIn("exact check-in grouping", result["backfill_note"])

    def test_history_file_is_owner_private(self):
        record_checkin(self.root, payload("sync-1"))
        path = self.root / "private/checkin-history/student-a.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "owner-private"):
            checkin_history(self.root, "Student A")


if __name__ == "__main__":
    unittest.main()
