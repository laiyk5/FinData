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
from findata.storage import Coverage, DatasetGate, Workspace
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

    def test_all_publication_fault_boundaries_preserve_one_complete_snapshot(self) -> None:
        for point in (
            "after_staging_created",
            "after_snapshot_flush",
            "before_manifest_commit",
            "after_manifest_commit",
        ):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = Workspace.init(root)
                workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
                first = self.client.query(
                    "tushare_stock_basic", list_status="L", exchange="SSE"
                )
                old_id = workspace.publisher("tushare_stock_basic").publish(first)
                second = self.client.query(
                    "tushare_stock_basic", list_status="L", exchange="SZSE"
                )

                def fail(actual: str) -> None:
                    if actual == point:
                        raise RuntimeError(point)

                with self.assertRaisesRegex(RuntimeError, point):
                    workspace.publisher(
                        "tushare_stock_basic", fault_injector=fail
                    ).publish(second)

                reader = DataLoader(root).dataset("tushare_stock_basic")
                if point == "after_manifest_commit":
                    self.assertNotEqual(reader.publication_id, old_id)
                    self.assertEqual(reader.query().column("exchange").to_pylist(), ["SZSE"])
                else:
                    self.assertEqual(reader.publication_id, old_id)
                    self.assertEqual(reader.query().column("exchange").to_pylist(), ["SSE"])

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

    def test_batch_reader_streams_without_using_eager_query_path(self) -> None:
        self.workspace.register_dataset("tushare_daily_basic", strategy="partitioned-parquet")
        table = self.client.query(
            "tushare_daily_basic",
            ts_code="000001.SZ",
            start_date="20260701",
            end_date="20260710",
        )
        self.workspace.publisher("tushare_daily_basic").publish(
            table,
            coverage=[Coverage("000001.SZ", date(2026, 7, 1), date(2026, 7, 11))],
        )
        reader = DataLoader(self.root).dataset("tushare_daily_basic")

        def eager_must_not_run(*_args: object, **_kwargs: object) -> pa.Table:
            raise AssertionError("streaming used eager query")

        reader._query_locked = eager_must_not_run  # type: ignore[method-assign]
        with reader.iter_batches(
            batch_size=2,
            keys=["000001.SZ"],
            time_range=("2026-07-01", "2026-07-11"),
            columns=["ts_code", "trade_date", "pe"],
            filters=[("pe", ">", 0)],
            limit=3,
            require_coverage=True,
        ) as batches:
            materialized = list(batches)

        self.assertLessEqual(max(batch.num_rows for batch in materialized), 2)
        self.assertEqual(sum(batch.num_rows for batch in materialized), 3)
        self.assertEqual(materialized[0].schema.names, ["ts_code", "trade_date", "pe"])

    def test_recovery_removes_only_abandoned_and_unreachable_storage(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        table = self.client.query("tushare_stock_basic", list_status="L", exchange="SSE")
        publication = self.workspace.publisher("tushare_stock_basic").publish(table)
        dataset_root = self.root / "datasets" / "tushare_stock_basic"
        abandoned = dataset_root / "staging" / "abandoned"
        abandoned.mkdir()
        (abandoned / "partial").write_text("partial", encoding="utf-8")
        unreachable = dataset_root / "snapshots" / "unreachable"
        unreachable.mkdir()

        removed = self.workspace.recover_storage()

        self.assertEqual(removed, 2)
        self.assertFalse(abandoned.exists())
        self.assertFalse(unreachable.exists())
        self.assertTrue((dataset_root / "snapshots" / publication).is_dir())
        self.assertEqual(DataLoader(self.root).dataset("tushare_stock_basic").query().num_rows, table.num_rows)

    def test_write_gate_wait_is_cancelable_before_commit(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        first = self.client.query("tushare_stock_basic", list_status="L", exchange="SSE")
        publisher = self.workspace.publisher("tushare_stock_basic")
        publication = publisher.publish(first)
        second = self.client.query("tushare_stock_basic", list_status="L", exchange="SZSE")
        waiting: list[str] = []

        class Canceled(Exception):
            pass

        with DatasetGate(
            self.root / "datasets" / "tushare_stock_basic" / "gate.lock", exclusive=False
        ):
            cancelable = self.workspace.publisher(
                "tushare_stock_basic",
                checkpoint=lambda: (_ for _ in ()).throw(Canceled()),
                waiting=lambda reason: waiting.append(reason),
            )
            with self.assertRaises(Canceled):
                cancelable.publish(second)

        self.assertEqual(waiting, ["write_gate"])
        self.assertEqual(DataLoader(self.root).dataset("tushare_stock_basic").publication_id, publication)

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

    def test_incompatible_data_layout_is_read_only_failure(self) -> None:
        self.workspace.register_dataset("tushare_stock_basic", strategy="single-file-csv")
        manifest_path = self.root / "datasets" / "tushare_stock_basic" / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["data_layout_version"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        incompatible = manifest_path.read_bytes()

        with self.assertRaises(IncompatibleDatasetError):
            DataLoader(self.root).dataset("tushare_stock_basic").query()

        self.assertEqual(manifest_path.read_bytes(), incompatible)


if __name__ == "__main__":
    unittest.main()
