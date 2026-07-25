import { useCallback } from "react";
import {
  listDatasets,
  listDatasetsStatus,
  listTasks,
  type DatasetDescription,
  type DatasetStatus,
  type TaskHandle,
} from "../api";
import { DatasetCard } from "../components/DatasetCard";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  Loading,
} from "../components/common";
import { useLiveData } from "../hooks";

interface DatasetsData {
  datasets: DatasetDescription[];
  statuses: Record<string, DatasetStatus>;
  tasksByDataset: Record<string, TaskHandle[]>;
}

export default function DatasetsPage() {
  const loader = useCallback(async (): Promise<DatasetsData> => {
    const [d, s, t] = await Promise.all([
      listDatasets(),
      listDatasetsStatus(),
      listTasks(),
    ]);
    const tasksByDataset: Record<string, TaskHandle[]> = {};
    for (const task of t.items) {
      (tasksByDataset[task.dataset] ??= []).push(task);
    }
    return {
      datasets: d.items,
      statuses: Object.fromEntries(s.items.map((item) => [item.name, item])),
      tasksByDataset,
    };
  }, []);

  // Slow poll: cards stay fresh after operations run.
  const live = useLiveData(loader, 12_000);
  const { data } = live;

  if (!data && !live.error) return <Loading />;

  return (
    <div>
      <div className="page-head">
        <h1>Datasets</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={live.error} />

      {data && data.datasets.length === 0 && (
        <EmptyState>
          No datasets are registered in this workspace. Install a dataset plugin to
          register one, then configure its settings here.
        </EmptyState>
      )}

      {data && data.datasets.length > 0 && (
        <div className="dataset-grid">
          {data.datasets.map((d) => (
            <DatasetCard
              key={d.name}
              name={d.name}
              state={d.state}
              provider={d.provider}
              providerReady={d.provider_ready}
              updateReady={data.statuses[d.name]?.update_ready ?? false}
              missingRequired={d.settings
                .filter((s) => s.required && !s.configured)
                .map((s) => s.key)}
              capabilities={d.capabilities}
              publicationId={d.publication_id}
              status={data.statuses[d.name] ?? null}
              tasks={data.tasksByDataset[d.name] ?? []}
            />
          ))}
        </div>
      )}
    </div>
  );
}
