# Demo plugins

The **findata-test** family provides example plugins for evaluation. They require no
API token and work immediately with `--provider-mode mock`.

## Datasets

| Dataset | Contents | Operations |
|---|---|---|
| `findata-test/demo_hello` | Hardcoded greeting rows | `update`, `complete`, `refresh` |
| `findata-test/demo_random` | Deterministic random-walk price data | `update`, `complete`, `refresh` |

## Provider

| Provider | Description |
|---|---|
| `findata-test/demo` | Always-ready mock provider, no credentials |

## Usage

Install from a checkout of this repository:

```bash
pip install -e ./plugins/demo/provider \
             ./plugins/demo/datasets/demo-hello \
             ./plugins/demo/datasets/demo-random
```

Start the server with mock mode and run a task:

```bash
findata-server init ~/market-data
findata-server start ~/market-data --provider-mode mock
findata task run findata-test/demo_random complete \
  --param tickers=AAPL \
  --param timerange=2026-07-01:2026-07-10 \
  --wait
```

See the [Quick start](../get-started/quickstart.md) for a full walkthrough.
