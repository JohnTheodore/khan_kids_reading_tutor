from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.ui import Rect
from khan_kids.vision import CheckboxState, read_checkbox

IMAGE_COMMAND = shutil.which("magick") or shutil.which("convert")


@unittest.skipUnless(IMAGE_COMMAND, "ImageMagick is required")
class VisionTests(unittest.TestCase):
    def test_checkbox_interior_distinguishes_tick_from_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty.png"
            checked = Path(temporary) / "checked.png"
            subprocess.run([IMAGE_COMMAND, "-size", "300x200", "xc:white", str(empty)], check=True)
            subprocess.run(
                [
                    IMAGE_COMMAND,
                    "-size",
                    "300x200",
                    "xc:white",
                    "-stroke",
                    "#173b75",
                    "-strokewidth",
                    "14",
                    "-draw",
                    "line 90,105 105,120 line 105,120 135,80",
                    str(checked),
                ],
                check=True,
            )
            label = Rect(300, 75, 440, 125)
            self.assertEqual(read_checkbox(empty, label).state, CheckboxState.UNCHECKED)
            self.assertEqual(read_checkbox(checked, label).state, CheckboxState.CHECKED)

    def test_ambiguous_checkbox_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ambiguous = Path(temporary) / "ambiguous.png"
            subprocess.run(
                [
                    IMAGE_COMMAND,
                    "-size",
                    "300x200",
                    "xc:white",
                    "-fill",
                    "#173b75",
                    "-draw",
                    "rectangle 85,95 94,114",
                    str(ambiguous),
                ],
                check=True,
            )
            with self.assertRaisesRegex(RuntimeError, "Ambiguous checkbox"):
                read_checkbox(ambiguous, Rect(300, 75, 440, 125))


if __name__ == "__main__":
    unittest.main()
