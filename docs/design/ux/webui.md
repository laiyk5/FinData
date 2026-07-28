# WebUI design

This file owns the WebUI design: the thin-client boundary, the static-asset serving contract, the
product principles, the application shell, the page designs, and the presentation, polling, and
confirmation policies. Architectural rules shared by the whole system live in
[core.md](../core.md); CLI presentation rules live in [cli.md](cli.md).

## Thin-client boundary

The WebUI is a second thin client over the same versioned localhost HTTP API used by the CLI. It
is a static single-page application served by the server itself; it renders only server-reported
semantics and follows live work by polling. `findata web open` uses the workspace token locally to
obtain a one-time login code; the page exchanges that loopback-only code for an `HttpOnly`,
`SameSite=Strict` session cookie. The workspace token is never placed in a URL or browser storage.
Manual bearer-token login remains available for direct browser access. The UI adds no lifecycle
states or policy of its own: every piece of meaning (states, readiness, reasons, progress) comes
from the server, and every mutation maps to a documented CLI command.

The dataset Data tab is the one browser read surface: it sends guarded read-only SQL to the server,
which executes it through the standard DataLoader contract. It previews a bounded result and exports
only that exact query result as CSV or Parquet. Client-side polling is the liveness contract; adding
push channels such as websockets requires an architectural revision.

## Static-asset serving

The server serves the WebUI's static assets (HTML, JavaScript, CSS) for non-API paths.
Those assets carry no secrets and are served without the token; every data request made by the
loaded page still requires it. The page never embeds the token in markup, and the build output
contains no workspace-specific values.

## Product principles

1. **Attention first.** The first screen answers "does anything need me?" — failed tasks,
   unacknowledged events, unready providers, missed cron jobs, and datasets whose update is not
   ready are surfaced as actionable items, never as bare counters.
2. **One tap to act.** Every listed problem or entity offers its natural next action inline:
   explain a failure, retry it, open the dataset, fix the provider's config, acknowledge the
   event. Navigation to a pre-filtered detail view beats re-typing context.
3. **Server semantics, human shape.** States, reasons, and errors are rendered verbatim from the
   server, but never as raw JSON dumps. Structured values (capabilities, settings, plans, event
   context, results) get purpose-built renderings; raw JSON is available only behind an explicit
   disclosure.
4. **General datasets only.** Nothing in the UI is designed around a specific dataset or provider.
   All dataset-specific rendering derives from server-reported description, status, and operation
   schemas (`name`, `provider`, `state`, `provider_ready`, `update_ready`, `settings`,
   `capabilities`, `dependencies`, `covered_keys`, `coverage_start`, `coverage_end`), so any
   registered dataset gets the same quality of experience. Dataset and provider names are never
   hardcoded.
5. **Trust the live view.** Polling is adaptive, and every live view shows its freshness (last
   update time, connection state), so a quiet screen reads as "all good" rather than "maybe
   stale".

## Application shell

- **Navigation** is grouped by intent: *Overview* (Home), *Data* (Datasets), *Activity* (Tasks,
  Events), *Automation* (Cron), *System* (Providers, Config). The active section is always
  visible.
- **Global status bar** shows the running-task count (a live link to the filtered task list), the
  unacknowledged-event count (link to Events), and the connection/freshness indicator. It is
  visible from every page. The WebUI's own version renders alongside the brand mark and in the
  browser tab title, so the user can tell which UI build they are running.
- **Toasts** confirm every successful mutation and surface mutation failures with the server's
  error text; they never replace in-page error context for form validation.
- **Confirmation dialogs** are a shared component used by every destructive or state-changing
  action per the confirmation policy below.
- **Identifiers** (handle, execution, publication) are displayed shortened but copy the full value
  on click, matching the CLI's prefix convention.
