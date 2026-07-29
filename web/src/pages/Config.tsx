import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  deleteConfig,
  errorMessage,
  getConfig,
  getConfigKeys,
  setConfig,
  type ConfigKey,
} from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SettingEditor, renderStoredValue } from "../components/SettingEditor";
import { useToast } from "../components/Toast";
import { EmptyState, ErrorBanner, Loading } from "../components/common";
import { DatabaseIcon, GearIcon, ProvidersIcon } from "../components/icons";
import { filterConfigKeys, groupConfigKeys, type ConfigGroupKind } from "../configGroups";

function GroupIcon({ kind }: { kind: ConfigGroupKind | "other" }) {
  if (kind === "provider") return <ProvidersIcon />;
  if (kind === "dataset") return <DatabaseIcon />;
  return <GearIcon />;
}

function settingLabel(key: string): string {
  const segments = key.split(".");
  const segment = segments[segments.length - 1] ?? key;
  return segment.replace(/_/g, " ").replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

function groupLabel(kind: ConfigGroupKind | "other", label: string): string {
  if (kind === "core") return "Workspace";
  const [, name = label] = label.split(": ", 2);
  return `${kind === "provider" ? "Provider" : kind === "dataset" ? "Dataset" : "Other"} · ${name}`;
}

export default function ConfigPage() {
  const { notify } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filter, setFilter] = useState(searchParams.get("q") ?? "");
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [declared, setDeclared] = useState<ConfigKey[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [unsetKey, setUnsetKey] = useState<string | null>(null);
  const [unsetBusy, setUnsetBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [v, k] = await Promise.all([getConfig(), getConfigKeys()]);
      setValues(v.values);
      setDeclared(k.items);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const updateFilter = (q: string): void => {
    setFilter(q);
    setSearchParams(q.trim() ? { q: q.trim() } : {}, { replace: true });
  };

  const confirmUnset = async (): Promise<void> => {
    if (!unsetKey) return;
    setUnsetBusy(true);
    try {
      await deleteConfig(unsetKey);
      notify("success", `removed ${unsetKey}`);
      setUnsetKey(null);
      await refresh();
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setUnsetBusy(false);
    }
  };

  const groups = useMemo(() => {
    return groupConfigKeys(filterConfigKeys(declared ?? [], filter));
  }, [declared, filter]);

  const configuredCount = useMemo(
    () => (declared ?? []).filter((item) => item.configured).length,
    [declared],
  );

  const undeclared = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const declaredKeys = new Set((declared ?? []).map((k) => k.key));
    return Object.keys(values ?? {})
      .filter((k) => !declaredKeys.has(k))
      .filter((k) => q === "" || k.toLowerCase().includes(q))
      .sort();
  }, [declared, values, filter]);

  if ((!values || !declared) && !error) return <Loading />;

  return (
    <div>
      <div className="page-head">
        <div className="page-head-main">
          <h1>Configuration</h1>
          <p>Manage workspace preferences, provider connections, and dataset defaults.</p>
        </div>
      </div>
      <ErrorBanner error={error} />

      <section className="config-intro panel">
        <div>
          <p className="config-intro-title">Find a setting</p>
          <p className="muted">Changes are saved to this workspace. Plugin changes apply after a plugin reload.</p>
        </div>
        <div className="config-summary" aria-label="Configuration summary">
          <span><strong>{configuredCount}</strong> configured</span>
          <span><strong>{declared?.length ?? 0}</strong> available</span>
        </div>
        <label className="config-filter">
          <span>Search settings</span>
          <input
            type="search"
            value={filter}
            onChange={(e) => updateFilter(e.target.value)}
            placeholder="Name or description"
            aria-label="filter config keys"
          />
        </label>
      </section>

      {groups.length === 0 && undeclared.length === 0 && (
        <EmptyState>
          {filter.trim()
            ? `No config keys match "${filter.trim()}".`
            : "No configuration keys are declared yet."}
        </EmptyState>
      )}

      {groups.map((group) => (
        <details
          key={group.label}
          className="panel config-group"
          open={filter.trim() !== "" || group.kind !== "dataset"}
        >
          <summary className="config-group-head">
            <GroupIcon kind={group.kind} />
            <span className="config-group-title">
              {group.kind === "provider" && group.name !== null ? (
                <Link to={`/providers/${encodeURIComponent(group.name)}`}>{groupLabel(group.kind, group.label)}</Link>
              ) : group.kind === "dataset" && group.name !== null ? (
                <Link to={`/datasets/${encodeURIComponent(group.name)}`}>{groupLabel(group.kind, group.label)}</Link>
              ) : (
                groupLabel(group.kind, group.label)
              )}
            </span>
            <span className="muted config-group-count">
              {group.keys.filter((item) => item.configured).length} configured · {group.keys.length} setting{group.keys.length === 1 ? "" : "s"}
            </span>
          </summary>
          <div className="config-group-body">
            {group.keys.map((item) => (
              <div key={item.key} className="setting-row">
                <div className="setting-heading">
                  <h4>{settingLabel(item.key)}</h4>
                  <span className="mono setting-key">{item.key}</span>
                </div>
                {item.help && <p className="muted setting-help">{item.help}</p>}
                <SettingEditor
                  schema={item.schema}
                  configured={item.configured}
                  secret={item.secret}
                  required={item.required}
                  defaultValue={item.default}
                  allowEnvRef={item.secret}
                  hasCurrentValue={
                    values !== null &&
                    Object.prototype.hasOwnProperty.call(values, item.key)
                  }
                  currentValue={values?.[item.key]}
                  onSet={async (value) => {
                    try {
                      await setConfig(item.key, value);
                      notify("success", `set ${item.key}`);
                      await refresh();
                    } catch (err) {
                      notify("error", errorMessage(err));
                      throw err;
                    }
                  }}
                  onUnset={async () => {
                    setUnsetKey(item.key);
                  }}
                />
              </div>
            ))}
          </div>
        </details>
      ))}

      {undeclared.length > 0 && (
        <section className="panel config-group">
          <h3 className="config-group-head config-group-static">
            <GearIcon />
            Legacy or unrecognized values
            <span className="muted config-group-count">
              {undeclared.length} key{undeclared.length === 1 ? "" : "s"}
            </span>
          </h3>
          <p className="muted config-other-help">These values are no longer declared by an installed plugin. You can remove them safely.</p>
          {undeclared.map((k) => (
            <div key={k} className="setting-row">
              <div className="mono setting-key">{k}</div>
              <div className="muted setting-current-line">
                current: <span className="mono">{renderStoredValue((values ?? {})[k])}</span>
              </div>
              <button className="btn" onClick={() => setUnsetKey(k)}>
                Unset
              </button>
            </div>
          ))}
        </section>
      )}

      <ConfirmDialog
        open={unsetKey !== null}
        title="Unset config key"
        message={
          <>
            Remove the configured value for <span className="mono">{unsetKey}</span>?
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
