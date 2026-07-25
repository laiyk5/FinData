import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  cronDisable,
  cronEnable,
  cronReset,
  cronSchedule,
  errorMessage,
  listCron,
  listEvents,
  type CronJob,
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
}

export default function CronPage() {
  const { notify } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const datasetFilter = searchParams.get("dataset") ?? "";
  const [disabling, setDisabling] = useState<CronJob | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const loader = useCallback(async (): Promise<CronData> => {
    const [cron, events] = await Promise.all([listCron(), listEvents({ unread: true })]);
    return {
      jobs: cron.items,
      missed: events.items.filter((e) => !e.acknowledged && e.kind === "cron_missed"),
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

  if (!data && !live.error) return <Loading />;

  const jobs = data?.jobs ?? [];
  const visibleJobs = datasetFilter
    ? jobs.filter((j) => j.dataset === datasetFilter)
    : jobs;

  return (
    <div>
      <div className="page-head">
        <h1>Cron</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={live.error} />

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
            : "No cron jobs — jobs appear when a dataset plugin suggests an update schedule."}
        </EmptyState>
      )}

      {data && visibleJobs.length > 0 && (
        <div className="cron-list">
          {visibleJobs.map((job) => (
            <CronJobCard
              key={job.dataset}
              job={job}
              highlight={job.dataset === datasetFilter}
              busy={busyKey !== null}
              onEnable={() =>
                void run(`enable:${job.dataset}`, `enabled ${job.dataset}`, () =>
                  cronEnable(job.dataset),
                )
              }
              onDisable={() => setDisabling(job)}
              onReset={() =>
                void run(`reset:${job.dataset}`, `restored suggested schedule for ${job.dataset}`, () =>
                  cronReset(job.dataset),
                )
              }
              onSave={(expression, timezone) =>
                run(`schedule:${job.dataset}`, `schedule updated for ${job.dataset}`, () =>
                  cronSchedule(job.dataset, { expression, timezone }),
                )
              }
            />
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
    >      <div className="health-head">
        <Link to={`/datasets/${encodeURIComponent(job.dataset)}`} className="mono">
          {job.dataset}
        </Link>
        <span className="chips">
          <span className={`badge ${job.enabled ? "bool-yes" : "bool-no"}`}>
            {job.enabled ? "enabled" : "disabled"}
          </span>
          <span className="badge badge-source">{job.source}</span>
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
        <div className="cron-schedule">
          <span>{humanizeCron(job.expression)}</span>{" "}
          <code className="muted">{job.expression}</code>{" "}
          <span className="muted">({job.timezone})</span>
        </div>
      )}

      <div className="cron-runs muted">
        last run: {lastRun !== null ? <Time unix={lastRun} /> : "—"} · next run:{" "}
        {nextRun !== null ? <Time unix={nextRun} timeZone={job.timezone} /> : "—"}
      </div>

      <div className="health-actions">
        {job.enabled ? (
          <button className="btn" disabled={busy} onClick={onDisable}>
            Disable
          </button>
        ) : (
          <button className="btn" disabled={busy} onClick={onEnable}>
            Enable
          </button>
        )}{" "}
        {!editing && (
          <>
            <button className="btn" onClick={() => setEditing(true)}>
              Edit schedule
            </button>{" "}
            <button className="btn" disabled={busy} onClick={onReset}>
              Reset to suggested
            </button>
          </>
        )}
      </div>
    </div>
  );
}
