from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceLayout:
    published_root: Path
    sandboxes_root: Path
    cache_dir: Path
    backups_dir: Path
    docs_dir: Path


def default_layout(repo_root: Path) -> WorkspaceLayout:
    """Return the classic hardcoded layout (current defaults)."""
    return WorkspaceLayout(
        published_root=repo_root / "published" / "datasets",
        sandboxes_root=repo_root / "sandboxes" / "runs",
        cache_dir=repo_root / "cache",
        backups_dir=repo_root / "backups",
        docs_dir=repo_root / "docs" / "datasets",
    )


_layout_cache: dict[int, WorkspaceLayout] = {}


def load_layout(repo_root: Path) -> WorkspaceLayout:
    """Load workspace layout from repo_root/workspace.yaml, falling back to defaults."""
    cache_key = hash(repo_root.resolve())
    if cache_key in _layout_cache:
        return _layout_cache[cache_key]

    config_path = repo_root / "workspace.yaml"
    layout = default_layout(repo_root)

    if config_path.exists():
        import yaml

        try:
            with open(config_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            data = {}

        # published
        if "published" in data and isinstance(data["published"], dict):
            pub_root = data["published"].get("root")
            if pub_root:
                layout = WorkspaceLayout(
                    published_root=_resolve_path(repo_root, pub_root),
                    sandboxes_root=layout.sandboxes_root,
                    cache_dir=layout.cache_dir,
                    backups_dir=layout.backups_dir,
                    docs_dir=layout.docs_dir,
                )

        # sandboxes
        if "sandboxes" in data and isinstance(data["sandboxes"], dict):
            sb_root = data["sandboxes"].get("root")
            if sb_root:
                layout = WorkspaceLayout(
                    published_root=layout.published_root,
                    sandboxes_root=_resolve_path(repo_root, sb_root),
                    cache_dir=layout.cache_dir,
                    backups_dir=layout.backups_dir,
                    docs_dir=layout.docs_dir,
                )

        # maintenance
        if "maintenance" in data and isinstance(data["maintenance"], dict):
            maint = data["maintenance"]
            cache = maint.get("cache")
            backups = maint.get("backups")
            docs = maint.get("docs")
            layout = WorkspaceLayout(
                published_root=layout.published_root,
                sandboxes_root=layout.sandboxes_root,
                cache_dir=_resolve_path(repo_root, cache) if cache else layout.cache_dir,
                backups_dir=_resolve_path(repo_root, backups) if backups else layout.backups_dir,
                docs_dir=_resolve_path(repo_root, docs) if docs else layout.docs_dir,
            )

    _layout_cache[cache_key] = layout
    return layout


def clear_layout_cache() -> None:
    """Clear the layout cache (for test isolation)."""
    _layout_cache.clear()


def _resolve_path(repo_root: Path, value: str) -> Path:
    """Resolve a relative or absolute path string against repo_root."""
    p = Path(value)
    if p.is_absolute():
        return p
    return repo_root / p


DEFAULT_CONFIG_TEXT = """\
# FinData workspace layout.
# All paths are relative to this file's directory unless absolute.
# Remove or comment out any key to use the default path.

published:
  root: published/datasets

sandboxes:
  root: sandboxes/runs

maintenance:
  cache: cache
  backups: backups
  docs: docs/datasets
"""


def write_default_config(repo_root: Path, create_dirs: bool = False) -> Path:
    """Write a default workspace.yaml to repo_root. Returns the config file path."""
    config_path = repo_root / "workspace.yaml"
    config_path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")

    if create_dirs:
        layout = default_layout(repo_root)
        for path in [
            layout.published_root,
            layout.sandboxes_root,
            layout.cache_dir,
            layout.backups_dir,
            layout.docs_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    return config_path
