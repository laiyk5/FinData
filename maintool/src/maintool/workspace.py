from __future__ import annotations

from pathlib import Path


def datasets_root(repo_root: Path) -> Path:
    return repo_root / "datasets"


def backups_root(repo_root: Path) -> Path:
    return repo_root / "backups"


def cache_root(repo_root: Path) -> Path:
    return repo_root / "cache"


def dataset_root(repo_root: Path, dataset_name: str) -> Path:
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    return datasets_root(repo_root) / spec.provider / spec.api_name


def dataset_published_root(repo_root: Path, dataset_name: str) -> Path:
    return dataset_root(repo_root, dataset_name) / "published"


def dataset_current_root(repo_root: Path, dataset_name: str) -> Path:
    return dataset_published_root(repo_root, dataset_name) / "current"


def dataset_backup_root(repo_root: Path, dataset_name: str) -> Path:
    from .dataset_specs import get_spec

    spec = get_spec(dataset_name)
    return backups_root(repo_root) / spec.provider / spec.api_name


def sandbox_dataset_root(sandbox_root: Path) -> Path:
    return sandbox_root / "dataset"


def sandbox_dataset_published_root(sandbox_root: Path) -> Path:
    return sandbox_dataset_root(sandbox_root) / "published"


def sandbox_dataset_current_root(sandbox_root: Path) -> Path:
    return sandbox_dataset_published_root(sandbox_root) / "current"


def sandbox_dataset_staged_root(sandbox_root: Path) -> Path:
    return sandbox_dataset_root(sandbox_root) / "staged"
