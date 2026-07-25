# Checkpoint-batch planner

- **purpose**: group provider work into bounded, resumable transactional checkpoints rather than
  committing once per provider request
- **requires**: deterministic, independently committable work items, a declarative core mutation
  scope, and request-count, staged-byte, and approximate-duration limits; a complete-table
  replacement is one indivisible work item
- **interface**: yield ordered batches of validated Arrow inputs and matching coverage deltas
- **invariant**: a work item appears in exactly one batch; limits are observed only between items,
  so one oversized indivisible item forms one oversized batch; processed progress and committed
  progress remain distinguishable
- **failure behavior**: failure discards only the uncommitted batch; a rerun reconstructs it from
  committed coverage without relying on private database state
- **boundary**: the helper never opens DuckDB, issues SQL, manages transactions, or changes the
  dataset gate; those are core storage responsibilities
