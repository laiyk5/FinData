# Task logging bridge

- **purpose**: let normal plugin logging flow into persistent task logs without dataset-specific protocol code
- **requires**: an authenticated task communication channel
- **interface**: a logging handler that serializes sanitized records to the TaskRunner
- **failure behavior**: logging failure must not corrupt a database commit or expose credentials;
  protocol backpressure is bounded
