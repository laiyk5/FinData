from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click


def command_tree(*, version: str) -> click.Group:
    """Build the Click command tree while command execution remains elsewhere."""

    @click.group(name="findata")
    @click.option("--workspace", type=click.Path(path_type=Path))
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["human", "json", "jsonl"]),
        default="human",
        show_default=True,
    )
    @click.option(
        "--color", type=click.Choice(["auto", "always", "never"]), default="auto", show_default=True
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

        group.add_command(click.Command(action, params=params, callback=callback))

    config = click.Group("config")
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

    provider = click.Group("provider")
    root.add_command(provider)
    attach(provider, "provider", "ls", [])
    for action in ("status", "check"):
        attach(provider, "provider", action, [click.Argument(["name"])])

    dataset = click.Group("dataset")
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

    data = click.Group("data")
    root.add_command(data)
    attach(data, "data", "schema", [click.Argument(["dataset"])])
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

    attach(
        data,
        "data",
        "preview",
        [*query_options(), click.Option(["--limit"], type=click.IntRange(0), default=20)],
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
            click.Option(["--batch-size"], type=click.IntRange(1), default=65_536),
            click.Option(["--force"], is_flag=True),
        ],
    )

    task = click.Group("task")
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
    attach(
        task,
        "task",
        "ls",
        [
            click.Option(["--dataset"]),
            click.Option(["--status"]),
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

    cron = click.Group("cron")
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

    events = click.Group("events")
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

    system = click.Group("system")
    root.add_command(system)
    attach(system, "system", "status", [])

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

    hidden = click.Command(
        "_complete",
        params=[click.Argument(["words"], nargs=-1)],
        callback=hidden_completion,
        hidden=True,
    )
    root.add_command(hidden)
    return root
