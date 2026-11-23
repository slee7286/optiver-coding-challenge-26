# Optibook challenge prep

Start with [the runbook](PREP.md). The original archive and the six WhatsApp slides are at the repository root. `optibook_resources/` contains the extracted API reference, setup/FAQ, screenshots and checksum manifest.

## Offline checks (Python 3.10+, no dependencies)

```text
python -m unittest discover -s tests -v
python prep.py slide
python prep.py research examples/synthetic_books.csv --horizon-ms 1000 --tolerance-ms 250 --trigger-bps 10 --cost-bps 2
```

The fixture is synthetic and only checks the pipeline. No provided historical event data was found in this repository. No script connects to Optibook, submits orders, or records credentials.

## Historical data input

Convert actual top-of-book snapshots to `ts_ms,instrument,bid,ask,bid_size,ask_size`; timestamps must be integer milliseconds on a common clock, unique per instrument, and prices/lots must be positive with `bid < ask`. Preserve source ordering when resolving timestamp ties before export; never synthesize a missing book side. Use separate chronological train and holdout CSVs; invoke the same fixed parameters on both. Any additional data dumps belong under ignored `data/`.

The replay compares one-step midpoint momentum and top-level microprice pressure, one lot per instrument, without overlapping trades within a signal/instrument. It crosses both observed spreads and deducts the specified per-side cost. The exit is the first book at or after the target horizon within tolerance; a missing exit is counted separately and halts that instrument/signal replay with one unresolved lot. Reported PnL covers closed trades only; do not rank an incomplete replay by this figure. It assumes execution at snapshot quotes: no decision latency, queue, partial fills, portfolio risk, or modeled market impact. Use it to reject weak signals, not to infer live profitability. Microprice is inside the spread: it is a directional predictor here, not immediate executable arbitrage.
