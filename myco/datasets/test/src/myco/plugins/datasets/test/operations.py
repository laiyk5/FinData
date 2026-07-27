"""Operations for myco/test.

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
from myco.plugins.datasets.test import SPEC, FIELDS


@dataclass(frozen=True, slots=True)
class TestWorker:
    """Pickle-safe callable executed in a task subprocess."""

    workspace: Path

    def __call__(
        self, request: OperationRequest, context: OperationReporter,
    ) -> dict[str, Any]:
        operation = str(request["operation"])
        operands = dict(request.get("operands", {}))
        context.log(f"Starting {operation}")

        # ---- replace with your data logic ----
        # 1. Fetch: call your data source, parse response
        rows_data: list[dict[str, Any]] = self._fetch_data(operands)
        if not rows_data:
            context.log("No data returned; nothing to commit")
            return {"publication_id": None, "rows": 0}

        # 2. Transform and validate through the spec contract
        table = SPEC.table_from_response(
            FIELDS,
            [[row.get(f) for f in FIELDS] for row in rows_data],
        )

        # 3. Publish atomically
        pub = Workspace(self.workspace).publisher(SPEC.name)
        publication = pub.publish(table)
        context.log(f"Published {publication} — {len(rows_data)} rows")
        return {"publication_id": publication, "rows": len(rows_data)}

    def _fetch_data(self, operands: dict[str, Any]) -> list[dict[str, Any]]:
        """Replace with your actual data fetching logic.

        For a coverage-tracked dataset (one with ``time_field`` set), also
        return ``Coverage`` objects and pass them to ``publish()``::

            from findata.storage import Coverage
            coverage = [Coverage(key=ticker, start=start, end=end)]
            pub.publish(table, coverage=coverage)
        """
        rows = int(operands.get("rows", 5))
        return [{"key": f"row_{i}", "value": float(i)} for i in range(rows)]


class TestRuntime(DatasetRuntimeBase):
    """Dataset runtime for myco/test.

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
    ) -> TestWorker:
        return TestWorker(workspace=workspace)
