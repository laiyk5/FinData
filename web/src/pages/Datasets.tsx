import { useCallback, useMemo, useState } from "react";
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

interface DatasetFamilyNode {
  datasets: DatasetDescription[];
  children: Record<string, DatasetFamilyNode>;
}

type DatasetFilter = "all" | "attention" | "runnable" | "has_data";

function datasetFamilyTree(datasets: DatasetDescription[]): DatasetFamilyNode {
  const root: DatasetFamilyNode = { datasets: [], children: {} };
  for (const dataset of datasets) {
    const segments = dataset.family?.filter(Boolean) ?? [];
    let node = root;
    for (const segment of segments.length > 0 ? segments : ["Unclassified"]) {
      node = (node.children[segment] ??= { datasets: [], children: {} });
    }
    node.datasets.push(dataset);
  }
  return root;
}

function datasetsIn(node: DatasetFamilyNode): number {
  return (
    node.datasets.length +
    Object.values(node.children).reduce((total, child) => total + datasetsIn(child), 0)
  );
}

function DatasetFamilyGroup({
  name,
  node,
  depth,
  data,
}: {
  name: string;
  node: DatasetFamilyNode;
  depth: number;
  data: DatasetsData;
}) {
  const childGroups = Object.entries(node.children).sort(([left], [right]) =>
    left.localeCompare(right),
  );

  return (
    <section className="dataset-family-group" data-depth={depth}>
      {depth === 0 ? (
        <h2 className="dataset-family-name">
          {name} <span>{datasetsIn(node)}</span>
        </h2>
      ) : (
        <h3 className="dataset-family-name">
          {name} <span>{datasetsIn(node)}</span>
        </h3>
      )}
      {node.datasets.length > 0 && (
        <div className="dataset-grid">
          {node.datasets
            .slice()
            .sort((left, right) => left.name.localeCompare(right.name))
            .map((dataset) => (
              <DatasetCard
                key={dataset.name}
                name={dataset.name}
                state={dataset.state}
                provider={dataset.provider}
                providerReady={dataset.provider_ready}
                updateReady={data.statuses[dataset.name]?.update_ready ?? false}
                missingRequired={dataset.settings
                  .filter((setting) => setting.required && !setting.configured)
                  .map((setting) => setting.key)}
                capabilities={dataset.capabilities}
                publicationId={dataset.publication_id}
                status={data.statuses[dataset.name] ?? null}
                tasks={data.tasksByDataset[dataset.name] ?? []}
              />
            ))}
        </div>
      )}
      {childGroups.length > 0 && (
        <div className="dataset-family-children">
          {childGroups.map(([childName, childNode]) => (
            <DatasetFamilyGroup
              key={childName}
              name={childName}
              node={childNode}
              depth={depth + 1}
              data={data}
            />
          ))}
        </div>
      )}
    </section>
  );
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
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<DatasetFilter>("all");
  const [family, setFamily] = useState<string | null>(null);

  const families = useMemo(
    () =>
      Array.from(
        new Set(
          (data?.datasets ?? []).map((dataset) => dataset.family?.[0] || "Unclassified"),
        ),
      ).sort((left, right) => left.localeCompare(right)),
    [data],
  );
  const matchingDatasets = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    return data.datasets.filter((dataset) => {
      const status = data.statuses[dataset.name];
      const matchesQuery =
        needle.length === 0 ||
        [dataset.name, dataset.provider, ...(dataset.family ?? [])]
          .join(" ")
          .toLowerCase()
          .includes(needle);
      const matchesFamily = family === null || (dataset.family?.[0] || "Unclassified") === family;
      const matchesFilter =
        filter === "all" ||
        (filter === "attention" && !status?.update_ready) ||
        (filter === "runnable" && Boolean(status?.update_ready)) ||
        (filter === "has_data" && dataset.state === "ready");
      return matchesQuery && matchesFamily && matchesFilter;
    });
  }, [data, family, filter, query]);

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
        <>
          <section className="dataset-discovery" aria-label="Find datasets">
            <div className="dataset-search-row">
              <label className="dataset-search">
                <span>Find a dataset</span>
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Name, provider, or family"
                />
              </label>
              <span className="dataset-result-count">
                {matchingDatasets.length} of {data.datasets.length} datasets
              </span>
            </div>
            <div className="dataset-filter-row">
              <div className="filter-chips" aria-label="Dataset status filter">
                {(
                  [
                    ["all", "All"],
                    ["runnable", "Update ready"],
                    ["attention", "Needs attention"],
                    ["has_data", "Has data"],
                  ] as [DatasetFilter, string][]
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={`filter-chip ${filter === value ? "active" : ""}`}
                    onClick={() => setFilter(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {families.length > 1 && (
                <div className="filter-chips" aria-label="Dataset family filter">
                  <button
                    type="button"
                    className={`filter-chip ${family === null ? "active" : ""}`}
                    onClick={() => setFamily(null)}
                  >
                    All families
                  </button>
                  {families.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={`filter-chip ${family === item ? "active" : ""}`}
                      onClick={() => setFamily(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </section>

          {matchingDatasets.length === 0 ? (
            <EmptyState>No datasets match these filters.</EmptyState>
          ) : (
            <div className="dataset-family-tree">
              {Object.entries(datasetFamilyTree(matchingDatasets).children)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([name, node]) => (
              <DatasetFamilyGroup
                key={name}
                name={name}
                node={node}
                depth={0}
                data={data}
              />
            ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
