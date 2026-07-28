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
import { ConnectionWarning, EmptyState, FreshnessNote, Loading } from "../components/common";
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

function readableName(name: string): string {
  return name
    .slice(name.lastIndexOf("/") + 1)
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function settingLabel(name: string): string {
  return name.replace(/[_-]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** Provider control surface: setup, connectivity, and dependent datasets. */
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
      provider: providers.items.find((provider) => provider.name === name) ?? null,
      configKeys: Object.fromEntries(keys.items.map((key) => [key.key, key])),
      datasets: datasets.items.filter((dataset) => dataset.provider === name).map((dataset) => dataset.name),
    };
  }, [name]);

  const live = useLiveData(loader, 12_000);
  const { data } = live;

  const runCheck = async (): Promise<void> => {
    setCheckBusy(true);
    try {
      const result = await checkProvider(name);
      setCheck({ ready: result.ready, authenticated: result.authenticated, mode: result.mode });
    } catch (error) {
      setCheck({ error: errorMessage(error) });
    } finally {
      setCheckBusy(false);
    }
  };

  if (!data && !live.error) return <Loading />;

  const provider = data?.provider ?? null;
  const secretFields = provider?.secret_fields ?? [];
  const missing = provider
    ? secretFields.filter((field) => data?.configKeys[`provider.${provider.name}.${field}`]?.configured === false)
    : [];
  const configured = provider?.configured ?? provider?.ready ?? false;

  return (
    <div>
      <ConnectionWarning error={live.error} />
      {data && provider === null && (
        <EmptyState>
          No provider named <span className="mono">{name}</span> is registered in this workspace. {" "}
          <Link to="/providers">Back to providers</Link>.
        </EmptyState>
      )}
      {provider && data && (
        <>
          <header className="provider-detail-header">
            <div className="provider-detail-title">
              <Link to="/providers" className="provider-detail-back">← Providers</Link>
              <div className="provider-detail-name-row">
                <span className="provider-head-icon"><ProvidersIcon /></span>
                <div>
                  <h1>{readableName(provider.name)}</h1>
                  <span className="mono muted">{provider.name}</span>
                </div>
              </div>
            </div>
            <div className="provider-detail-actions">
              <FreshnessNote lastUpdated={live.lastUpdated} />
              <Link className="btn btn-primary" to={`/config?q=${encodeURIComponent(`provider.${provider.name}`)}`}>
                {configured ? "Edit configuration" : "Configure provider"}
              </Link>
            </div>
          </header>

          <section className={`provider-readiness ${configured ? "is-ready" : "needs-setup"}`}>
            <div className="provider-readiness-icon">{configured ? "✓" : "!"}</div>
            <div>
              <p className="provider-readiness-eyebrow">Provider status</p>
              <h2>{configured ? "Ready to use" : "Setup needed"}</h2>
              <p>
                {configured
                  ? "Configuration is present. Run a connection check when you want to verify credentials."
                  : missing.length > 0
                    ? `Add ${missing.length === 1 ? "the required credential" : `${missing.length} required credentials`} to use this provider.`
                    : "This provider is not ready yet. Review its configuration to continue."}
              </p>
            </div>
            <div className="provider-readiness-meta">
              <span className={`badge mode-${provider.mode}`}>{provider.mode} mode</span>
              <span className={`badge ${configured ? "bool-yes" : "bool-no"}`}>
                {providerConfigLabel(configured)}
              </span>
            </div>
          </section>

          <div className="provider-detail-grid">
            <ConfigurationPanel provider={provider} configKeys={data.configKeys} missing={missing} />
            <ConnectionPanel check={check} busy={checkBusy} onCheck={runCheck} />
          </div>

          <DatasetPanel provider={provider.name} datasets={data.datasets} />
        </>
      )}
    </div>
  );
}

function ConfigurationPanel({ provider, configKeys, missing }: { provider: Provider; configKeys: Record<string, ConfigKey>; missing: string[] }) {
  const secretFields = provider.secret_fields ?? [];
  return (
    <section className="panel provider-configuration-panel">
      <div className="provider-panel-heading">
        <div><p className="eyebrow">Step 1</p><h2>Configuration</h2></div>
        <Link to={`/config?q=${encodeURIComponent(`provider.${provider.name}`)}`}>Open config</Link>
      </div>
      {secretFields.length === 0 ? (
        <p className="muted">No credentials are required by this provider.</p>
      ) : (
        <div className="provider-credential-list">
          {secretFields.map((field) => {
            const key = `provider.${provider.name}.${field}`;
            const isConfigured = configKeys[key]?.configured === true;
            return (
              <Link key={field} to={`/config?q=${encodeURIComponent(key)}`} className="provider-credential-row">
                <span className={`provider-credential-dot ${isConfigured ? "configured" : "missing"}`} />
                <span><strong>{settingLabel(field)}</strong><small className="mono">{key}</small></span>
                <span className={`badge ${isConfigured ? "bool-yes" : "bool-no"}`}>
                  {isConfigured ? "configured" : "required"}
                </span>
              </Link>
            );
          })}
        </div>
      )}
      {missing.length > 0 && <p className="provider-setup-note">Configure the required credentials to enable provider-backed operations.</p>}
    </section>
  );
}

function ConnectionPanel({ check, busy, onCheck }: { check: CheckResult | null; busy: boolean; onCheck: () => Promise<void> }) {
  const result = check && "error" in check
    ? { title: "Connection check failed", detail: check.error, tone: "error" }
    : check
      ? { title: check.ready && check.authenticated ? "Connection verified" : "Connection needs attention", detail: `Provider is ${check.ready ? "ready" : "not ready"}; authentication ${check.authenticated ? "succeeded" : "did not succeed"}.`, tone: check.ready && check.authenticated ? "success" : "warning" }
      : null;
  return (
    <section className="panel provider-connection-panel">
      <div className="provider-panel-heading"><div><p className="eyebrow">Step 2</p><h2>Connection check</h2></div></div>
      <p className="muted">Verify that this workspace can authenticate with the provider.</p>
      <button className="btn" disabled={busy} onClick={() => void onCheck()}>
        {busy ? "Checking connection…" : "Test connection"}
      </button>
      {result && <div className={`provider-check-result ${result.tone}`}><strong>{result.title}</strong><span>{result.detail}</span></div>}
    </section>
  );
}

function DatasetPanel({ provider, datasets }: { provider: string; datasets: string[] }) {
  return (
    <section className="panel provider-dataset-panel">
      <div className="provider-panel-heading">
        <div><p className="eyebrow">Available data</p><h2>Datasets</h2></div>
        <span className="muted">{datasets.length} available</span>
      </div>
      {datasets.length === 0 ? <EmptyState>No registered datasets use this provider.</EmptyState> : (
        <div className="provider-dataset-list">
          {datasets.map((dataset) => <Link key={dataset} to={`/datasets/${encodeURIComponent(dataset)}`} className="provider-dataset-row"><strong>{readableName(dataset)}</strong><span className="mono">{dataset}</span><span aria-hidden="true">→</span></Link>)}
        </div>
      )}
    </section>
  );
}
