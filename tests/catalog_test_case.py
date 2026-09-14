from __future__ import annotations

import unittest
from pathlib import Path

from khan_kids.catalog import CatalogIndex


class ArchiveCatalogTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = CatalogIndex(Path("data/reading-ela-archive.json"))
