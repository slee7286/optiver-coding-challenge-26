# Ten-minute Optibook runbook

## Confirmed from the supplied material

- Mission slide (unnumbered WhatsApp JPEG): research historical orders, trades and book snapshots, then trade AMD, AVGO, CRWV, MU, NBIS, NVDA, SNDK and VRT against teams and bots.
- Risk slide (1): hard cap of 100 lots long or short **per instrument**. Reserve capacity against same-side outstanding quantity independently; do not assume opposite-side fills. Positions in different stocks do not offset this cap.
- Fair-value slides (2–4): assess the price you can actually execute on, not just a midpoint. ABC is a teaching example, not a tradable symbol. Marking the 100-lot entry at 100.60 gives 18 before costs; the later book shows 50 lots bid at 100.70 and enough at 100.60 to model a 23 gross unwind. Displayed volume is not guaranteed to remain available.
- Competition slide (5): practice until approximately 20:20 local event time as shown on the slide, then the organizers reset positions/PnL and run a ten-minute scored round. The team must start its algorithm after reset. PnL, approach, risk and novelty matter; prize eligibility is for teams of 3–4. Confirm organizer timing and reset in the venue, not from the screenshot clock.
- Archive setup/FAQ: use hosted VS Code via Dev Environment and the Visualizer. Only one connection per username; a new client can displace a teammate. Disconnect pulls orders, not positions. Private fills are polled, public ticks start at connection. The API docs describe last-trade PnL and optional custom marks; neither custom mark nor replay PnL proves the leaderboard result.

## Before practice: highest-leverage sequence

1. Assign one connection owner; a second person owns data analysis, a third watches Visualizer/PnL and reset. Check the organizer's rules for AI use, external repositories, rate limits, and allowed data transfer. Keep all credentials and unapproved data out of Git.
2. Open `your_optiver_workspace/optibook_guides/code_snippets.ipynb` in the hosted workspace. Review cells before running; start with read-only calls (`get_tradable_instruments`, `get_last_price_book`, `get_positions`, `get_outstanding_orders`, `get_pnl`). Confirm IDs, tick sizes, depth, book timestamps and timestamp timezone/clock. Only the designated operator should connect.
3. Locate the historical files in the hosted workspace or organizer distribution; capture filenames, schemas, units, time ranges and first few permitted sample rows. No event files are in the supplied ZIP. Use data in place if transfer is restricted. For each symbol, first measure quote coverage, spread in ticks, top depth, trade frequency, and gaps. Prefer a liquid, testable 1–2 symbols rather than starting with all eight.
4. Benchmark midpoint momentum and microprice pressure first. Explore a peer-adjusted short-horizon residual (e.g., subtract a lagged basket return) only if synchronized history supports it. For trades/orders, align on timestamps using only events known then. Compare chronologically held-out blocks with fixed thresholds and horizons; stress extra cost, delays, and stale/missing exits. Check edge versus spread, counts, drawdown and concentration; reject weak or thinly sampled results. Do not call a winner on the synthetic fixture.
5. Practice at one lot on one instrument. Verify one limit/IOC request, response, actual fill/partial fill, outstanding-order reconciliation and voluntary unwind. A successful insert is not a fill; size exit from observed position and book, not intended size. Confirm what happens on disconnect and ensure the notebook is not still connected when the bot starts.

## Strategy and safety gates

- Prefer a simple strategy that actually demonstrates net edge: use small price-capped IOC only when the forecast clears the executable quote plus costs and a cushion; otherwise stand down. For passive quoting, prove the fill-and-markout behavior separately; the supplied replay models taking only. Skew toward reducing inventory and avoid stacking correlated bets.
- Suggested rehearsal defaults (not competition rules): 1 lot/order, soft cap 10 lots per stock and 20 gross lots initially, never above the hard cap after accounting for resting orders. Use venue-defined message limits; do not guess a permitted rate. Have one writer and never retry an ambiguous order without reconciling.
- Stop opening exposure on stale/empty/crossed book, connection or order-state uncertainty, repeated rejects, or a pre-agreed practice-calibrated drawdown limit. Cancel existing orders deliberately, reconcile fills and positions, then reduce risk if there is liquidity and time. Avoid unbounded marketable orders.

## Scored-round checklist

- Before reset: stop practice processes, check outstanding orders, confirm owner and exact start/end with organizers. Freeze parameters and record the commit.
- After reset: verify new positions/PnL/order state in Visualizer and client; start once on organizer signal. Start small, monitor private fills, inventory, rejected messages, book age and PnL; only expand if practice and live behavior support it.
- Final minute: stop new risk, cancel stale resting orders, reconcile positions, and work out of exposure with bounded prices where appropriate. Verify cancellation and actual remaining exposure; record final PnL, worst drawdown, peak inventory, rationale and any residual risk for judges.
