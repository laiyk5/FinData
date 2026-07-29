import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  cronDisable,
  cronEnable,
  cronReset,
  cronSchedule,
  errorMessage,
  listCron,
  listDatasets,
  listEvents,
  type CronJob,
  type DatasetDescription,
  type EventRecord,
} from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { CronEditor } from "../components/CronEditor";
import { RunUpdateButton } from "../components/RunUpdate";
import { useToast } from "../components/Toast";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  Loading,
  Time,
} from "../components/common";
import { humanizeCron } from "../cron";
import { parseServerTime } from "../format";
import { useLiveData } from "../hooks";

interface CronData {
  jobs: CronJob[];
  missed: EventRecord[];
  datasets: DatasetDescription[];
}

type ScheduleFilter = "all" | "enabled" | "disabled" | "attention";

function pluginFamilyFor(dataset: DatasetDescription): string {
  return dataset.provider.replace("/", ".");
}

export default function CronPage() {
  const { notify } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const datasetFilter = searchParams.get("dataset") ?? "";
  const [query, setQuery] = useState("");
  const [scheduleFilter, setScheduleFilter] = useState<ScheduleFilter>("all");
  const [familyFilter, setFamilyFilter] = useState("all");
  const [disabling, setDisabling] = useState<CronJob | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const loader = useCallback(async (): Promise<CronData> => {
    const [cron, events, datasets] = await Promise.all([listCron(), listEvents({ unread: true }), listDatasets()]);
    return {
      jobs: cron.items,
      missed: events.items.filter((e) => !e.acknowledged && e.kind === "cron_missed"),
      datasets: datasets.items,
    };
  }, []);

  const live = useLiveData(loader, 10_000);
  const { data } = live;

  const run = async (key: string, ok: string, fn: () => Promise<unknown>): Promise<boolean> => {
    setBusyKey(key);
    try {
      await fn();
      notify("success", ok);
      await live.refresh();
      return true;
    } catch (err) {
      notify("error", errorMessage(err));
      return false;
    } finally {
      setBusyKey(null);
    }
  };

  const jobs = data?.jobs ?? [];
  const familyByDataset = useMemo(
    () => new Map((data?.datasets ?? []).map((dataset) => [dataset.name, pluginFamilyFor(dataset)])),
    [data],
  );
  const missedDatasets = useMemo(
    () => new Set((data?.missed ?? []).flatMap((event) => typeof event.context.dataset === "string" ? [event.context.dataset] : [])),
    [data],
  );
  const families = useMemo(
    () => [...new Set(jobs.map((job) => familyByDataset.get(job.dataset) ?? "other"))].sort(),
    [familyByDataset, jobs],
  );
  const visibleJobs = jobs
    .filter((job) => !datasetFilter || job.dataset === datasetFilter)
    .filter((job) => familyFilter === "all" || (familyByDataset.get(job.dataset) ?? "other") === familyFilter)
    .filter((job) => {
      if (scheduleFilter === "enabled") return job.enabled;
      if (scheduleFilter === "disabled") return !job.enabled;
      if (scheduleFilter === "attention") return missedDatasets.has(job.dataset);
      return true;
    })
    .filter((job) => {
      const haystack = `${job.dataset} ${job.operation} ${humanizeCron(job.expression)} ${job.timezone}`.toLowerCase();
      return haystack.includes(query.trim().toLowerCase());
    })
    .sort((left, right) => {
      if (left.enabled !== right.enabled) return left.enabled ? -1 : 1;
      const leftNext = parseServerTime(left.next_run) ?? Number.MAX_SAFE_INTEGER;
      const rightNext = parseServerTime(right.next_run) ?? Number.MAX_SAFE_INTEGER;
      return leftNext - rightNext || left.dataset.localeCompare(right.dataset);
    });
  const groups = visibleJobs.reduce<Map<string, CronJob[]>>((result, job) => {
    const family = familyByDataset.get(job.dataset) ?? "other";
    result.set(family, [...(result.get(family) ?? []), job]);
    return result;
  }, new Map());

  if (!data && !live.error) return <Loading />;

  return (
    <div>
      <div className="page-head">
        <h1>Cron</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={live.error} />

      {data && jobs.length > 0 && (
        <section className="cron-discovery panel" aria-label="Find schedules">
          <label className="cron-search">
            <span>Find a schedule</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Dataset, operation, or schedule"
            />
          </label>
          <div className="cron-filter-row">
            <div className="filter-chips" aria-label="Schedule status">
              {(["all", "enabled", "disabled", "attention"] as ScheduleFilter[]).map((value) => (
                <button key={value} type="button" className={`filter-chip ${scheduleFilter === value ? "active" : ""}`} onClick={() => setScheduleFilter(value)}>
                  {value === "all" ? "All schedules" : value === "attention" ? `Needs attention${missedDatasets.size ? ` (${missedDatasets.size})` : ""}` : value[0].toUpperCase() + value.slice(1)}
                </button>
              ))}
            </div>
            {families.length > 1 && (
              <label className="cron-family-filter">
                <span>Plugin family</span>
                <select value={familyFilter} onChange={(event) => setFamilyFilter(event.target.value)}>
                  <option value="all">All families</option>
                  {families.map((family) => <option key={family} value={family}>{family}</option>)}
                </select>
              </label>
            )}
            <span className="cron-result-count">{visibleJobs.length} schedule{visibleJobs.length === 1 ? "" : "s"}</span>
          </div>
        </section>
      )}

      {datasetFilter && (
        <div className="filters">
          <span className="chip">
            dataset: <span className="mono">{datasetFilter}</span>
          </span>
          <button className="btn btn-xs" onClick={() => setSearchParams({})}>
            Clear filter
          </button>
        </div>
      )}

      {data && data.missed.length > 0 && (
        <div className="warning-banner missed-banner">
          <strong>
            {data.missed.length} scheduled update{data.missed.length === 1 ? "" : "s"}{" "}
            missed while the server was down.
          </strong>
          {data.missed.map((e) => {
            const dataset = typeof e.context?.dataset === "string" ? e.context.dataset : null;
            return (
              <div key={e.event_id} className="missed-row">
                <span>
                  {e.message}{" "}
                  <span className="muted">
                    <Time unix={e.timestamp} />
                  </span>
                </span>
                {dataset && (
                  <span className="row-actions">
                    <RunUpdateButton dataset={dataset} label="Run update now" />
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {data && visibleJobs.length === 0 && (
        <EmptyState>
          {datasetFilter
            ? `No suggested cron job exists for ${datasetFilter}.`
            : query || scheduleFilter !== "all" || familyFilter !== "all"
              ? "No schedules match these filters."
              : "No cron jobs — jobs appear when a dataset plugin suggests an update schedule."}
        </EmptyState>
      )}

      {data && visibleJobs.length > 0 && (
        <div className="cron-groups">
          {[...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([family, familyJobs]) => (
            <section key={family} className="cron-family-group">
              <div className="cron-family-heading">
                <div>
                  <span className="cron-family-label">Plugin family</span>
                  <h2>{family}</h2>
                </div>
                <span>{familyJobs.length} schedule{familyJobs.length === 1 ? "" : "s"}</span>
              </div>
              <div className="cron-list">
                {familyJobs.map((job) => (
                  <CronJobCard
                    key={job.dataset}
                    job={job}
                    highlight={job.dataset === datasetFilter}
                    busy={busyKey !== null}
                    onEnable={() => void run(`enable:${job.dataset}`, `enabled ${job.dataset}`, () => cronEnable(job.dataset))}
                    onDisable={() => setDisabling(job)}
                    onReset={() => void run(`reset:${job.dataset}`, `restored suggested schedule for ${job.dataset}`, () => cronReset(job.dataset))}
                    onSave={(expression, timezone) => run(`schedule:${job.dataset}`, `schedule updated for ${job.dataset}`, () => cronSchedule(job.dataset, { expression, timezone }))}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={disabling !== null}
        title="Disable cron job"
        message={
          <>
            Disable the scheduled update for <span className="mono">{disabling?.dataset}</span>?
            No automatic updates will run until it is re-enabled.
          </>
        }
        confirmLabel="Disable"
        danger
        cliCommand={`findata cron disable ${disabling?.dataset ?? ""}`}
        busy={busyKey === `disable:${disabling?.dataset ?? ""}`}
        onConfirm={() => {
          if (!disabling) return;
          void run(`disable:${disabling.dataset}`, `disabled ${disabling.dataset}`, () =>
            cronDisable(disabling.dataset),
          ).then((ok) => {
            if (ok) setDisabling(null);
          });
        }}
        onCancel={() => setDisabling(null)}
      />
    </div>
  );
}

function CronJobCard({
  job,
  highlight = false,
  busy,
  onEnable,
  onDisable,
  onReset,
  onSave,
}: {
  job: CronJob;
  highlight?: boolean;
  busy: boolean;
  onEnable: () => void;
  onDisable: () => void;
  onReset: () => void;
  onSave: (expression: string, timezone: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);

  const lastRun = parseServerTime(job.last_run);
  const nextRun = parseServerTime(job.next_run);

  return (
    <div
      className={`card cron-card ${job.enabled ? "" : "cron-disabled"} ${highlight ? "cron-highlight" : ""}`}
    >
      <div className="cron-card-header">
        <div className="cron-target">
          <span className="card-label">Scheduled dataset update</span>
          <Link to={`/datasets/${encodeURIComponent(job.dataset)}`} className="cron-dataset mono">
            {job.dataset}
          </Link>
          <span className="cron-target-meta">
            <code className="cron-operation-value">{job.operation}</code>
            <span className="muted">{job.source} schedule</span>
          </span>
        </div>
        <span className={`badge ${job.enabled ? "bool-yes" : "bool-no"}`}>
          {job.enabled ? "enabled" : "disabled"}
        </span>
      </div>

      {editing ? (
        <CronEditor
          expression={job.expression}
          timezone={job.timezone}
          busy={busy}
          onSave={(expr, tz) => {
            void onSave(expr, tz).then((ok) => {
              if (ok) setEditing(false);
            });
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div className="cron-timing">
          <div className="cron-next-run">
            <span className="card-label">Next update</span>
            <strong>
              {nextRun !== null ? <Time unix={nextRun} timeZone={job.timezone} /> : "Not scheduled"}
            </strong>
            <span className="muted">{job.timezone}</span>
          </div>
          <div className="cron-schedule">
            <span className="card-label">Runs</span>
            <strong>{humanizeCron(job.expression)}</strong>
            <code className="muted">{job.expression}</code>
          </div>
          <div className="cron-last-run">
            <span className="card-label">Last run</span>
            <span>{lastRun !== null ? <Time unix={lastRun} /> : "—"}</span>
          </div>
        </div>
      )}

      <div className="cron-actions">
        {job.enabled ? (
          <button className="btn" disabled={busy} onClick={onDisable}>
            Disable
          </button>
        ) : (
          <button className="btn btn-primary" disabled={busy} onClick={onEnable}>
            Enable
          </button>
        )}{" "}
        {!editing && (
          <>
            <button className="btn" disabled={busy} onClick={() => setEditing(true)}>
              Edit schedule
            </button>
            <button className="link-button" disabled={busy} onClick={onReset}>
              Reset to suggested
            </button>
          </>
        )}
      </div>
    </div>
  );
}
