# Constituent-set resolver

- **purpose**: resolve a dataset plugin's already-parsed request for index constituents
- **requires**: a constituent dataset declared in `dependencies`
- **interface**: accept a semantic request containing the dependency key and one of:
  - `latest`, resolved as the most recent snapshot effective by a target endpoint; or
  - `range-union`, resolved as the snapshot effective at range start plus changes inside the range
- **boundary**: the toolkit never parses a CLI string, configuration value, provider reference, or
  friendly alias. The consuming dataset plugin owns those syntaxes, validates them against any
  provider reference metadata it uses, and passes the exact dependency key to the resolver.
- **identity rule**: preserve the exact dependency key supplied by the plugin; never infer or
  transform it
- **temporal rule**: for snapshot-backed membership, the declared effective-date column is
  authoritative; a storage partition or request bucket never changes the snapshot's effective date
- **fulfillment**: ask the plugin-supplied callback to establish dependency coverage before reading;
  snapshot-backed callbacks include enough predecessor coverage to establish the initial as-of state
