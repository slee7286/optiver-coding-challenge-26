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
