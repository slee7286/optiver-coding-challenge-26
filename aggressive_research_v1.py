"""Aggressive directional research; import-safe, standard library only.
Run: python -c "from aggr_model_part import replay_cli; replay_cli(r'C:/Users/slee7/Downloads')"
"""

import math as _ag_math


def target(history, tick):
    """Absolute inventory or None (keep existing inventory, not old order)."""
    if len(history) < 12 or not _ag_math.isfinite(tick) or tick <= 0:
        return None
    end = history[-1][0]
    window = []
    for row in reversed(history):
        if end - row[0] > 30:
            break
        if (
            not all(map(_ag_math.isfinite, row))
            or not (0 < row[1] < row[2])
            or min(row[3:5]) <= 0
            or min(row[5:]) < 0
        ):
            return None
        if window and not 0 < window[-1][0] - row[0] <= 3:
            break
        window.append(row)
    if len(window) < 12 or end - window[-1][0] < 15:
        return None

    def mid(r):
        return (r[1] + r[2]) / 2

    current = window[0]
    spread = current[2] - current[1]
    recent = [r for r in window if end - r[0] <= 5]
    old = [r for r in window if r[0] - window[-1][0] <= 5]
    move = sum(map(mid, recent)) / len(recent) - sum(map(mid, old)) / len(old)
    buys = sum(r[5] for r in window if end - r[0] <= 12)
    sells = sum(r[6] for r in window if end - r[0] <= 12)
    flow = (buys - sells) / (buys + sells) if buys + sells else 0.0
    # Entry pays both crossings and a fixed fee cushion; neutral band holds.
    if (
        abs(move) > max(spread + 0.02, 1.5 * tick)
        and move * flow > 0
        and abs(flow) >= 0.15
    ):
        return 20 if move > 0 else -20
    if abs(move) < 0.4 * tick and spread <= 2.01 * tick:
        return 0
    return None


