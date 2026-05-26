from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .cninfo import CninfoProviderError, fetch_cninfo_org_id_map
from .ingest import ingest_prepared_raw
from .pipeline import run_full_pipeline
from .prepare import prepare_raw
from .publish import publish_sandbox
from .qa import run_qa
from .review import build_review
from .run_sandbox import create_run_sandbox, get_run_context
from .tushare_daily import validate_staged_csv


DEFAULT_UNIVERSE_SOURCES = {
    "index:SSE50": "000016.SH",
    "index:CSI300": "000300.SH",
    "index:CSI500": "000905.SH",
}


REQUIRED_DATASET_FILES = (
    "dataset_card.md",
    "manifest.yaml",
    "schema.yaml",
    "checks/missingness.yaml",
)

REQUIRED_DATASET_DIRS = (
    "data/raw",
    "data/staged",
    "data/published/current",
    "data/archive",
    "logs",
    "checks",
)


@dataclass(frozen=True)
class DatasetPaths:
    repo_root: Path
    dataset_name: str

    @property
    def root(self) -> Path:
        return self.repo_root / "datasets" / self.dataset_name


def resolve_repo_root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def dataset_paths(repo_root: Path, dataset_name: str) -> DatasetPaths:
    return DatasetPaths(repo_root=repo_root, dataset_name=dataset_name)


def list_datasets(repo_root: Path) -> int:
    datasets_root = repo_root / "datasets"
    if not datasets_root.exists():
        print(f"No datasets directory found at {datasets_root}")
        return 1

    names = sorted(path.name for path in datasets_root.iterdir() if path.is_dir())
    if not names:
        print("No datasets found.")
        return 0

    for name in names:
        print(name)
    return 0


def inspect_dataset(repo_root: Path, dataset_name: str) -> int:
    paths = dataset_paths(repo_root, dataset_name)
    if not paths.root.exists():
        print(f"Dataset not found: {paths.root}")
        return 1

    print(f"Dataset: {dataset_name}")
    print(f"Path: {paths.root}")
    print()

    for relative in REQUIRED_DATASET_FILES:
        status = "ok" if (paths.root / relative).is_file() else "missing"
        print(f"file {relative}: {status}")

    for relative in REQUIRED_DATASET_DIRS:
        status = "ok" if (paths.root / relative).is_dir() else "missing"
        print(f"dir  {relative}: {status}")

    return 0


def validate_dataset(repo_root: Path, dataset_name: str) -> int:
    paths = dataset_paths(repo_root, dataset_name)
    errors: list[str] = []

    if not paths.root.exists():
        errors.append(f"Dataset directory does not exist: {paths.root}")
    else:
        for relative in REQUIRED_DATASET_FILES:
            file_path = paths.root / relative
            if not file_path.is_file():
                errors.append(f"Missing required file: {relative}")
            elif file_path.stat().st_size == 0:
                errors.append(f"Required file is empty: {relative}")

        for relative in REQUIRED_DATASET_DIRS:
            dir_path = paths.root / relative
            if not dir_path.is_dir():
                errors.append(f"Missing required directory: {relative}")

        card = paths.root / "dataset_card.md"
        if card.is_file():
            card_text = card.read_text(encoding="utf-8")
            for heading in ("## Summary", "## Coverage", "## Known Missingness", "## Validation Expectations"):
                if heading not in card_text:
                    errors.append(f"Dataset card missing heading: {heading}")

        manifest = paths.root / "manifest.yaml"
        if manifest.is_file():
            manifest_text = manifest.read_text(encoding="utf-8")
            for key in ("dataset:", "status:", "storage:", "coverage:", "missingness:", "quality:", "publication:"):
                if key not in manifest_text:
                    errors.append(f"Manifest missing key: {key}")

        schema = paths.root / "schema.yaml"
        if schema.is_file():
            schema_text = schema.read_text(encoding="utf-8")
            for key in ("dataset:", "primary_key:", "fields:"):
                if key not in schema_text:
                    errors.append(f"Schema missing key: {key}")

        if dataset_name == "tushare_daily":
            staged_files = sorted((paths.root / "data" / "staged").glob("*.csv"))
            for staged_file in staged_files:
                errors.extend(validate_staged_csv(staged_file))

    if errors:
        print(f"Validation failed for {dataset_name}:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Validation passed for {dataset_name}.")
    return 0


