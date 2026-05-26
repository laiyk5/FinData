from __future__ import annotations

import csv
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .dataset_specs import DatasetSpec, expected_keys, get_spec
from .dataset_specs import INSTRUMENT_UNIVERSE_FIELDS, REPORT_CATALOG_FIELDS
from .jsonio import read_json, write_json
from .run_sandbox import RunContext, load_run_manifest, mark_step, utc_stamp
from .stage_logs import append_stage_event, write_stage_summary
from .tushare_daily import FIELDS
from .tushare_daily_basic import FIELDS as DAILY_BASIC_FIELDS
from .trade_calendar import FIELDS as TRADE_CALENDAR_FIELDS


ACCEPTED_MISSING_REASONS = {"market_holiday", "suspension", "outside_scope"}
NULLABLE_DAILY_BASIC_DECIMAL_FIELDS = {
    "pe",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "volume_ratio",
    "free_share",
}


def run_qa(context: RunContext) -> dict[str, Any]:
    started_at = time.monotonic()
    manifest = load_run_manifest(context)
    context.qa_root.mkdir(parents=True, exist_ok=True)
    append_stage_event(
        context,
        "qa",
        {
            "event": "start",
            "created_at": utc_stamp(),
            "dataset": context.dataset_name,
        },
    )

    validation = build_validation_report(context)
    append_stage_event(
        context,
        "qa",
        {
            "event": "validation_complete",
            "created_at": utc_stamp(),
            "passed": validation["passed"],
            "checked_file_count": len(validation["checked_files"]),
            "error_count": validation["error_count"],
        },
    )
    missingness = build_missingness_report(context, manifest)
    append_stage_event(
        context,
        "qa",
        {
            "event": "missingness_complete",
            "created_at": utc_stamp(),
            "missing_count": missingness["missing_count"],
            "blocks_publish": missingness["blocks_publish"],
        },
    )
    unusual = build_unusual_values_report(context)
    append_stage_event(
        context,
        "qa",
        {
            "event": "unusual_complete",
            "created_at": utc_stamp(),
            "warning_count": unusual["warning_count"],
        },
    )
    passed = validation["passed"] and not missingness["blocks_publish"]

    status = {
        "run_id": context.run_id,
        "dataset": context.dataset_name,
        "passed": passed,
        "validation_passed": validation["passed"],
        "missingness_blocks_publish": missingness["blocks_publish"],
        "warning_count": len(unusual["warnings"]),
        "finished_at": utc_stamp(),
    }

    write_json(context.qa_root / "validation_report.json", validation)
    write_json(context.qa_root / "missingness_report.json", missingness)
    write_json(context.qa_root / "unusual_values_report.json", unusual)
    write_json(context.qa_root / "status.json", status)
    append_stage_event(
        context,
        "qa",
        {
            "event": "done",
            "created_at": utc_stamp(),
            "passed": passed,
            "validation_passed": validation["passed"],
            "missingness_blocks_publish": missingness["blocks_publish"],
            "warning_count": len(unusual["warnings"]),
        },
    )
    write_stage_summary(
        context,
        "qa",
        status,
        status="completed",
        elapsed_seconds=time.monotonic() - started_at,
    )
    mark_step(context, "qa_passed", passed)
    return status


