from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click


class _ArgumentHelpMixin:
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        arguments = [
            (parameter.make_metavar(ctx), parameter.help)
            for parameter in self.get_params(ctx)
            if isinstance(parameter, click.Argument) and getattr(parameter, "help", None)
        ]
        if arguments:
            with formatter.section("Arguments"):
                formatter.write_dl(arguments)
        super().format_options(ctx, formatter)


class DocumentedCommand(_ArgumentHelpMixin, click.Command):
    pass


class DocumentedGroup(_ArgumentHelpMixin, click.Group):
    pass


GROUP_HELP = {
    "config": "Read and change workspace configuration.",
    "provider": "Inspect provider availability and credentials.",
    "dataset": "Inspect and maintain registered datasets.",
    "data": "Discover, preview, verify, and export committed data.",
    "task": "Submit and inspect asynchronous dataset work.",
    "cron": "Manage dataset update schedules.",
    "events": "Inspect and acknowledge retained operational events.",
    "system": "Inspect the local findata service.",
    "plugin": "List, diagnose, and scaffold plugin distributions.",
}

COMMAND_HELP = {
    ("config", "set"): "Set one typed workspace configuration value.",
    ("config", "get"): "Show one value, or all values when KEY is omitted.",
    ("config", "ls"): "List workspace configuration values.",
    ("config", "unset"): "Remove one workspace configuration value.",
    ("provider", "ls"): "List registered providers.",
    ("provider", "status"): "Show one provider's configured readiness.",
    ("provider", "check"): "Verify one provider can authenticate and respond.",
    ("dataset", "ls"): "List registered datasets.",
    ("dataset", "describe"): "Show one dataset's schema, settings, and capabilities.",
    ("dataset", "operations"): "List operations supported by one dataset.",
    ("dataset", "status"): "Show maintenance and readiness state.",
    ("dataset", "operation"): "Describe one dataset operation and its operands.",
    ("dataset", "reset"): "Replace one dataset with a new uninitialized database.",
    ("dataset", "update"): "Update a dataset using its configured selection.",
    ("dataset", "complete"): "Backfill an explicit dataset selection and time range.",
    ("dataset", "refresh"): "Refetch data strictly inside existing coverage.",
    ("data", "schema"): "Show the committed Arrow schema and query keys.",
    ("data", "coverage"): "Inspect committed coverage or check a requested time range.",
    ("data", "preview"): "Show a bounded preview of committed rows.",
    ("data", "export"): "Stream committed rows to a file or stdout.",
    ("data", "snapshot"): "Copy one consistent single-file database snapshot.",
    ("task", "run"): "Submit a dataset operation through the generic task interface.",
    ("task", "ls"): "List retained task handles.",
    ("task", "status"): "Show the current state of one task.",
    ("task", "cancel"): "Cancel this subscriber's task handle.",
    ("task", "watch"): "Wait for task progress and its terminal result.",
    ("task", "explain"): "Explain a task plan, dependencies, or failure.",
    ("task", "logs"): "Print retained logs for one task.",
    ("task", "retry"): "Submit a new task from a retained terminal task.",
    ("cron", "ls"): "List dataset schedules.",
    ("cron", "enable"): "Enable one dataset's schedule.",
    ("cron", "disable"): "Disable one dataset's schedule.",
    ("cron", "reset"): "Restore one dataset's default schedule.",
    ("cron", "set"): "Set one dataset's cron expression and timezone.",
    ("events", "ls"): "List retained operational events.",
    ("events", "ack"): "Acknowledge one event or every matching event.",
    ("system", "status"): "Show server identity and runtime health.",
    ("system", "health"): "Aggregate health check — plugins, providers, datasets.",
    ("plugin", "ls"): "List installed plugin distributions with entry points and versions.",
    ("plugin", "check"): "Check whether a specific plugin entry point loads correctly.",
    ("plugin", "blocked"): "Show the workspace plugin blocklist.",
    ("plugin", "scaffold"): "Generate a complete plugin distribution tree.",
    ("plugin", "block"): "Add a plugin to the workspace blocklist.",
    ("plugin", "unblock"): "Remove a plugin from the workspace blocklist.",
    ("completion", "completion"): "Generate a shell script that enables command completion.",
}

