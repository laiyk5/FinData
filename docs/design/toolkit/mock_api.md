# Mock API

- **purpose**: develop and test operations without calling a real provider
- **requires**: one row generator per dataset
- **interface**: given a parsed request, fabricate rows matching that dataset's schema and invariants
- **activation**: the reserved provider token `findata-mock` selects deterministic success;
  `findata-mock:fail=<api>@<call>` selects one deterministic terminal failure for E2E recovery tests
- **failure behavior**: support deterministic injection of provider failures, rate limiting, malformed responses, and publication-window-aware empty responses
- **sharing rule**: a provider-family harness may share envelope format and error simulation, but never dataset-specific row shapes
