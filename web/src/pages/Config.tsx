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
        <h1>Config</h1>
      </div>
      <ErrorBanner error={error} />

      <div className="config-filter">
        <input
          type="search"
          value={filter}
          onChange={(e) => updateFilter(e.target.value)}
          placeholder="Filter keys by name or description…"
          aria-label="filter config keys"
        />
      </div>

      {groups.length === 0 && undeclared.length === 0 && (
        <EmptyState>
          {filter.trim()
            ? `No config keys match "${filter.trim()}".`
            : "No configuration keys are declared yet."}
        </EmptyState>
      )}

      {groups.map((group) => (
        <div key={group.label} className="panel config-group">
          <h3 className="config-group-head">
            <GroupIcon kind={group.kind} />
            {group.kind === "provider" && group.name !== null ? (
              <Link to={`/providers/${encodeURIComponent(group.name)}`}>{group.label}</Link>
            ) : group.kind === "dataset" && group.name !== null ? (
              <Link to={`/datasets/${encodeURIComponent(group.name)}`}>{group.label}</Link>
            ) : (
              group.label
            )}
            <span className="muted config-group-count">
              {group.keys.length} key{group.keys.length === 1 ? "" : "s"}
            </span>
          </h3>
          {group.keys.map((item) => (
            <div key={item.key} className="setting-row">
              <div className="mono setting-key">{item.key}</div>
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
      ))}

      {undeclared.length > 0 && (
        <div className="panel config-group">
          <h3 className="config-group-head">
            <GearIcon />
            Other values
            <span className="muted config-group-count">
              {undeclared.length} key{undeclared.length === 1 ? "" : "s"}
            </span>
          </h3>
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
        </div>
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
