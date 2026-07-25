import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import {
  TERMINAL_STATUSES,
  createTask,
  deleteConfig,
  errorMessage,
  getConfig,
  getDataset,
  getDatasetStatus,
  listDatasetsStatus,
  listTasks,
  planOperation,
  resetDataset,
  setConfig,
  type DatasetDescription,
  type DatasetStatus,
  type TaskHandle,
} from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DatasetDotStatus } from "../components/DatasetCard";
import { DatasetCoverage, DatasetFreshness } from "../components/DatasetCoverage";
import { RunUpdateButton } from "../components/RunUpdate";
import { SettingEditor } from "../components/SettingEditor";
import { TaskList } from "../components/TaskList";
import { useToast } from "../components/Toast";
import {
  ConnectionWarning,
  CopyableId,
  EmptyState,
  ErrorBanner,
  FreshnessNote,
  JsonBlock,
  Loading,
  StateDot,
} from "../components/common";
import { useLiveData } from "../hooks";
import { EMPTY_FIELD, buildOperands, fieldsForOperation, type FieldState } from "../operationForm";
import { updateBlockedReason } from "../readiness";

const TABS = ["overview", "run", "settings", "activity", "danger"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  overview: "Overview",
  run: "Run",
  settings: "Settings",
  activity: "Activity",
  danger: "Danger zone",
};

interface DetailData {
  dataset: DatasetDescription;
  status: DatasetStatus | null;
  allStatuses: Record<string, DatasetStatus>;
  tasks: TaskHandle[];
}

/** Facts for the shared readiness helpers. */
function readinessFacts(dataset: DatasetDescription, status: DatasetStatus | null) {
  return {
    state: dataset.state,
    providerReady: dataset.provider_ready,
    updateReady: status?.update_ready ?? false,
    missingRequired: dataset.settings
      .filter((s) => s.required && !s.configured)
      .map((s) => s.key),
  };
}

