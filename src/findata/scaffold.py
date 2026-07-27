"""``findata plugin scaffold`` — code generator for new plugin distributions."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_NAMESPACE_RE = re.compile(r"\A[a-z][a-z0-9_-]*\Z")

_PROVIDER_PYPROJECT = '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{dist_name}"
version = "0.1.0"
description = "{title}"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = [
  "findata",
]

[project.entry-points."findata.providers"]
{ep_name} = "{pkg_path}:provider_plugin"

[tool.hatch.build.targets.wheel]
only-include = ["src/{pkg_path}"]
sources = ["src"]
'''

_DATASET_PYPROJECT = '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{dist_name}"
version = "0.1.0"
description = "{title}"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = [
  "findata",
  "{provider_dist}==0.1.0",
  "pyarrow>=15",
]

[project.entry-points."findata.datasets"]
{ep_name} = "{pkg_path}:{plugin_fn}"

[tool.hatch.build.targets.wheel]
only-include = ["src/{pkg_path}"]
sources = ["src"]
'''

_UMBRELLA_PYPROJECT = '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{dist_name}"
version = "0.1.0"
description = "Metapackage installing every {namespace} plugin for findata"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = [
  "{provider_dist}==0.1.0",
  "{dataset_dist}==0.1.0",
]

[tool.hatch.build.targets.wheel]
bypass-selection = true

[tool.hatch.build.targets.sdist]
bypass-selection = true
'''

_PROVIDER_INIT = '''\
"""Provider plugin for {namespace}/{name}."""

from {pkg_path}.provider import {runtime_cls}
from {pkg_path}.provider import provider_plugin

__all__ = ["{runtime_cls}", "provider_plugin"]
'''

_PROVIDER_MODULE = '''\
"""Always-ready provider for {namespace}/{name}."""

from datetime import date
from findata.sdk import ProviderPlugin, ProviderRuntime
from findata.storage import Workspace


class {runtime_cls}(ProviderRuntime):
    """Always-ready provider — no credentials required."""

    def ready(self, workspace: Workspace, mode: str) -> bool:
        return True

    def is_mock(self, workspace: Workspace, mode: str) -> bool:
        return True

    def probe(self, workspace: Workspace, *, today: date) -> None:
        pass


def provider_plugin() -> ProviderPlugin:
    return ProviderPlugin(
        provider_id="{namespace}/{name}",
        configuration_schema={{"type": "object", "properties": {{}}}},
        secret_fields=(),
        rate_limit=1000,
        period=60,
        runtime={runtime_cls}(),
    )
'''

_DATASET_INIT = '''\
"""Dataset plugin for {namespace}/{name}."""

import pyarrow as pa
from findata.sdk import DatasetPlugin, DatasetRuntimeBase, DatasetSpec

FIELDS = {fields!r}

def _schema() -> pa.Schema:
    return pa.schema([
        pa.field("key", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
    ])

SPEC = DatasetSpec(
    name="{namespace}/{name}",
    api_name="{name}",
    schema=_schema(),
    provider_fields=FIELDS,
    primary_key=("key",),
)


def {plugin_fn}():
    from {pkg_path}.operations import {runtime_cls}
    return DatasetPlugin(
        name=SPEC.name,
        provider="{name}",
        spec=SPEC,
        runtime={runtime_cls}(),
        operations=("update", "complete", "refresh"),
    )
'''

_DATASET_OPERATIONS = '''\
"""Operations for {namespace}/{name}.

Replace the placeholder below with your actual data-fetch and
transform logic.  The general pattern is:

1. Fetch data from your source (HTTP API, file, database, …)
2. Transform into a list of dicts matching SPEC.provider_fields
3. Call SPEC.table_from_response() to validate and build an Arrow table
4. Publish through the core writer
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from findata.sdk import (
    DatasetRuntimeBase, DatasetSpec, OperandError,
    OperationReporter, OperationRequest,
)
from findata.storage import Coverage, Workspace
from {pkg_path} import SPEC, FIELDS


@dataclass(frozen=True, slots=True)
class {worker_cls}:
    """Pickle-safe callable executed in a task subprocess."""

    workspace: Path

    def __call__(
        self, request: OperationRequest, context: OperationReporter,
    ) -> dict[str, Any]:
        operation = str(request["operation"])
        operands = dict(request.get("operands", {{}}))
        context.log(f"Starting {{operation}}")

        # ---- replace with your data logic ----
        # 1. Fetch: call your data source, parse response
        rows_data: list[dict[str, Any]] = self._fetch_data(operands)
        if not rows_data:
            context.log("No data returned; nothing to commit")
            return {{"publication_id": None, "rows": 0}}

        # 2. Transform and validate through the spec contract
        table = SPEC.table_from_response(
            FIELDS,
            [[row.get(f) for f in FIELDS] for row in rows_data],
        )

        # 3. Publish atomically
        pub = Workspace(self.workspace).publisher(SPEC.name)
        publication = pub.publish(table)
        context.log(f"Published {{publication}} — {{len(rows_data)}} rows")
        return {{"publication_id": publication, "rows": len(rows_data)}}

    def _fetch_data(self, operands: dict[str, Any]) -> list[dict[str, Any]]:
        """Replace with your actual data fetching logic.

        For a coverage-tracked dataset (one with ``time_field`` set), also
        return ``Coverage`` objects and pass them to ``publish()``::

            from findata.storage import Coverage
            coverage = [Coverage(key=ticker, start=start, end=end)]
            pub.publish(table, coverage=coverage)
        """
        rows = int(operands.get("rows", 5))
        return [{{"key": f"row_{{i}}", "value": float(i)}} for i in range(rows)]


