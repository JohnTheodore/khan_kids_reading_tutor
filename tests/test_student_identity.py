from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from audit_student_privacy import audit, audit_history, fingerprints, is_image, name_matcher
from khan_kids.adb import AndroidDevice
from khan_kids.student_identity import anonymize_text, public_student, validate_identity_view


class StudentIdentityTests(unittest.TestCase):
    def test_unknown_score_identity_and_picker_label_are_rejected(self) -> None:
        aliases = {"Actual Child": "Student A"}
        score = ET.fromstring(
            '<hierarchy><node text="Unmapped Child\'s Lesson Scores" bounds="[0,0][100,100]"/></hierarchy>'
        )
        picker = ET.fromstring(
            '<hierarchy><node text="Select Students" bounds="[0,0][100,100]"/><node text="Student A" bounds="[500,500][900,600]"/><node text="Unmapped Child" bounds="[500,700][900,800]"/></hierarchy>'
        )
        for root in (score, picker):
            with self.assertRaises(ValueError):
                validate_identity_view(root, aliases)

    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {"GITHUB_ACTIONS": "false"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_ci_keyed_matching_and_missing_configuration(self) -> None:
        key = "synthetic-secret"
        name = "example-child"
        with patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "true",
                "KHAN_PRIVACY_KEY": key,
                "KHAN_PRIVACY_FINGERPRINTS": json.dumps(fingerprints({name: "Student A"}, key)),
            },
        ):
            matches = name_matcher()
            self.assertTrue(matches("EXAMPLE-CHILD's scores"))
            self.assertFalse(matches("Student A's scores"))
            with (
                patch.dict(os.environ, {"KHAN_PRIVACY_KEY": "wrong-key"}),
                self.assertRaises(ValueError),
            ):
                name_matcher()
        with (
            patch.dict(
                os.environ,
                {"GITHUB_ACTIONS": "true", "KHAN_PRIVACY_KEY": "", "KHAN_PRIVACY_FINGERPRINTS": ""},
            ),
            self.assertRaises(ValueError),
        ):
            name_matcher()

    def test_images_rejected_even_with_renamed_files(self) -> None:
        self.assertTrue(is_image("capture.PNG", b""))
        self.assertTrue(is_image("capture.txt", b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(is_image("capture.txt", b"RIFF1234WEBP"))
        self.assertFalse(is_image("code.py", b"print('safe')"))

    def test_history_scan_catches_sensitive_blob_not_present_at_tip(self) -> None:
        with (
            patch("audit_student_privacy.load_aliases", return_value={"Actual Child": "Student A"}),
            patch(
                "audit_student_privacy.subprocess.check_output",
                side_effect=[
                    b"new\nold\n",
                    b"safe message",
                    b"",
                    b"safe message",
                    b"100644 blob abc\tcapture.txt\0",
                    b"Actual Child",
                ],
            ),
        ):
            self.assertEqual(audit_history(["HEAD"]), ["abc"])

    def test_private_screenshot_cleanup_on_analysis_failure(self) -> None:
        device = AndroidDevice("test-device")
        with patch.object(device, "command", return_value=b"synthetic-image"):
            with self.assertRaises(RuntimeError), device.private_screenshot() as path:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertTrue(
                    path.is_relative_to(Path(__file__).resolve().parents[1] / "private")
                )
                raise RuntimeError("analysis failed")
            self.assertFalse(path.exists())

    def test_screenshot_refuses_public_destination_before_device_io(self) -> None:
        device = AndroidDevice("test-device")
        with patch.object(device, "command") as command:
            with self.assertRaises(ValueError):
                device.screenshot(Path("public-capture.png"))
            command.assert_not_called()

    def test_index_audit_detects_names_in_paths_and_contents(self) -> None:
        entries = b"100644 abc 0\tActual Child.txt\0" + b"100644 def 0\tpublic.txt\0"
        with (
            patch("audit_student_privacy.load_aliases", return_value={"Actual Child": "Student A"}),
            patch(
                "audit_student_privacy.subprocess.check_output",
                side_effect=[entries, b"safe", b"ACTUAL CHILD scored"],
            ),
        ):
            self.assertEqual(audit(), ["abc", "def"])

    def test_index_audit_requires_private_mapping_before_reading_git(self) -> None:
        with (
            patch("audit_student_privacy.load_aliases", return_value={}),
            patch("audit_student_privacy.subprocess.check_output") as command,
        ):
            with self.assertRaises(ValueError):
                audit()
            command.assert_not_called()

    def test_missing_mapping_blocks_unknown_identity_and_capture_before_io(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("khan_kids.student_identity.ALIAS_PATH", Path(temporary) / "missing"):
                self.assertEqual(public_student("Student A"), "Student A")
                with self.assertRaises(ValueError):
                    public_student("Unmapped Child")
                device = AndroidDevice("test-device")
            with patch.object(device, "command") as command:
                with self.assertRaises(ValueError):
                    device.dump(Path(temporary) / "capture.xml")
                command.assert_not_called()
                self.assertFalse((Path(temporary) / "capture.xml").exists())

    def test_empty_mapping_cannot_anonymize_text(self) -> None:
        with self.assertRaises(ValueError):
            anonymize_text("Unmapped Child", {})

    def test_invalid_mapping_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "aliases.json"
            for data in ({"Child": "Child"}, {"Child": "Student A", "CHILD": "Student B"}):
                path.write_text(json.dumps(data))
                with (
                    patch("khan_kids.student_identity.ALIAS_PATH", path),
                    self.assertRaises(ValueError),
                ):
                    public_student("Student A")

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
