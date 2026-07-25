import { useCallback } from "react";
import { Link } from "react-router";
import { listDatasets, listProviders, type Provider } from "../api";
import { providerConfigLabel } from "../readiness";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  Loading,
} from "../components/common";
import { useLiveData } from "../hooks";

interface ProvidersData {
  providers: Provider[];
  datasetCounts: Record<string, number>;
}

/**
 * Providers index — calm cards with only the provider's own facts. The
 * control surface (secrets, Check, Configure) lives on the detail page.
 */
export default function ProvidersPage() {
  const loader = useCallback(async (): Promise<ProvidersData> => {
    const [p, datasets] = await Promise.all([
      listProviders(),
      listDatasets().catch(() => ({ items: [] as { provider: string }[] })),
    ]);
    const datasetCounts: Record<string, number> = {};
    for (const d of datasets.items) {
      datasetCounts[d.provider] = (datasetCounts[d.provider] ?? 0) + 1;
    }
    return { providers: p.items, datasetCounts };
  }, []);

  const live = useLiveData(loader, 12_000);
  const { data } = live;

  if (!data && !live.error) return <Loading />;

  return (
    <div>
      <div className="page-head">
        <h1>Providers</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={live.error} />
      {data && data.providers.length === 0 && (
        <EmptyState>
          No providers registered — providers come with installed dataset plugins.
        </EmptyState>
      )}
      {data && data.providers.length > 0 && (
        <div className="provider-grid">
          {data.providers.map((p) => {
            const configured = p.configured ?? p.ready;
            const count = data.datasetCounts[p.name] ?? 0;
            return (
              <div key={p.name} className="card provider-card">
                <div className="health-head">
                  <Link to={`/providers/${encodeURIComponent(p.name)}`} className="mono">
                    {p.name}
                  </Link>
                  <span className="chips">
                    <span className={`badge mode-${p.mode}`}>{p.mode}</span>
                    <span className={`badge ${configured ? "bool-yes" : "bool-no"}`}>
                      {providerConfigLabel(configured)}
                    </span>
                  </span>
                </div>
                <div className="muted provider-card-datasets">
                  {count === 0
                    ? "no datasets use this provider"
                    : `${count} dataset${count === 1 ? "" : "s"}`}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
