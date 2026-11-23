"""Verify the history export, stream CSV.gz partitions into research feeds, inventory gaps."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path


def normalize(zpath, out):
    out.mkdir(parents=True, exist_ok=False)
    report = {}
    with zipfile.ZipFile(zpath) as z:
        m = json.loads(z.read("manifest.json"))
        report.update(
            requested=[m["from_utc"], m["to_utc_exclusive"]],
            errors=m["errors"],
            feeds={},
        )
        for feed, items in m["feeds"].items():
            ranges = []
            count = 0
            symbols = Counter()
            w = None
            with gzip.open(
                out / (feed + ".csv.gz"), "wt", newline="", encoding="utf8"
            ) as f:
                for item in sorted(items, key=lambda x: x["path"]):
                    with z.open(item["path"]) as raw:
                        assert (
                            hashlib.file_digest(raw, "sha256").hexdigest()
                            == item["sha256"]
                        ), item["path"]
                    with z.open(item["path"]) as raw:
                        prefix = raw.read(2)
                        raw.seek(0)
                        with io.TextIOWrapper(
                            gzip.GzipFile(fileobj=raw)
                            if prefix == b"\x1f\x8b"
                            else raw,
                            encoding="utf-8-sig",
                            newline="",
                        ) as src:
                            reader = csv.DictReader(src)
                            fields = reader.fieldnames
                            if not fields or "timestamp_ns" not in fields:
                                raise ValueError((item["path"], fields))
                            if w is None:
                                w = csv.DictWriter(f, fieldnames=fields)
                                w.writeheader()
                            if set(fields) != set(w.fieldnames):
                                raise ValueError("inconsistent schema")
                            lo = hi = None
                            n = 0
                            for r in reader:
                                if r["timestamp_ns"] == "timestamp_ns":
                                    continue
                                ts = int(r["timestamp_ns"])
                                lo = ts if lo is None else min(lo, ts)
                                hi = ts if hi is None else max(hi, ts)
                                symbols[r["instrument_id"]] += 1
                                n += 1
                                w.writerow(r)
                            count += n
                            if n:
                                ranges.append((lo, hi, n))
            ranges.sort()
            report["feeds"][feed] = dict(
                files=len(items),
                rows=count,
                instruments=dict(symbols),
                first_ns=ranges[0][0] if ranges else None,
                last_ns=max((r[1] for r in ranges), default=None),
                gaps_over_120s=[
                    (a[1], b[0])
                    for a, b in zip(ranges, ranges[1:])
                    if b[0] - a[1] > 120e9
                ],
                partition_ranges=ranges,
            )
            print(
                feed,
                "files",
                len(items),
                "rows",
                count,
                "symbols",
                len(symbols),
                flush=True,
            )
    (out / "coverage.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("archive", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    normalize(a.archive, a.out)