def update_dataset(repo_root: Path, dataset_name: str, provider: str, symbols: list[str], trade_date: str) -> int:
    print("Direct dataset update is disabled. Use maintain-plan/prepare/ingest/qa/publish or maintain-run.")
    return 1


def maintain_plan(
    repo_root: Path,
    dataset_name: str,
    provider: str,
    symbols: list[str],
    trade_dates: list[str],
    run_id: str | None,
    rate_limit_seconds: float | None,
    max_retries: int,
    retry_backoff_seconds: float | None,
    enable_real_api: bool,
    dataset_extras: dict[str, str | None] | None = None,
) -> int:
    guard_error = validate_provider_guard(provider, enable_real_api)
    if guard_error:
        print(guard_error)
        return 1
    resolved_rate_limit, resolved_retry_backoff = resolve_request_settings(
        provider, rate_limit_seconds, retry_backoff_seconds
    )
    try:
        context = create_run_sandbox(
            repo_root=repo_root,
            dataset_name=dataset_name,
            provider=provider,
            symbols=symbols,
            trade_dates=trade_dates,
            run_id=run_id,
            rate_limit_seconds=resolved_rate_limit,
            max_retries=max_retries,
            retry_backoff_seconds=resolved_retry_backoff,
            jitter_seconds=dataset_extras.get("jitter_seconds") if dataset_extras else None,
            request_budget=int(dataset_extras["request_budget"]) if dataset_extras and dataset_extras.get("request_budget") else None,
            extras=dataset_extras,
        )
    except Exception as exc:
        print(f"Failed to create maintenance plan: {exc}")
        return 1

    print("Maintenance plan created.")
    print(f"run_id: {context.run_id}")
    print(f"sandbox: {context.sandbox_root}")
    return 0


def prepare_dataset(repo_root: Path, dataset_name: str, run_id: str) -> int:
    context = get_run_context(repo_root, dataset_name, run_id)
    try:
        summary = prepare_raw(context)
    except Exception as exc:
        print(f"Prepare failed: {exc}")
        return 1

    print("Prepare completed.")
    print(f"prepared: {summary['prepared']}")
    print(f"skipped: {summary['skipped']}")
    print(f"failed: {summary['failed']}")
    return 1 if summary["failed"] else 0


def ingest_dataset(repo_root: Path, dataset_name: str, run_id: str) -> int:
    context = get_run_context(repo_root, dataset_name, run_id)
    try:
        report = ingest_prepared_raw(context)
    except Exception as exc:
        print(f"Ingest failed: {exc}")
        return 1

    print("Ingest completed.")
    print(f"prepared_rows: {report['prepared_rows']}")
    print(f"published_rows_after_merge: {report['published_rows_after_merge']}")
    return 0


def qa_dataset(repo_root: Path, dataset_name: str, run_id: str) -> int:
    context = get_run_context(repo_root, dataset_name, run_id)
    try:
        status = run_qa(context)
    except Exception as exc:
        print(f"QA failed to run: {exc}")
        return 1

    print("QA completed.")
    print(f"passed: {status['passed']}")
    print(f"validation_passed: {status['validation_passed']}")
    print(f"missingness_blocks_publish: {status['missingness_blocks_publish']}")
    print(f"warning_count: {status['warning_count']}")
    return 0 if status["passed"] else 1


def publish_dataset(repo_root: Path, dataset_name: str, run_id: str, fail_before_final_rename: bool) -> int:
    context = get_run_context(repo_root, dataset_name, run_id)
    try:
        publish_log = publish_sandbox(context, fail_before_final_rename=fail_before_final_rename)
    except Exception as exc:
        print(f"Publish failed: {exc}")
        return 1

    print("Publish completed.")
    print(f"published_at: {publish_log['published_at']}")
    print(f"archive_path: {publish_log['archive_path']}")
    return 0


