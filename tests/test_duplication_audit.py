from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from audit_code_duplication import audit


class DuplicationAuditTests(unittest.TestCase):
    def test_repeated_function_bodies_are_detected_despite_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = "    a = 1\n    b = 2\n    c = 3\n    d = 4\n    return a+b+c+d\n"
            (root / "one.py").write_text("def one():\n" + body)
            (root / "two.py").write_text("def two():\n" + body.replace("a+b+c+d", "a + b + c + d"))
            duplicates = audit((root,))
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(len(duplicates[0]), 2)

    def test_repository_has_no_substantial_exact_cross_file_duplication(self) -> None:
        self.assertEqual(audit((Path("tools"), Path("tests"))), [])
