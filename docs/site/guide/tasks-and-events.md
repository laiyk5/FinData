# Tasks and events

All maintenance work runs as asynchronous server tasks. The CLI reports acceptance and
the task ID as soon as the server accepts the submission; without waiting, success means
the task was accepted.

## Following work

```bash
findata task run findata-test/demo_random complete --param symbols=600000.SH   # submit, return
findata task run findata-test/demo_random update --wait                        # wait for terminal state
findata task run findata-test/demo_random update --follow                      # stream logs, implies --wait

findata task ls [--all] [--dataset NAME] [--status STATUS]
findata task status <id>
findata task logs <id> [--follow]
findata task watch <id>          # follow progress without submitting work
findata task explain <id>        # current/terminal reason, dependency chain, next steps
findata task cancel <id>
findata task retry <id> [--wait|--follow]
```

- A log follow prints existing logs, continues with new entries, and exits when the task
  reaches a terminal state.
- `--wait` waits for the terminal result; a failed or canceled waited task exits `1`.
- Pressing **Ctrl-C** while waiting or following detaches the client and leaves the
  accepted server task running (exit `130`). Use `findata task cancel <id>` when
  cancellation is intended. A temporary connection loss is reported with the exact
  `task status` command needed to inspect work that may still be running.
- `task retry` submits a new handle using the retained task's normalized dataset,
  operation, and operands; configuration is snapshotted again, and the old record is
  unchanged.
- `task explain` shows the current or terminal reason, the dependency-failure chain,
  diagnostics, and concrete inspection or retry commands without changing task state.

While waiting in human mode, the CLI renders the server's semantic stage and progress on
stderr. Waiting states name their reported reason, such as a rate permit, dependency, or
write gate. `--no-progress` disables the live region, `--quiet` suppresses nonterminal
human output, and `--verbose` includes dependency and request-planning detail.

## Task lifecycle

Every task ID names the submitting handle, even when several handles share one coalesced
execution. Public handle states are:

| state | meaning |
| --- | --- |
| `queued` | accepted and waiting for execution capacity or the dataset mutex |
| `running` | executing provider, transformation, validation, or publication work |
| `waiting` | paused for a rate permit, dependency, or write gate |
| `canceling` | the last subscription was canceled and its execution has not exited yet |
| `succeeded` | terminal; all required work completed |
| `failed` | terminal; work stopped with an error or was interrupted by server restart |
| `canceled` | terminal; this handle's subscription was canceled |

Canceling one coalesced handle makes that handle `canceled` immediately while another
subscriber's handle continues. Canceling the final handle requests cooperative
cancellation; after five seconds the runner terminates a process that has not exited, and
the handle then becomes `canceled` regardless of the process exit code. Cancellation of
an already terminal handle is a no-op reported as such.

The default `task ls` view contains every nonterminal handle and the 50 most recent
terminal handles; `--all` means all retained handles, up to the newest 1,000 terminal
handles per dataset.

## Identifier prefixes

Commands that address a task handle and `events ack` accept either the full identifier or
a lowercase hexadecimal prefix of at least eight characters. An exact identifier always
wins. A prefix must identify exactly one retained resource; no match is reported as not
found, and multiple matches are reported as ambiguous with no action performed. Success
output always includes the full resolved identifier. Dataset, provider, publication, and
execution identifiers must be supplied in full.

## Events and system status

```bash
findata system status
findata events ls [--unread] [--since 12h] [--severity warning]
findata events ack <id>          # or: events ack --all
```

`system status` shows server liveness, running tasks, and per-dataset queue lengths.
Events include task failures, queue rejections, liveness escalations, and skipped or
missed cron jobs. Acknowledging appends a reference record instead of mutating the
original event.
