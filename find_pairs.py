#!/usr/bin/env python3
"""find_pairs.py — search Polymarket markets + Kalshi events by keywords.

Polymarket: paginates with offset across all open markets.
Kalshi:     searches /events endpoint (human-readable titles), shows nested markets.

Kalshi API uses *_dollars / *_fp field names (Apr 2026 schema).

Usage:
    python3 find_pairs.py fed
    python3 find_pairs.py bitcoin 150k
    python3 find_pairs.py nba celtics
"""
import sys
import requests

TIMEOUT = 20
TOP_N = 12


def _f(x):
    """Kalshi returns prices/volumes as strings. Coerce to float or return None."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def search_poly(kws):
    out = []
    offset = 0
    scanned = 0
    for _ in range(20):
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"closed": "false", "limit": 500, "offset": offset},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        scanned += len(batch)
        for m in batch:
            q = (m.get("question") or "").lower()
            if all(k in q for k in kws):
                try:
                    vol = float(m.get("volume24hr") or 0)
                except (TypeError, ValueError):
                    vol = 0
                out.append({
                    "slug": m.get("slug"),
                    "question": m.get("question"),
                    "volume_24h": vol,
                    "end": m.get("endDate"),
                })
        if len(batch) < 500:
            break
        offset += 500
    out.sort(key=lambda x: x["volume_24h"], reverse=True)
    return out, scanned


def search_kalshi_events(kws):
    """Search Kalshi EVENTS with nested markets, keep live price fields."""
    out = []
    cursor = ""
    scanned = 0
    for _ in range(20):
        params = {"status": "open", "limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            "https://api.elections.kalshi.com/trade-api/v2/events",
            params=params,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        events = d.get("events", [])
        scanned += len(events)
        for e in events:
            text = ((e.get("title") or "") + " " + (e.get("sub_title") or "")).lower()
            if all(k in text for k in kws):
                out.append(e)
        cursor = d.get("cursor") or ""
        if not cursor:
            break

    def _evol(e):
        tot = 0.0
        for m in e.get("markets") or []:
            v = _f(m.get("volume_24h_fp")) or _f(m.get("volume_fp")) or 0
            tot += v
        return tot

    out.sort(key=_evol, reverse=True)
    return out, scanned


def main():
    if len(sys.argv) < 2:
        print("usage: find_pairs.py <keyword> [keyword2 ...]")
        sys.exit(1)
    kws = [w.lower() for w in sys.argv[1:]]
    kws_str = " ".join(kws)

    print(f"\n================  POLYMARKET  (AND: {kws_str})  ================\n")
    try:
        pm, scanned = search_poly(kws)
        print(f"  scanned {scanned} open markets, matched {len(pm)}\n")
        for m in pm[:TOP_N]:
            print(f"  slug:    {m['slug']}")
            print(f"  q:       {m['question']}")
            print(f"  vol24h:  ${m['volume_24h']:,.0f}   end: {m.get('end')}")
            print()
    except Exception as e:
        print(f"  Polymarket search failed: {e}\n")

    print(f"================  KALSHI EVENTS  (AND: {kws_str})  ================\n")
    try:
        kk, scanned = search_kalshi_events(kws)
        print(f"  scanned {scanned} open events, matched {len(kk)}\n")
        for e in kk[:TOP_N]:
            markets = e.get("markets") or []
            vol_sum = sum(
                (_f(m.get("volume_24h_fp")) or _f(m.get("volume_fp")) or 0)
                for m in markets
            )
            print(f"  event:   {e.get('event_ticker')}")
            print(f"  title:   {e.get('title')}")
            if e.get("sub_title"):
                print(f"  sub:     {e.get('sub_title')}")
            print(f"  markets: {len(markets)}   24h_vol_sum: ${vol_sum:,.0f}")
            # sort inner markets by 24h volume, show top ones
            markets_sorted = sorted(
                markets,
                key=lambda m: _f(m.get("volume_24h_fp")) or 0,
                reverse=True,
            )
            for m in markets_sorted[:6]:
                y_bid = _f(m.get("yes_bid_dollars"))
                y_ask = _f(m.get("yes_ask_dollars"))
                v24 = _f(m.get("volume_24h_fp")) or 0
                sub = (m.get("yes_sub_title") or m.get("subtitle")
                       or m.get("no_sub_title") or "")
                bid_str = f"{y_bid:.2f}" if y_bid is not None else "  - "
                ask_str = f"{y_ask:.2f}" if y_ask is not None else "  - "
                print(f"      - {m.get('ticker'):45s}  "
                      f"yes={bid_str}/{ask_str}  "
                      f"v24=${v24:>8,.0f}  {sub[:40]}")
            if len(markets_sorted) > 6:
                print(f"      ... and {len(markets_sorted) - 6} more markets")
            print()
    except Exception as e:
        print(f"  Kalshi search failed: {e}\n")


if __name__ == "__main__":
    main()
