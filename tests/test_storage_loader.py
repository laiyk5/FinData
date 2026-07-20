from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

import pyarrow as pa

from findata import DataLoader
from findata.loader import (
    CoverageError,
    DatasetNotReadyError,
    IncompatibleDatasetError,
    UnsupportedCoverageError,
)
from findata.storage import Coverage, Workspace
from findata.testing.tushare import MockTushareTransport
from findata.providers.tushare import TushareClient


class StorageLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = Workspace.init(self.root)
        self.client = TushareClient(
            token="test-token",
            transport=MockTushareTransport(today=date(2026, 7, 20)),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_registered_but_unpublished_dataset_is_not_ready(self) -> None:
        self.workspace.register_dataset("tushare_trade_cal", strategy="single-file-csv")

        with self.assertRaises(DatasetNotReadyError):
            DataLoader(self.root).dataset("tushare_trade_cal").query()

    def test_single_file_publication_queries_with_uniform_semantics(self) -> None:
        self.workspace.register_dataset("tushare_trade_cal", strategy="single-file-csv")
        table = self.client.query(
            "tushare_trade_cal",
            exchange="SSE",
            start_date="20260717",
            end_date="20260720",
        )
        publication = self.workspace.publisher("tushare_trade_cal").publish(
            table,
            coverage=[Coverage("SSE", date(2026, 7, 17), date(2026, 7, 21))],
        )

        result = DataLoader(self.root).dataset("tushare_trade_cal").query(
            keys=["SSE"],
            time_range=("2026-07-17", "2026-07-21"),
            columns=["exchange", "cal_date", "is_open"],
            filters=[("is_open", "=", True)],
            order_by=[("cal_date", "desc")],
            limit=1,
            require_coverage=True,
        )

        self.assertEqual(result.to_pylist(), [{"exchange": "SSE", "cal_date": date(2026, 7, 20), "is_open": True}])
        self.assertTrue((self.root / "datasets" / "tushare_trade_cal" / "snapshots" / publication / "data.csv").is_file())

    def test_partitioned_publication_uses_symbol_and_month_paths(self) -> None:
        self.workspace.register_dataset("tushare_daily_basic", strategy="partitioned-parquet")
        table = self.client.query(
            "tushare_daily_basic",
            ts_code="000001.SZ",
            start_date="20260630",
            end_date="20260702",
        )
        publication = self.workspace.publisher("tushare_daily_basic").publish(
            table,
            coverage=[Coverage("000001.SZ", date(2026, 6, 30), date(2026, 7, 3))],
        )
        snapshot = self.root / "datasets" / "tushare_daily_basic" / "snapshots" / publication

        self.assertTrue((snapshot / "000001.SZ" / "202606.parquet").is_file())
        self.assertTrue((snapshot / "000001.SZ" / "202607.parquet").is_file())
        result = DataLoader(self.root).dataset("tushare_daily_basic").query(
            keys=["000001.SZ"],
            time_range=(date(2026, 7, 1), date(2026, 7, 3)),
            columns=["ts_code", "trade_date", "pe"],
            order_by=["trade_date"],
            require_coverage=True,
        )
        self.assertEqual(result.num_rows, 2)

    def test_coverage_error_identifies_exact_left_and_right_gaps(self) -> None:
        self.workspace.register_dataset("tushare_trade_cal", strategy="single-file-csv")
        table = self.client.query(
            "tushare_trade_cal",
            exchange="SSE",
            start_date="20260718",
            end_date="20260719",
        )
        self.workspace.publisher("tushare_trade_cal").publish(
            table,
            coverage=[Coverage("SSE", date(2026, 7, 18), date(2026, 7, 20))],
        )

        with self.assertRaises(CoverageError) as caught:
            DataLoader(self.root).dataset("tushare_trade_cal").query(
                keys=["SSE"],
                time_range=("2026-07-17", "2026-07-21"),
                require_coverage=True,
            )

        self.assertEqual(
            caught.exception.missing_intervals,
            {
                "SSE": [
                    (date(2026, 7, 17), date(2026, 7, 18)),
                    (date(2026, 7, 20), date(2026, 7, 21)),
                ]
            },
        )

    def test_snapshot_dataset_rejects_coverage_enforcement(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        table = self.client.query("tushare_stock_basic", list_status="L", exchange="SSE")
        self.workspace.publisher("tushare_stock_basic").publish(table)

        with self.assertRaises(UnsupportedCoverageError):
            DataLoader(self.root).dataset("tushare_stock_basic").query(
                keys=["600000.SH"],
                time_range=("2026-01-01", "2026-02-01"),
                require_coverage=True,
            )

    def test_failure_before_commit_keeps_previous_snapshot_visible(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        first = self.client.query("tushare_stock_basic", list_status="L", exchange="SSE")
        publisher = self.workspace.publisher("tushare_stock_basic")
        first_id = publisher.publish(first)

        def fail(point: str) -> None:
            if point == "before_manifest_commit":
                raise RuntimeError("injected crash")

        second = self.client.query("tushare_stock_basic", list_status="L", exchange="SZSE")
        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            self.workspace.publisher("tushare_stock_basic", fault_injector=fail).publish(second)

        dataset = DataLoader(self.root).dataset("tushare_stock_basic")
        self.assertEqual(dataset.publication_id, first_id)
        self.assertEqual(dataset.query().column("exchange").to_pylist(), ["SSE"])

    def test_batch_reader_holds_snapshot_while_writer_waits(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        first = self.client.query("tushare_stock_basic", list_status="L", exchange="SSE")
        publisher = self.workspace.publisher("tushare_stock_basic")
        first_id = publisher.publish(first)
        second = self.client.query("tushare_stock_basic", list_status="L", exchange="SZSE")
        committed = threading.Event()

        def publish_second() -> None:
            publisher.publish(second)
            committed.set()

        with DataLoader(self.root).dataset("tushare_stock_basic").iter_batches(batch_size=1) as batches:
            self.assertEqual(batches.publication_id, first_id)
            thread = threading.Thread(target=publish_second, daemon=True)
            thread.start()
            time.sleep(0.05)
            self.assertFalse(committed.is_set())
            self.assertEqual(sum(batch.num_rows for batch in batches), first.num_rows)

        thread.join(timeout=2)
        self.assertTrue(committed.is_set())
        self.assertEqual(DataLoader(self.root).dataset("tushare_stock_basic").query().column("exchange").to_pylist(), ["SZSE"])

    def test_incompatible_manifest_is_read_only_failure(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        manifest_path = self.root / "datasets" / "tushare_stock_basic" / "manifest.json"
        before = manifest_path.read_bytes()
        manifest = json.loads(before)
        manifest["manifest_version"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        incompatible = manifest_path.read_bytes()

        with self.assertRaises(IncompatibleDatasetError):
            DataLoader(self.root).dataset("tushare_stock_basic").query()

        self.assertEqual(manifest_path.read_bytes(), incompatible)


if __name__ == "__main__":
    unittest.main()

