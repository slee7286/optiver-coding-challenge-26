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
