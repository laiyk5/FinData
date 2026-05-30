from __future__ import annotations

import unittest
from pathlib import Path


class ScaffoldTests(unittest.TestCase):
    def test_scaffold_expected_files_exist(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        dataset_root = repo_root / "published" / "datasets" / "tushare" / "daily"

        self.assertTrue((dataset_root / "manifest.yaml").is_file())
        self.assertTrue((dataset_root / "current").is_dir())