def build_validation_report(context: RunContext) -> dict[str, Any]:
    dataset_root = context.sandbox_dataset_root
    spec = get_spec(context.dataset_name)
    checked_files: list[str] = []
    errors: list[str] = []

    for root in (dataset_root / "data" / "staged", dataset_root / "data" / "published" / "current"):
        seen_keys: dict[tuple[str, ...], Path] = {}
        for csv_path in sorted(root.rglob("*.csv")):
            checked_files.append(str(csv_path.relative_to(context.sandbox_root)))
            if context.dataset_name == "trade_calendar":
                errors.extend(validate_trade_calendar_csv(csv_path))
            elif context.dataset_name == "instrument_universe":
                errors.extend(validate_instrument_universe_csv(csv_path))
            elif context.dataset_name == "report_catalog":
                errors.extend(validate_report_catalog_csv(csv_path))
            elif context.dataset_name == "tushare_daily_basic":
                errors.extend(validate_daily_basic_csv(csv_path))
            else:
                errors.extend(validate_daily_csv(csv_path))
            errors.extend(validate_unique_keys_across_files(root, csv_path, spec, seen_keys))

    if context.dataset_name == "instrument_universe":
        current_keys = read_actual_keys(dataset_root / "data" / "published" / "current", spec)
        if not current_keys:
            errors.append("instrument_universe: empty published current")

    return {
        "run_id": context.run_id,
        "passed": not errors,
        "checked_files": checked_files,
        "error_count": len(errors),
        "errors": errors,
        "finished_at": utc_stamp(),
    }


def validate_daily_csv(path: Path) -> list[str]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for field in FIELDS:
            if field not in fieldnames:
                errors.append(f"{path.name}: missing column {field}")
        if errors:
            return errors

        for row_number, row in enumerate(reader, start=2):
            key = (row["ts_code"], row["trade_date"])
            if key in seen_keys:
                errors.append(f"{path.name}:{row_number}: duplicate primary key {key}")
            seen_keys.add(key)

            if not valid_trade_date(row["trade_date"]):
                errors.append(f"{path.name}:{row_number}: invalid trade_date {row['trade_date']}")

            decimals: dict[str, Decimal] = {}
            for field in ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"):
                try:
                    decimals[field] = Decimal(row[field])
                except (InvalidOperation, KeyError):
                    errors.append(f"{path.name}:{row_number}: invalid decimal in {field}")

            if not {"open", "high", "low", "close"}.issubset(decimals):
                continue

            if decimals["high"] < max(decimals["open"], decimals["close"], decimals["low"]):
                errors.append(f"{path.name}:{row_number}: high is lower than another OHLC price")
            if decimals["low"] > min(decimals["open"], decimals["close"], decimals["high"]):
                errors.append(f"{path.name}:{row_number}: low is higher than another OHLC price")
            if decimals.get("vol", Decimal("0")) < 0:
                errors.append(f"{path.name}:{row_number}: vol is negative")
            if decimals.get("amount", Decimal("0")) < 0:
                errors.append(f"{path.name}:{row_number}: amount is negative")

    return errors


def validate_daily_basic_csv(path: Path) -> list[str]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for field in DAILY_BASIC_FIELDS:
            if field not in fieldnames:
                errors.append(f"{path.name}: missing column {field}")
        if errors:
            return errors

        for row_number, row in enumerate(reader, start=2):
            key = (row["ts_code"], row["trade_date"])
            if key in seen_keys:
                errors.append(f"{path.name}:{row_number}: duplicate primary key {key}")
            seen_keys.add(key)

            if not valid_trade_date(row["trade_date"]):
                errors.append(f"{path.name}:{row_number}: invalid trade_date {row['trade_date']}")

            for field in DAILY_BASIC_FIELDS:
                if field in {"ts_code", "trade_date"}:
                    continue
                if field in NULLABLE_DAILY_BASIC_DECIMAL_FIELDS and row.get(field, "") == "":
                    continue
                try:
                    value = Decimal(row[field])
                except (InvalidOperation, KeyError):
                    errors.append(f"{path.name}:{row_number}: invalid decimal in {field}")
                    continue
                if field != "volume_ratio" and value < 0:
                    errors.append(f"{path.name}:{row_number}: {field} is negative")

            try:
                total_share = Decimal(row["total_share"])
                float_share = Decimal(row["float_share"])
                free_share = Decimal(row["free_share"])
            except (InvalidOperation, KeyError):
                continue
            if float_share > total_share:
                errors.append(f"{path.name}:{row_number}: float_share exceeds total_share")

    return errors


