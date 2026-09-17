from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.adb import AndroidDevice
from khan_kids.student_identity import anonymize_text, public_student


class StudentIdentityTests(unittest.TestCase):
    def test_private_mapping_accepts_real_name_and_preserves_public_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "aliases.json"
            path.write_text(json.dumps({"Actual Child": "Student A"}))
            with patch("khan_kids.student_identity.ALIAS_PATH", path):
                self.assertEqual(public_student("actual child"), "Student A")
                self.assertEqual(public_student("Student A"), "Student A")
                device = AndroidDevice("test-device")
            raw = b'<hierarchy><node text="Actual Child\'s Lesson Scores" bounds="[0,0][2560,1600]"/></hierarchy>'
            with patch.object(device, "command", side_effect=[b"", raw]):
                root, sanitized = device._hierarchy_root(attempts=1, include_raw=True)
            self.assertEqual(root[0].get("text"), "Student A's Lesson Scores")
            self.assertNotIn(b"Actual Child", sanitized)

    def test_replacement_is_case_insensitive_and_does_not_change_partial_names(self) -> None:
        self.assertEqual(
            anonymize_text("Actual Child and Actual Children", {"Actual Child": "Student A"}),
            "Student A and Actual Children",
        )
