#!/usr/bin/env python3
"""kalshi_event.py — dump all markets in a Kalshi event with prices and volumes.
Usage: python3 kalshi_event.py KXPRESPERSON-28
"""
import sys, json, requests

def _f(x):
    try: return float(x) if x not in (None, "") else None
    except: return None

def main():
    if len(sys.argv) < 2:
        print("usage: kalshi_event.py <event_ticker>")
        sys.exit(1)
    ticker = sys.argv[1]
    r = requests.get(
        f"https://api.elections.kalshi.com/trade-api/v2/events/{ticker}",
        params={"with_nested_markets": "true"},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    ev = d.get("event") or {}
    markets = ev.get("markets") or []
    print(f"Event: {ev.get('event_ticker')}  — {ev.get('title')}")
    print(f"Sub: {ev.get('sub_title')}")
    print(f"Markets: {len(markets)}\n")
    rows = []
    for m in markets:
        rows.append({
            "ticker": m.get("ticker"),
            "sub": m.get("yes_sub_title") or m.get("subtitle") or "",
            "yes_bid": _f(m.get("yes_bid_dollars")) or 0,
            "yes_ask": _f(m.get("yes_ask_dollars")) or 0,
            "v24": _f(m.get("volume_24h_fp")) or 0,
            "oi": _f(m.get("open_interest_fp")) or 0,
            "status": m.get("status"),
        })
    rows.sort(key=lambda r: r["v24"], reverse=True)
    print(f"{'Ticker':45s} {'Candidate':35s} {'yes':>12s} {'v24h':>10s} {'OI':>10s}")
    print("-" * 115)
    for r in rows:
        print(f"{r['ticker']:45s} {r['sub'][:35]:35s} "
              f"{r['yes_bid']:.2f}/{r['yes_ask']:.2f}   "
              f"${r['v24']:>8,.0f} ${r['oi']:>8,.0f}")

if __name__ == "__main__":
    main()
