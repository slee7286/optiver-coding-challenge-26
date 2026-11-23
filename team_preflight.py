"""Team 011 practice handoff check. Read-only, but connecting takes the team slot."""
import sys
from time import sleep


def main():
    from optibook.synchronous_client import Exchange
    from optibook.historical_data_loader import HistoricalDataLoader
    print('PYTHON', sys.executable, sys.version.split()[0], 'HISTORY', HistoricalDataLoader.__name__, flush=True)
    ex = Exchange()
    ex.connect()
    try:
        assert ex.is_connected(), 'connection not active'
        ids = sorted(ex.get_tradable_instruments())
        print('CONNECTED', ids, flush=True)
        print('POSITIONS', ex.get_positions(), 'PNL', ex.get_pnl(), flush=True)
        for iid in ids:
            b = ex.get_last_price_book(iid)
            print(iid, 'best bid/ask', b.bids[0].price if b and b.bids else None,
                  b.asks[0].price if b and b.asks else None,
                  'own orders', len(ex.get_outstanding_orders(iid)), flush=True)
            sleep(.25)
    finally:
        ex.disconnect()
    print('PREFLIGHT COMPLETE; DISCONNECTED', flush=True)


if __name__ == '__main__':
    main()