def build_missingness_report(context: RunContext, manifest: dict[str, Any]) -> dict[str, Any]:
    spec = get_spec(context.dataset_name)
    expected_source = "manifest"
    if context.dataset_name == "tushare_daily_basic":
        expected = read_raw_primary_keys(context, spec)
        expected_source = "prepared_raw"
    else:
        expected = expected_keys(context.dataset_name, manifest)
    actual_keys = read_actual_keys(context.sandbox_dataset_root / "data" / "published" / "current", spec)
    accepted = read_accepted_missingness(context)
    trading_calendar = (
        load_trade_calendar(context.repo_root)
        if context.dataset_name in {"tushare_daily", "tushare_daily_basic"}
        else {}
    )

    missing: list[dict[str, str]] = []
    blocks_publish = False
    for first, date_value in sorted(expected - actual_keys, key=lambda item: (item[1], item[0])):
        calendar_missing_reason = classify_daily_missingness(first, date_value, trading_calendar)
        accepted_record = accepted_missingness_record(accepted, first, date_value)
        if accepted_record:
            record = {
                "key": first,
                "date": date_value,
                "ts_code": first if context.dataset_name in {"tushare_daily", "tushare_daily_basic"} else None,
                "trade_date": date_value if context.dataset_name in {"tushare_daily", "tushare_daily_basic"} else None,
                "exchange": first if context.dataset_name == "trade_calendar" else None,
                "cal_date": date_value if context.dataset_name == "trade_calendar" else None,
                "reason": accepted_record["reason"],
                "status": "accepted",
                "blocks_publish": "false",
            }
        elif context.dataset_name in {"tushare_daily", "tushare_daily_basic"} and calendar_missing_reason:
            record = {
                "key": first,
                "date": date_value,
                "ts_code": first,
                "trade_date": date_value,
                "exchange": None,
                "cal_date": None,
                "reason": calendar_missing_reason,
                "status": "accepted",
                "blocks_publish": "false",
            }
        else:
            record = {
                "key": first,
                "date": date_value,
                "ts_code": first if context.dataset_name in {"tushare_daily", "tushare_daily_basic"} else None,
                "trade_date": date_value if context.dataset_name in {"tushare_daily", "tushare_daily_basic"} else None,
                "exchange": first if context.dataset_name == "trade_calendar" else None,
                "cal_date": date_value if context.dataset_name == "trade_calendar" else None,
                "reason": "unknown",
                "status": "unresolved",
                "blocks_publish": "true",
            }
            blocks_publish = True
        missing.append(record)

    return {
        "run_id": context.run_id,
        "expected_count": len(expected),
        "actual_count": len(expected & actual_keys),
        "missing_count": len(missing),
        "blocks_publish": blocks_publish,
        "expected_source": expected_source,
        "missing": missing,
        "finished_at": utc_stamp(),
    }


def read_raw_primary_keys(context: RunContext, spec: DatasetSpec) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not context.raw_root.exists():
        return keys

    for raw_path in sorted(context.raw_root.glob("*.json")):
        payload = read_json(raw_path)
        for item in payload.get("items", []):
            try:
                keys.add(tuple(str(item[field]) for field in spec.primary_key))
            except KeyError:
                continue
    return keys