class {runtime_cls}(DatasetRuntimeBase):
    """Dataset runtime for {namespace}/{name}.

    Override ``normalize_operation`` if your operations accept specific
    operands beyond the generic pass-through.
    """
    spec = SPEC
    operations = ("update", "complete", "refresh")

    def operation_worker(
        self,
        workspace: Path,
        *,
        mode: str,
        today: date,
        now: datetime | None,
    ) -> {worker_cls}:
        return {worker_cls}(workspace=workspace)
'''


class ScaffoldError(ValueError):
    """Invalid scaffold arguments."""


def validate_namespace(value: str) -> str:
    """Raise ``ScaffoldError`` if *value* is not a valid plugin namespace."""
    if not _NAMESPACE_RE.match(value):
        raise ScaffoldError(
            f"invalid namespace {value!r}: must match [a-z][a-z0-9_-]*"
        )
    return value


def validate_name(value: str) -> str:
    """Raise ``ScaffoldError`` if *value* is not a valid plugin local name."""
    if not _NAMESPACE_RE.match(value):
        raise ScaffoldError(
            f"invalid name {value!r}: must match [a-z][a-z0-9_-]*"
        )
    return value


def scaffold_plugin(namespace: str, name: str, *, output_dir: str | Path = Path()) -> Path:
    """Generate a complete plugin family under ``output_dir / namespace /``.

    Returns the path to the generated root directory.
    """
    validate_namespace(namespace)
    validate_name(name)

    root = (output_dir / namespace).resolve()
    if root.exists():
        raise ScaffoldError(f"target directory {root} already exists")

    ns_pkg = namespace.replace("-", "_")
    local_pkg = name.replace("-", "_")

    # Derived names
    runtime_cls = f"{local_pkg.title().replace('_', '')}Runtime"
    worker_cls = f"{local_pkg.title().replace('_', '')}Worker"
    plugin_fn = f"{local_pkg}_plugin"
    ep_name = local_pkg
    provider_dist = f"{namespace}-provider-{name}"
    dataset_dist = f"{namespace}-datasets-{name}"
    umbrella_dist = f"{namespace}-plugins-{name}"

    # PEP 420 namespace root — no __init__.py
    ns_dir = root / "src" / ns_pkg

    files: list[tuple[str, str, bool]] = [
        # (relative path, content, is_template)
        (
            "provider/pyproject.toml",
            _PROVIDER_PYPROJECT.format(
                dist_name=provider_dist,
                title=f"Provider plugin for {namespace}/{name}",
                ep_name=ep_name,
                pkg_path=f"{ns_pkg}.plugins.providers.{local_pkg}",
            ),
            False,
        ),
        (
            f"provider/src/{ns_pkg}/plugins/providers/{local_pkg}/__init__.py",
            _PROVIDER_INIT.format(
                namespace=namespace,
                name=name,
                pkg_path=f"{ns_pkg}.plugins.providers.{local_pkg}",
                runtime_cls=runtime_cls,
            ),
            False,
        ),
        (
            f"provider/src/{ns_pkg}/plugins/providers/{local_pkg}/provider.py",
            _PROVIDER_MODULE.format(
                namespace=namespace,
                name=name,
                runtime_cls=runtime_cls,
            ),
            False,
        ),
        (
            f"datasets/{name}/pyproject.toml",
            _DATASET_PYPROJECT.format(
                dist_name=dataset_dist,
                title=f"Dataset plugin for {namespace}/{name}",
                ep_name=ep_name,
                pkg_path=f"{ns_pkg}.plugins.datasets.{local_pkg}",
                plugin_fn=plugin_fn,
                provider_dist=provider_dist,
            ),
            False,
        ),
        (
            f"datasets/{name}/src/{ns_pkg}/plugins/datasets/{local_pkg}/__init__.py",
            _DATASET_INIT.format(
                namespace=namespace,
                name=name,
                fields=("key", "value"),
                plugin_fn=plugin_fn,
                pkg_path=f"{ns_pkg}.plugins.datasets.{local_pkg}",
                runtime_cls=runtime_cls,
            ),
            False,
        ),
        (
            f"datasets/{name}/src/{ns_pkg}/plugins/datasets/{local_pkg}/operations.py",
            _DATASET_OPERATIONS.format(
                namespace=namespace,
                name=name,
                pkg_path=f"{ns_pkg}.plugins.datasets.{local_pkg}",
                runtime_cls=runtime_cls,
                worker_cls=worker_cls,
            ),
            False,
        ),
        (
            "umbrella/pyproject.toml",
            _UMBRELLA_PYPROJECT.format(
                dist_name=umbrella_dist,
                namespace=namespace,
                provider_dist=provider_dist,
                dataset_dist=dataset_dist,
            ),
            False,
        ),
    ]

    for rel_path, content, _is_template in files:
        file_path = root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        os.chmod(file_path, 0o644)

    return root
