import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import {
  errorMessage,
  exportDatasetSql,
  queryDatasetSql,
  type DatasetSqlPreview,
} from "../api";
import { ConnectionWarning, EmptyState, ErrorBanner, Loading } from "../components/common";
import { useToast } from "../components/Toast";

function datasetLabel(name: string): string {
  return name.slice(name.lastIndexOf("/") + 1).replace(/_/g, " ");
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Run a guarded DataLoader SQL query and export precisely that result. */
export function DatasetDataWorkspace({
  name,
  preferredFormat = "csv",
  standalone = false,
}: {
  name: string;
  preferredFormat?: "csv" | "parquet";
  standalone?: boolean;
}) {
  const { notify } = useToast();
  const [sql, setSql] = useState("SELECT *\nFROM data");
  const [limit, setLimit] = useState(100);
  const [format, setFormat] = useState<"csv" | "parquet">(preferredFormat);
  const [preview, setPreview] = useState<DatasetSqlPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setPreview(await queryDatasetSql(name, sql, limit));
      setError(null);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }, [limit, name, sql]);

  useEffect(() => {
    setLoading(true);
    setSql("SELECT *\nFROM data");
    setLimit(100);
    void queryDatasetSql(name, "SELECT *\nFROM data", 100)
      .then((result) => {
        setPreview(result);
        setError(null);
      })
      .catch((reason) => setError(reason))
      .finally(() => setLoading(false));
  }, [name]);
  useEffect(() => { setFormat(preferredFormat); }, [preferredFormat]);

  const exportData = async (): Promise<void> => {
    setExporting(true);
    try {
      await exportDatasetSql(name, sql, format);
      notify("success", `${format.toUpperCase()} export started`);
    } catch (reason) {
      notify("error", errorMessage(reason));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="dataset-data-page">
      <header className="dataset-data-header">
        <div>
          {standalone && <Link to={`/datasets/${encodeURIComponent(name)}`} className="dataset-detail-back">← Dataset</Link>}
          <h1>{standalone ? `Explore ${datasetLabel(name)}` : "Query data"}</h1>
          <p className="mono muted">{name}</p>
        </div>
        <div className="dataset-data-export-actions">
          <select aria-label="Export format" value={format} onChange={(event) => setFormat(event.target.value as "csv" | "parquet")}><option value="csv">CSV</option><option value="parquet">Parquet</option></select>
          <button className="btn btn-primary" disabled={exporting} onClick={() => void exportData()}>{exporting ? "Preparing export…" : "Export query result"}</button>
        </div>
      </header>
      <p className="dataset-data-intro">Write one read-only <code>SELECT</code> query against <code>data</code>. The preview is limited for safety; export writes every row returned by this same query.</p>
      <ErrorBanner error={error} />
      <section className="panel dataset-data-query-panel">
        <div className="dataset-data-query-heading"><div><p className="eyebrow">SQL query</p><h2>Explore the committed dataset</h2></div><button className="btn btn-primary" disabled={loading} onClick={() => void load()}>{loading ? "Running query…" : "Run query"}</button></div>
        <textarea className="dataset-sql-editor" spellCheck={false} value={sql} onChange={(event) => setSql(event.target.value)} aria-label="SQL query" />
        <div className="dataset-data-query-footer"><label className="field"><span className="field-label">Preview rows</span><select value={limit} onChange={(event) => setLimit(Number(event.target.value))}><option value={25}>25 rows</option><option value={100}>100 rows</option><option value={250}>250 rows</option><option value={1000}>1,000 rows</option></select></label><details><summary>SQL rules</summary><p>Use a single <code>SELECT</code> statement with <code>FROM data</code>. Filters, expressions, grouping, ordering, and aggregation are supported. Joins, subqueries, external files, and multiple statements are blocked.</p></details></div>
      </section>
      <ConnectionWarning error={error} />
      {loading && preview === null && <Loading label="running data query…" />}
      {preview && <section className="panel dataset-data-preview-panel"><div className="dataset-data-query-heading"><div><p className="eyebrow">Query result</p><h2>{preview.items.length === 0 ? "No rows returned" : `${preview.items.length} preview row${preview.items.length === 1 ? "" : "s"}`}</h2></div><span className="muted">Showing up to {preview.limit} rows</span></div>{preview.items.length === 0 ? <EmptyState>Try changing the query or export it to confirm that no rows match.</EmptyState> : <div className="dataset-data-table-wrap"><table><thead><tr>{preview.columns.map((column) => <th key={column.name} title={column.type}>{column.name}</th>)}</tr></thead><tbody>{preview.items.map((row, index) => <tr key={index}>{preview.columns.map((column) => <td key={column.name} title={cell(row[column.name])}>{cell(row[column.name])}</td>)}</tr>)}</tbody></table></div>}</section>}
    </div>
  );
}

export default function DatasetDataPage() {
  const { name = "" } = useParams();
  return <DatasetDataWorkspace name={name} standalone />;
}