def _ag_frames(books, trades, lo, hi):
    """Integer-second decisions, using only events timestamped <= decision."""
    from collections import defaultdict

    ns = 10**9
    books = [e for e in books if lo <= e[0] < hi]
    trades = [e for e in trades if lo <= e[0] < hi]
    i = j = 0
    latest = {}
    frames = []
    for sec in range((lo + ns - 1) // ns, (hi - 1) // ns + 1):
        now = sec * ns
        flow = defaultdict(lambda: [0, 0])
        while i < len(books) and books[i][0] <= now:
            t, k, q = books[i]
            latest[k] = (t, q)
            i += 1
        while j < len(trades) and trades[j][0] <= now:
            _, k, side, qty = trades[j]
            flow[k][side] += qty
            j += 1
        quotes = {
            k: (sec, *q, *flow[k])
            for k, (t, q) in latest.items()
            if q is not None and now - t <= 2 * ns
        }
        frames.append((sec, quotes))
    return frames


def _ag_replay(frames, model, fee=0.01):
    """One globally scheduled IOC at next second's touch; no stale quote fills.
    Flat-start; scheduled close begins 18 seconds before known segment end.
    Missing close liquidity leaves residuals, never a fictitious liquidation.
    """
    from collections import defaultdict, deque

    history = defaultdict(lambda: deque(maxlen=64))
    pos = defaultdict(int)
    pending = None
    peak = units = fills = misses = orders = gross_time = 0
    cash = cross = 0.0
    first = None
    last = {}
    end = frames[-1][0] if frames else 0
    sym_cash = defaultdict(float)
    for s, quotes in frames:
        if pending is not None:
            k, order = pending
            row = quotes.get(k)
            pending = None
            if row is None:
                misses += 1
            else:
                buy = order > 0
                size = min(abs(order), int(row[4 if buy else 3]))
                qty = size if buy else -size
                if size:
                    price = row[2 if buy else 1]
                    delta = -qty * price - size * fee
                    cash += delta
                    sym_cash[k] += delta
                    pos[k] += qty
                    units += size
                    fills += 1
                    cross += size * (row[2] - row[1]) / 2
                    if first is None:
                        first = s
        gross = sum(abs(x) for x in pos.values())
        peak = max(peak, gross)
        gross_time += gross
        choices = []
        for k, row in quotes.items():
            last[k] = row
            h = history[k]
            if h and s - h[-1][0] > 3:
                h.clear()
            h.append(row)
            p = pos[k]
            dest = 0 if s >= end - 18 else model(h, 0.1)
            if dest is None or dest == p or s == end:
                continue
            step = max(-5, min(5, dest - p))
            close = p * step < 0
            if not close:
                room = 80 - gross
                step = max(-room, min(room, step))
            if step:
                choices.append((not close, -abs(dest - p), k, step))
        if choices:
            _, _, k, step = min(choices)
            pending = (k, step)
            orders += 1
    residual = {k: v for k, v in pos.items() if v}
    # Diagnostic only: last observed liquidation-side mark, may be stale.
    mark = cash + sum(
        v * last[k][1 if v > 0 else 2] - abs(v) * fee for k, v in residual.items()
    )
    return dict(
        net_executable=None if residual else round(cash, 4),
        residual=residual,
        last_touch_mark=round(mark, 4),
        cash=round(cash, 4),
        fees=round(units * fee, 4),
        half_spreads_paid=round(cross, 4),
        units=units,
        fills=fills,
        orders=orders,
        missing_quote_orders=misses,
        peak_gross=peak,
        mean_gross=round(gross_time / max(1, len(frames)), 3),
        first_fill_sec=first,
        closed_symbol_net={
            k: round(v, 4) for k, v in sorted(sym_cash.items()) if k not in residual
        },
    )


def replay_cli(data_dir):
    """Print JSON report and return it. No hidden fit, calibration or I/O on import."""
    import csv
    import gzip
    import json
    from pathlib import Path

    root = Path(data_dir)
    books = []
    trades = []
    sessions = set()
    for name in ("pricebooks", "trades"):
        with gzip.open(
            root / (name + ".csv.gz"), "rt", newline="", encoding="utf-8-sig"
        ) as f:
            for row in csv.DictReader(f):
                t = int(row["timestamp_ns"])
                k = row["instrument_id"]
                sessions.add(row["exchange_started_at"])
                if name == "trades":
                    side = {"bid": 0, "ask": 1}[row["aggressor_side"]]
                    trades.append((t, k, side, int(row["volume"])))
                else:
                    bids = json.loads(row["bids_json"])
                    asks = json.loads(row["asks_json"])
                    q = None
                    if bids and asks:
                        b, bs = max(bids)
                        a, az = min(asks)
                        if 0 < b < a and min(bs, az) >= 1:
                            q = (b, a, bs, az)
                    books.append((t, k, q))
    if len(sessions) != 1 or not books:
        raise ValueError("expected one nonempty exchange session")
    books.sort(key=lambda x: x[0])
    trades.sort(key=lambda x: x[0])
    lo = books[0][0]
    hi = books[-1][0] + 1
    split = lo + (hi - lo) * 2 // 3
    report = {"books": len(books), "trades": len(trades), "split_ns": split}
    for name, a, b in [("first_two_thirds", lo, split), ("last_third", split, hi)]:
        frames = _ag_frames(books, trades, a, b)
        report[name] = {
            "seconds": len(frames),
            "model": _ag_replay(frames, target),
            "long_10_each": _ag_replay(frames, lambda h, t: 10),
            "no_trade": 0.0,
        }
    print(json.dumps(report, indent=2))
    return report


# Standalone execution adapter; concatenate after target(history, tick).
# All exits HOLD current inventory. No cancellation, flattening or reconnection.
# Keep .aggr-risk.json and .aggr-live.lock beside the script across restarts.
# A crash lock, pending IOC or latched brake requires manual operator review.
import json as _ag_json
import math as _ag_math
import os as _ag_os
import time as _ag_time
from datetime import timezone as _ag_utc
from pathlib import Path as _AgPath

_AG_NAMES = ("VRT", "NVDA", "MU", "AMD", "AVGO", "SNDK", "NBIS", "CRWV")


def _aggr_quote(x, symbol, wall):
    b = x.get_last_price_book(symbol)
    if not b or not b.bids or not b.asks or not b.timestamp:
        return None
    ts = b.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_ag_utc.utc)
    bid, ask = b.bids[0], b.asks[0]
    if not (
        -0.1 <= wall() - ts.timestamp() <= 1
        and 0 < bid.price < ask.price
        and _ag_math.isfinite(ask.price)
        and type(bid.volume) is int
        and type(ask.volume) is int
        and min(bid.volume, ask.volume) > 0
    ):
        return None
    return bid.price, ask.price, bid.volume, ask.volume


