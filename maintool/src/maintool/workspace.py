from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workspace_config import WorkspaceLayout


def published_datasets_root(repo_root: Path, layout: WorkspaceLayout | None = None) -> Path:
    if layout is not None:
        return layout.published_root
    return repo_root / "published" / "datasets"


def backups_root(repo_root: Path, layout: WorkspaceLayout | None = None) -> Path:
    if layout is not None:
        return layout.backups_dir
    return repo_root / "backups"


def cache_root(repo_root: Path, layout: WorkspaceLayout | None = None) -> Path:
    if layout is not None:
        return layout.cache_dir
    return repo_root / "cache"


def dataset_root(repo_root: Path, dataset_name: str, layout: WorkspaceLayout | None = None) -> Path:
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    return published_datasets_root(repo_root, layout) / spec.provider / (spec.dir_name or spec.api_name)


def dataset_current_root(repo_root: Path, dataset_name: str, layout: WorkspaceLayout | None = None) -> Path:
    return dataset_root(repo_root, dataset_name, layout) / "current"


def dataset_backup_root(repo_root: Path, dataset_name: str, layout: WorkspaceLayout | None = None) -> Path:
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    return backups_root(repo_root, layout) / spec.provider / (spec.dir_name or spec.api_name)


def dataset_docs_dir(repo_root: Path, dataset_name: str, layout: WorkspaceLayout | None = None) -> Path:
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    if layout is not None:
        return layout.docs_dir / spec.provider / (spec.dir_name or spec.api_name)
    return repo_root / "docs" / "datasets" / spec.provider / (spec.dir_name or spec.api_name)


def sandbox_dataset_root(sandbox_root: Path) -> Path:
    return sandbox_root / "dataset"


def sandbox_dataset_current_root(sandbox_root: Path) -> Path:
    return sandbox_dataset_root(sandbox_root) / "current"


def sandbox_dataset_staged_root(sandbox_root: Path) -> Path:
    return sandbox_dataset_root(sandbox_root) / "staged"
