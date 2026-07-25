# Publication-window pruning

- **purpose**: determine whether requested data is due and whether an empty result is transient or final
- **requires**: a declared publication window
- **interface**: given a target range and the current time, return:
  - `not-due` — prune the interval
  - `due-transient` — fetch, but do not resolve an empty response
  - `due` — fetch and resolve an allowed empty response
