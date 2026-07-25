/**
 * Typed client for the findata JSON HTTP API (base path /v1).
 *
 * Auth: every request sends `Authorization: Bearer <token>` read from
 * sessionStorage. Any 401 response clears the token and notifies the app
 * (via the `findata:unauthorized` window event) so it can redirect to login.
 */

export const TOKEN_KEY = "findata.token";
export const UNAUTHORIZED_EVENT = "findata:unauthorized";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // sessionStorage unavailable (e.g. privacy mode) — nothing to clear
  }
}

/** Clears the stored token and notifies the app that re-login is required. */
export function handleUnauthorized(): void {
  clearToken();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.message} (HTTP ${err.status})`;
  if (err instanceof Error) return err.message;
  return String(err);
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SystemStatus {
  status: string;
  pid: number;
  tasks: number;
  running_tasks: number;
  queue_lengths: Record<string, number>;
  /** Absolute path of the workspace this server serves. */
  workspace: string;
  /** Epoch seconds when the server started. */
  started_at: number;
  /** Server package version. */
  version: string;
  /** Walked on-disk size of the workspace, broken down by top-level entry. */
  workspace_disk: { total_bytes: number; breakdown: { name: string; bytes: number }[] };
}

export type TaskStatus =
  | "queued"
  | "running"
  | "waiting"
  | "canceling"
  | "succeeded"
  | "failed"
  | "canceled";

export interface TaskHandle {
  handle_id: string;
  execution_id: string;
  dataset: string;
  operation: string;
  owner: string;
  status: TaskStatus;
  created_at: number;
  updated_at: number;
  result?: unknown;
  error?: string | null;
  progress?: { current?: number; total?: number; checkpointed?: number } | null;
  reason?: string | null;
  stage?: string | null;
  diagnostic_counts?: { warning: number; error: number };
}

export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "succeeded",
  "failed",
  "canceled",
]);

export interface TaskDetail extends TaskHandle {
  subscriber_count: number;
}

/**
 * One entry in a task's log stream. Newer servers emit typed records
 * (`log` lines with an epoch-seconds `time`, `task.diagnostic` records with
 * an optional `time`); older servers may still send bare strings.
 */
export type TaskLogEntry =
  | { type: "log"; time?: number; message: string }
  | {
      type: "task.diagnostic";
      severity: string;
      code: string;
      message: string;
      context: Record<string, unknown>;
      count: number;
      time?: number;
    }
  | string;

export interface TaskLogs {
  handle_id: string;
  items: TaskLogEntry[];
}

export interface TaskExplanation {
  handle_id: string;
  dataset: string;
  operation: string;
  status: string;
  reason: string | null;
  diagnostics: unknown[];
  subscriber_count: number;
  inspection: Record<string, string>;
}

export interface OperationPropertySchema {
  type: string;
  items?: { type: string };
  minItems?: number;
  format?: string;
  help?: string;
}

export interface OperationDescription {
  name: string;
  help?: string;
  required: string[];
  properties: Record<string, OperationPropertySchema>;
}

export interface DatasetSetting {
  key: string;
  schema: Record<string, unknown>;
  help: string;
  configured: boolean;
  /** Server-declared classification; optional settings never warn. */
  required?: boolean;
}

export interface DatasetDescription {
  name: string;
  provider: string;
  provider_ready: boolean;
  capabilities: Record<string, unknown>;
  dependencies: string[];
  settings: DatasetSetting[];
  storage: string;
  state: "uninitialized" | "ready" | string;
  publication_id: string | null;
  operations: OperationDescription[];
}

export interface DatasetStatus {
  name: string;
  provider: string;
  provider_ready: boolean;
  update_ready: boolean;
  state: string;
  publication_id: string | null;
  covered_keys: number | null;
  coverage_start: string | null;
  coverage_end: string | null;
  /** DuckDB file + WAL size; null when no database file exists. */
  storage_bytes: number | null;
}

export interface ConfigKey {
  key: string;
  help: string;
  schema: Record<string, unknown>;
  configured: boolean;
  secret: boolean;
  /** Present on dataset-setting keys (`dataset.<name>.*`). */
  required?: boolean;
  /** Server-declared effective default (e.g. a provider rate limit). */
  default?: unknown;
}

export interface Provider {
  name: string;
  ready: boolean;
  configured?: boolean;
  mode: "mock" | "real";
  secret_fields?: string[];
}

export interface ProviderCheck {
  provider: string;
  ready: boolean;
  authenticated: boolean;
  mode: string;
}

export interface CronJob {
  dataset: string;
  expression: string;
  timezone: string;
  enabled: boolean;
  source: string;
  last_run: string | null;
  next_run: string | null;
}

export interface EventRecord {
  event_id: string;
  timestamp: number;
  kind: string;
  severity: string;
  message: string;
  context: Record<string, unknown>;
  acknowledged: boolean;
}

// ---------------------------------------------------------------------------
// Request plumbing
// ---------------------------------------------------------------------------

type QueryValue = string | number | boolean | undefined | null;

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, QueryValue>,
): Promise<T> {
  let url = `/v1${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        params.set(key, String(value));
      }
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const init: RequestInit = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  const resp = await fetch(url, init);
  const text = await resp.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!resp.ok) {
    if (resp.status === 401) handleUnauthorized();
    const message =
      data !== null &&
      typeof data === "object" &&
      typeof (data as { error?: unknown }).error === "string"
        ? (data as { error: string }).error
        : `HTTP ${resp.status}`;
    throw new ApiError(resp.status, message);
  }
  return data as T;
}

