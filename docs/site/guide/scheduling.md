# Scheduling

Automatic maintenance is **opt-in**. Every dataset ships a suggested schedule as a
disabled default job; nothing runs until you enable it. A job must have a ready provider,
and its dataset plugin must report that its settings and committed state are sufficient
for `update`.

```bash
findata cron ls                                  # schedules, enabled state, last/next run
findata cron enable  findata-plugins/tushare_daily_basic
findata cron disable findata-plugins/tushare_daily_basic
findata cron set     findata-plugins/tushare_daily_basic --expression "30 18 * * 1-5" --timezone Asia/Shanghai
findata cron reset   findata-plugins/tushare_daily_basic         # restore the plugin's suggested schedule
```

`cron ls` shows enabled state, schedule source (default or override), last run, and next
run. `cron reset` restores the plugin's suggested schedule without changing enabled state.

## Timezone and daylight-saving behavior

Cron expressions are evaluated in the job's IANA timezone. Market jobs should use the
exchange timezone.

- A local wall time that does not exist because of a daylight-saving jump is **skipped**
  and records a warning event.
- A wall time that occurs twice runs **once**, at its first occurrence.
- Jobs missed while the server is down record a missed-job event after restart and are
  not submitted automatically.

See [Tasks and events](tasks-and-events.md#events-and-system-status) for inspecting those
events.
