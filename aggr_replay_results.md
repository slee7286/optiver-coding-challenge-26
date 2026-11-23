# Aggressive strategy: replay result

**Verdict: do not deploy on this evidence.** Both chronological partitions lost against doing nothing. Parameters were fixed before running either partition; no fit or holdout tuning. Dataset: one capture, eight instruments, 17,060 book events and 529 trades. Split at `1795459396331776705` ns by elapsed time. Portfolio, observations and pending orders start empty in each partition.

| Partition / policy | Net after crossings and fees | Units / fills | Half-spreads paid | Fees | Peak / average gross inventory |
|---|---:|---:|---:|---:|---:|
| First 207 seconds, model | −29.20 | 170 / 34 | 32.25 | 1.70 | 80 / 49.88 |
| First, long 10 per instrument | +66.70 | 160 / 33 | 38.05 | 1.60 | 80 / 72.43 |
| Last 103 seconds, model | −12.00 | 50 / 10 | 6.25 | 0.50 | 25 / 5.00 |
| Last, long 10 per instrument | Not fully liquidated | 159 / 33 | 26.75 | 1.59 | 80 / 63.89 |
| No trade, either partition | 0.00 | 0 / 0 | 0.00 | 0.00 | 0 / 0 |

The holdout long baseline finished with cash −131.59 and one NBIS share. Its last observed executable-side *mark* was −36.80 including an estimated exit fee, not realized P&L. Model closed all positions. Holdout losses: AMD −3.10 and VRT −8.90. The long baseline is a capacity-matched directional exposure comparator, not beta- or time-exposure-matched.

## Mechanics and limitations

- Short and long endpoint averages of midpoint over a trailing window of up to 30s, recent 12s signed trade volume confirmation (`bid` aggressor buys), spread and fee hurdle, neutral hold band. Targets are absolute; `None` means hold current position. Sampling gaps greater than 3s break the feature history.
- Clock uses the latest two-sided uncrossed book no older than 2s. Empty/invalid updates invalidate the prior book immediately. A decision on second `s` executes on second `s+1` at that time's displayed touch, clipped to displayed quantity. Includes 0.01 assumed fee per share per side and a 0.10 assumed price tick. No passive fills, no stale exits, no market impact, queue or price protection. A live limit IOC with a stale decision-time limit may fill less often; snapshots may also overstate reusable liquidity.
- Globally one order per second, clip 5, gross 80, inventory ±20 per instrument. Reductions take priority, then largest target gap, then symbol name. Scheduled liquidation starts 18s before each known partition end subject to identical constraints. For unscheduled termination this closeout window cannot be assumed.
- The 103s holdout had just 10 model fills across two instruments; not a statistical test of persistent alpha. Deterministic symbol tie-breaks and cold starts influence exposures. Cash-only results are supplied only when flat; stale positions remain explicitly unresolved.

Reproduce from repo root (Python standard library):

```sh
python -m unittest test_aggr_model_part -v
python -c "from aggr_model_part import replay_cli; replay_cli('C:/Users/slee7/Downloads')"
```