const enc = encodeURIComponent;

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export function getSystemStatus(): Promise<SystemStatus> {
  return request("GET", "/system/status");
}

export function listTasks(
  params: { dataset?: string; status?: string; all?: boolean } = {},
): Promise<{ items: TaskHandle[] }> {
  return request("GET", "/tasks", undefined, {
    dataset: params.dataset,
    status: params.status,
    all: params.all ? "true" : undefined,
  });
}

export function getTask(id: string): Promise<TaskDetail> {
  return request("GET", `/tasks/${enc(id)}`);
}

export function getTaskLogs(id: string): Promise<TaskLogs> {
  return request("GET", `/tasks/${enc(id)}/logs`);
}

export function createTask(body: {
  dataset: string;
  operation: string;
  operands: Record<string, unknown>;
}): Promise<{ handle_id: string; execution_id: string }> {
  return request("POST", "/tasks", body);
}

export function cancelTask(
  id: string,
): Promise<TaskHandle & { already_terminal?: boolean }> {
  return request("POST", `/tasks/${enc(id)}/cancel`);
}

export function retryTask(
  id: string,
): Promise<{ handle_id: string; execution_id: string; retried_from: string }> {
  return request("POST", `/tasks/${enc(id)}/retry`);
}

export function explainTask(id: string): Promise<TaskExplanation> {
  return request("GET", `/tasks/${enc(id)}/explain`);
}

export function listDatasets(): Promise<{ items: DatasetDescription[] }> {
  return request("GET", "/datasets");
}

export function getDataset(name: string): Promise<DatasetDescription> {
  return request("GET", `/datasets/${enc(name)}`);
}

export function listDatasetsStatus(): Promise<{ items: DatasetStatus[] }> {
  return request("GET", "/datasets/status");
}

export function getDatasetStatus(name: string): Promise<DatasetStatus> {
  return request("GET", `/datasets/${enc(name)}/status`);
}

export function getOperations(
  name: string,
): Promise<{ items: OperationDescription[] }> {
  return request("GET", `/datasets/${enc(name)}/operations`);
}

export function planOperation(
  name: string,
  operation: string,
  operands: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return request(
    "POST",
    `/datasets/${enc(name)}/operations/${enc(operation)}/plan`,
    { operands },
  );
}

export function resetDataset(
  name: string,
): Promise<{ dataset: string; state: string; reset: boolean }> {
  return request("POST", `/datasets/${enc(name)}/reset`, { confirm: true });
}

export function listProviders(): Promise<{ items: Provider[] }> {
  return request("GET", "/providers");
}

export function getProvider(
  name: string,
): Promise<{ name: string; ready: boolean; configured: boolean; mode: string }> {
  return request("GET", `/providers/${enc(name)}`);
}

export function checkProvider(name: string): Promise<ProviderCheck> {
  return request("GET", `/providers/${enc(name)}/check`);
}

export function listCron(): Promise<{ items: CronJob[] }> {
  return request("GET", "/cron");
}

export function cronEnable(dataset: string): Promise<CronJob> {
  return request("POST", `/cron/${enc(dataset)}/enable`);
}

export function cronDisable(dataset: string): Promise<CronJob> {
  return request("POST", `/cron/${enc(dataset)}/disable`);
}

export function cronReset(dataset: string): Promise<CronJob> {
  return request("POST", `/cron/${enc(dataset)}/reset`);
}

export function cronSchedule(
  dataset: string,
  body: { expression: string; timezone: string },
): Promise<CronJob> {
  return request("PUT", `/cron/${enc(dataset)}/schedule`, body);
}

export function listEvents(
  params: { unread?: boolean; since?: number; severity?: string } = {},
): Promise<{ items: EventRecord[] }> {
  return request("GET", "/events", undefined, {
    unread: params.unread ? "true" : undefined,
    since: params.since,
    severity: params.severity,
  });
}

export function ackEvent(
  body: { event_id: string } | { all: true },
): Promise<{ acknowledged: number }> {
  return request("POST", "/events/ack", body);
}

export function getConfig(): Promise<{ values: Record<string, unknown> }> {
  return request("GET", "/config");
}

export function getConfigKeys(): Promise<{ items: ConfigKey[] }> {
  return request("GET", "/config/keys");
}

export function setConfig(
  key: string,
  value: unknown,
): Promise<{ updated: boolean; key: string; value: unknown; revision: number }> {
  return request("POST", "/config", { key, value });
}

export function deleteConfig(key: string): Promise<{ removed: boolean }> {
  return request("DELETE", `/config/${enc(key)}`);
}
