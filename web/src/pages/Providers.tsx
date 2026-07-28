import { useCallback } from "react";
import { Link } from "react-router";
import { listDatasets, listProviders, type Provider } from "../api";
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

function familyLabel(family: string[] | undefined): string {
  return family && family.length > 0
    ? family.map(readableName).join(" / ")
    : "Other providers";
}

function readableName(name: string): string {
  return name
    .slice(name.lastIndexOf("/") + 1)
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
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
  const readyCount = data?.providers.filter((provider) => provider.configured ?? provider.ready).length ?? 0;
  const setupCount = (data?.providers.length ?? 0) - readyCount;

  return (
    <div>
      <header className="provider-index-header">
        <div>
          <h1>Providers</h1>
          <p>Connections that supply data to your datasets.</p>
        </div>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </header>
      <ConnectionWarning error={live.error} />
      {data && data.providers.length === 0 && (
        <EmptyState>
          No providers registered — providers come with installed dataset plugins.
        </EmptyState>
      )}
      {data && data.providers.length > 0 && (
        <>
          <div className="provider-index-summary">
            <div><strong>{readyCount}</strong><span>ready</span></div>
            <div><strong>{setupCount}</strong><span>need setup</span></div>
            <div><strong>{data.providers.length}</strong><span>providers</span></div>
          </div>
          <div className="plugin-family-list">
          {Object.entries(
            data.providers.reduce<Record<string, Provider[]>>((families, provider) => {
              const label = familyLabel(provider.family);
              (families[label] ??= []).push(provider);
              return families;
            }, {}),
          )
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([family, providers]) => (
              <section key={family} className="plugin-family">
                <h2 className="plugin-family-title">{family}</h2>
                <div className="provider-grid">
                  {providers.map((p) => {
                    const configured = p.configured ?? p.ready;
                    const count = data.datasetCounts[p.name] ?? 0;
                    return (
                      <article key={p.name} className={`card provider-card ${configured ? "is-ready" : "needs-setup"}`}>
                        <div className="provider-card-heading">
                          <div>
                            <h3>{readableName(p.name)}</h3>
                            <span className="mono muted">{p.name}</span>
                          </div>
                          <span className={`badge ${configured ? "bool-yes" : "bool-no"}`}>
                            {configured ? "ready" : "setup needed"}
                          </span>
                        </div>
                        <p className="provider-card-message">
                          {configured
                            ? `${count === 0 ? "No datasets use this provider yet" : `${count} dataset${count === 1 ? "" : "s"} available`}.`
                            : "Add credentials before using its datasets."}
                        </p>
                        <div className="provider-card-footer">
                          <span className="muted">{p.mode} mode</span>
                          <Link to={`/providers/${encodeURIComponent(p.name)}`}>
                            {configured ? "View provider" : "Set up provider"} →
                          </Link>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