def review_run(repo_root: Path, dataset_name: str, run_id: str) -> int:
    context = get_run_context(repo_root, dataset_name, run_id)
    try:
        print(build_review(context), end="")
    except Exception as exc:
        print(f"Review failed: {exc}")
        return 1
    return 0


def maintain_run(
    repo_root: Path,
    dataset_name: str,
    provider: str,
    symbols: list[str],
    trade_dates: list[str],
    run_id: str | None,
    rate_limit_seconds: float | None,
    max_retries: int,
    retry_backoff_seconds: float | None,
    enable_real_api: bool,
    dataset_extras: dict[str, str | None] | None = None,
) -> int:
    guard_error = validate_provider_guard(provider, enable_real_api)
    if guard_error:
        print(guard_error)
        return 1
    resolved_rate_limit, resolved_retry_backoff = resolve_request_settings(
        provider, rate_limit_seconds, retry_backoff_seconds
    )

    try:
        context, result = run_full_pipeline(
            repo_root=repo_root,
            dataset_name=dataset_name,
            provider=provider,
            symbols=symbols,
            trade_dates=trade_dates,
            run_id=run_id,
            rate_limit_seconds=resolved_rate_limit,
            max_retries=max_retries,
            retry_backoff_seconds=resolved_retry_backoff,
            jitter_seconds=dataset_extras.get("jitter_seconds") if dataset_extras else None,
            request_budget=int(dataset_extras["request_budget"]) if dataset_extras and dataset_extras.get("request_budget") else None,
            extras=dataset_extras,
        )
    except Exception as exc:
        print(f"Maintenance run failed: {exc}")
        return 1

    print("Maintenance run completed.")
    print(f"run_id: {context.run_id}")
    print(f"sandbox: {context.sandbox_root}")
    print(f"prepared: {result['prepare']['prepared']}")
    print(f"published_at: {result['publish']['published_at']}")
    return 0


def parse_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def validate_provider_guard(provider: str, enable_real_api: bool) -> str | None:
    if provider in {"fake", "mock"}:
        return None
    if provider not in {"tushare", "cninfo"}:
        return f"Unsupported provider: {provider}"
    if provider == "cninfo" and not enable_real_api:
        return "Real Cninfo ingestion requires --enable-real-api."
    if not enable_real_api:
        return "Real Tushare ingestion requires --enable-real-api."
    if provider == "tushare" and not os.environ.get("TUSHARE_API_KEY"):
        return "TUSHARE_API_KEY is required for provider=tushare."
    return None


