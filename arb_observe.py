"""Read-only same-instrument price-cross monitor for hosted Optibook."""

import argparse
from decimal import Decimal
from math import isfinite
from time import sleep


def observe(exchange, scans, *, atomic_submit=None, max_cost=None):
    """Optional trusted adapter: both price-limited legs fill together or neither.
    max_cost bounds all fees on the one-unit package. Never emulate with two IOCs.
    Stop after one submission attempt; returning is not confirmation of a fill.
    """
    if atomic_submit is not None:
        if (
            not callable(atomic_submit)
            or not isinstance(max_cost, Decimal)
            or not max_cost.is_finite()
            or max_cost < 0
        ):
            raise ValueError(
                "atomic adapter requires a finite nonnegative Decimal cost bound"
            )
    if scans < 1:
        raise ValueError("scans must be positive")
    exchange.connect()
    try:
        symbols = sorted(exchange.get_tradable_instruments())
        print(
            "Connected;",
            "observation only:"
            if atomic_submit is None
            else "one atomic attempt enabled:",
            ", ".join(symbols),
            flush=True,
        )
        crosses = 0
        for i in range(scans):
            books = 0
            for symbol in symbols:
                book = exchange.get_last_price_book(symbol)
                if book:
                    bids = [
                        l.price
                        for l in book.bids
                        if l.volume >= 1 and isfinite(l.price) and l.price > 0
                    ]
                    asks = [
                        l.price
                        for l in book.asks
                        if l.volume >= 1 and isfinite(l.price) and l.price > 0
                    ]
                    if bids and asks:
                        books += 1
                        if max(bids) > min(asks):
                            crosses += 1
                            bid, ask = max(bids), min(asks)
                            if (
                                atomic_submit is not None
                                and max_cost is not None
                                and Decimal(str(bid)) - Decimal(str(ask)) > max_cost
                            ):
                                print(
                                    symbol, "attempting one atomic package", flush=True
                                )
                                atomic_submit(
                                    symbol=symbol, quantity=1, max_buy=ask, min_sell=bid
                                )
                                return crosses
                            print(
                                symbol,
                                "displayed cross; no approved package:",
                                bid,
                                ask,
                                flush=True,
                            )
                sleep(0.2)
            print("scan", i + 1, "two-sided books", books, "orders sent 0", flush=True)
        return crosses
    finally:
        exchange.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scans", type=int, default=10)
    a = p.parse_args()
    from optibook.synchronous_client import Exchange

    observe(Exchange(), a.scans)
