#!/usr/bin/env python3
"""Lightweight interim analysis over cross_arb_data.jsonl. Streams; stdlib only."""
import heapq
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "cross_arb_data.jsonl"
DEAD_THRESHOLD_SEC = 3600  # 1 hour
TOP_N_EVENTS = 5


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def main():
    t0 = time.time()
    print(f"Reading: {DATA}")
    print(f"File size: {DATA.stat().st_size / 1024 / 1024:.1f} MB")

    # Block 1: dataset stats
    total_rows = 0
    parse_errors = 0
    first_ts = None
    last_ts = None
    first_iso = None
    last_iso = None

    # Block 2: mortality — per pair track last_seen_any, last_seen_with_books, first_seen
    last_seen_any = {}  # name -> ts
    last_seen_iso_any = {}
    last_seen_with_books = {}  # name -> ts (both poly.yes_book and kalshi.orderbook present)
    last_seen_with_books_iso = {}
    first_seen_with_books_lost_ts = {}  # name -> ts of first row where books became None
    first_seen_with_books_lost_iso = {}
    pair_row_counts = defaultdict(int)

    # Block 3: arb_gap stats per pair
    gaps_by_pair = defaultdict(list)  # name -> [arb_gap, ...] (non-None only)
    pos_count_by_pair = defaultdict(int)

    # Block 4: big-gap episodes
    gt05_count_by_pair = defaultdict(int)
    gt10_count_by_pair = defaultdict(int)
    # global top-5 events overall by gap (min-heap of (gap, ts_iso, name))
    top_events_global = []  # heap
    # also per-pair top-5 to show breakdown? Spec says "5 самых больших по gap событий" — assume global. But also keep per-pair for context.
    top_events_by_pair = defaultdict(list)  # name -> heap of (gap, ts_iso) size <=5

    with DATA.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            total_rows += 1
            ts = row.get("ts")
            ts_iso = row.get("ts_iso")
            name = row.get("name")
            if ts is None or name is None:
                continue

            if first_ts is None:
                first_ts = ts
                first_iso = ts_iso
            last_ts = ts
            last_iso = ts_iso

            pair_row_counts[name] += 1
            last_seen_any[name] = ts
            last_seen_iso_any[name] = ts_iso

            poly = row.get("poly") or {}
            kal = row.get("kalshi") or {}
            poly_yes = poly.get("yes_book")
            kal_ob = kal.get("orderbook")
            books_ok = poly_yes is not None and kal_ob is not None

            if books_ok:
                last_seen_with_books[name] = ts
                last_seen_with_books_iso[name] = ts_iso
            else:
                # if pair had books before but now lost — record first loss
                if name in last_seen_with_books and name not in first_seen_with_books_lost_ts:
                    first_seen_with_books_lost_ts[name] = ts
                    first_seen_with_books_lost_iso[name] = ts_iso

            arb = row.get("arb") or {}
            gap = arb.get("arb_gap")
            if gap is not None:
                gaps_by_pair[name].append(gap)
                if gap > 0:
                    pos_count_by_pair[name] += 1
                if gap > 0.05:
                    gt05_count_by_pair[name] += 1
                if gap > 0.10:
                    gt10_count_by_pair[name] += 1

                event = (gap, ts_iso, name)
                if len(top_events_global) < TOP_N_EVENTS:
                    heapq.heappush(top_events_global, event)
                elif gap > top_events_global[0][0]:
                    heapq.heapreplace(top_events_global, event)

                pheap = top_events_by_pair[name]
                pevent = (gap, ts_iso)
                if len(pheap) < TOP_N_EVENTS:
                    heapq.heappush(pheap, pevent)
                elif gap > pheap[0][0]:
                    heapq.heapreplace(pheap, pevent)

    # ------------------------------------------------------------------
    # Block 1
    print()
    print("=" * 92)
    print("BLOCK 1 — Dataset size & time coverage")
    print("=" * 92)
    print(f"  total rows:       {total_rows:,}")
    print(f"  parse errors:     {parse_errors}")
    print(f"  unique pairs:     {len(pair_row_counts)}")
    if first_ts and last_ts:
        span_sec = last_ts - first_ts
        print(f"  first ts:         {first_iso}  ({first_ts:.3f})")
        print(f"  last  ts:         {last_iso}  ({last_ts:.3f})")
        print(f"  span:             {span_sec/3600:.2f} h "
              f"({span_sec/86400:.2f} d)")

    # ------------------------------------------------------------------
    # Block 2 — mortality
    print()
    print("=" * 92)
    print("BLOCK 2 — Mortality check (books-lost gap > 1h)")
    print("=" * 92)
    print(f"{'pair':<26} {'rows':>7}  {'last_books_iso':<32} "
          f"{'last_any_iso':<32} {'gap_h':>7}")
    print("-" * 110)
    dead_pairs = []
    alive_with_loss = []
    for name in sorted(pair_row_counts.keys()):
        rows = pair_row_counts[name]
        lb_ts = last_seen_with_books.get(name)
        lb_iso = last_seen_with_books_iso.get(name) or "NEVER"
        la_ts = last_seen_any.get(name)
        la_iso = last_seen_iso_any.get(name)
        if lb_ts is None:
            gap_h = None
            status = " NEVER-BOOKS"
        else:
            gap_h = (la_ts - lb_ts) / 3600.0
            status = " DEAD" if gap_h > (DEAD_THRESHOLD_SEC / 3600.0) else ""
        gap_str = f"{gap_h:.2f}" if gap_h is not None else "-"
        print(f"{name:<26} {rows:>7}  {lb_iso[:32]:<32} {la_iso[:32]:<32} "
              f"{gap_str:>7}{status}")
        if gap_h is not None and gap_h > (DEAD_THRESHOLD_SEC / 3600.0):
            dead_pairs.append((name, lb_iso, la_iso,
                              first_seen_with_books_lost_iso.get(name)))
        elif lb_ts is None:
            dead_pairs.append((name, "NEVER", la_iso, None))
        elif name in first_seen_with_books_lost_ts:
            alive_with_loss.append((name, first_seen_with_books_lost_iso[name]))

    if dead_pairs:
        print()
        print("DEAD pairs detail:")
        for name, lb_iso, la_iso, lost_iso in dead_pairs:
            print(f"  - {name}")
            print(f"      last row with books:       {lb_iso}")
            print(f"      first row after books lost:{lost_iso}")
            print(f"      last row in dataset:       {la_iso}")

    # ------------------------------------------------------------------
    # Block 3 — arb_gap stats per pair
    print()
    print("=" * 92)
    print("BLOCK 3 — arb_gap stats per pair (full sample)")
    print("=" * 92)
    print(f"{'pair':<26} {'N_total':>8} {'N_gap':>8} {'pos%':>7} "
          f"{'mean':>9} {'median':>9} {'max':>9} {'p95':>9}")
    print("-" * 92)
    summary_rows = []
    for name in sorted(pair_row_counts.keys()):
        n_total = pair_row_counts[name]
        gaps = gaps_by_pair.get(name) or []
        n_gap = len(gaps)
        if n_gap == 0:
            print(f"{name:<26} {n_total:>8} {0:>8} {'-':>7} "
                  f"{'-':>9} {'-':>9} {'-':>9} {'-':>9}")
            summary_rows.append((name, n_total, 0, None, None, None, None, None))
            continue
        pos_pct = 100.0 * pos_count_by_pair[name] / n_gap
        mean_g = statistics.fmean(gaps)
        sgaps = sorted(gaps)
        median_g = sgaps[n_gap // 2] if n_gap % 2 == 1 else \
            (sgaps[n_gap // 2 - 1] + sgaps[n_gap // 2]) / 2.0
        max_g = sgaps[-1]
        p95_g = percentile(sgaps, 0.95)
        print(f"{name:<26} {n_total:>8} {n_gap:>8} {pos_pct:>6.2f}% "
              f"{mean_g:>+9.4f} {median_g:>+9.4f} {max_g:>+9.4f} {p95_g:>+9.4f}")
        summary_rows.append((name, n_total, n_gap, pos_pct, mean_g, median_g,
                             max_g, p95_g))

    # ------------------------------------------------------------------
    # Block 4 — big-gap episodes
    print()
    print("=" * 92)
    print("BLOCK 4 — Big-gap episodes (gap > 0.05 / > 0.10)")
    print("=" * 92)
    print(f"{'pair':<26} {'N>0.05':>8} {'N>0.10':>8}")
    print("-" * 46)
    any_big = False
    for name in sorted(pair_row_counts.keys()):
        c5 = gt05_count_by_pair.get(name, 0)
        c10 = gt10_count_by_pair.get(name, 0)
        if c5 == 0 and c10 == 0:
            continue
        any_big = True
        print(f"{name:<26} {c5:>8} {c10:>8}")
    if not any_big:
        print("  (no pair had any gap > 0.05)")

    print()
    print("Top-5 largest gap events (global):")
    top_sorted = sorted(top_events_global, key=lambda x: -x[0])
    if not top_sorted:
        print("  (none)")
    else:
        for gap, ts_iso, name in top_sorted:
            print(f"  gap={gap:+.4f}   {ts_iso}   {name}")

    # per-pair top-5 (only for pairs that had any gap > 0.05)
    print()
    print("Per-pair top-5 events (only pairs with any gap > 0.05):")
    for name in sorted(top_events_by_pair.keys()):
        if gt05_count_by_pair.get(name, 0) == 0:
            continue
        events = sorted(top_events_by_pair[name], key=lambda x: -x[0])
        print(f"  {name}:")
        for gap, ts_iso in events:
            print(f"    gap={gap:+.4f}   {ts_iso}")

    print()
    print(f"[done in {time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