def resolve_request_settings(
    provider: str,
    rate_limit_seconds: float | None,
    retry_backoff_seconds: float | None,
) -> tuple[float, float]:
    if provider == "tushare":
        return (
            0.25 if rate_limit_seconds is None else rate_limit_seconds,
            1.0 if retry_backoff_seconds is None else retry_backoff_seconds,
        )
    if provider == "cninfo":
        return (
            2.0 if rate_limit_seconds is None else rate_limit_seconds,
            30.0 if retry_backoff_seconds is None else retry_backoff_seconds,
        )
    return (
        0.0 if rate_limit_seconds is None else rate_limit_seconds,
        0.0 if retry_backoff_seconds is None else retry_backoff_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maintool", description="Maintain the FinData repository.")
    parser.add_argument("--repo-root", default="..", help="Path to the FinData repository root.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List datasets.")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect dataset scaffold.")
    inspect_parser.add_argument("dataset")

    validate_parser = subparsers.add_parser("validate", help="Validate dataset scaffold.")
    validate_parser.add_argument("dataset")

    update_parser = subparsers.add_parser("update", help="Update dataset from a provider.")
    update_parser.add_argument("dataset")
    update_parser.add_argument("--provider", default="fake", choices=("fake", "mock"), help="Provider implementation to use.")
    update_parser.add_argument("--trade-date", default="20240506", help="Trade date in YYYYMMDD format.")
    update_parser.add_argument(
        "--symbols",
        default="000001.SZ,600000.SH",
        help="Comma-separated Tushare security codes.",
    )

    maintain_plan_parser = subparsers.add_parser("maintain-plan", help="Create a restartable maintenance run sandbox.")
    add_pipeline_arguments(maintain_plan_parser, include_run_id=True)

    prepare_parser = subparsers.add_parser("prepare", help="Prepare raw data for a maintenance run.")
    add_run_id_arguments(prepare_parser)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest prepared raw data into the run sandbox.")
    add_run_id_arguments(ingest_parser)

    qa_parser = subparsers.add_parser("qa", help="Run pre-publish QA for a maintenance run.")
    add_run_id_arguments(qa_parser)

    publish_parser = subparsers.add_parser("publish", help="Publish a QA-passed maintenance run.")
    add_run_id_arguments(publish_parser)
    publish_parser.add_argument(
        "--fail-before-final-rename",
        action="store_true",
        help="Testing hook: fail after next version is created but before current is moved.",
    )

    review_parser = subparsers.add_parser("review", help="Review a maintenance run.")
    add_run_id_arguments(review_parser)

    maintain_run_parser = subparsers.add_parser("maintain-run", help="Run the full maintenance pipeline.")
    add_pipeline_arguments(maintain_run_parser, include_run_id=True)

    return parser


def add_run_id_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("dataset")
    parser.add_argument("--run-id", required=True, help="Maintenance run id.")


def add_pipeline_arguments(parser: argparse.ArgumentParser, include_run_id: bool = False) -> None:
    parser.add_argument("dataset")
    parser.add_argument(
        "--provider",
        default="fake",
        choices=("fake", "mock", "tushare", "cninfo"),
        help="Provider implementation to use.",
    )
    parser.add_argument(
        "--enable-real-api",
        action="store_true",
        help="Required safety flag for provider=tushare.",
    )
    parser.add_argument("--trade-date", default="20240506", help="Trade date in YYYYMMDD format.")
    parser.add_argument(
        "--trade-dates",
        default=None,
        help="Comma-separated trade dates in YYYYMMDD format. Overrides --trade-date.",
    )
    parser.add_argument(
        "--symbols",
        default="000001.SZ,600000.SH",
        help="Comma-separated Tushare security codes.",
    )
    parser.add_argument("--rate-limit-seconds", type=float, default=None)
    parser.add_argument("--jitter-seconds", default=None, help="Random delay range as min,max seconds for cninfo.")
    parser.add_argument("--request-budget", type=int, default=None, help="Maximum non-skipped provider requests in this run.")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--exchange", default="SSE", help="Exchange for trade_calendar.")
    parser.add_argument("--start-date", default=None, help="Start date for trade_calendar in YYYYMMDD format.")
    parser.add_argument("--end-date", default=None, help="End date for trade_calendar in YYYYMMDD format.")
    parser.add_argument("--is-open", default=None, choices=("0", "1"), help="Optional Tushare trade_cal is_open filter.")
    parser.add_argument("--universe-id", default=None, help="Universe id for instrument_universe, such as index:SSE50.")
    parser.add_argument("--source-id", default=None, help="Provider-native universe source id, such as 000016.SH.")
    parser.add_argument("--start-year", default=None, help="Start disclosure year for report_catalog.")
    parser.add_argument("--end-year", default=None, help="End disclosure year for report_catalog.")
    parser.add_argument(
        "--report-types",
        default="annual,semiannual,q1,q3",
        help="Comma-separated report_catalog types: annual,semiannual,q1,q3.",
    )
    parser.add_argument("--max-pages-per-request", type=int, default=None, help="Maximum cninfo pages per symbol/year.")
    parser.add_argument(
        "--daily-request-strategy",
        default="auto",
        choices=("auto", "symbol_range", "trade_date_all"),
        help="Request scheduler for tushare_daily.",
    )
    if include_run_id:
        parser.add_argument("--run-id", default=None, help="Optional explicit maintenance run id.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = resolve_repo_root(args.repo_root)

    if args.command == "list":
        return list_datasets(repo_root)
    if args.command == "inspect":
        return inspect_dataset(repo_root, args.dataset)
    if args.command == "validate":
        return validate_dataset(repo_root, args.dataset)
    if args.command == "update":
        symbols = parse_csv_arg(args.symbols)
        return update_dataset(repo_root, args.dataset, args.provider, symbols, args.trade_date)
    if args.command == "maintain-plan":
        trade_dates = parse_csv_arg(args.trade_dates) if args.trade_dates else [args.trade_date]
        dataset_extras = build_dataset_extras(args, repo_root)
        symbols = resolve_symbols_arg(repo_root, args.dataset, args.symbols, dataset_extras)
        if args.dataset in {"trade_calendar", "instrument_universe", "report_catalog"}:
            trade_dates = []
        if args.dataset == "tushare_daily" and dataset_extras and dataset_extras.get("start_date"):
            trade_dates = []
        return maintain_plan(
            repo_root,
            args.dataset,
            args.provider,
            symbols,
            trade_dates,
            args.run_id,
            args.rate_limit_seconds,
            args.max_retries,
            args.retry_backoff_seconds,
            args.enable_real_api,
            dataset_extras,
        )
    if args.command == "prepare":
        return prepare_dataset(repo_root, args.dataset, args.run_id)
    if args.command == "ingest":
        return ingest_dataset(repo_root, args.dataset, args.run_id)
    if args.command == "qa":
        return qa_dataset(repo_root, args.dataset, args.run_id)
    if args.command == "publish":
        return publish_dataset(repo_root, args.dataset, args.run_id, args.fail_before_final_rename)
    if args.command == "review":
        return review_run(repo_root, args.dataset, args.run_id)
    if args.command == "maintain-run":
        trade_dates = parse_csv_arg(args.trade_dates) if args.trade_dates else [args.trade_date]
        dataset_extras = build_dataset_extras(args, repo_root)
        symbols = resolve_symbols_arg(repo_root, args.dataset, args.symbols, dataset_extras)
        if args.dataset in {"trade_calendar", "instrument_universe", "report_catalog"}:
            trade_dates = []
        if args.dataset == "tushare_daily" and dataset_extras and dataset_extras.get("start_date"):
            trade_dates = []
        return maintain_run(
            repo_root,
            args.dataset,
            args.provider,
            symbols,
            trade_dates,
            args.run_id,
            args.rate_limit_seconds,
            args.max_retries,
            args.retry_backoff_seconds,
            args.enable_real_api,
            dataset_extras,
        )

    parser.error(f"Unknown command: {args.command}")
    return 2


def build_dataset_extras(args, repo_root: Path) -> dict[str, str | None] | None:
    if args.dataset == "trade_calendar":
        return {
            "exchange": args.exchange,
            "start_date": args.start_date or args.trade_date,
            "end_date": args.end_date or args.start_date or args.trade_date,
            "is_open": args.is_open,
        }
    if args.dataset == "instrument_universe":
        universe_id = args.universe_id or "index:SSE50"
        return {
            "universe_id": universe_id,
            "source_id": args.source_id or DEFAULT_UNIVERSE_SOURCES.get(universe_id),
            "start_date": args.start_date or args.trade_date,
            "end_date": args.end_date or args.start_date or args.trade_date,
        }
    if args.dataset == "report_catalog":
        universe_id = args.universe_id or infer_universe_id_from_symbols(args.symbols)
        start_year = args.start_year or str(datetime.now().year)
        end_year = args.end_year or start_year
        org_id_map = {}
        if args.provider == "cninfo" and args.enable_real_api:
            try:
                org_id_map = fetch_cninfo_org_id_map()
            except CninfoProviderError as exc:
                raise RuntimeError(f"Failed to load Cninfo stock orgId map: {exc}") from exc
        return {
            "universe_id": universe_id,
            "start_year": start_year,
            "end_year": end_year,
            "report_types": parse_csv_arg(args.report_types),
            "page_size": "30",
            "max_pages_per_request": str(args.max_pages_per_request or (2 if args.provider == "cninfo" else 1)),
            "jitter_seconds": args.jitter_seconds or ("1.0,3.0" if args.provider == "cninfo" else None),
            "request_budget": str(args.request_budget) if args.request_budget is not None else None,
            "org_id_map": org_id_map,
        }
    if args.dataset == "tushare_daily" and (args.start_date or args.end_date):
        start_date = args.start_date or args.trade_date
        end_date = args.end_date or args.start_date or args.trade_date
        return {
            "start_date": start_date,
            "end_date": end_date,
            "expected_trade_dates": open_trade_dates(repo_root, start_date, end_date),
            "daily_request_strategy": args.daily_request_strategy,
        }
    if args.dataset == "tushare_daily":
        return {
            "daily_request_strategy": args.daily_request_strategy,
        }
    return {}


def infer_universe_id_from_symbols(symbols_value: str) -> str:
    if symbols_value.startswith("@universe:"):
        return symbols_value.removeprefix("@universe:")
    return "manual"


def resolve_symbols_arg(
    repo_root: Path,
    dataset_name: str,
    symbols_value: str,
    dataset_extras: dict[str, str | None] | None,
) -> list[str]:
    if dataset_name in {"trade_calendar", "instrument_universe"}:
        return []
    if not symbols_value.startswith("@universe:"):
        return parse_csv_arg(symbols_value)

    universe_id = symbols_value.removeprefix("@universe:")
    symbols = resolve_universe_symbols(repo_root, universe_id)
    if dataset_extras is not None:
        dataset_extras["symbol_selector"] = symbols_value
        dataset_extras["symbol_selector_resolved_at"] = latest_universe_date(repo_root, universe_id)
    return symbols


def resolve_universe_symbols(repo_root: Path, universe_id: str) -> list[str]:
    rows = read_universe_rows(repo_root, universe_id)
    if not rows:
        raise ValueError(f"No published universe rows found for {universe_id}")
    latest_date = max(row["as_of_date"] for row in rows if row.get("as_of_date"))
    return sorted({row["member_code"] for row in rows if row.get("as_of_date") == latest_date and row.get("member_code")})


def latest_universe_date(repo_root: Path, universe_id: str) -> str | None:
    rows = read_universe_rows(repo_root, universe_id)
    dates = [row["as_of_date"] for row in rows if row.get("as_of_date")]
    return max(dates) if dates else None


def read_universe_rows(repo_root: Path, universe_id: str) -> list[dict[str, str]]:
    current_dir = repo_root / "datasets" / "instrument_universe" / "data" / "published" / "current"
    rows: list[dict[str, str]] = []
    for csv_path in sorted(current_dir.rglob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                if row.get("universe_id") == universe_id:
                    rows.append({key: str(value or "") for key, value in row.items()})
    return rows


def open_trade_dates(repo_root: Path, start_date: str, end_date: str) -> list[str]:
    calendar = load_calendar_rows(repo_root)
    expected = []
    for trade_date in all_dates(start_date, end_date):
        if calendar:
            is_open_values = {calendar.get(("SSE", trade_date)), calendar.get(("SZSE", trade_date))}
            if "1" in is_open_values:
                expected.append(trade_date)
        elif datetime.strptime(trade_date, "%Y%m%d").weekday() < 5:
            expected.append(trade_date)
    return expected


def load_calendar_rows(repo_root: Path) -> dict[tuple[str, str], str]:
    current_dir = repo_root / "datasets" / "trade_calendar" / "data" / "published" / "current"
    calendar: dict[tuple[str, str], str] = {}
    for csv_path in sorted(current_dir.rglob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                exchange = str(row.get("exchange", ""))
                cal_date = str(row.get("cal_date", ""))
                is_open = str(row.get("is_open", ""))
                if exchange and cal_date:
                    calendar[(exchange, cal_date)] = is_open
    return calendar


def all_dates(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


if __name__ == "__main__":
    sys.exit(main())
