# File rate limiter

- **purpose**: share one provider request budget across local task processes so every external
  request, including readiness probes, observes the provider's declared rate limit
- **requires**: a per-provider state file path inside the workspace and the provider's declared
  `rate_limit`/`period`; callers pass task `checkpoint` and `waiting` hooks so waits stay
  cancelable and report `provider_rate_limit`
- **interface**: `FileRateLimiter(path, limit=..., period=...)` with `try_acquire(now=None)` and
  blocking `acquire(checkpoint=None, waiting=None)`
- **failure behavior**: a missing or corrupt state file restarts the bucket empty rather than
  failing; state updates are atomic (temporary file, fsync, rename) and serialized by an flock
  on a sibling `.lock` file, so concurrent task processes cannot oversubscribe the budget
- **example**: `findata_tushare.datasets` routes every Tushare request through a limiter backed
  by `<workspace>/providers/tushare-rate.json`