def _aggr_run(minutes, signal, factory, root, mono, wall, sleep):
    """Dependency-injected runner. Signal is a signed desired position, or None to hold."""
    if not callable(signal) or not _ag_math.isfinite(minutes) or minutes <= 0:
        raise ValueError("positive finite minutes and callable target required")
    end = mono() + minutes * 60
    if not _ag_math.isfinite(end):
        raise ValueError("duration overflow")

    def stopped():
        return mono() >= end or any(
            (root / n).exists() for n in ("STOP", "STOP_PRACTICE", "STOP_AGGR")
        )

    if stopped():
        return "stopped"
    lock = root / ".aggr-live.lock"
    lock.mkdir()  # exclusive; stale lock is deliberately not automatically broken
    path = root / ".aggr-risk.json"
    x = None

    def persist():
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf8") as f:
            _ag_json.dump(state, f, allow_nan=False)
            f.flush()
            _ag_os.fsync(f.fileno())
        _ag_os.replace(tmp, path)

    try:
        state = (
            _ag_json.loads(path.read_text())
            if path.exists()
            else {"peak": None, "brake": False, "pending": None}
        )
        if state["pending"] is not None:
            raise RuntimeError("unreconciled IOC: inspect account before restart")
        if type(state["brake"]) is not bool or (
            path.exists() and not _ag_math.isfinite(state["peak"])
        ):
            raise ValueError("invalid persistent risk state")
        if state["brake"]:
            return "drawdown brake; holding inventory"
        if stopped():
            return "stopped"
        x = factory()
        x.connect()
        ids = tuple(x.get_instruments())
        instruments = x.get_tradable_instruments()
        names = [s for s in _AG_NAMES if s in instruments]
        if not names:
            raise RuntimeError("no eligible basket instruments")

        def positions():
            p = x.get_positions()
            if any(type(v) is not int for v in p.values()):
                raise RuntimeError("invalid position")
            return {s: v for s, v in p.items() if v}

        def verify(expected):
            if (
                not x.is_connected()
                or any(x.get_outstanding_orders(s) for s in ids)
                or positions() != expected
            ):
                raise RuntimeError(
                    "disconnected, resting orders or position mismatch; stopping"
                )

        def brake():
            pnl = x.get_pnl()
            if not _ag_math.isfinite(pnl):
                raise RuntimeError("nonfinite account PnL")
            if state["peak"] is None or pnl > state["peak"]:
                state["peak"] = pnl
                persist()
            if state["peak"] - pnl >= 5:
                state["brake"] = True
                persist()
                return True
            return False

        p = positions()
        verify(p)
        if (
            any(s not in _AG_NAMES or abs(v) > 20 for s, v in p.items())
            or sum(map(abs, p.values())) > 80
        ):
            raise RuntimeError("starting inventory outside basket limits")
        if brake():
            return "drawdown brake; holding inventory"
        for s in names:
            x.poll_new_trade_ticks(s)
            x.poll_new_trades(s)  # discard pre-interval backlogs
        histories = {s: [] for s in names}
        cursor = 0
        last = mono()
        while not stopped():
            started = mono()
            verify(p)
            if brake():
                return "drawdown brake; holding inventory"
            candidates = []
            for j in range(len(names)):
                s = names[(cursor + j) % len(names)]
                tick = instruments[s].tick_size
                trades = x.poll_new_trade_ticks(s)
                q = _aggr_quote(x, s, wall)
                if not q or not _ag_math.isfinite(tick) or tick <= 0:
                    histories[s].clear()
                    continue
                buy = sell = 0
                for t in trades:
                    if type(t.volume) is not int or t.volume <= 0:
                        raise ValueError("invalid public volume")
                    if t.aggressor_side == "bid":
                        buy += t.volume
                    elif t.aggressor_side == "ask":
                        sell += t.volume
                    else:
                        raise ValueError("unknown public aggressor side")
                h = histories[s]
                h.append((wall(), *q, buy, sell))
                del h[:-3600]
                want = signal(h, tick)
                if want is None:
                    continue
                if (
                    isinstance(want, bool)
                    or not _ag_math.isfinite(want)
                    or int(want) != want
                ):
                    raise ValueError("invalid signed target")
                v = p.get(s, 0)
                d = max(-20, min(20, int(want))) - v
                if d:
                    candidates.append((s, q, d))
            for s, q, d in candidates:
                if mono() - last < 1 or stopped():
                    break
                sign = 1 if d > 0 else -1
                side = "bid" if d > 0 else "ask"
                i = 1 if d > 0 else 0
                v = p.get(s, 0)
                room = 80 - sum(map(abs, p.values())) + abs(v) - sign * v
                if room <= 0:
                    continue
                verify(p)
                if x.poll_new_trades(s):
                    raise RuntimeError("unexpected private trade before order")
                if brake():
                    return "drawdown brake; holding inventory"
                fresh = _aggr_quote(x, s, wall)
                if not fresh or sign * (fresh[i] - q[i]) > 0:
                    continue
                size = min(5, abs(d), room, fresh[i + 2], q[i + 2])
                price = fresh[i]
                state["pending"] = {
                    "symbol": s,
                    "side": side,
                    "volume": size,
                    "price": price,
                    "before": p,
                }
                persist()  # write ahead: even a lost acknowledgement blocks automatic restart
                if stopped() or wall() - histories[s][-1][0] > 1:
                    state["pending"] = None
                    persist()
                    break
                last = mono()
                r = x.insert_order(
                    s, price=price, volume=size, side=side, order_type="ioc"
                )
                if r.success is not True or type(r.order_id) is not int:
                    raise RuntimeError("IOC not confirmed; no retry")
                sleep(0.4)
                fills = x.poll_new_trades(s)
                seen = set()
                got = 0
                for f in fills:
                    if (
                        f.instrument_id != s
                        or f.side != side
                        or f.order_id != r.order_id
                        or f.trade_id in seen
                        or type(f.volume) is not int
                        or f.volume <= 0
                        or not _ag_math.isfinite(f.price)
                        or sign * (f.price - price) > 1e-8
                    ):
                        raise RuntimeError("unexpected private fill")
                    seen.add(f.trade_id)
                    got += f.volume
                if got > size:
                    raise RuntimeError("overfill")
                expected = dict(p)
                expected[s] = v + sign * got
                if not expected[s]:
                    del expected[s]
                verify(expected)
                p = expected
                state["pending"] = None
                persist()
                cursor = (names.index(s) + 1) % len(names)
                print(
                    "IOC",
                    s,
                    side,
                    "requested",
                    size,
                    "filled",
                    got,
                    "positions",
                    p,
                    flush=True,
                )
                if brake():
                    return "drawdown brake; holding inventory"
                break
            sleep(max(0, min(end - mono(), started + 1 - mono())))
        return "stopped; holding inventory"
    finally:
        try:
            if x is not None:
                x.disconnect()
        finally:
            lock.rmdir()


