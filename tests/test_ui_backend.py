from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from khan_kids.ui_backend import UiAutomator2Backend


class UiBackendTests(unittest.TestCase):
    def test_compression_pilot_falls_back_when_unlabeled_control_is_missing(self) -> None:
        backend = UiAutomator2Backend.__new__(UiAutomator2Backend)
        backend._compressed = None
        backend._device = Mock()
        full = '<hierarchy><node bounds="[0,0][20,20]" clickable="true"/></hierarchy>'
        backend._device.dump_hierarchy.side_effect = ["<hierarchy/>", full, full]
        self.assertEqual(backend.dump_hierarchy(), full.encode())
        self.assertFalse(backend._compressed)
        backend.dump_hierarchy()
        self.assertFalse(backend._device.dump_hierarchy.call_args.kwargs["compressed"])

    def test_uncompressed_default_does_not_pay_for_pilot_probe(self) -> None:
        backend = UiAutomator2Backend.__new__(UiAutomator2Backend)
        backend._compressed = False
        backend._device = Mock()
        backend._device.dump_hierarchy.return_value = "<hierarchy/>"
        backend.dump_hierarchy()
        backend._device.dump_hierarchy.assert_called_once_with(compressed=False, pretty=False)
