"""Read-only Optibook historical API export; never constructs Exchange."""

import csv
import gzip
import hashlib
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    from optibook.historical_data_loader import HistoricalDataLoader

    root = Path(__file__).resolve().parent
    with gzip.open(root / "orders.csv.gz", "rt", newline="") as f:
        stamp = next(csv.DictReader(f))["exchange_started_at"]
    start = datetime.fromisoformat(stamp).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    loader = HistoricalDataLoader(timeout=45)
    print("HISTORY RANGE UTC", start, end, flush=True)
    dest = root / ("event_history_" + end.strftime("%Y%m%dT%H%MZ") + ".zip")
    temp = dest.with_suffix(".partial")
    if dest.exists() or temp.exists():
        raise SystemExit("snapshot already exists or is running")
    manifest = {
        "from_utc": start.isoformat(),
        "to_utc_exclusive": end.isoformat(),
        "feeds": {},
        "errors": [],
    }
    with zipfile.ZipFile(temp, "x", allowZip64=True) as z:
        for feed in ("trades", "orders", "pricebooks"):
            seen = set()
            items = []
            manifest["feeds"][feed] = items
            begin = start
            while begin < end:
                until = min(begin + timedelta(days=1), end)
                try:
                    paths = getattr(loader, "download_" + feed)(
                        begin, until, show_progress=False
                    )
                    for file in paths:
                        p = Path(file)
                        ident = str(p.resolve())
                        if ident in seen:
                            continue
                        seen.add(ident)
                        key = f"{feed}/{len(items):04d}_{p.name}"
                        z.write(p, key)
                        with p.open("rb") as inp:
                            h = hashlib.file_digest(inp, "sha256").hexdigest()
                        items.append(
                            {"path": key, "bytes": p.stat().st_size, "sha256": h}
                        )
                    print(
                        feed,
                        begin.date(),
                        until.date(),
                        "files so far",
                        len(items),
                        flush=True,
                    )
                except Exception as err:
                    manifest["errors"].append(
                        [feed, begin.isoformat(), until.isoformat(), type(err).__name__]
                    )
                    print("ERROR", feed, type(err).__name__, flush=True)
                begin = until
                time.sleep(2.2)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    temp.rename(dest)
    print(
        "HISTORY EXPORT FINISHED",
        dest.name,
        dest.stat().st_size,
        "errors",
        len(manifest["errors"]),
        flush=True,
    )


if __name__ == "__main__":
    main()
