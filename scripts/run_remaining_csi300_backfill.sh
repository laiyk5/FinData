#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="${1:-.}"
CURRENT_RUN_ID="report-catalog-csi300-2017-2021-20260524"
LOG_PATH="${REPO_ROOT}/sandboxes/runs/report_catalog/csi300-backfill-20260524.log"

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "${LOG_PATH}"
}

wait_for_prepare() {
  local run_id="$1"
  local summary_path="${REPO_ROOT}/sandboxes/runs/report_catalog/${run_id}/logs/prepare_summary.json"

  log "waiting for prepare summary: ${run_id}"
  while true; do
    if [ -f "${summary_path}" ]; then
      if python3 - "${summary_path}" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
raise SystemExit(0 if summary.get("status") == "completed" else 1)
PY
      then
        break
      fi
    fi
    sleep 60
  done
  log "prepare summary detected: ${run_id}"
}

run_stage_sequence() {
  local run_id="$1"

  log "ingest start: ${run_id}"
  python3 maintool/bin/fintool --repo-root "${REPO_ROOT}" ingest report_catalog --run-id "${run_id}" | tee -a "${LOG_PATH}"

  log "qa start: ${run_id}"
  python3 maintool/bin/fintool --repo-root "${REPO_ROOT}" qa report_catalog --run-id "${run_id}" | tee -a "${LOG_PATH}"

  log "publish start: ${run_id}"
  python3 maintool/bin/fintool --repo-root "${REPO_ROOT}" publish report_catalog --run-id "${run_id}" | tee -a "${LOG_PATH}"
}

run_five_year_batch() {
  local start_year="$1"
  local end_year="$2"
  local run_id="report-catalog-csi300-${start_year}-${end_year}-20260524"

  log "maintain-plan start: ${run_id}"
  python3 maintool/bin/fintool \
    --repo-root "${REPO_ROOT}" \
    maintain-plan report_catalog \
    --provider cninfo \
    --enable-real-api \
    --symbols '@universe:index:CSI300' \
    --start-year "${start_year}" \
    --end-year "${end_year}" \
    --report-types annual,semiannual,q1,q3 \
    --max-pages-per-request 1 \
    --request-budget 1500 \
    --run-id "${run_id}" | tee -a "${LOG_PATH}"

  log "prepare start: ${run_id}"
  python3 maintool/bin/fintool --repo-root "${REPO_ROOT}" prepare report_catalog --run-id "${run_id}" | tee -a "${LOG_PATH}"

  run_stage_sequence "${run_id}"
}

mkdir -p "$(dirname "${LOG_PATH}")"
log "backfill supervisor started"

wait_for_prepare "${CURRENT_RUN_ID}"
run_stage_sequence "${CURRENT_RUN_ID}"
run_five_year_batch 2012 2016
run_five_year_batch 2007 2011

log "backfill supervisor completed"