ARGUMENT_HELP = {
    "key": "Configuration key, for example dataset.tushare_daily_basic.update_symbols; "
    "declared keys are listed by 'findata dataset describe <dataset>' and suggested by "
    "shell completion.",
    "value": "Plain string value; use an alternate input option for typed or secret values.",
    "name": "Registered provider identifier.",
    "dataset": "Registered dataset identifier.",
    "operation": "Dataset operation identifier.",
    "namespace": "Plugin author namespace, for example ``mycompany``.",
    "name": "Entry-point name or plugin full name to inspect.",
    "handle": "Full task handle or an unambiguous lowercase-hex prefix of at least eight characters.",
    "event_id": "Full event identifier or an unambiguous lowercase-hex prefix of at least eight characters.",
    "shell": "Shell whose sourceable completion script should be generated.",
}

OPTION_HELP = {
    "workspace": "Workspace path; otherwise use FINDATA_WORKSPACE or nearest parent workspace.",
    "output_format": "Presentation format written to stdout.",
    "color": "When human output may contain terminal colors.",
    "value_json": "Configuration value as JSON, @file, or - for stdin.",
    "env": "Read the configuration value from this environment variable.",
    "stdin": "Read the configuration value from stdin.",
    "all": "Apply to or include all matching resources.",
    "symbols": "Repeat for each provider symbol to select; accepts Tushare security codes like "
    "600000.SH or constituent selectors tushare:<ts_code>[@latest|@YYYYMM].",
    "indexes": "Repeat for each provider-qualified index to select, spelled tushare:<ts_code> "
    "(for example tushare:000300.SH).",
    "exchanges": "Repeat for each exchange to select; SSE and/or SZSE.",
    "timerange": "Half-open date range in START:END form; dates are YYYY-MM-DD or today, the "
    "end is exclusive, and today resolves in the dataset timezone.",
    "range_start": "Inclusive start date in YYYY-MM-DD form, or today.",
    "range_end": "Exclusive end date in YYYY-MM-DD form, or today.",
    "wait": "Wait until the submitted task reaches a terminal state.",
    "follow": "Stream progress or logs while waiting.",
    "dry_run": "Validate and show the plan without submitting work.",
    "keys": "Repeat for each partition key to query.",
    "columns": "Comma-separated or repeatable columns to return.",
    "require_coverage": "Require complete coverage for every key and requested date.",
    "allow_partial": "Return available rows even when requested coverage is incomplete.",
    "limit": "Maximum preview rows to return.",
    "export_format": "Serialized data format for the export.",
    "output": "Destination path, or - for stdout.",
    "batch_size": "Maximum rows processed per export batch.",
    "force": "Replace an existing export file.",
    "param": "Repeat KEY=VALUE to supply an operation operand.",
    "params": "JSON object, @file, or - containing operation operands.",
    "dataset": "Filter results to one dataset.",
    "status": "Filter tasks by status.",
    "expression": "Five-field cron expression.",
    "timezone": "IANA timezone used to evaluate the schedule.",
    "unread": "Show only unacknowledged events.",
    "since": "Show only events newer than this duration, for example 30m, 12h, or 7d.",
    "severity": "Filter events by severity.",
    "yes": "Confirm the destructive reset without prompting.",
}


def _document_command(command: click.Command, *, family: str, action: str) -> None:
    command.help = COMMAND_HELP[(family, action)]
    for parameter in command.params:
        if isinstance(parameter, click.Argument):
            parameter.help = ARGUMENT_HELP[parameter.name]
        elif isinstance(parameter, click.Option) and not parameter.help:
            parameter.help = OPTION_HELP[parameter.name]


