#!/usr/bin/env python3
"""Diagnostic checks over cross_arb_data.jsonl. Read-only, stdlib only."""
import json
import statistics
from collections import defaultdict, deque
from pathlib import Path

DATA = Path(__file__).parent / "cross_arb_data.jsonl"

REPORT1_PAIRS = [
    "demnom28-buttigieg",
    "demnom28-kelly",
    "demnom28-newsom",
    "winner28-aoc",
    "winner28-rubio",
]
REPORT2_PAIR = "nba26-thunder"
REPORT3_PAIRS = ["col26-fajardo", "nba26-76ers"]


def fmt(x, w=10):
    if x is None:
        return f"{'-':>{w}}"
    if isinstance(x, float):
        return f"{x:>{w}.4f}"
    return f"{str(x):>{w}}"


def med_max(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, 0
    return statistics.median(vals), max(vals), len(vals)


def pass1():
    """Single pass over the file, collecting what each report needs."""
    last100 = {p: deque(maxlen=100) for p in REPORT1_PAIRS}
    thunder_rows = []
    last_row = {p: None for p in REPORT3_PAIRS}

    with DATA.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("name")
            if name in last100:
                last100[name].append(row)
            if name == REPORT2_PAIR:
                thunder_rows.append(row)
            if name in last_row:
                last_row[name] = row
    return last100, thunder_rows, last_row


def report1(last100):
    print("=" * 92)
    print("REPORT 1 — Top-of-book sizes (last 100 rows per pair, pos% = 100% pairs)")
    print("=" * 92)
    print(f"{'pair':<22} {'N':>4} | {'PolyYesAsk med':>15} {'max':>10} | "
          f"{'PolyNoAsk med':>14} {'max':>10} | {'KalYesAsk med':>14} {'max':>10} | "
          f"{'KalNoAsk med':>14} {'max':>10}")
    print("-" * 92)
    for pair in REPORT1_PAIRS:
        rows = list(last100[pair])
        if not rows:
            print(f"{pair:<22} {'0':>4} | (no data)")
            continue
        py_ask = [(r.get("poly", {}).get("yes_book") or {}).get("ask_size") for r in rows]
        pn_ask = [(r.get("poly", {}).get("no_book") or {}).get("ask_size") for r in rows]
        ky_ask = [(r.get("kalshi", {}).get("orderbook") or {}).get("yes_ask_size") for r in rows]
        kn_ask = [(r.get("kalshi", {}).get("orderbook") or {}).get("no_ask_size") for r in rows]

        py_med, py_max, _ = med_max(py_ask)
        pn_med, pn_max, _ = med_max(pn_ask)
        ky_med, ky_max, _ = med_max(ky_ask)
        kn_med, kn_max, _ = med_max(kn_ask)
        print(f"{pair:<22} {len(rows):>4} | {fmt(py_med, 15)} {fmt(py_max)} | "
              f"{fmt(pn_med, 14)} {fmt(pn_max)} | {fmt(ky_med, 14)} {fmt(ky_max)} | "
              f"{fmt(kn_med, 14)} {fmt(kn_max)}")
    print()


def report2(rows):
    print("=" * 92)
    print(f"REPORT 2 — gap distribution per day for {REPORT2_PAIR}")
    print("=" * 92)
    by_day = defaultdict(list)
    for r in rows:
        ts = r.get("ts_iso") or ""
        day = ts[:10]
        gap = (r.get("arb") or {}).get("arb_gap")
        if gap is None or not day:
            continue
        by_day[day].append(gap)

    print(f"{'day':<12} {'N':>6} {'min':>8} {'median':>8} {'max':>8} "
          f"{'>0.03':>8} {'>0.05':>8}")
    print("-" * 64)
    total_n = 0
    total_05 = 0
    total_03 = 0
    for day in sorted(by_day):
        vals = by_day[day]
        n = len(vals)
        gt05 = sum(1 for g in vals if g > 0.05)
        gt03 = sum(1 for g in vals if g > 0.03)
        total_n += n
        total_05 += gt05
        total_03 += gt03
        print(f"{day:<12} {n:>6} {min(vals):>8.4f} {statistics.median(vals):>8.4f} "
              f"{max(vals):>8.4f} {gt03:>8} {gt05:>8}")
    print("-" * 64)
    print(f"{'TOTAL':<12} {total_n:>6} {'':>8} {'':>8} {'':>8} {total_03:>8} {total_05:>8}")
    print()


def report3(last_row):
    print("=" * 92)
    print("REPORT 3 — raw last-row diagnostic for pos%=0 pairs")
    print("=" * 92)
    for pair in REPORT3_PAIRS:
        row = last_row.get(pair)
        print(f"\n--- {pair} ---")
        if row is None:
            print("  (no rows in file)")
            continue
        print(f"  ts_iso:        {row.get('ts_iso')}")
        print(f"  poly_slug:     {row.get('poly_slug')}")
        print(f"  kalshi_ticker: {row.get('kalshi_ticker')}")
        poly = row.get("poly") or {}
        kal = row.get("kalshi") or {}
        meta = kal.get("meta") or {}
        ob = kal.get("orderbook") or {}
        arb = row.get("arb") or {}

        print(f"  poly.yes_book: {json.dumps(poly.get('yes_book'), ensure_ascii=False)}")
        print(f"  poly.no_book:  {json.dumps(poly.get('no_book'), ensure_ascii=False)}")
        print(f"  kalshi.orderbook: {json.dumps(ob, ensure_ascii=False)}")
        print(f"  kalshi.meta.volume_24h: {meta.get('volume_24h')}")
        print(f"  kalshi.meta.status:     {meta.get('status')}")
        print(f"  arb: {json.dumps(arb, ensure_ascii=False)}")
    print()


def main():
    print(f"Reading: {DATA}")
    last100, thunder_rows, last_row = pass1()
    print(f"thunder rows scanned: {len(thunder_rows)}\n")
    report1(last100)
    report2(thunder_rows)
    report3(last_row)


if __name__ == "__main__":
    main()
