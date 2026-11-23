"""Offline, one-lot cancellation/refill replay. No exchange client or network I/O."""

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import tempfile
from collections import Counter, deque
from contextlib import closing
from dataclasses import asdict, dataclass
from itertools import groupby
from pathlib import Path

MS = 1_000_000
DAY = 86_400_000 * MS
SCHEMA = {
    "orders": "order_id side action order_type volume price",
    "trades": "trade_id aggressor_side volume price",
    "pricebooks": "bids_json asks_json",
}


@dataclass(frozen=True)
class Policy:
    window_ms: int = 1000
    min_cancel: int = 100
    cancel_exec_ratio: int = 2
    refill_fraction: float = 0.5
    latency_ms: int = 100
    hold_ms: int = 2000
    quote_wait_ms: int = 1000
    cost_per_leg: float = 0.01  # one unit, in price units; spread charged separately


def lifecycle(live, event):
    """Archive volumes are *remaining*, including zero on cancel and fill.

    Ignore IOC liquidity. An unseen order seeds state but cannot imply a delta.
    Amend-down is not counted as an explicit cancel. Reprices are not refills.
    """
    oid, side, action, typ, size, price = event
    if typ != "limit":
        return 0, 0, 0
    old = live.get(oid)
    if action in ("cancelled", "fill"):
        live.pop(oid, None)
    else:
        live[oid] = side, size, price
    prior = old[1] if old and old[0] == side else None
    if action == "new":
        return 0, 0, size if prior is None else 0
    if prior is None:
        return 0, 0, 0
    if action == "cancelled":
        return prior, 0, 0
    if action in ("partialFill", "fill"):
        return 0, max(0, prior - size), 0
    return 0, 0, max(0, size - prior) if old[2] == price else 0


def stage(db, directory: Path):
    """Disk-backed sort and unique index; preserve rows differing only in ID."""
    db.execute(
        "create table e(t integer,s text,i text,k text,r text, primary key(t,s,i,k,r)) without rowid"
    )
    stats = Counter(duplicates=0, repeated_headers=0)
    hashes = {}
    for kind, fields in SCHEMA.items():
        path = directory / (kind + ".csv.gz")
        with path.open("rb") as f:
            digest = hashlib.sha256()
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
            hashes[path.name] = digest.hexdigest()
        with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required = fields.split() + [
                "timestamp_ns",
                "exchange_started_at",
                "instrument_id",
            ]
            if not set(required) <= set(reader.fieldnames or []):
                raise ValueError(f"{path}: missing required columns")
            for row in reader:
                if row["timestamp_ns"] == "timestamp_ns":
                    stats["repeated_headers"] += 1
                    continue
                if None in row or any(row[x] is None for x in required):
                    raise ValueError(f"{path}:{reader.line_num}: malformed row")
                record = (
                    int(row["timestamp_ns"]),
                    row["exchange_started_at"],
                    row["instrument_id"],
                    kind,
                    json.dumps(row, separators=(",", ":")),
                )
                stored = db.execute(
                    "insert or ignore into e values(?,?,?,?,?)", record
                ).rowcount
                stats[kind] += stored
                stats["duplicates"] += 1 - stored
    db.commit()
    return dict(stats), hashes


def quote(row):
    bids, asks = (json.loads(row[k]) for k in ("bids_json", "asks_json"))
    if not bids or not asks:
        return None
    b, bq = max(bids)
    a, aq = min(asks)
    if not (0 < b < a and min(bq, aq) >= 1 and all(map(math.isfinite, (b, a, bq, aq)))):
        return None
    return b, a, bq, aq