def read_actual_keys(current_dir: Path, spec: DatasetSpec | None = None) -> set[tuple[str, str]]:
    spec = spec or get_spec("tushare_daily")
    keys: set[tuple[str, str]] = set()
    if not current_dir.exists():
        return keys
    for csv_path in sorted(current_dir.rglob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                keys.add(tuple(row[field] for field in spec.primary_key))
    return keys


def read_accepted_missingness(context: RunContext) -> dict[tuple[str, str], dict[str, str]]:
    if not context.accepted_missingness_path.is_file():
        return {}
    payload = read_json(context.accepted_missingness_path)
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for record in payload.get("accepted", []):
        reason = record.get("reason", "")
        status = record.get("status", "")
        ts_code = str(record.get("ts_code", ""))
        trade_date = str(record.get("trade_date", ""))
        if status == "accepted" and reason in ACCEPTED_MISSING_REASONS:
            accepted[(ts_code, trade_date)] = {
                "reason": reason,
                "status": status,
            }
    return accepted


def accepted_missingness_record(
    accepted: dict[tuple[str, str], dict[str, str]],
    ts_code: str,
    trade_date: str,
) -> dict[str, str] | None:
    record = accepted.get((ts_code, trade_date))
    if record:
        return record

    wildcard = accepted.get(("*", "*"))
    if wildcard:
        return wildcard

    wildcard_ts = accepted.get(("*", trade_date))
    if wildcard_ts:
        return wildcard_ts

    wildcard_date = accepted.get((ts_code, "*"))
    if wildcard_date:
        return wildcard_date

    return None


def load_trade_calendar(repo_root: Path) -> dict[tuple[str, str], str]:
    calendar_root = repo_root / "datasets" / "trade_calendar" / "data" / "published" / "current"
    calendar: dict[tuple[str, str], str] = {}
    if not calendar_root.exists():
        return calendar

    for csv_path in sorted(calendar_root.rglob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                exchange = str(row.get("exchange", ""))
                cal_date = str(row.get("cal_date", ""))
                is_open = str(row.get("is_open", ""))
                if exchange and cal_date:
                    calendar[(exchange, cal_date)] = is_open
    return calendar


def classify_daily_missingness(
    ts_code: str,
    trade_date: str,
    trading_calendar: dict[tuple[str, str], str],
) -> str | None:
    exchange = exchange_for_ts_code(ts_code)
    if exchange:
        is_open = trading_calendar.get((exchange, trade_date))
        if is_open == "0":
            return "market_holiday"
        if is_open == "1":
            return None
    if is_weekend_trade_date(trade_date):
        return "market_holiday"
    return None


def exchange_for_ts_code(ts_code: str) -> str | None:
    if ts_code.endswith(".SH"):
        return "SSE"
    if ts_code.endswith(".SZ"):
        return "SZSE"
    return None


def build_unusual_values_report(context: RunContext) -> dict[str, Any]:
    if context.dataset_name != "tushare_daily":
        return {"run_id": context.run_id, "warning_count": 0, "warnings": [], "finished_at": utc_stamp()}

    rows = read_daily_rows(context.sandbox_dataset_root / "data" / "published" / "current")
    warnings: list[str] = []
    previous_by_symbol: dict[str, dict[str, str]] = {}

    for row in sorted(rows, key=lambda item: (item["ts_code"], item["trade_date"])):
        location = f"{row['ts_code']}:{row['trade_date']}"
        decimals = parse_decimals(row)
        pct_chg = decimals.get("pct_chg")
        vol = decimals.get("vol")
        close = decimals.get("close")
        pre_close = decimals.get("pre_close")

        if pct_chg is not None and abs(pct_chg) > Decimal("20"):
            warnings.append(f"{location}: pct_chg exceeds 20%")
        if vol == Decimal("0") and not is_weekend_trade_date(row["trade_date"]):
            warnings.append(f"{location}: zero volume on a claimed trading day")
        if close is not None and pre_close not in (None, Decimal("0")):
            move = abs((close - pre_close) / pre_close)
            if move > Decimal("0.20"):
                warnings.append(f"{location}: close/pre_close move exceeds 20%")

        previous = previous_by_symbol.get(row["ts_code"])
        if previous:
            previous_vol = parse_decimals(previous).get("vol")
            if vol is not None and previous_vol not in (None, Decimal("0")):
                ratio = vol / previous_vol
                if ratio > Decimal("10") or ratio < Decimal("0.1"):
                    warnings.append(f"{location}: volume changed by more than 10x versus prior row")
        previous_by_symbol[row["ts_code"]] = row

    return {
        "run_id": context.run_id,
        "warning_count": len(warnings),
        "warnings": warnings,
        "finished_at": utc_stamp(),
    }


def read_daily_rows(current_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not current_dir.exists():
        return rows
    for csv_path in sorted(current_dir.rglob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            rows.extend({field: str(row.get(field, "")) for field in FIELDS} for row in csv.DictReader(input_file))
    return rows


def validate_unique_keys_across_files(
    root: Path, csv_path: Path, spec: DatasetSpec, seen_keys: dict[tuple[str, ...], Path]
) -> list[str]:
    errors: list[str] = []
    with csv_path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames or any(field not in reader.fieldnames for field in spec.primary_key):
            return errors
        for row_number, row in enumerate(reader, start=2):
            key = tuple(row[field] for field in spec.primary_key)
            previous_path = seen_keys.get(key)
            if previous_path and previous_path != csv_path:
                errors.append(
                    f"{csv_path.relative_to(root)}:{row_number}: duplicate primary key {key} "
                    f"also appears in {previous_path.relative_to(root)}"
                )
            else:
                seen_keys[key] = csv_path
    return errors


def parse_decimals(row: dict[str, str]) -> dict[str, Decimal]:
    decimals: dict[str, Decimal] = {}
    for field in ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"):
        try:
            decimals[field] = Decimal(row[field])
        except (InvalidOperation, KeyError):
            continue
    return decimals


def valid_trade_date(value: str) -> bool:
    if len(value) != 8 or not value.isdigit():
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def is_weekend_trade_date(value: str) -> bool:
    if not valid_trade_date(value):
        return False
    return datetime.strptime(value, "%Y%m%d").weekday() >= 5


def validate_trade_calendar_csv(path: Path) -> list[str]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()
    open_days: list[str] = []

    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for field in TRADE_CALENDAR_FIELDS:
            if field not in fieldnames:
                errors.append(f"{path.name}: missing column {field}")
        if errors:
            return errors

        rows = list(reader)

    row_numbers = {id(row): index for index, row in enumerate(rows, start=2)}
    min_cal_date = min((row["cal_date"] for row in rows if valid_trade_date(row["cal_date"])), default=None)
    for row in sorted(rows, key=lambda item: (item["exchange"], item["cal_date"])):
        row_number = row_numbers[id(row)]
        key = (row["exchange"], row["cal_date"])
        if key in seen_keys:
            errors.append(f"{path.name}:{row_number}: duplicate primary key {key}")
        seen_keys.add(key)

        if not valid_trade_date(row["cal_date"]):
            errors.append(f"{path.name}:{row_number}: invalid cal_date {row['cal_date']}")
        if row.get("pretrade_date") and not valid_trade_date(row["pretrade_date"]):
            errors.append(f"{path.name}:{row_number}: invalid pretrade_date {row['pretrade_date']}")
        if row["is_open"] not in {"0", "1"}:
            errors.append(f"{path.name}:{row_number}: invalid is_open {row['is_open']}")
        if row["is_open"] == "1":
            pretrade_date = row.get("pretrade_date", "")
            if pretrade_date and pretrade_date not in open_days and (not min_cal_date or pretrade_date >= min_cal_date):
                errors.append(f"{path.name}:{row_number}: pretrade_date does not point to a previous open day")
            open_days.append(row["cal_date"])

    return errors


def validate_instrument_universe_csv(path: Path) -> list[str]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str, str]] = set()
    row_count = 0

    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for field in INSTRUMENT_UNIVERSE_FIELDS:
            if field not in fieldnames:
                errors.append(f"{path.name}: missing column {field}")
        if errors:
            return errors

        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            key = (row["universe_id"], row["member_code"], row["as_of_date"])
            if key in seen_keys:
                errors.append(f"{path.name}:{row_number}: duplicate primary key {key}")
            seen_keys.add(key)

            for field in ("universe_id", "member_code", "as_of_date"):
                if not row.get(field):
                    errors.append(f"{path.name}:{row_number}: missing required field {field}")
            for field in ("valid_from", "valid_to", "as_of_date"):
                if row.get(field) and not valid_trade_date(row[field]):
                    errors.append(f"{path.name}:{row_number}: invalid {field} {row[field]}")
            if not valid_member_code(row.get("member_code", "")):
                errors.append(f"{path.name}:{row_number}: invalid member_code {row.get('member_code', '')}")
            if row.get("weight"):
                try:
                    weight = Decimal(row["weight"])
                    if weight < 0:
                        errors.append(f"{path.name}:{row_number}: weight is negative")
                except InvalidOperation:
                    errors.append(f"{path.name}:{row_number}: invalid decimal in weight")
            if row.get("rank") and not row["rank"].isdigit():
                errors.append(f"{path.name}:{row_number}: invalid rank {row['rank']}")

    if row_count == 0:
        errors.append(f"{path.name}: empty universe file")
    return errors


def validate_report_catalog_csv(path: Path) -> list[str]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()
    latest_versions: dict[tuple[str, str, str], int] = {}

    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        for field in REPORT_CATALOG_FIELDS:
            if field not in fieldnames:
                errors.append(f"{path.name}: missing column {field}")
        if errors:
            return errors

        for row_number, row in enumerate(reader, start=2):
            key = (row["source"], row["announcement_id"])
            if key in seen_keys:
                errors.append(f"{path.name}:{row_number}: duplicate primary key {key}")
            seen_keys.add(key)

            for field in (
                "source",
                "announcement_id",
                "ts_code",
                "report_type",
                "report_year",
                "announcement_title",
                "pdf_url",
            ):
                if not row.get(field):
                    errors.append(f"{path.name}:{row_number}: missing required field {field}")
            if row.get("source") != "cninfo":
                errors.append(f"{path.name}:{row_number}: unsupported source {row.get('source', '')}")
            if row.get("report_type") not in {"annual", "semiannual", "q1", "q3"}:
                errors.append(f"{path.name}:{row_number}: invalid report_type {row.get('report_type', '')}")
            if not valid_year(row.get("report_year", "")):
                errors.append(f"{path.name}:{row_number}: invalid report_year {row.get('report_year', '')}")
            for field in ("period_end", "announcement_date"):
                if row.get(field) and not valid_trade_date(row[field]):
                    errors.append(f"{path.name}:{row_number}: invalid {field} {row[field]}")
            if row.get("pdf_url") and not row["pdf_url"].startswith("https://static.cninfo.com.cn/"):
                errors.append(f"{path.name}:{row_number}: invalid pdf_url {row['pdf_url']}")
            for field in ("is_correction", "is_summary", "is_english", "is_cancelled", "latest_version"):
                if row.get(field) and row[field] not in {"true", "false"}:
                    errors.append(f"{path.name}:{row_number}: invalid boolean {field}={row[field]}")
            if row.get("version_no") and not row["version_no"].isdigit():
                errors.append(f"{path.name}:{row_number}: invalid version_no {row['version_no']}")
            if row.get("latest_version") == "true":
                latest_key = (row.get("ts_code", ""), row.get("report_year", ""), row.get("report_type", ""))
                latest_versions[latest_key] = latest_versions.get(latest_key, 0) + 1

    for latest_key, count in latest_versions.items():
        if count > 1:
            errors.append(f"{path.name}: multiple latest_version=true rows for {latest_key}")
    return errors


def valid_year(value: str) -> bool:
    return len(value) == 4 and value.isdigit() and 1990 <= int(value) <= 2100


def valid_member_code(value: str) -> bool:
    if "." not in value:
        return False
    symbol, exchange = value.split(".", 1)
    return bool(symbol) and symbol.isdigit() and exchange in {"SH", "SZ", "BJ"}
