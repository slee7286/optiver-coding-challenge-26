"""Optibook practice only. Run Python File after exclusive team handoff.
Connecting evicts the previous client; disconnect pulls orders, not positions.
No post-only order type: the book can move between observation and submission.
"""
import math
import os
import threading
import time


class Stop(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Stop(reason)


def quote(x, symbol, stock):
    book = x.get_last_price_book(symbol)
    if not book or book.instrument_id != symbol or not book.bids or not book.asks:
        return None
    bid, ask = book.bids[0], book.asks[0]
    tick, bounds = stock.tick_size, stock.price_limit
    if not math.isfinite(tick) or tick <= 0 or bid.price >= ask.price:
        return None
    for level in (bid, ask):
        p = level.price
        if not (0 < p <= 1_000_000 and level.volume >= 1 and
                math.isclose(p/tick, round(p/tick), rel_tol=0, abs_tol=1e-7)):
            return None
        if bounds and not bounds.min <= p <= bounds.max:
            return None
    return bid.price, ask.price


def run(x, *, sleep=time.sleep, emit=print):
    """One bounded round on an already connected exclusive practice account."""
    universe, symbol = (), None
    owned, fills = {}, {}

    def snapshot():
        require(x.is_connected(), 'connection lost')
        positions = {s: v for s, v in x.get_positions().items() if v != 0}
        orders = {}
        for s in universe:
            ids = list(x.get_outstanding_orders(s))
            if ids:
                orders[s] = ids
        return positions, orders

    def check(active=None):
        pos, orders = snapshot()
        for t in x.poll_new_trades(symbol):
            require(t.instrument_id == symbol and t.order_id in owned and
                    t.side == owned[t.order_id] and t.volume == 1 and
                    t.order_id not in fills and math.isfinite(t.price), 'unexpected private trade')
            fills[t.order_id] = t.price
            emit(f'fill id={t.order_id} {t.side} 1 @ {t.price:g}')
        net = sum(1 if owned[i] == 'bid' else -1 for i in fills)
        require(pos == ({symbol: net} if net else {}), f'position/fill mismatch: {pos}')
        require(not orders or (active is not None and orders == {symbol: [active]} and active not in fills),
                f'unexpected outstanding: {orders}')
        return bool(orders)

    def leg(side, kind, min_exit=None):
        check()
        prices = quote(x, symbol, stock)
        if not prices and min_exit is not None:
            return None
        if not prices:
            raise Stop('no valid quote')
        price = prices[1] if side == 'ask' and kind == 'limit' else prices[0]
        if min_exit is not None and price <= min_exit:
            return None
        # Reconcile after reading the book, including late fills after a cancel.
        check()
        require(len(fills) == (0 if side == 'bid' else 1), 'inventory changed before submit')
        require(x.is_connected(), 'connection lost')
        emit(f'submit {symbol} {side} 1 @ {price:g} {kind}')
        r = x.insert_order(symbol, price=price, volume=1, side=side, order_type=kind)
        require(r.success is True, f'insert not accepted: {getattr(r, "error_reason", None)}')
        oid = r.order_id
        require(type(oid) is int and oid not in owned, 'missing or reused order id')
        owned[oid] = side
        emit(f'ack id={oid}')
        for _ in range(1 if kind == 'ioc' else 4):
            sleep(.5)
            if not check(oid):
                require(oid in fills or kind == 'ioc', f'order {oid} disappeared without confirmed fill')
                return oid
        require(kind == 'limit' and x.is_connected(), 'unexpected resting IOC or disconnect')
        r = x.delete_order(symbol, order_id=oid)
        require(r.success is True, f'cancel not confirmed id={oid}')
        emit(f'cancel ack id={oid}')
        sleep(.25)
        check()
        return oid

    try:
        universe = tuple(sorted(x.get_instruments()))
        require(universe, 'empty instrument universe')
        positions, orders = snapshot()
        require(not positions and not orders, f'preflight not clean: positions={positions} outstanding={orders}')
        tradable = x.get_tradable_instruments()
        for s, stock in sorted(tradable.items()):
            if s in universe and getattr(stock.instrument_type, 'name', None) == 'STOCK' and quote(x, s, stock):
                symbol = s
                break
        if symbol is None:
            raise Stop('no eligible stock with a two-sided book')
        entry = leg('bid', 'limit')
        if entry in fills:
            leg('ask', 'limit', min_exit=fills[entry])
            if len(fills) == 1:
                leg('ask', 'ioc')
        check()
        require(len(fills) != 1, 'IOC did not flatten; no retry')
        emit(f'complete {symbol} fills={len(fills)} positions={{}} outstanding={{}}')
        return True
    except (Exception, KeyboardInterrupt) as exc:
        emit(f'STOP {exc if isinstance(exc, Stop) else type(exc).__name__}; no further orders')
        try:
            require(universe, 'unknown universe')
            positions, orders = snapshot()
            emit(f'last observed positions={positions} outstanding={orders}; verify in dashboard')
        except (Exception, KeyboardInterrupt):
            emit('positions/outstanding UNKNOWN; inspect dashboard')
        return False


def main():
    # Independent of a stalled synchronous API call; never reconnect on timeout.
    def timeout():
        print('TIMEOUT: positions/outstanding UNKNOWN; inspect dashboard; do not rerun', flush=True)
        os._exit(2)

    timer = threading.Timer(30, timeout)
    timer.daemon = True
    timer.start()
    try:
        from optibook.synchronous_client import Exchange
        x = Exchange()
        print('PRACTICE ONLY | exclusive team handoff required | one round | 30s cap', flush=True)
        x.connect()
        try:
            return 0 if run(x, emit=lambda s: print(s, flush=True)) else 1
        finally:
            x.disconnect()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'STOP {type(exc).__name__}: session state unverified; inspect dashboard; do not rerun', flush=True)
        return 2
    finally:
        timer.cancel()


if __name__ == '__main__':
    raise SystemExit(main())