def command_tree(*, version: str) -> click.Group:
    """Build the Click command tree while command execution remains elsewhere."""

    @click.group(name="findata", cls=DocumentedGroup)
    @click.option(
        "--workspace",
        type=click.Path(path_type=Path),
        help=OPTION_HELP["workspace"],
    )
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["human", "json", "jsonl"]),
        default="human",
        show_default=True,
        help=OPTION_HELP["output_format"],
    )
    @click.option(
        "--color",
        type=click.Choice(["auto", "always", "never"]),
        default="auto",
        show_default=True,
        help=OPTION_HELP["color"],
    )
    @click.option("--quiet", is_flag=True, help="Suppress nonterminal human output.")
    @click.option("--verbose", is_flag=True, help="Show planning and dependency detail.")
    @click.option("--no-progress", is_flag=True, help="Disable interactive live progress.")
    @click.version_option(version=version, prog_name="findata")
    def root(**_: Any) -> None:
        """Maintain and inspect local financial datasets."""

    def attach(group: click.Group, name: str, action: str, params: list[click.Parameter]) -> None:
        def callback(**values: Any) -> SimpleNamespace:
            context = click.get_current_context().find_root()
            return SimpleNamespace(
                group=name,
                action=action,
                workspace=context.params.get("workspace"),
                **values,
            )

        command = DocumentedCommand(action, params=params, callback=callback)
        _document_command(command, family=name, action=action)
        group.add_command(command)

    config = DocumentedGroup("config", help=GROUP_HELP["config"])
    root.add_command(config)
    attach(
        config,
        "config",
        "set",
        [
            click.Argument(["key"]),
            click.Argument(["value"], required=False),
            click.Option(["--value-json"]),
            click.Option(["--env"]),
            click.Option(["--stdin"], is_flag=True),
        ],
    )
    attach(config, "config", "get", [click.Argument(["key"], required=False)])
    attach(config, "config", "ls", [])
    attach(config, "config", "unset", [click.Argument(["key"])])

    provider = DocumentedGroup("provider", help=GROUP_HELP["provider"])
    root.add_command(provider)
    attach(provider, "provider", "ls", [])
    for action in ("status", "check"):
        attach(provider, "provider", action, [click.Argument(["name"])])

    dataset = DocumentedGroup("dataset", help=GROUP_HELP["dataset"])
    root.add_command(dataset)
    attach(dataset, "dataset", "ls", [])
    for action in ("describe", "operations"):
        attach(dataset, "dataset", action, [click.Argument(["dataset"])])
    attach(
        dataset,
        "dataset",
        "status",
        [
            click.Argument(["dataset"], required=False),
            click.Option(["--all"], is_flag=True),
        ],
    )
    attach(
        dataset,
        "dataset",
        "operation",
        [click.Argument(["dataset"]), click.Argument(["operation"])],
    )
    attach(
        dataset,
        "dataset",
        "reset",
        [click.Argument(["dataset"]), click.Option(["--yes"], is_flag=True)],
    )
    for action in ("update", "complete", "refresh"):
        attach(
            dataset,
            "dataset",
            action,
            [
                click.Argument(["dataset"]),
                click.Option(["--symbols"], multiple=True),
                click.Option(["--indexes"], multiple=True),
                click.Option(["--exchanges"], multiple=True),
                click.Option(["--timerange"]),
                click.Option(["--from", "range_start"]),
                click.Option(["--to", "range_end"]),
                click.Option(["--wait"], is_flag=True),
                click.Option(["--follow"], is_flag=True),
                click.Option(["--dry-run", "dry_run"], is_flag=True),
            ],
        )

    data = DocumentedGroup("data", help=GROUP_HELP["data"])
    root.add_command(data)

    def query_options() -> list[click.Parameter]:
        return [
            click.Argument(["dataset"]),
            click.Option(["--keys"], multiple=True),
            click.Option(["--columns"], multiple=True),
            click.Option(["--from", "range_start"]),
            click.Option(["--to", "range_end"]),
            click.Option(["--require-coverage", "require_coverage"], is_flag=True),
            click.Option(["--allow-partial", "allow_partial"], is_flag=True),
        ]

    attach(data, "data", "schema", [click.Argument(["dataset"])])
    attach(
        data,
        "data",
        "preview",
        [
            *query_options(),
            click.Option(["--limit"], type=click.IntRange(0), default=20, show_default=True),
        ],
    )
    attach(
        data,
        "data",
        "coverage",
        [
            click.Argument(["dataset"]),
            click.Option(["--keys"], multiple=True),
            click.Option(["--from", "range_start"]),
            click.Option(["--to", "range_end"]),
        ],
    )
    attach(
        data,
        "data",
        "export",
        [
            *query_options(),
            click.Option(
                ["--output-format", "export_format"],
                type=click.Choice(["csv", "parquet", "arrow", "jsonl"]),
                required=True,
            ),
            click.Option(["--output"], required=True),
            click.Option(
                ["--batch-size"], type=click.IntRange(1), default=65_536, show_default=True
            ),
            click.Option(["--force"], is_flag=True),
        ],
    )
    attach(
        data,
        "data",
        "snapshot",
        [
            click.Argument(["dataset"]),
            click.Option(
                ["--output"],
                help="Snapshot destination; defaults to "
                "<workspace>/snapshots/<dataset>.duckdb and is replaced atomically.",
            ),
        ],
    )

    task = DocumentedGroup("task", help=GROUP_HELP["task"])
    root.add_command(task)
    attach(
        task,
        "task",
        "run",
        [
            click.Argument(["dataset"]),
            click.Argument(["operation"], required=False, default="update"),
            click.Option(["--param"], multiple=True),
            click.Option(["--params"]),
            click.Option(["--wait"], is_flag=True),
            click.Option(["--follow"], is_flag=True),
            click.Option(["--dry-run", "dry_run"], is_flag=True),
        ],
    )
    for parameter in task.commands["run"].params:
        if isinstance(parameter, click.Argument) and parameter.name == "operation":
            parameter.help = "Dataset operation identifier; defaults to update."
    attach(
        task,
        "task",
        "ls",
        [
            click.Option(["--dataset"]),
            click.Option(
                ["--status"],
                type=click.Choice(
                    [
                        "queued",
                        "running",
                        "waiting",
                        "canceling",
                        "succeeded",
                        "failed",
                        "canceled",
                    ]
                ),
                help="Filter tasks by lifecycle status.",
            ),
            click.Option(["--all"], is_flag=True),
        ],
    )
    for action in ("status", "cancel", "watch", "explain"):
        attach(task, "task", action, [click.Argument(["handle"])])
    attach(
        task,
        "task",
        "logs",
        [
            click.Argument(["handle"]),
            click.Option(["--follow", "-f"], is_flag=True),
        ],
    )
    attach(
        task,
        "task",
        "retry",
        [
            click.Argument(["handle"]),
            click.Option(["--wait"], is_flag=True),
            click.Option(["--follow"], is_flag=True),
        ],
    )

    cron = DocumentedGroup("cron", help=GROUP_HELP["cron"])
    root.add_command(cron)
    attach(cron, "cron", "ls", [])
    for action in ("enable", "disable", "reset"):
        attach(cron, "cron", action, [click.Argument(["dataset"])])
    attach(
        cron,
        "cron",
        "set",
        [
            click.Argument(["dataset"]),
            click.Option(["--expression"], required=True),
            click.Option(["--timezone"], required=True),
        ],
    )

    events = DocumentedGroup("events", help=GROUP_HELP["events"])
    root.add_command(events)
    attach(
        events,
        "events",
        "ls",
        [
            click.Option(["--unread"], is_flag=True),
            click.Option(["--since"]),
            click.Option(["--severity"], type=click.Choice(["info", "warning", "error"])),
        ],
    )
    attach(
        events,
        "events",
        "ack",
        [
            click.Argument(["event_id"], required=False),
            click.Option(["--all"], is_flag=True),
        ],
    )

    system = DocumentedGroup("system", help=GROUP_HELP["system"])
    root.add_command(system)
    attach(system, "system", "status", [])
    attach(system, "system", "health", [])

    plugin = DocumentedGroup("plugin", help=GROUP_HELP["plugin"])
    root.add_command(plugin)
    attach(plugin, "plugin", "ls", [])
    attach(
        plugin,
        "plugin",
        "check",
        [click.Argument(["name"])],
    )
    attach(
        plugin,
        "plugin",
        "blocked",
        [click.Option(["--all"], is_flag=True, help="Show all installed plugins and their block status.")],
    )
    attach(
        plugin,
        "plugin",
        "block",
        [click.Argument(["name"])],
    )
    attach(
        plugin,
        "plugin",
        "unblock",
        [click.Argument(["name"])],
    )
    attach(
        plugin,
        "plugin",
        "scaffold",
        [
            click.Argument(["namespace"]),
            click.Argument(["name"]),
            click.Option(["--install"], is_flag=True, help="Install the generated plugins with pip install -e."),
        ],
    )

    attach(
        root,
        "completion",
        "completion",
        [click.Argument(["shell"], type=click.Choice(["bash", "zsh", "fish"]))],
    )

    def hidden_completion(words: tuple[str, ...]) -> SimpleNamespace:
        context = click.get_current_context().find_root()
        return SimpleNamespace(
            group="_complete",
            action="_complete",
            workspace=context.params.get("workspace"),
            words=words,
        )

    hidden = DocumentedCommand(
        "_complete",
        params=[click.Argument(["words"], nargs=-1)],
        callback=hidden_completion,
        hidden=True,
        context_settings={"ignore_unknown_options": True},
    )
    root.add_command(hidden)
    return root
