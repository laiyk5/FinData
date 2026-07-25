import { Link } from "react-router";
import type { DatasetStatus, TaskHandle } from "../api";
import {
  providerConfigLabel,
  updateBlockedShort,
  updateReadinessLabel,
  type ReadinessFacts,
} from "../readiness";
import { DatasetCoverage, DatasetFreshness } from "./DatasetCoverage";
import { RunUpdateButton } from "./RunUpdate";
import { AlertIcon } from "./icons";

/**
 * Quiet single-line readiness summary: colored dots + plain text, no pills.
 * Provider readiness reads as configuration state; update readiness as
 * runnable/blocked, with a short reason when blocked.
 */
export function DatasetDotStatus({
  provider,
  facts,
}: {
  provider: string;
  facts: ReadinessFacts;
}) {
  const short = updateBlockedShort(facts);
  return (
    <div className="dataset-card-status muted">
      <span className={`dot ${facts.providerReady ? "dot-ok" : "dot-warn"}`} />
      <Link to={`/providers/${encodeURIComponent(provider)}`} className="mono">
        {provider}
      </Link>{" "}
      {providerConfigLabel(facts.providerReady)}
      <span className="dataset-card-sep">·</span>
      <span className={`dot ${facts.updateReady ? "dot-ok" : "dot-warn"}`} />
      <span>
        {updateReadinessLabel(facts.updateReady)}
        {short !== null && <span className="text-faint"> ({short})</span>}
      </span>
    </div>
  );
}

/**
 * Shared dataset card (Datasets page grid + Home health grid). Uniform
 * structure: header (name + freshness), one calm dot status line,
 * conditional warning row, facts block, pinned footer with the primary
 * action and a Details link. All content is server-derived.
 */
export function DatasetCard({
  name,
  state,
  provider,
  providerReady,
  updateReady,
  missingRequired,
  capabilities,
  publicationId,
  status,
  tasks,
}: {
  name: string;
  state: string;
  provider: string;
  providerReady: boolean;
  updateReady: boolean;
  /** Keys of unconfigured required settings (optional settings never count). */
  missingRequired: string[];
  capabilities: Record<string, unknown>;
  publicationId: string | null;
  status: DatasetStatus | null;
  tasks: TaskHandle[];
}) {
  const detailPath = `/datasets/${encodeURIComponent(name)}`;

  return (
    <div className="card dataset-card">
      <div className="dataset-card-head">
        <Link to={detailPath} className="mono dataset-card-name">
          {name}
        </Link>
        <DatasetFreshness state={state} tasks={tasks} />
      </div>

      <DatasetDotStatus
        provider={provider}
        facts={{ state, providerReady, updateReady, missingRequired }}
      />

      {missingRequired.length > 0 && (
        <Link to={`${detailPath}?tab=settings`} className="dataset-card-warning">
          <AlertIcon />
          <span>
            {missingRequired.length} required setting
            {missingRequired.length === 1 ? "" : "s"} not configured
          </span>
        </Link>
      )}

      <div className="dataset-card-facts">
        <DatasetCoverage
          capabilities={capabilities}
          publicationId={publicationId}
          status={status}
          tasks={tasks}
        />
      </div>

      <div className="dataset-card-footer">
        {updateReady ? (
          <RunUpdateButton dataset={name} />
        ) : (
          <Link className="btn btn-primary" to={`${detailPath}?tab=settings`}>
            Configure
          </Link>
        )}
        <Link to={detailPath} className="dataset-card-details">
          Details →
        </Link>
      </div>
    </div>
  );
}