export default function DatasetDetailPage() {
  const { name = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(tabParam ?? "")
    ? (tabParam as Tab)
    : "overview";

  const loader = useCallback(async (): Promise<DetailData> => {
    const [d, s, all, tasks] = await Promise.all([
      getDataset(name),
      getDatasetStatus(name).catch(() => null),
      listDatasetsStatus().catch(() => ({ items: [] as DatasetStatus[] })),
      listTasks({ dataset: name }).catch(() => ({ items: [] as TaskHandle[] })),
    ]);
    return {
      dataset: d,
      status: s,
      allStatuses: Object.fromEntries(all.items.map((item) => [item.name, item])),
      tasks: tasks.items,
    };
  }, [name]);

  const live = useLiveData(loader, 12_000);
  const { data } = live;

  if (!data && !live.error) return <Loading />;

  const facts = data ? readinessFacts(data.dataset, data.status) : null;
  const blocked = facts ? updateBlockedReason(facts) : null;

  return (
    <div>
      <ConnectionWarning error={live.error} />
      {data && facts && (
        <>
          <div className="page-head">
            <div className="page-head-main">
              <h1>
                <span className="mono">{data.dataset.name}</span>{" "}
                <DatasetFreshness state={data.dataset.state} tasks={data.tasks} />
              </h1>
              <DatasetDotStatus provider={data.dataset.provider} facts={facts} />
            </div>
            <div className="page-head-actions">
              <Link
                className="btn"
                to={`/events?dataset=${encodeURIComponent(data.dataset.name)}`}
              >
                Events
              </Link>
              <Link
                className="btn"
                to={`/cron?dataset=${encodeURIComponent(data.dataset.name)}`}
              >
                Cron
              </Link>
              <FreshnessNote lastUpdated={live.lastUpdated} />
              <RunUpdateButton
                dataset={data.dataset.name}
                disabled={blocked !== null}
                disabledReason={blocked ?? undefined}
              />
            </div>
          </div>

          <div className="tabs">
            {TABS.map((key) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setSearchParams(key === "overview" ? {} : { tab: key })}
              >
                {TAB_LABELS[key]}
              </button>
            ))}
          </div>

          {tab === "overview" && (
            <OverviewTab
              dataset={data.dataset}
              status={data.status}
              allStatuses={data.allStatuses}
              tasks={data.tasks}
            />
          )}
          {tab === "run" && <RunTab dataset={data.dataset} />}
          {tab === "settings" && (
            <SettingsTab dataset={data.dataset} onChanged={live.refresh} />
          )}
          {tab === "activity" && <ActivityTab dataset={data.dataset.name} />}
          {tab === "danger" && (
            <DangerTab name={data.dataset.name} onReset={live.refresh} />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function OverviewTab({
  dataset,
  status,
  allStatuses,
  tasks,
}: {
  dataset: DatasetDescription;
  status: DatasetStatus | null;
  allStatuses: Record<string, DatasetStatus>;
  tasks: TaskHandle[];
}) {
  return (
    <div>
      <div className="panel">
        <h3>Coverage</h3>
        <p>
          <DatasetCoverage
            capabilities={dataset.capabilities}
            publicationId={dataset.publication_id}
            status={status}
            tasks={tasks}
          />
        </p>
        <dl className="kv">
          <dt>storage</dt>
          <dd className="mono">{dataset.storage}</dd>
          <dt>publication</dt>
          <dd>
            {dataset.publication_id ? (
              <CopyableId id={dataset.publication_id} />
            ) : (
              <span className="muted">—</span>
            )}
          </dd>
        </dl>
      </div>

      <div className="panel">
        <h3>Dependencies</h3>
        {dataset.dependencies.length === 0 ? (
          <p className="muted">This dataset has no dependencies.</p>
        ) : (
          <div className="chips">
            {dataset.dependencies.map((dep) => {
              const st = allStatuses[dep];
              return (
                <Link key={dep} to={`/datasets/${encodeURIComponent(dep)}`} className="chip">
                  {st && <StateDot state={st.state} />}
                  <span className="mono">{dep}</span>
                </Link>
              );
            })}
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Capabilities</h3>
        {Object.keys(dataset.capabilities).length === 0 ? (
          <p className="muted">This dataset declares no capabilities.</p>
        ) : (
          <dl className="kv">
            {Object.entries(dataset.capabilities).map(([key, value]) => (
              <CapabilityFact key={key} name={key} value={value} />
            ))}
          </dl>
        )}
        <JsonBlock value={dataset.capabilities} label="capabilities JSON" />
      </div>
    </div>
  );
}

function CapabilityFact({ name, value }: { name: string; value: unknown }) {
  return (
    <>
      <dt>{name}</dt>
      <dd>
        {typeof value === "boolean" ? (
          <span className={`badge ${value ? "bool-yes" : "bool-no"}`}>
            {value ? "yes" : "no"}
          </span>
        ) : value === null || typeof value !== "object" ? (
          String(value)
        ) : (
          <span className="mono">{JSON.stringify(value)}</span>
        )}
      </dd>
    </>
  );
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

/** Renders the operands as the equivalent `task run` CLI flags. */
function cliForOperands(dataset: string, operation: string, operands: Record<string, unknown>): string {
  const parts = [`findata task run ${dataset} ${operation}`];
  for (const [key, value] of Object.entries(operands)) {
    if (Array.isArray(value)) {
      for (const item of value) parts.push(`--param ${key}=${String(item)}`);
    } else {
      parts.push(`--param ${key}=${String(value)}`);
    }
  }
  return parts.join(" ");
}

function RunTab({ dataset }: { dataset: DatasetDescription }) {
  const navigate = useNavigate();
  const { notify } = useToast();
  const ops = dataset.operations;
  const [opName, setOpName] = useState(ops[0]?.name ?? "");
  const op = ops.find((o) => o.name === opName);
  const fields = useMemo(() => (op ? fieldsForOperation(op) : []), [op]);
  const [values, setValues] = useState<Record<string, FieldState>>({});
  const [busy, setBusy] = useState<"dry" | "submit" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [plan, setPlan] = useState<Record<string, unknown> | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    setValues({});
    setPlan(null);
    setError(null);
  }, [opName]);

  const fieldState = (name: string): FieldState => values[name] ?? EMPTY_FIELD;
  const patchField = (name: string, patch: Partial<FieldState>): void =>
    setValues((v) => ({ ...v, [name]: { ...(v[name] ?? EMPTY_FIELD), ...patch } }));

  const dryRun = async (): Promise<void> => {
    setBusy("dry");
    setError(null);
    setPlan(null);
    try {
      const operands = buildOperands(fields, values);
      setPlan(await planOperation(dataset.name, opName, operands));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const submit = async (): Promise<void> => {
    setBusy("submit");
    setError(null);
    try {
      const operands = buildOperands(fields, values);
      const res = await createTask({ dataset: dataset.name, operation: opName, operands });
      notify("success", `${opName} submitted for ${dataset.name}`);
      setConfirming(false);
      navigate(`/tasks/${encodeURIComponent(res.handle_id)}`);
    } catch (err) {
      setError(err);
      setBusy(null);
    }
  };

  if (ops.length === 0) {
    return <EmptyState>This dataset exposes no operations.</EmptyState>;
  }

  let cliPreview = "";
  try {
    cliPreview = cliForOperands(dataset.name, opName, buildOperands(fields, values));
  } catch {
    cliPreview = `findata task run ${dataset.name} ${opName}`;
  }

  return (
    <div>
      <div className="panel">
        <label className="field">
          <span className="field-label">operation</span>
          <select value={opName} onChange={(e) => setOpName(e.target.value)}>
            {ops.map((o) => (
              <option key={o.name} value={o.name}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
        {op?.help && <p className="muted">{op.help}</p>}

        {fields.length === 0 && <p className="muted">This operation takes no operands.</p>}
        {fields.map((field) => (
          <label key={field.name} className="field">
            <span className="field-label">
              {field.name} {field.required && <span className="req">*</span>}{" "}
              <span className="muted">({field.kind})</span>
            </span>
            {field.help && (
              <span className="muted" style={{ display: "block", fontSize: 12, marginBottom: 3 }}>
                {field.help}
              </span>
            )}
            {field.kind === "array" && (
              <textarea
                placeholder="one value per line"
                value={fieldState(field.name).text}
                onChange={(e) => patchField(field.name, { text: e.target.value })}
              />
            )}
            {field.kind === "date-range" && (
              <span className="form-row">
                <input
                  type="date"
                  value={fieldState(field.name).from}
                  onChange={(e) => patchField(field.name, { from: e.target.value })}
                />
                <span className="muted">to</span>
                <input
                  type="date"
                  value={fieldState(field.name).to}
                  onChange={(e) => patchField(field.name, { to: e.target.value })}
                />
              </span>
            )}
            {field.kind === "text" && (
              <input
                type="text"
                value={fieldState(field.name).text}
                onChange={(e) => patchField(field.name, { text: e.target.value })}
              />
            )}
          </label>
        ))}

        <ErrorBanner error={error} />
        <div className="form-row">
          <button className="btn" disabled={busy !== null || !op} onClick={() => void dryRun()}>
            {busy === "dry" ? "Planning…" : "Dry run"}
          </button>
          <button
            className="btn btn-primary"
            disabled={busy !== null || !op}
            onClick={() => setConfirming(true)}
          >
            Submit task
          </button>
        </div>
      </div>

      {plan && (
        <div className="panel">
          <h3>Plan</h3>
          <dl className="kv">
            {"strategy" in plan && (
              <>
                <dt>strategy</dt>
                <dd>{String(plan.strategy)}</dd>
              </>
            )}
            {"estimated_requests" in plan && (
              <>
                <dt>estimated requests</dt>
                <dd>{String(plan.estimated_requests)}</dd>
              </>
            )}
            {"dependencies" in plan && (
              <>
                <dt>dependencies</dt>
                <dd>
                  {Array.isArray(plan.dependencies) && plan.dependencies.length > 0 ? (
                    <span className="chips">
                      {plan.dependencies.map((d, i) =>
                        d !== null && typeof d === "object" ? (
                          <Link
                            key={i}
                            to={`/datasets/${encodeURIComponent(String((d as Record<string, unknown>).dataset))}`}
                            className="chip"
                          >
                            <StateDot
                              state={String((d as Record<string, unknown>).state)}
                            />
                            <span className="mono">
                              {String((d as Record<string, unknown>).dataset)}
                            </span>
                          </Link>
                        ) : (
                          <span key={i} className="chip mono">
                            {String(d)}
                          </span>
                        ),
                      )}
                    </span>
                  ) : (
                    <span className="muted">none</span>
                  )}
                </dd>
              </>
            )}
          </dl>
          <JsonBlock value={plan} label="raw plan JSON" />
        </div>
      )}

      <ConfirmDialog
        open={confirming}
        title="Submit task"
        message={
          <>
            Submit <span className="mono">{opName}</span> on{" "}
            <span className="mono">{dataset.name}</span>?
          </>
        }
        confirmLabel="Submit"
        cliCommand={cliPreview}
        busy={busy === "submit"}
        onConfirm={() => void submit()}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function SettingsTab({
  dataset,
  onChanged,
}: {
  dataset: DatasetDescription;
  onChanged: () => Promise<void>;
}) {
  const { notify } = useToast();
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [unsetKey, setUnsetKey] = useState<string | null>(null);
  const [unsetBusy, setUnsetBusy] = useState(false);

  const refreshConfig = useCallback(async () => {
    try {
      const r = await getConfig();
      setValues(r.values);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    void refreshConfig();
  }, [refreshConfig]);

  const confirmUnset = async (): Promise<void> => {
    if (!unsetKey) return;
    setUnsetBusy(true);
    try {
      await deleteConfig(unsetKey);
      notify("success", `removed ${unsetKey}`);
      setUnsetKey(null);
      await Promise.all([refreshConfig(), onChanged()]);
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setUnsetBusy(false);
    }
  };

  return (
    <div className="panel">
      <div className="settings-head">
        <Link
          to={`/config?q=${encodeURIComponent(`dataset.${dataset.name}`)}`}
          className="muted"
        >
          Open in Config →
        </Link>
      </div>
      {dataset.settings.length === 0 ? (
        <EmptyState>This dataset declares no settings.</EmptyState>
      ) : (
        dataset.settings.map((setting) => (
          <div key={setting.key} className="setting-row">
            <div className="mono setting-key">{setting.key}</div>
            {setting.help && <p className="muted setting-help">{setting.help}</p>}
            <SettingEditor
              schema={setting.schema}
              configured={setting.configured}
              required={setting.required}
              hasCurrentValue={
                values !== null &&
                Object.prototype.hasOwnProperty.call(values, setting.key)
              }
              currentValue={values?.[setting.key]}
              onSet={async (value) => {
                try {
                  await setConfig(setting.key, value);
                  notify("success", `set ${setting.key}`);
                  await Promise.all([refreshConfig(), onChanged()]);
                } catch (err) {
                  notify("error", errorMessage(err));
                  throw err;
                }
              }}
              onUnset={async () => {
                setUnsetKey(setting.key);
              }}
            />
          </div>
        ))
      )}
      <ErrorBanner error={error} />
      <ConfirmDialog
        open={unsetKey !== null}
        title="Unset setting"
        message={
          <>
            Remove the configured value for <span className="mono">{unsetKey}</span>? The
            dataset falls back to its default.
          </>
        }
        confirmLabel="Unset"
        danger
        cliCommand={`findata config unset ${unsetKey ?? ""}`}
        busy={unsetBusy}
        onConfirm={() => void confirmUnset()}
        onCancel={() => setUnsetKey(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity
// ---------------------------------------------------------------------------

function ActivityTab({ dataset }: { dataset: string }) {
  const [hasActive, setHasActive] = useState(false);
  const loader = useCallback(async () => {
    const r = await listTasks({ dataset, all: true });
    setHasActive(r.items.some((t) => !TERMINAL_STATUSES.has(t.status)));
    return r.items;
  }, [dataset]);
  const live = useLiveData<TaskHandle[]>(loader, hasActive ? 2_500 : 12_000);

  if (!live.data && !live.error) return <Loading />;

  return (
    <div>
      <div className="page-head">
        <span />
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={live.error} />
      {live.data && live.data.length === 0 && (
        <EmptyState>No tasks recorded for this dataset yet.</EmptyState>
      )}
      {live.data && live.data.length > 0 && (
        <TaskList items={live.data} onChanged={() => void live.refresh()} showDataset={false} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Danger zone
// ---------------------------------------------------------------------------

function DangerTab({ name, onReset }: { name: string; onReset: () => Promise<void> }) {
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const doReset = async (): Promise<void> => {
    setBusy(true);
    try {
      const r = await resetDataset(name);
      notify("success", `reset complete — dataset "${r.dataset}" is now ${r.state}`);
      setConfirming(false);
      await onReset();
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel danger-zone">
      <h3>Reset dataset</h3>
      <p className="warning-text">
        Reset replaces this dataset's published data with a new uninitialized database.
        Settings, task history, and other datasets are preserved.
      </p>
      <button className="btn btn-danger" onClick={() => setConfirming(true)}>
        Reset {name}
      </button>
      <ConfirmDialog
        open={confirming}
        title="Reset dataset"
        message={
          <>
            Reset replaces <span className="mono">{name}</span>'s published data with a new
            uninitialized database. Settings, task history, and other datasets are
            preserved.
          </>
        }
        confirmLabel="Reset"
        danger
        typedName={name}
        cliCommand={`findata dataset reset ${name} --yes`}
        busy={busy}
        onConfirm={() => void doReset()}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}