def live_cli(minutes):
    """Explicit simulator entrypoint only. STOP, STOP_AGGR or STOP_PRACTICE beside script stops writes."""

    def factory():
        from optibook.synchronous_client import Exchange

        return Exchange()

    result = _aggr_run(
        minutes,
        target,
        factory,
        _AgPath(__file__).resolve().parent,
        _ag_time.monotonic,
        _ag_time.time,
        _ag_time.sleep,
    )
    print(result, flush=True)
    return result


"""CLI for an assembled, single-file Optibook research candidate."""


def tape_audit(path):
    import csv
    import gzip
    from collections import Counter
    from pathlib import Path

    for feed in ("orders", "trades", "pricebooks"):
        p = Path(path) / (feed + ".csv.gz")
        if not p.exists():
            print(feed, "not present")
            continue
        counts = Counter()
        fields = Counter()
        n = 0
        earliest = None
        latest = None
        with gzip.open(p, "rt", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                n += 1
                counts[row["instrument_id"]] += 1
                t = int(row["timestamp_ns"])
                earliest = t if earliest is None else min(t, earliest)
                latest = t if latest is None else max(t, latest)
                if feed == "orders":
                    fields[row.get("action", "") or "unspecified"] += 1
        print(
            "audit",
            feed,
            "rows",
            n,
            "symbols",
            len(counts),
            "span_seconds",
            round((latest - earliest) / 1e9, 2) if n else None,
            "order_events",
            dict(fields) if fields else "-",
        )


def fetch_history(directory, minutes):
    """Read-only historical service; does not construct Exchange or submit orders."""
    import json
    import time
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from optibook.historical_data_loader import HistoricalDataLoader

    if not 1 <= minutes <= 120:
        raise ValueError("history minutes must be 1..120")
    end = datetime.now(timezone.utc).replace(
        tzinfo=None, second=0, microsecond=0
    ) - timedelta(minutes=1)
    start = end - timedelta(minutes=minutes)
    out = Path(directory) / (end.strftime("history_%Y%m%dT%H%MZ"))
    out.mkdir(parents=True, exist_ok=False)
    loader = HistoricalDataLoader()
    counts = {}
    for feed in ("trades", "orders", "pricebooks"):
        if counts:
            time.sleep(2.1)
        df = getattr(loader, "get_" + feed + "_df")(start, end)
        df.to_csv(out / (feed + ".csv.gz"), index=False, compression="gzip")
        counts[feed] = len(df)
    (out / "window.json").write_text(
        json.dumps(
            {"start_utc": start.isoformat(), "end_utc": end.isoformat(), "rows": counts}
        ),
        encoding="utf8",
    )
    print("Historical archive", out, counts, flush=True)
    if min(counts.values()) == 0:
        print("Incomplete or empty historical feed; do not infer zero market activity.")


def command_line():
    import argparse
    from pathlib import Path

    p = argparse.ArgumentParser(
        description="Team 011 aggressive candidate. Default is offline replay; live orders require explicit live mode."
    )
    p.add_argument(
        "mode", choices=["research", "fetch", "live"], nargs="?", default="research"
    )
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.add_argument("--minutes", type=int, default=10)
    args = p.parse_args()
    if args.mode == "fetch":
        return fetch_history(args.data_dir, args.minutes)
    if args.mode == "live":
        if not 1 <= args.minutes <= 10:
            p.error("live runtime must be 1..10 minutes")
        return live_cli(args.minutes)
    tape_audit(args.data_dir)
    return replay_cli(args.data_dir)


if __name__ == "__main__":
    command_line()
