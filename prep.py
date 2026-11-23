"""Offline Optibook prep. No network, credentials, or order submission."""
import argparse
from bisect import bisect_left
import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import TypedDict

D = Decimal
UNIVERSE = ('AMD', 'AVGO', 'CRWV', 'MU', 'NBIS', 'NVDA', 'SNDK', 'VRT')
POSITION_LIMIT = 100


class ReplayResult(TypedDict):
    trades: int
    wins: int
    missing_exit: int
    net_pnl: Decimal
    worst_trade: Decimal | None


@dataclass(frozen=True)
class Book:
    ts_ms: int
    instrument: str
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int


def room(position, side, resting=0):
    """Additional lots allowed if all same-side outstanding lots execute."""
    if side not in ('bid', 'ask') or resting < 0 or abs(position) > POSITION_LIMIT:
        raise ValueError('invalid side, resting size, or position')
    directional_position = position if side == 'bid' else -position
    return max(0, POSITION_LIMIT - directional_position - resting)


def walk(levels, lots, side, limit):
    """Hypothetical IOC fills from best-first opposing levels, not an order."""
    if side not in ('bid', 'ask') or lots < 0:
        raise ValueError('invalid side or quantity')
    fills = []
    for price, size in levels:
        if not lots or (price > limit if side == 'bid' else price < limit):
            break
        take = min(lots, size)
        if take:
            fills.append((price, take))
            lots -= take
    return fills


def slide_example():
    buys = walk([(D('100.30'), 15), (D('100.40'), 50), (D('100.50'), 110)],
                room(0, 'bid'), 'bid', D('100.50'))
    inventory = sum(q for _, q in buys)
    cost = sum(p * q for p, q in buys)
    sells = walk([(D('100.70'), 50), (D('100.60'), 76)], inventory, 'ask', D('100.60'))
    return D('100.60') * inventory - cost, sum(p * q for p, q in sells) - cost


def evaluate(books, *, signal, horizon_ms, tolerance_ms, trigger_bps, cost_bps):
    """First snapshot at/after fixed horizon, bounded gap; one lot, no overlap."""
    if (signal not in ('momentum', 'microprice') or horizon_ms <= 0 or tolerance_ms < 0
            or not trigger_bps.is_finite() or trigger_bps <= 0
            or not cost_bps.is_finite() or cost_bps < 0):
        raise ValueError('invalid replay parameters')
    times = [b.ts_ms for b in books]
    if len({b.instrument for b in books}) > 1 or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('one instrument with strictly increasing timestamps required')
    r: ReplayResult = {'trades': 0, 'wins': 0, 'missing_exit': 0,
                       'net_pnl': D(0), 'worst_trade': None}
    free_after = None
    prev_mid = None
    for i, b in enumerate(books):
        mid = (b.bid + b.ask) / 2
        estimate = ((b.ask * b.bid_size + b.bid * b.ask_size) / (b.bid_size + b.ask_size)
                    if signal == 'microprice' else (mid if prev_mid is None else 2 * mid - prev_mid))
        prev_mid = mid
        strength = (estimate - mid) * 10000 / mid
        if abs(strength) < trigger_bps or (free_after is not None and b.ts_ms <= free_after):
            continue
        j = bisect_left(times, b.ts_ms + horizon_ms, lo=i + 1)
        if j == len(books) or times[j] > b.ts_ms + horizon_ms + tolerance_ms:
            # The entry already owns the lot. Without a valid exit, stop
            # this instrument's replay rather than assume it became flat.
            r['missing_exit'] += 1
            break
        exit_book = books[j]
        buy, sell = (b.ask, exit_book.bid) if strength > 0 else (exit_book.ask, b.bid)
        pnl = sell - buy - (buy + sell) * cost_bps / 10000
        r['trades'] += 1
        r['wins'] += int(pnl > 0)
        r['net_pnl'] += pnl
        r['worst_trade'] = pnl if r['worst_trade'] is None else min(r['worst_trade'], pnl)
        free_after = times[j]
    return r


def load_books(path):
    """Read normalized top-of-book CSV; refuse ambiguous or invalid books."""
    grouped = defaultdict(list)
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fields = ('ts_ms', 'instrument', 'bid', 'ask', 'bid_size', 'ask_size')
        if not set(fields).issubset(reader.fieldnames or []):
            raise ValueError(f'expected columns: {", ".join(fields)}')
        for row in reader:
            try:
                b = Book(int(row['ts_ms']), row['instrument'], D(row['bid']), D(row['ask']),
                         int(row['bid_size']), int(row['ask_size']))
                if (b.instrument not in UNIVERSE or not b.bid.is_finite() or not b.ask.is_finite()
                        or not 0 < b.bid < b.ask or min(b.bid_size, b.ask_size) <= 0):
                    raise ValueError('invalid instrument or top of book')
            except (ValueError, TypeError, InvalidOperation) as exc:
                raise ValueError(f'line {reader.line_num}: {exc}') from exc
            grouped[b.instrument].append(b)
    if not grouped:
        raise ValueError('no book rows')
    return {instrument: sorted(rows, key=lambda b: b.ts_ms) for instrument, rows in grouped.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('slide', help='replay the ABC slide cash flows')
    research = sub.add_parser('research', help='compare two signals on normalized CSV')
    research.add_argument('csv')
    research.add_argument('--horizon-ms', type=int, required=True)
    research.add_argument('--tolerance-ms', type=int, required=True)
    research.add_argument('--trigger-bps', type=D, required=True)
    research.add_argument('--cost-bps', type=D, required=True, help='additional cost per side')
    args = parser.parse_args()
    if args.command == 'slide':
        expected, realized = slide_example()
        print(f'ABC: mark at fair value {expected:.2f}; executable slide unwind {realized:.2f} before costs')
        return
    try:
        grouped = load_books(args.csv)
        output = {symbol: {signal: evaluate(rows, signal=signal, horizon_ms=args.horizon_ms,
                                             tolerance_ms=args.tolerance_ms, trigger_bps=args.trigger_bps,
                                             cost_bps=args.cost_bps)
                           for signal in ('momentum', 'microprice')}
                  for symbol, rows in sorted(grouped.items())}
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, default=str, indent=2))


if __name__ == '__main__':
    main()
