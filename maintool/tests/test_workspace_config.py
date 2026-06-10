from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from maintool.workspace_config import (
    WorkspaceLayout,
    clear_layout_cache,
    default_layout,
    load_layout,
    write_default_config,
)


class WorkspaceConfigTests(unittest.TestCase):
    def setUp(self):
        clear_layout_cache()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name)

    def tearDown(self):
        clear_layout_cache()
        self.tmpdir.cleanup()

    def test_default_layout_matches_hardcoded(self):
        layout = default_layout(self.repo_root)
        self.assertEqual(layout.published_root, self.repo_root / "published" / "datasets")
        self.assertEqual(layout.sandboxes_root, self.repo_root / "sandboxes" / "runs")
        self.assertEqual(layout.cache_dir, self.repo_root / "cache")
        self.assertEqual(layout.backups_dir, self.repo_root / "backups")
        self.assertEqual(layout.docs_dir, self.repo_root / "docs" / "datasets")

    def test_load_layout_no_config_returns_defaults(self):
        layout = load_layout(self.repo_root)
        default = default_layout(self.repo_root)
        self.assertEqual(layout.published_root, default.published_root)
        self.assertEqual(layout.sandboxes_root, default.sandboxes_root)
        self.assertEqual(layout.cache_dir, default.cache_dir)
        self.assertEqual(layout.backups_dir, default.backups_dir)
        self.assertEqual(layout.docs_dir, default.docs_dir)

    def test_load_layout_with_full_config(self):
        config_path = self.repo_root / "workspace.yaml"
        config_path.write_text(
            """\
published:
  root: out/published
sandboxes:
  root: out/sandboxes
maintenance:
  cache: out/cache
  backups: out/backups
  docs: out/docs
"""
        )
        layout = load_layout(self.repo_root)
        self.assertEqual(layout.published_root, self.repo_root / "out" / "published")
        self.assertEqual(layout.sandboxes_root, self.repo_root / "out" / "sandboxes")
        self.assertEqual(layout.cache_dir, self.repo_root / "out" / "cache")
        self.assertEqual(layout.backups_dir, self.repo_root / "out" / "backups")
        self.assertEqual(layout.docs_dir, self.repo_root / "out" / "docs")

    def test_load_layout_with_absolute_paths(self):
        config_path = self.repo_root / "workspace.yaml"
        abs_pub = str(self.repo_root / "absolute" / "published")
        config_path.write_text(
            f"""\
published:
  root: {abs_pub}
"""
        )
        layout = load_layout(self.repo_root)
        self.assertEqual(layout.published_root, Path(abs_pub))

    def test_load_layout_partial_config(self):
        config_path = self.repo_root / "workspace.yaml"
        config_path.write_text(
            """\
published:
  root: custom_published
"""
        )
        layout = load_layout(self.repo_root)
        # Custom key
        self.assertEqual(layout.published_root, self.repo_root / "custom_published")
        # Default keys
        default = default_layout(self.repo_root)
        self.assertEqual(layout.sandboxes_root, default.sandboxes_root)
        self.assertEqual(layout.cache_dir, default.cache_dir)
        self.assertEqual(layout.backups_dir, default.backups_dir)
        self.assertEqual(layout.docs_dir, default.docs_dir)

    def test_init_creates_config_file(self):
        config_path = write_default_config(self.repo_root, create_dirs=False)
        self.assertTrue(config_path.exists())
        self.assertEqual(config_path, self.repo_root / "workspace.yaml")
        content = config_path.read_text()
        self.assertIn("published:", content)
        self.assertIn("sandboxes:", content)
        self.assertIn("maintenance:", content)

    def test_init_with_create_dirs(self):
        write_default_config(self.repo_root, create_dirs=True)
        layout = default_layout(self.repo_root)
        self.assertTrue(layout.published_root.is_dir())
        self.assertTrue(layout.sandboxes_root.is_dir())
        self.assertTrue(layout.cache_dir.is_dir())
        self.assertTrue(layout.backups_dir.is_dir())
        self.assertTrue(layout.docs_dir.is_dir())

    def test_init_overwrites_existing_config(self):
        first = write_default_config(self.repo_root)
        first.write_text("# modified")
        second = write_default_config(self.repo_root)
        self.assertEqual(second, first)
        content = second.read_text()
        self.assertIn("published:", content)
        self.assertNotIn("# modified", content)

    def test_cache_hit(self):
        # First call loads and caches
        layout1 = load_layout(self.repo_root)
        # Second call should hit cache (even if we change filesystem)
        self.repo_root.mkdir(parents=True, exist_ok=True)  # no-op on existing dir
        layout2 = load_layout(self.repo_root)
        self.assertIs(layout1, layout2)

    def test_clear_layout_cache(self):
        layout1 = load_layout(self.repo_root)
        clear_layout_cache()
        layout2 = load_layout(self.repo_root)
        self.assertIsNot(layout1, layout2)
        self.assertEqual(layout1.published_root, layout2.published_root)

    def test_corrupt_yaml_falls_back_to_defaults(self):
        config_path = self.repo_root / "workspace.yaml"
        config_path.write_text("{{{ this is not valid yaml")
        layout = load_layout(self.repo_root)
        default = default_layout(self.repo_root)
        self.assertEqual(layout.published_root, default.published_root)

    def test_empty_yaml_falls_back_to_defaults(self):
        config_path = self.repo_root / "workspace.yaml"
        config_path.write_text("")
        layout = load_layout(self.repo_root)
        default = default_layout(self.repo_root)
        self.assertEqual(layout.published_root, default.published_root)


if __name__ == "__main__":
    unittest.main()
