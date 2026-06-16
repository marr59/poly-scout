#!/usr/bin/env python3
"""(a) Stale-price audit of 3 big-gap clusters; (b) daily gap histogram for suspect pairs."""
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "cross_arb_data.jsonl"

# (a) clusters: name -> (window_start_iso, window_end_iso) (±5 min around cluster)
CLUSTERS = [
    ("nba26-celtics",    "2026-04-28T12:17:00", "2026-04-28T12:29:00"),
    ("demnom28-newsom",  "2026-04-28T11:38:00", "2026-04-28T11:51:00"),
    ("nba26-thunder",    "2026-04-24T12:47:00", "2026-04-24T13:01:00"),
]

# (b) pairs to drill down by day
SUSPECT_PAIRS = {
    "winner28-rubio",
    "demnom28-buttigieg",
    "demnom28-kelly",
    "demnom28-newsom",
    "winner28-aoc",
}


def in_window(ts_iso, start_iso, end_iso):
    # All UTC, simple lexical compare works on ISO-8601
    return start_iso <= ts_iso[:19] <= end_iso


def main():
    t0 = time.time()
    print(f"Reading: {DATA}")

    # (a) per cluster: list of rows
    cluster_rows = {name: [] for name, _, _ in CLUSTERS}

    # (b) per (pair, day) -> list of gaps
    by_pair_day = defaultdict(lambda: defaultdict(list))

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
            ts_iso = row.get("ts_iso") or ""
            if not name or not ts_iso:
                continue

            # (a)
            for cname, ws, we in CLUSTERS:
                if name == cname and in_window(ts_iso, ws, we):
                    cluster_rows[cname].append(row)

            # (b)
            if name in SUSPECT_PAIRS:
                day = ts_iso[:10]
                gap = (row.get("arb") or {}).get("arb_gap")
                if gap is not None and day:
                    by_pair_day[name][day].append(gap)

    # ------------------------------------------------------------------
    # (a) Stale-price audit
    print()
    print("=" * 110)
    print("(a) STALE-PRICE AUDIT — ±5 min around each big-gap cluster")
    print("=" * 110)

    for cname, ws, we in CLUSTERS:
        rows = sorted(cluster_rows[cname], key=lambda r: r.get("ts") or 0)
        print()
        print(f"--- {cname}   window {ws} … {we}   ({len(rows)} rows) ---")
        hdr = (f"{'ts':<27} {'p_yb_bid':>9} {'p_yb_ask':>9} {'p_yb_bsz':>10} "
               f"{'k_yes_bid':>10} {'k_yes_ask':>10} {'arb_gap':>9}")
        print(hdr)
        print("-" * len(hdr))
        # collapse: print only when something changed in (p_bid,p_ask,p_bsz,k_bid,k_ask,gap)
        last_key = None
        run_start = None
        run_count = 0
        last_ts = None

        def emit_run(ts_first, ts_last, count, key):
            if ts_first is None:
                return
            p_bid, p_ask, p_bsz, k_bid, k_ask, gap = key
            if count == 1:
                ts_str = ts_first
            else:
                ts_str = f"{ts_first[11:23]}…{ts_last[11:23]} (x{count})"
            def f(v, w):
                if v is None:
                    return f"{'-':>{w}}"
                if isinstance(v, float):
                    return f"{v:>{w}.4f}"
                return f"{v:>{w}}"
            print(f"{ts_str:<27} {f(p_bid,9)} {f(p_ask,9)} {f(p_bsz,10)} "
                  f"{f(k_bid,10)} {f(k_ask,10)} {f(gap,9)}")

        for r in rows:
            poly = r.get("poly") or {}
            kal = r.get("kalshi") or {}
            yb = poly.get("yes_book") or {}
            ob = kal.get("orderbook") or {}
            arb = r.get("arb") or {}
            key = (yb.get("bid"), yb.get("ask"), yb.get("bid_size"),
                   ob.get("yes_bid"), ob.get("yes_ask"), arb.get("arb_gap"))
            ts = r.get("ts_iso") or ""
            if key == last_key:
                run_count += 1
                last_ts = ts
            else:
                emit_run(run_start, last_ts, run_count, last_key) if last_key is not None else None
                last_key = key
                run_start = ts
                last_ts = ts
                run_count = 1
        emit_run(run_start, last_ts, run_count, last_key) if last_key is not None else None

        # quick verdict heuristic
        gaps = [(r.get("arb") or {}).get("arb_gap") for r in rows]
        gaps = [g for g in gaps if g is not None]
        big = [g for g in gaps if g > 0.05]
        if big:
            # detect stale: count of consecutive rows where (poly bid/ask) AND (kalshi bid/ask) all unchanged during big-gap window
            stale_streaks_poly = 0
            stale_streaks_kal = 0
            prev_p = None
            prev_k = None
            cur_p = 0
            cur_k = 0
            for r in rows:
                arb = r.get("arb") or {}
                gap = arb.get("arb_gap")
                if gap is None or gap <= 0.05:
                    cur_p = cur_k = 0
                    prev_p = prev_k = None
                    continue
                yb = (r.get("poly") or {}).get("yes_book") or {}
                ob = (r.get("kalshi") or {}).get("orderbook") or {}
                p = (yb.get("bid"), yb.get("ask"))
                k = (ob.get("yes_bid"), ob.get("yes_ask"))
                if p == prev_p and p[0] is not None:
                    cur_p += 1
                    stale_streaks_poly = max(stale_streaks_poly, cur_p)
                else:
                    cur_p = 1
                if k == prev_k and k[0] is not None:
                    cur_k += 1
                    stale_streaks_kal = max(stale_streaks_kal, cur_k)
                else:
                    cur_k = 1
                prev_p = p
                prev_k = k
            print(f"  during gap>0.05: longest unchanged poly run = {stale_streaks_poly}, "
                  f"longest unchanged kalshi run = {stale_streaks_kal}")
            verdict = "GLITCH/STALE" if stale_streaks_poly >= 3 or stale_streaks_kal >= 3 else "INCONCLUSIVE"
            print(f"  verdict: {verdict}")

    # ------------------------------------------------------------------
    # (b) Daily histogram for suspect pairs
    print()
    print("=" * 110)
    print("(b) DAILY arb_gap HISTOGRAM — suspect pairs")
    print("=" * 110)
    for pair in sorted(SUSPECT_PAIRS):
        days = by_pair_day.get(pair) or {}
        print()
        print(f"--- {pair} ---")
        if not days:
            print("  (no data)")
            continue
        print(f"{'day':<12} {'N':>6} {'mean':>9} {'median':>9} {'std':>9} "
              f"{'min':>9} {'max':>9}")
        print("-" * 70)
        all_means = []
        for day in sorted(days):
            vals = days[day]
            n = len(vals)
            m = statistics.fmean(vals)
            med = statistics.median(vals)
            sd = statistics.pstdev(vals) if n > 1 else 0.0
            print(f"{day:<12} {n:>6} {m:>+9.4f} {med:>+9.4f} {sd:>9.4f} "
                  f"{min(vals):>+9.4f} {max(vals):>+9.4f}")
            all_means.append(m)
        if len(all_means) > 1:
            drift = max(all_means) - min(all_means)
            print(f"  daily-mean range: {min(all_means):+.4f} … {max(all_means):+.4f}  "
                  f"(drift = {drift:.4f})")

    print()
    print(f"[done in {time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