- **Cross-navigation by deep link.** List pages honor filter query parameters (`tasks?dataset=`,
  `cron?dataset=`, `config?q=`, `events?dataset=`, `events?unread=`), and every entity view links
  to its related views — the user never re-types context to get from a problem to its remedy.
  The full graph: a dataset links to its provider, tasks, events, cron job, and config keys; a
  provider links to its datasets and config keys; a task links to its dataset; a cron job links
  to its dataset; a provider's config group links back to the provider, and a dataset's config
  group links back to the dataset.

## Pages

### Home (dashboard)

The home page is an attention queue plus a health overview:

- **Needs attention** — one actionable row per problem: failed tasks (with explain/retry links),
  unacknowledged warning/error events (link pre-filtered), unready or unconfigured providers
  (link to Providers), enabled cron jobs that were missed during downtime (link to Cron), and
  datasets that are `ready` but not `update_ready` (link to the dataset's settings). When nothing
  needs attention, the section says so plainly.
- **Live now** — running, queued, and canceling tasks with progress bars, polled fast.
- **Dataset health** — a card per dataset rendered from its status: state, provider readiness,
  update readiness, covered-key count, and coverage interval. Each card links to the dataset
  detail and offers its primary action (run `update` when eligible).
- Server status (status word, PID, workspace path, queue lengths) is available but demoted to a
  small footer line — it is diagnostics, not the headline, and links to the Server page.

### Server

A read-only status page under *System* answering "what exactly is this server?":

- **Identity** — workspace path, PID, server version, listen address, and uptime (from the
  server-reported start time).
- **Capacity** — the workspace's own disk usage (walked on disk, not filesystem-level), boiled
  down per top-level component (datasets, tasks, providers, …) with per-component sizes and
  proportions; running task count and per-dataset queue lengths.
- **Task load** — an activity timeline over the last 24 hours, derived client-side from the task
  list (no new endpoint): one status-colored mark per task at its creation time, clickable to
  open the task, so sparse activity reads as discrete events rather than empty chart buckets.

### Datasets

The page is a hierarchical card index, not a bare table. It groups cards by each plugin's declared
family path and lets users collapse or scan a family before reading individual datasets. A card
shows only its findability and operational facts: name, provider, state, coverage, update readiness,
and one primary action. Configuration warnings are visible but do not compete with the name. The
page polls slowly so cards stay fresh after operations run. An empty registry states how datasets
get registered (install a dataset plugin) instead of showing a blank table.

### Dataset detail

Header: name, the dataset freshness/state presentation (see the presentation policy), a quiet
single-line status summary (provider configuration and update readiness as dot-prefixed text,
never a large capsule), the primary action button (Run `update`, disabled with the
server-reported reason when not eligible), and a link to the dataset's cron job
(`cron?dataset=<name>`). Tabs:

- **Overview** — coverage panel (interval and key count), dependencies rendered as linked chips
  with each dependency's current state, and capabilities rendered as labeled key/value facts
  (booleans as badges), never a raw JSON block as the primary view.
- **Run** — the schema-driven operation form (select operation, typed operand fields, per-field
  help from the server schema). Dry-run renders the plan in human shape — strategy, estimated
  request count, and dependency states as linked chips — with raw plan JSON behind a disclosure.
  Date ranges default from the start of the current year through today unless the operation declares
  a narrower default. Selector fields surface the plugin's `all` or coverage-backed `stored` default
  as an explicit choice. Submit requires a confirmation that shows the equivalent CLI command.
- **Settings** — one typed editor per declared setting, driven by its JSON schema (arrays as
  line/tag editors, booleans as switches, numbers as number inputs, strings as text), with the
  server-provided help text. Every setting carries its server-declared classification: required
  settings show a "required" marker and warn while unconfigured; optional settings show an
  "optional" marker and never warn. Values never require the user to write raw JSON. Unset
  requires confirmation.
- **Activity** — the shared task-list component pre-filtered to this dataset.
- **Data** — a SQL editor with a bounded preview, schema facts, and one export action whose format
  choice is CSV or Parquet. It queries through the server's DataLoader route; it never exports the
  whole dataset implicitly.
- **Danger zone** — reset with a typed-name confirmation, stating that published data is replaced
  and showing the equivalent CLI command.

### Tasks

The list is built for scanning and acting:

- Status filter chips (active / succeeded / failed / canceled / all) plus dataset filter and the
  retained-history toggle.
- Each row leads with its task ID, operation, and state; the dataset is a clearly secondary linked
  fact so opening task detail never accidentally navigates to the dataset. Progress appears only
  while work is active. Failed and canceled rows offer one explanatory action, not duplicate links.
  Owner, diagnostic counts, and relative update time remain scannable facts.
- The list polls fast while any row is active and slowly otherwise.

### Task detail

- A status-first header: status badge, dataset and operation links, progress bar with
  processed/checkpointed distinction, elapsed time, and contextual actions — Cancel only while
  active, Retry and auto-loaded Explain when failed or canceled. Cancel and retry confirm.
- The explain view renders reason, diagnostics as severity-coded entries with counts, and the
  inspection CLI commands as copyable snippets — loaded automatically for failed tasks.
- Logs deduplicate consecutive identical lines (rendered once with a ×N count), support severity
  filtering, and offer follow mode with auto-scroll for live tasks. Diagnostics are rendered
  distinctly from plain log lines.
- Result and remaining metadata live behind a "details" disclosure, with IDs copyable.

### Cron

- Each job row leads with its dataset and triggered operation, with enabled state and source
  (suggested/override) alongside. It then shows a human-readable schedule summary alongside the
  raw expression, timezone, last run, and next run as a relative countdown in the job's timezone.
- Missed-during-downtime jobs (from `cron_missed` events) surface as a banner offering the
  corresponding update submission — never auto-submitted.
- Schedule editing is guided, not free text: a structured editor offers presets (daily, weekdays,
  weekly, monthly) with time and day pickers plus a custom mode with per-field inputs constrained
  to valid five-field cron values, and the timezone is chosen from an IANA timezone list. The
  editor shows a live humanized preview and never lets an illegal expression reach the API; the
  server remains the validation authority.
- Enable/disable and reset-to-suggested act immediately with toasts; disable confirms.
- The list filters by dataset (and enabled state); it honors a `?dataset=<name>` deep link,
  pre-filtering and highlighting that job, so dataset views can jump straight to their job.

### Events

- Polls like other live views; filters by severity, kind, and unread; shows relative times.
- Event context is rendered as labeled key/value chips, not a JSON dump.
- Every event offers contextual actions derived generically from its `kind` and `context` (never
  per-dataset code): task-failure and queue events link to the task list filtered by the
  context's dataset, cron events link to the cron job filtered by dataset, and any event naming
  a dataset links to that dataset's detail. Acknowledge is always available but never the only
  action.
- Acknowledge per row and acknowledge-all, with a toast; acknowledged events remain visible under
  the "all" filter, visually settled.

### Providers

The providers list is a calm card index: one card per provider showing only the provider's own
facts — name, mode, configured state, and how many datasets use it. Everything else lives on the
**provider detail page** (`/providers/<name>`), which is the provider's control surface:

- configured state, with unconfigured providers stating exactly which config keys are missing;
- secret fields with per-field configured state, each linking to the matching Config entry;
- the Check action whose authenticated-probe result renders inline;
- a Configure action jumping to the Config page pre-filtered to that provider's keys;
- the datasets using this provider as linked chips.

### Config

- **Fully generated, never hardcoded.** The page is built entirely from `GET /v1/config/keys` —
  each item's `key`, `help`, `schema` (including `format`), `secret`, `required`, and `default`
  drives its rendering. No key-, provider-, or dataset-specific code exists in the page; a newly
  registered plugin's keys appear automatically. Schema `format` selects specialized editors
  (for example `iana-timezone` renders a timezone picker, not free text); if a future contract
  needs richer editing, the plugin contract is amended to declare it — the page never special-cases.
- **Classification and filtering.** Keys group under clear section headers (Core, one section per
  provider, one per dataset) with per-group counts, and a text filter narrows keys across groups.
  The page honors a filter query parameter so provider/dataset views can deep-link into it.
- Each key uses a schema-typed editor with its server-provided help. A configured key shows its
  current value as a muted "current: …" line in place of badge clutter — secret values stay
  redacted, but an `{env:VAR}` reference is shown as the current value since it names no secret.
  Editor placeholders carry the declared default or current value.
- Set shows a toast; unset confirms.

## Presentation policy

- **Readiness labels are self-explanatory.** The three server-reported readiness facts never
  share a word and never appear as bare "ready":
  - Dataset state is presented as *freshness and coverage*, not an abstract badge: an
    `uninitialized` dataset shows "no data"; a dataset with committed data shows its last
    maintenance freshness ("updated 3h ago" from its latest task) alongside the coverage facts
    defined below.
  - Provider readiness renders as "provider configured / provider needs configuration" — that is
    what the fact means.
  - Update readiness renders as "update ready to run / update blocked", and a blocked state
    names why from server-reported facts (for example the unconfigured required settings).
- **Coverage presentation follows the dataset's declared structure.** Time-accumulating datasets
  (`capabilities.time_accumulating`) render their coverage interval explicitly as
  `[start → end)` with the covered-key count, or "no coverage yet" when empty.
  Complete-replacement datasets have no coverage record by design; they render as
  "complete replacement — no coverage tracked", with the current publication ID and the last
  maintenance activity (latest task for the dataset) instead of a coverage placeholder.
  Both kinds show the dataset's on-disk storage use (server-reported `storage_bytes`,
  human-formatted) in their facts.
