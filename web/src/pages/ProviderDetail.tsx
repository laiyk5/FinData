import { useCallback, useState } from "react";
import { Link, useParams } from "react-router";
import {
  checkProvider,
  errorMessage,
  getConfigKeys,
  listDatasets,
  listProviders,
  type ConfigKey,
  type Provider,
} from "../api";
import { providerConfigLabel } from "../readiness";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  Loading,
} from "../components/common";
import { ProvidersIcon } from "../components/icons";
import { useLiveData } from "../hooks";

type CheckResult =
  | { ready: boolean; authenticated: boolean; mode: string }
  | { error: string };

interface ProviderData {
  provider: Provider | null;
  configKeys: Record<string, ConfigKey>;
  datasets: string[];
}

/**
 * Provider detail — the provider's control surface: configured state with
 * missing-keys statement, secret fields with per-field state, the Check
 * probe, the Configure jump, and the datasets using this provider.
 */
export default function ProviderDetailPage() {
  const { name = "" } = useParams();
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [checkBusy, setCheckBusy] = useState(false);

  const loader = useCallback(async (): Promise<ProviderData> => {
    const [providers, keys, datasets] = await Promise.all([
      listProviders(),
      getConfigKeys().catch(() => ({ items: [] as ConfigKey[] })),
      listDatasets().catch(() => ({ items: [] as { name: string; provider: string }[] })),
    ]);
    return {
      provider: providers.items.find((p) => p.name === name) ?? null,
      configKeys: Object.fromEntries(keys.items.map((k) => [k.key, k])),
      datasets: datasets.items.filter((d) => d.provider === name).map((d) => d.name),
    };
  }, [name]);

  const live = useLiveData(loader, 12_000);
  const { data } = live;

  const runCheck = async (): Promise<void> => {
    setCheckBusy(true);
    try {
      const r = await checkProvider(name);
      setCheck({ ready: r.ready, authenticated: r.authenticated, mode: r.mode });
    } catch (err) {
      setCheck({ error: errorMessage(err) });
    } finally {
      setCheckBusy(false);
    }
  };

  if (!data && !live.error) return <Loading />;

  const provider = data?.provider ?? null;

  return (
    <div>
      <div className="page-head">
        <div className="page-head-main">
          <h1>
            <span className="provider-head-icon">
              <ProvidersIcon />
            </span>
            <span className="mono">{name}</span>{" "}
            {provider && (
              <>
                <span className={`badge mode-${provider.mode}`}>{provider.mode}</span>{" "}
                <span className={`badge ${(provider.configured ?? provider.ready) ? "bool-yes" : "bool-no"}`}>
                  {providerConfigLabel(provider.configured ?? provider.ready)}
                </span>
              </>
            )}
          </h1>
          <div className="muted">
            <Link to="/providers">← all providers</Link>
          </div>
        </div>
        <div className="page-head-actions">
          <FreshnessNote lastUpdated={live.lastUpdated} />
          {provider && (
            <Link
              className="btn btn-primary"
              to={`/config?q=${encodeURIComponent(`provider.${provider.name}`)}`}
            >
              Configure
            </Link>
          )}
        </div>
      </div>
      <ConnectionWarning error={live.error} />

      {data && provider === null && (
        <EmptyState>
          No provider named <span className="mono">{name}</span> is registered in this
          workspace. <Link to="/providers">Back to the providers list</Link>.
        </EmptyState>
      )}

      {provider && data && (
        <>
          <div className="provider-detail-grid">
            <SecretFields provider={provider} configKeys={data.configKeys} />

            <div className="panel">
              <h3>Check</h3>
              <div className="form-row" style={{ alignItems: "center" }}>
                <button className="btn" disabled={checkBusy} onClick={() => void runCheck()}>
                  {checkBusy ? "Checking…" : "Run authenticated probe"}
                </button>
                {check && "error" in check && (
                  <span className="warning-text">{check.error}</span>
                )}
                {check && !("error" in check) && (
                  <span className="muted">
                    probe: ready {check.ready ? "yes" : "no"}, authenticated{" "}
                    {check.authenticated ? "yes" : "no"}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="panel">
            <h3>Datasets using {provider.name}</h3>
            {data.datasets.length === 0 ? (
              <EmptyState>No registered dataset uses this provider.</EmptyState>
            ) : (
              <div className="chips">
                {data.datasets.map((d) => (
                  <Link key={d} to={`/datasets/${encodeURIComponent(d)}`} className="chip mono">
                    {d}
                  </Link>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SecretFields({
  provider,
  configKeys,
}: {
  provider: Provider;
  configKeys: Record<string, ConfigKey>;
}) {
  const secretFields = provider.secret_fields ?? [];
  const missing = secretFields.filter(
    (f) => configKeys[`provider.${provider.name}.${f}`]?.configured === false,
  );

  return (
    <div className="panel">
      <h3>Configuration</h3>
      {missing.length > 0 && (
        <p className="warning-text">
          Missing config keys:{" "}
          {missing.map((f) => `provider.${provider.name}.${f}`).join(", ")} — set them in{" "}
          <Link to={`/config?q=${encodeURIComponent(`provider.${provider.name}`)}`}>
            Config
          </Link>
          .
        </p>
      )}
      {secretFields.length === 0 ? (
        <p className="muted">This provider declares no secret fields.</p>
      ) : (
        <div className="provider-secrets">
          {secretFields.map((field) => {
            const key = `provider.${provider.name}.${field}`;
            const configured = configKeys[key]?.configured;
            return (
              <div key={field} className="provider-secret-row">
                <Link to={`/config?q=${encodeURIComponent(key)}`} className="mono">
                  {key}
                </Link>{" "}
                <span className={`badge ${configured ? "bool-yes" : "bool-no"}`}>
                  {configured ? "configured" : "not configured"}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
