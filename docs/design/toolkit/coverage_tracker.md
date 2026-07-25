# Coverage tracker

- **purpose**: remember resolved-empty intervals, enable coverage pruning, and keep coverage/status queries inexpensive
- **requires**: a `time-accumulating` dataset with `strict` or `accept-empty` missing-data policy
- **interface**: submit a coverage delta beside its data mutation so core commits both in one
  transaction; expose committed coverage through DataLoader
- **invariant**: each partition key has one continuous half-open civil-time interval; every due observation in the dataset's declared observation domain inside it is resolved, while non-observation dates require no row
- **failure behavior**: a committed revision never exposes coverage newer than its matching data