class Instrument:
    def __init__(self, p, stats):
        self.p, self.stats = p, stats
        self.live, self.flow, self.mids = {}, deque(), deque()
        self.shock = self.pending = self.position = None
        self.halted = False

    def finish(self):
        if self.position:
            self.stats["unresolved_exits"] += 1
        if self.pending:
            self.stats["unfilled_entries"] += 1

    def book(self, t, q):
        p, s = self.p, self.stats
        start = t - p.window_ms * MS
        for dq in (self.flow, self.mids):
            while dq and dq[0][0] < start:
                dq.popleft()
        if self.halted:
            return
        if self.position:
            sign, price, entrymid, due = self.position
            if t > due + p.quote_wait_ms * MS:
                self.halted = (
                    True  # carry the actual exposure, never retrospectively skip it
                )
            elif t >= due and q:
                out = q[0] if sign == 1 else q[1]
                gross = sign * (out - price)
                s["closed_gross"] += gross
                s["closed_net"] += gross - 2 * p.cost_per_leg
                s["closed_mid"] += sign * ((q[0] + q[1]) / 2 - entrymid)
                s["completed"] += 1
                self.position = None
            return
        if self.pending:
            sign, due = self.pending
            if t > due + p.quote_wait_ms * MS:
                s["unfilled_entries"] += 1
                self.pending = None
            elif t >= due and q:
                self.position = (
                    sign,
                    q[1] if sign == 1 else q[0],
                    (q[0] + q[1]) / 2,
                    t + (p.hold_ms + p.latency_ms) * MS,
                )
                s["entries"] += 1
                self.pending = None
            return
        if not q:
            self.shock = None
            self.mids.clear()
            return
        b, a, bq, aq = q
        mid = (b + a) / 2
        anchor = self.mids[0][1] if self.mids else mid
        self.mids.append((t, mid))
        if self.shock:
            when, side, cancel, px, depth = self.shock
            if when < start:
                self.shock = None
            else:
                bid = side == "bid"
                refill = sum(
                    n
                    for ts, sd, c, e, tr, n, level in self.flow
                    if ts > when
                    and sd == side
                    and (level >= px if bid else level <= px)
                )
                recovered = (
                    (b > px or (b == px and bq > depth))
                    if bid
                    else (a < px or (a == px and aq > depth))
                )
                if refill >= cancel * p.refill_fraction and recovered:
                    self.pending = (1 if bid else -1, t + p.latency_ms * MS)
                    s["signals"] += 1
                    self.shock = None
                return
        if mid == anchor:
            return
        bid = mid < anchor
        side = "bid" if bid else "ask"
        c = e = tr = opposite = 0
        for _, sd, cancel, executed, tape, _, _ in self.flow:
            if sd == side:
                c += cancel
                e += executed
                tr += tape
            else:
                opposite += cancel
        # Same fills can appear on both feeds: do not sum their quantities.
        if (
            c >= p.min_cancel
            and c > opposite
            and c >= p.cancel_exec_ratio * max(e, tr, 1)
        ):
            self.shock = t, side, c, b if bid else a, bq if bid else aq
            s["shocks"] += 1

    def frame(self, t, rows):
        books, orders, trades = [], {}, []
        for kind, r in rows:
            if kind == "pricebooks":
                books.append(quote(r))
            elif kind == "trades":
                trades.append(r)
            else:
                orders.setdefault(r["order_id"], []).append(r)
        # Cross-feed ties have no known ordering. Books see strictly older flow.
        if books:
            self.book(t, books[0] if len(set(books)) == 1 else None)
        while self.flow and self.flow[0][0] < t - self.p.window_ms * MS:
            self.flow.popleft()
        for oid, updates in orders.items():
            if len(updates) != 1:
                self.live.pop(oid, None)
                self.stats["ambiguous_order_groups"] += 1
                continue
            r = updates[0]
            side = r["side"]
            price = float(r["price"])
            if side not in ("bid", "ask") or r["action"] not in (
                "new",
                "cancelled",
                "fill",
                "partialFill",
                "amended",
            ):
                raise ValueError(f"unknown order side/action: {side}/{r['action']}")
            c, e, n = lifecycle(
                self.live,
                (oid, side, r["action"], r["order_type"], int(r["volume"]), price),
            )
            if c or e or n:
                self.flow.append((t, side, c, e, 0, n, price))
        for r in trades:
            side = {"ask": "bid", "bid": "ask"}[r["aggressor_side"]]
            self.flow.append((t, side, 0, 0, int(r["volume"]), 0, 0))


def run(directory, train_fraction=0.7, policy=Policy()) -> dict:
    """Split on distinct timestamps. Both partitions and UTC dates start cold."""
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if (
        policy.latency_ms < 1
        or not math.isfinite(policy.cost_per_leg)
        or policy.cost_per_leg < 0
    ):
        raise ValueError("latency >= 1 ms and finite, nonnegative costs required")
    with (
        tempfile.TemporaryDirectory() as tmp,
        closing(sqlite3.connect(Path(tmp) / "events.db")) as db,
    ):
        stats, hashes = stage(db, Path(directory))
        n = db.execute("select count(distinct t) from e").fetchone()[0]
        if n < 2:
            raise ValueError("need two distinct timestamps")
        ix = max(1, min(n - 1, int(n * train_fraction)))
        cut = db.execute(
            "select distinct t from e order by t limit 1 offset ?", (ix,)
        ).fetchone()[0]
        report = dict(
            policy=asdict(policy),
            train_fraction=train_fraction,
            holdout_start_ns=cut,
            sha256=hashes,
            ingest=stats,
        )
        for label, op in [("train", "<"), ("holdout", ">=")]:
            result = Counter(
                {
                    k: 0
                    for k in "flat_net closed_mid closed_gross closed_net completed entries signals shocks unresolved_exits unfilled_entries".split()
                }
            )
            stream = db.execute(
                f"select * from e where t {op} ? order by t,s,i,k,r", (cut,)
            )
            states = {}
            prevday = None
            for (t, s, i), group in groupby(stream, key=lambda row: row[:3]):
                if prevday != t // DAY:
                    for state in states.values():
                        state.finish()
                    states.clear()
                    prevday = t // DAY
                if (s, i) not in states:
                    states[s, i] = Instrument(policy, result)
                states[s, i].frame(t, ((r[3], json.loads(r[4])) for r in group))
                result.setdefault("start_ns", t)
                result["end_ns"] = t
            for state in states.values():
                state.finish()
            report[label] = dict(result)
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "data_dir",
        type=Path,
        help="directory containing orders, trades and pricebooks .csv.gz",
    )
    cli.add_argument("--latency-ms", type=int, default=100)
    cli.add_argument("--cost-per-leg", type=float, default=0.01)
    cli.add_argument("--train-fraction", type=float, default=0.7)
    args = cli.parse_args()
    print(
        json.dumps(
            run(
                args.data_dir,
                args.train_fraction,
                Policy(latency_ms=args.latency_ms, cost_per_leg=args.cost_per_leg),
            ),
            sort_keys=True,
            indent=2,
        )
    )