- **Configuration defaults are visible.** When the server declares a default for an unconfigured
  key (for example a provider's declared rate limit), the value is shown as the effective default
  next to the "not configured" state, never as an empty field. The display timezone's default is
  the server-probed system timezone (fixed UTC+8 when unprobeable), exposed the same way.
- **Capsules belong to the owner.** A card or header carries pill badges only for its own
  subject's status — never for related objects. Related objects (a dataset's provider, a
  provider's datasets, a dataset's dependencies) are links with quiet dot indicators, so cards
  stay visually calm no matter how many relations exist.
- **Time**: primary display is relative ("3m ago") with the absolute timestamp on hover; absolute
  timestamps render in the workspace display timezone (`display.timezone` from configuration,
  falling back to browser local). Durations follow the CLI's adaptive rules. Coverage intervals
  render explicitly as `[start → end)`.
- **Progress**: processed and durably checkpointed work are always visually distinct (bar fill vs
  checkpoint marker).
- **Diagnostics**: severity-coded, with stable code and occurrence count; never collapsed into
  plain log text.
- **Errors**: server-produced actionable errors render verbatim, including the remediation they
  name. Empty states name the next step rather than showing blank panels.
- **Feedback**: every mutation ends in a toast or an inline error; no silent state changes.

## Polling and liveness

- Polling is adaptive per view: fast (≈1s) for a live task being followed, medium (≈2–3s) for
  lists containing active work, slow (≈10–15s) for idle views; polling for a resource stops when
  the view unmounts or the resource is terminal and unfollowed.
- Every polled view shows when it was last updated; a failed poll surfaces a connection warning
  instead of silently freezing.
- A global indicator of running-task count stays visible from every page.

## Confirmation policy

- Destructive or state-changing mutations — dataset reset, configuration unset, cron disable,
  task cancel — require an explicit confirmation that states the consequence and shows the
  equivalent CLI command. Cancel confirmation states that already committed checkpoint batches
  remain visible.
- Dataset reset requires a typed confirmation, mirroring the non-interactive `--yes` contract.
- Task submission confirms once with the equivalent CLI command; retry confirms. Benign reads,
  dry-runs, acknowledgement, and enable actions act immediately.
