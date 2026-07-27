from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa

from findata import DataLoader
from findata_plugins.plugins.datasets.tushare_daily_basic import DAILY_BASIC_SPEC
from findata_plugins.plugins.datasets.tushare_index_basic import INDEX_BASIC_SPEC
from findata_plugins.plugins.datasets.tushare_index_weight import INDEX_WEIGHT_SPEC
from findata.loader import (
    CoverageError,
    DatasetNotFoundError,
    DatasetNotReadyError,
    IncompatibleDatasetError,
    UnsupportedCoverageError,
)
from findata_plugins.shared.engine import TushareClient
from findata.storage import Coverage, DataMutation, DatasetGate, StorageError, Workspace
from findata_plugins.shared.testing import MockTushareTransport
from findata_plugins.plugins.datasets.tushare_stock_basic import STOCK_BASIC_SPEC
from findata_plugins.plugins.datasets.tushare_trade_cal import TRADE_CAL_SPEC

TUSHARE_DATASETS = {
    spec.name: spec
    for spec in (
        TRADE_CAL_SPEC,
        STOCK_BASIC_SPEC,
        INDEX_BASIC_SPEC,
        INDEX_WEIGHT_SPEC,
        DAILY_BASIC_SPEC,
    )
}


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

    def register(self, name: str) -> Path:
        self.workspace.register_dataset(name, spec=TUSHARE_DATASETS[name])
        return self.root / "datasets" / name / "dataset.duckdb"

    def test_registration_creates_one_uninitialized_database_for_every_shape(self) -> None:
        for name in ("findata-plugins/tushare_stock_basic", "findata-plugins/tushare_daily_basic"):
            database = self.register(name)
            self.assertTrue(database.is_file())
            self.assertFalse((database.parent / "manifest.json").exists())
            self.assertFalse((database.parent / "snapshots").exists())
            with duckdb.connect(str(database), read_only=True) as connection:
                tables = {row[0] for row in connection.execute("show tables").fetchall()}
                metadata = connection.execute(
                    "select state, revision, publication_id, missing_data_policy "
                    "from _findata_metadata"
                ).fetchone()
            self.assertEqual(tables, {"_findata_coverage", "_findata_metadata", "data"})
            expected_policy = (
                "accept-empty" if name == "findata-plugins/tushare_daily_basic" else "strict"
            )
            self.assertEqual(metadata, ("uninitialized", 0, None, expected_policy))
            with self.assertRaises(DatasetNotReadyError):
                DataLoader(self.root).dataset(name).query()

    def test_invalid_or_unknown_dataset_paths_have_no_filesystem_side_effect(self) -> None:
        outside = self.root / "outside"
        with self.assertRaises(ValueError):
            DataLoader(self.root).dataset("../outside")
        self.assertFalse(outside.exists())

        reader = DataLoader(self.root).dataset("nobody/not_registered")
        for read in (reader.describe, reader.coverage, reader.query):
            with self.subTest(read=read.__name__), self.assertRaises(DatasetNotFoundError):
                read()
        with self.assertRaises(DatasetNotFoundError):
            with reader.iter_batches():
                pass
        self.assertFalse((self.root / "datasets" / "nobody").exists())

        unsafe_spec = replace(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], name="../outside"
        )
        with self.assertRaises(ValueError):
            self.workspace.register_dataset("../outside", spec=unsafe_spec)
        self.assertFalse(outside.exists())

    def test_read_does_not_repair_a_missing_registered_gate(self) -> None:
        database = self.register("findata-plugins/tushare_stock_basic")
        gate = database.parent / "gate.lock"
        gate.unlink()

        with self.assertRaisesRegex(RuntimeError, "missing its storage gate"):
            DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").describe()

        self.assertFalse(gate.exists())

    def test_complete_replacement_queries_with_uniform_sql_semantics(self) -> None:
        database = self.register("findata-plugins/tushare_trade_cal")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260717",
            end_date="20260720",
        )
        publication = self.workspace.publisher("findata-plugins/tushare_trade_cal").publish(
            table,
            coverage=[Coverage("SSE", date(2026, 7, 17), date(2026, 7, 21))],
        )

        result = (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_trade_cal")
            .query(
                keys=["SSE"],
                time_range=("2026-07-17", "2026-07-21"),
                columns=["exchange", "cal_date", "is_open"],
                filters=[("is_open", "=", True)],
                order_by=[("cal_date", "desc")],
                limit=1,
                require_coverage=True,
            )
        )

        self.assertEqual(
            result.to_pylist(),
            [{"exchange": "SSE", "cal_date": date(2026, 7, 20), "is_open": True}],
        )
        self.assertEqual(
            DataLoader(self.root).dataset("findata-plugins/tushare_trade_cal").publication_id,
            publication,
        )
        self.assertTrue(database.is_file())

    def test_key_and_range_mutations_preserve_unaffected_rows(self) -> None:
        self.register("findata-plugins/tushare_daily_basic")
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_daily_basic"],
            ts_code="000001.SZ",
            start_date="20260701",
            end_date="20260703",
        )
        other = first.set_column(
            0, "ts_code", pa.array(["600000.SH"] * first.num_rows, type=pa.string())
        ).cast(first.schema)
        publisher = self.workspace.publisher("findata-plugins/tushare_daily_basic")
        publisher.publish(
            pa.concat_tables([first, other]),
            coverage=[
                Coverage("000001.SZ", date(2026, 7, 1), date(2026, 7, 4)),
                Coverage("600000.SH", date(2026, 7, 1), date(2026, 7, 4)),
            ],
        )
        replacement = first.slice(1, 1)

        publisher.commit(
            [
                DataMutation.replace_range(
                    replacement,
                    partition="000001.SZ",
                    start=date(2026, 7, 1),
                    end=date(2026, 7, 4),
                )
            ],
            coverage=[
                Coverage("000001.SZ", date(2026, 7, 1), date(2026, 7, 4)),
                Coverage("600000.SH", date(2026, 7, 1), date(2026, 7, 4)),
            ],
        )

        reader = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic")
        self.assertEqual(reader.query(keys=["000001.SZ"]).to_pylist(), replacement.to_pylist())
        self.assertEqual(reader.query(keys=["600000.SH"]).num_rows, other.num_rows)

    def test_primary_key_mutation_replaces_only_incoming_keys(self) -> None:
        self.register("findata-plugins/tushare_index_basic")
        spec = TUSHARE_DATASETS["findata-plugins/tushare_index_basic"]
        rows = [
            {
                "ts_code": "000300.SH",
                "name": "old",
                "fullname": None,
                "market": "SSE",
                "publisher": None,
                "index_type": None,
                "category": None,
                "base_date": None,
                "base_point": None,
                "list_date": None,
                "weight_rule": None,
                "desc": None,
                "exp_date": None,
            },
            {
                "ts_code": "000905.SH",
                "name": "keep",
                "fullname": None,
                "market": "SSE",
                "publisher": None,
                "index_type": None,
                "category": None,
                "base_date": None,
                "base_point": None,
                "list_date": None,
                "weight_rule": None,
                "desc": None,
                "exp_date": None,
            },
        ]
        publisher = self.workspace.publisher(spec.name)
        publisher.publish(pa.Table.from_pylist(rows, schema=spec.schema))
        changed = pa.Table.from_pylist([{**rows[0], "name": "new"}], schema=spec.schema)

        publisher.commit([DataMutation.replace_primary_keys(changed)])

        result = DataLoader(self.root).dataset(spec.name).query(order_by=["ts_code"])
        self.assertEqual(result.column("name").to_pylist(), ["new", "keep"])

    def test_coverage_error_identifies_exact_left_and_right_gaps(self) -> None:
        self.register("findata-plugins/tushare_trade_cal")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260718",
            end_date="20260719",
        )
        self.workspace.publisher("findata-plugins/tushare_trade_cal").publish(
            table,
            coverage=[Coverage("SSE", date(2026, 7, 18), date(2026, 7, 20))],
        )

        with self.assertRaises(CoverageError) as caught:
            DataLoader(self.root).dataset("findata-plugins/tushare_trade_cal").query(
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

    def test_non_coverage_dataset_rejects_coverage_enforcement(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        self.workspace.publisher("findata-plugins/tushare_stock_basic").publish(table)
        with self.assertRaises(UnsupportedCoverageError):
            DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").query(
                keys=["600000.SH"],
                time_range=("2026-01-01", "2026-02-01"),
                require_coverage=True,
            )

    def test_fault_before_commit_rolls_back_data_coverage_and_revision(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        first_id = self.workspace.publisher("findata-plugins/tushare_stock_basic").publish(first)

        def fail(point: str) -> None:
            if point == "before_commit":
                raise RuntimeError("injected crash")

        second = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
            list_status="L",
            exchange="SZSE",
        )
        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            self.workspace.publisher(
                "findata-plugins/tushare_stock_basic", fault_injector=fail
            ).publish(second)

        dataset = DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic")
        self.assertEqual(dataset.publication_id, first_id)
        self.assertEqual(dataset.query().column("exchange").to_pylist(), ["SSE"])

    def test_fault_after_commit_leaves_complete_new_revision(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        old_id = self.workspace.publisher("findata-plugins/tushare_stock_basic").publish(first)
        second = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
            list_status="L",
            exchange="SZSE",
        )

        def fail(point: str) -> None:
            if point == "after_commit":
                raise RuntimeError(point)

        with self.assertRaisesRegex(RuntimeError, "after_commit"):
            self.workspace.publisher(
                "findata-plugins/tushare_stock_basic", fault_injector=fail
            ).publish(second)
        reader = DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic")
        self.assertNotEqual(reader.publication_id, old_id)
        self.assertEqual(reader.query().column("exchange").to_pylist(), ["SZSE"])

    def test_batch_reader_holds_database_gate_while_writer_waits(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        publisher = self.workspace.publisher("findata-plugins/tushare_stock_basic")
        first_id = publisher.publish(first)
        second = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
            list_status="L",
            exchange="SZSE",
        )
        committed = threading.Event()

        def publish_second() -> None:
            publisher.publish(second)
            committed.set()

        with (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_stock_basic")
            .iter_batches(batch_size=1) as batches
        ):
            self.assertEqual(batches.publication_id, first_id)
            thread = threading.Thread(target=publish_second, daemon=True)
            thread.start()
            time.sleep(0.05)
            self.assertFalse(committed.is_set())
            self.assertEqual(sum(batch.num_rows for batch in batches), first.num_rows)
        thread.join(timeout=2)
        self.assertTrue(committed.is_set())

    def test_batch_reader_streams_without_using_eager_query_path(self) -> None:
        self.register("findata-plugins/tushare_daily_basic")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_daily_basic"],
            ts_code="000001.SZ",
            start_date="20260701",
            end_date="20260710",
        )
        self.workspace.publisher("findata-plugins/tushare_daily_basic").publish(
            table,
            coverage=[Coverage("000001.SZ", date(2026, 7, 1), date(2026, 7, 11))],
        )
        reader = DataLoader(self.root).dataset("findata-plugins/tushare_daily_basic")

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

    def test_recovery_removes_only_findata_temporary_inputs(self) -> None:
        database = self.register("findata-plugins/tushare_stock_basic")
        temporary = database.parent / ".findata-input-abandoned.arrow"
        temporary.write_bytes(b"partial")

        removed = self.workspace.recover_storage()

        self.assertEqual(removed, 1)
        self.assertFalse(temporary.exists())
        self.assertTrue(database.exists())

    def test_recovery_warns_and_times_out_naming_the_gate_holding_dataset(self) -> None:
        database = self.register("findata-plugins/tushare_stock_basic")

        with DatasetGate(database.parent / "gate.lock", exclusive=False):
            with self.assertLogs("findata.storage", level="WARNING") as captured:
                with self.assertRaisesRegex(StorageError, "findata-plugins/tushare_stock_basic"):
                    self.workspace.recover_storage(timeout=0.2)

        warnings = [line for line in captured.output if "WARNING" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("findata-plugins/tushare_stock_basic", warnings[0])
        self.assertIn("waiting", warnings[0])
        self.assertTrue(database.exists())

    def test_export_snapshot_copies_consistent_wal_free_database(self) -> None:
        self.register("findata-plugins/tushare_trade_cal")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_trade_cal"],
            exchange="SSE",
            start_date="20260717",
            end_date="20260720",
        )
        self.workspace.publisher("findata-plugins/tushare_trade_cal").publish(
            table,
            coverage=[Coverage("SSE", date(2026, 7, 17), date(2026, 7, 21))],
        )
        committed = DataLoader(self.root).dataset("findata-plugins/tushare_trade_cal").query()

        snapshot = self.workspace.export_snapshot("findata-plugins/tushare_trade_cal")

        self.assertEqual(
            snapshot, self.root / "snapshots" / "findata-plugins/tushare_trade_cal.duckdb"
        )
        self.assertTrue(snapshot.is_file())
        self.assertFalse(Path(f"{snapshot}.wal").exists())
        self.assertNotIn(snapshot, list((self.root / "datasets").rglob("*")))
        with duckdb.connect(str(snapshot), read_only=True) as connection:
            copied = connection.execute("select * from data").to_arrow_table()
            state = connection.execute("select state from _findata_metadata").fetchone()
        self.assertEqual(copied.combine_chunks().to_pylist(), committed.to_pylist())
        self.assertEqual(state, ("ready",))

    def test_export_snapshot_waits_for_batch_reader(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        self.workspace.publisher("findata-plugins/tushare_stock_basic").publish(table)
        destination = self.root / "reader-snapshot.duckdb"
        done = threading.Event()

        def snapshot() -> None:
            self.workspace.export_snapshot("findata-plugins/tushare_stock_basic", destination)
            done.set()

        with (
            DataLoader(self.root)
            .dataset("findata-plugins/tushare_stock_basic")
            .iter_batches(batch_size=1) as batches
        ):
            thread = threading.Thread(target=snapshot, daemon=True)
            thread.start()
            time.sleep(0.1)
            self.assertFalse(done.is_set())
            self.assertEqual(sum(batch.num_rows for batch in batches), table.num_rows)
        thread.join(timeout=5)
        self.assertTrue(done.is_set())
        with duckdb.connect(str(destination), read_only=True) as connection:
            rows = connection.execute("select count(*) from data").fetchone()
        self.assertEqual(rows, (table.num_rows,))

    def test_export_snapshot_is_consistent_under_concurrent_writer(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        second = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
            list_status="L",
            exchange="SZSE",
        )
        publisher = self.workspace.publisher("findata-plugins/tushare_stock_basic")
        publisher.publish(first)
        stop = threading.Event()

        def writer() -> None:
            while not stop.is_set():
                publisher.publish(first)
                publisher.publish(second)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for index in range(3):
                snapshot = self.workspace.export_snapshot(
                    "findata-plugins/tushare_stock_basic", self.root / f"snapshot-{index}.duckdb"
                )
                with duckdb.connect(str(snapshot), read_only=True) as connection:
                    exchanges = {
                        row[0]
                        for row in connection.execute(
                            "select distinct exchange from data"
                        ).fetchall()
                    }
                # Each publication is a complete replacement, so any consistent
                # snapshot observes exactly one of the two exchanges.
                self.assertEqual(len(exchanges), 1)
        finally:
            stop.set()
            thread.join(timeout=5)

    def test_export_snapshot_rejects_unknown_dataset_and_live_destination(self) -> None:
        database = self.register("findata-plugins/tushare_stock_basic")
        with self.assertRaisesRegex(StorageError, "unknown dataset"):
            self.workspace.export_snapshot("nobody/not_registered")
        self.assertFalse((self.root / "snapshots").exists())
        with self.assertRaises(ValueError):
            self.workspace.export_snapshot("findata-plugins/tushare_stock_basic", database)

    def test_write_gate_wait_is_cancelable_before_connection_opens(self) -> None:
        self.register("findata-plugins/tushare_stock_basic")
        first = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        publisher = self.workspace.publisher("findata-plugins/tushare_stock_basic")
        publication = publisher.publish(first)
        second = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
            list_status="L",
            exchange="SZSE",
        )
        waiting: list[str] = []

        class Canceled(Exception):
            pass

        with DatasetGate(
            self.root / "datasets" / "findata-plugins/tushare_stock_basic" / "gate.lock",
            exclusive=False,
        ):
            cancelable = self.workspace.publisher(
                "findata-plugins/tushare_stock_basic",
                checkpoint=lambda: (_ for _ in ()).throw(Canceled()),
                waiting=waiting.append,
            )
            with self.assertRaises(Canceled):
                cancelable.publish(second)
        self.assertEqual(waiting, ["write_gate"])
        self.assertEqual(
            DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").publication_id,
            publication,
        )

    def test_incompatible_storage_metadata_is_read_only_failure(self) -> None:
        database = self.register("findata-plugins/tushare_stock_basic")
        with duckdb.connect(str(database)) as connection:
            connection.execute("update _findata_metadata set storage_adapter_version = 999")
        before = database.read_bytes()

        with self.assertRaises(IncompatibleDatasetError):
            DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").query()

        self.assertEqual(database.read_bytes(), before)

    def test_reset_replaces_only_the_selected_database(self) -> None:
        first_db = self.register("findata-plugins/tushare_stock_basic")
        second_db = self.register("findata-plugins/tushare_index_basic")
        table = self.client.query(
            TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"], list_status="L", exchange="SSE"
        )
        self.workspace.publisher("findata-plugins/tushare_stock_basic").publish(table)
        second_before = second_db.read_bytes()

        self.workspace.reset_dataset(
            "findata-plugins/tushare_stock_basic",
            spec=TUSHARE_DATASETS["findata-plugins/tushare_stock_basic"],
        )

        self.assertTrue(first_db.exists())
        with self.assertRaises(DatasetNotReadyError):
            DataLoader(self.root).dataset("findata-plugins/tushare_stock_basic").query()
        self.assertEqual(second_db.read_bytes(), second_before)


if __name__ == "__main__":
    unittest.main()
