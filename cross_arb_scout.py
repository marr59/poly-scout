#!/usr/bin/env python3
"""
cross_arb_scout.py v1.1 — read-only cross-platform arbitrage scout
Polymarket (Gamma + CLOB) <-> Kalshi (public markets + orderbook API)

Tracks bid/ask on pre-configured market pairs, logs to JSONL.
Runs indefinitely. Clean shutdown on SIGINT/SIGTERM.

Kalshi API (Apr 2026 schema):
  /markets/{ticker}            — metadata + current top-of-book (yes_bid_dollars etc.)
  /markets/{ticker}/orderbook  — full depth (orderbook_fp.yes_dollars / no_dollars)

Config:  ~/poly_scout/market_pairs.json   (list of pair objects)
Output:  ~/poly_scout/cross_arb_data.jsonl
Log:     ~/poly_scout/cross_arb_scout.log

N<30 rule: do not draw conclusions until >= 30 independent arb windows observed.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# ==========================================================================
# Config
# ==========================================================================

HOME = Path.home()
SCOUT_DIR = HOME / "poly_scout"
SCOUT_DIR.mkdir(exist_ok=True)

LOG_FILE = SCOUT_DIR / "cross_arb_scout.log"
DATA_FILE = SCOUT_DIR / "cross_arb_data.jsonl"
PAIRS_FILE = SCOUT_DIR / "market_pairs.json"

POLL_INTERVAL_SEC = 30
HTTP_TIMEOUT = 10

POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE = "https://clob.polymarket.com"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

USER_AGENT = "cross_arb_scout/1.1 (read-only research)"

# ==========================================================================
# Logging
# ==========================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cross_arb")

# ==========================================================================
# Shutdown handling
# ==========================================================================

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    log.info(f"signal {signum} received, shutting down after current cycle")
    _stop = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ==========================================================================
# HTTP session
# ==========================================================================

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


def _get_json(url: str, params: Optional[dict] = None) -> Optional[Any]:
    try:
        r = session.get(url, params=params, timeout=HTTP_TIMEOUT)
        if r.status_code == 429:
            log.warning(f"429 from {url}, backing off")
            time.sleep(5)
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log.warning(f"GET failed {url}: {e}")
        return None
    except ValueError as e:
        log.warning(f"JSON decode failed {url}: {e}")
        return None


def _f(x):
    """Coerce to float, handling Kalshi's string-encoded numbers."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ==========================================================================
# Polymarket fetchers
# ==========================================================================


def _parse_maybe_json(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return val
    return val


def fetch_poly_market_meta(slug: str) -> Optional[dict]:
    data = _get_json(f"{POLY_GAMMA_BASE}/markets", params={"slug": slug})
    if not data:
        return None
    market = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    if not market:
        return None

    clob_token_ids = _parse_maybe_json(market.get("clobTokenIds"))
    outcomes = _parse_maybe_json(market.get("outcomes"))

    if not isinstance(clob_token_ids, list) or len(clob_token_ids) < 2:
        return None

    return {
        "slug": slug,
        "question": market.get("question"),
        "closed": bool(market.get("closed", False)),
        "active": bool(market.get("active", True)),
        "end_date": market.get("endDate"),
        "yes_token_id": clob_token_ids[0],
        "no_token_id": clob_token_ids[1],
        "outcomes": outcomes,
        "volume_24h": market.get("volume24hr"),
        "liquidity": market.get("liquidity"),
    }


def fetch_poly_book(token_id: str) -> Optional[dict]:
    data = _get_json(f"{POLY_CLOB_BASE}/book", params={"token_id": token_id})
    if not data:
        return None

    bids = data.get("bids") or []
    asks = data.get("asks") or []

    def _top(levels, reverse: bool):
        parsed = []
        for lvl in levels:
            try:
                p = float(lvl.get("price"))
                s = float(lvl.get("size"))
                parsed.append((p, s))
            except (TypeError, ValueError):
                continue
        if not parsed:
            return None, None
        parsed.sort(key=lambda x: x[0], reverse=reverse)
        return parsed[0]

    best_bid_px, best_bid_sz = _top(bids, reverse=True)
    best_ask_px, best_ask_sz = _top(asks, reverse=False)

    return {
        "bid": best_bid_px,
        "bid_size": best_bid_sz,
        "ask": best_ask_px,
        "ask_size": best_ask_sz,
    }


# ==========================================================================
# Kalshi fetchers
# ==========================================================================


def fetch_kalshi_market(ticker: str) -> Optional[dict]:
    """Kalshi /markets/{ticker}. Schema as of Apr 2026 uses *_dollars strings."""
    data = _get_json(f"{KALSHI_BASE}/markets/{ticker}")
    if not data:
        return None
    m = data.get("market") if isinstance(data, dict) else None
    if not m:
        return None

    return {
        "ticker": ticker,
        "title": m.get("title"),
        "subtitle": m.get("subtitle") or m.get("yes_sub_title") or m.get("no_sub_title"),
        "status": m.get("status"),
        "close_time": m.get("close_time"),
        "yes_bid": _f(m.get("yes_bid_dollars")),
        "yes_ask": _f(m.get("yes_ask_dollars")),
        "no_bid": _f(m.get("no_bid_dollars")),
        "no_ask": _f(m.get("no_ask_dollars")),
        "last_price": _f(m.get("last_price_dollars")),
        "volume_24h": _f(m.get("volume_24h_fp")),
        "volume_total": _f(m.get("volume_fp")),
        "open_interest": _f(m.get("open_interest_fp")),
        "liquidity": _f(m.get("liquidity_dollars")),
    }


def fetch_kalshi_orderbook(ticker: str) -> Optional[dict]:
    """Kalshi /markets/{ticker}/orderbook.

    Both sides are BIDS (offers to buy):
      yes_dollars: bids to buy YES, array of [price_str, size_str]
      no_dollars:  bids to buy NO,  array of [price_str, size_str]

    best_yes_bid = max price in yes_dollars
    best_no_bid  = max price in no_dollars
    best_yes_ask = 1 - best_no_bid   (the tightest price you could pay for YES)
    best_no_ask  = 1 - best_yes_bid
    """
    data = _get_json(f"{KALSHI_BASE}/markets/{ticker}/orderbook")
    if not data:
        return None
    ob = data.get("orderbook_fp") or data.get("orderbook") or {}
    yes_levels = ob.get("yes_dollars") or ob.get("yes") or []
    no_levels = ob.get("no_dollars") or ob.get("no") or []

    def _best(levels):
        best_px = None
        best_sz = None
        for lvl in levels:
            try:
                p = float(lvl[0])
                s = float(lvl[1])
            except (TypeError, ValueError, IndexError):
                continue
            if best_px is None or p > best_px:
                best_px = p
                best_sz = s
        return best_px, best_sz

    best_yes_bid, sz_yes_bid = _best(yes_levels)
    best_no_bid, sz_no_bid = _best(no_levels)

    yes_ask = round(1.0 - best_no_bid, 4) if best_no_bid is not None else None
    no_ask = round(1.0 - best_yes_bid, 4) if best_yes_bid is not None else None

    return {
        "yes_bid": best_yes_bid,
        "yes_bid_size": sz_yes_bid,
        "yes_ask": yes_ask,
        "yes_ask_size": sz_no_bid,
        "no_bid": best_no_bid,
        "no_bid_size": sz_no_bid,
        "no_ask": no_ask,
        "no_ask_size": sz_yes_bid,
        "yes_depth_levels": len(yes_levels),
        "no_depth_levels": len(no_levels),
    }


# ==========================================================================
# Arbitrage math
# ==========================================================================


def compute_arb(poly: dict, kalshi_meta: dict, kalshi_ob: Optional[dict]) -> dict:
    """Cost to cover both outcomes. <1.0 = gross arb BEFORE fees/slippage.

    Direction A: poly_yes_ask + kalshi_no_ask
    Direction B: poly_no_ask  + kalshi_yes_ask
    Prefer orderbook-derived bid/ask; fall back to metadata snapshot.
    """
    poly_yes = poly.get("yes_book") or {}
    poly_no = poly.get("no_book") or {}

    poly_yes_ask = poly_yes.get("ask")
    poly_no_ask = poly_no.get("ask")

    if kalshi_ob:
        k_yes_ask = kalshi_ob.get("yes_ask")
        k_no_ask = kalshi_ob.get("no_ask")
    else:
        k_yes_ask = (kalshi_meta or {}).get("yes_ask")
        k_no_ask = (kalshi_meta or {}).get("no_ask")

    dir_a = None
    if poly_yes_ask is not None and k_no_ask is not None:
        dir_a = round(poly_yes_ask + k_no_ask, 6)

    dir_b = None
    if poly_no_ask is not None and k_yes_ask is not None:
        dir_b = round(poly_no_ask + k_yes_ask, 6)

    best = None
    for label, v in (("poly_yes + kalshi_no", dir_a), ("poly_no + kalshi_yes", dir_b)):
        if v is None:
            continue
        if best is None or v < best[1]:
            best = (label, v)

    return {
        "cost_poly_yes_plus_kalshi_no": dir_a,
        "cost_poly_no_plus_kalshi_yes": dir_b,
        "best_direction": best[0] if best else None,
        "best_cost": best[1] if best else None,
        "arb_gap": round(1.0 - best[1], 6) if best else None,
    }


# ==========================================================================
# Config loading
# ==========================================================================

EXAMPLE_PAIRS = [
    {
        "name": "EXAMPLE-replace-me",
        "poly_slug": "polymarket-slug-here",
        "kalshi_ticker": "KALSHI-TICKER-HERE",
        "note": "Check resolution criteria match before trusting spread as real arb",
    }
]


def load_pairs() -> list[dict]:
    if not PAIRS_FILE.exists():
        PAIRS_FILE.write_text(json.dumps(EXAMPLE_PAIRS, indent=2, ensure_ascii=False))
        log.warning(f"wrote example {PAIRS_FILE}; edit it with real pairs and restart")
        return []

    try:
        pairs = json.loads(PAIRS_FILE.read_text())
    except Exception as e:
        log.error(f"failed to parse {PAIRS_FILE}: {e}")
        return []

    if not isinstance(pairs, list):
        log.error(f"{PAIRS_FILE} must be a JSON array")
        return []

    valid = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        slug = p.get("poly_slug", "")
        ticker = p.get("kalshi_ticker", "")
        if not slug or not ticker:
            continue
        if "replace" in slug.lower() or "here" in slug.lower():
            continue
        if "replace" in ticker.lower() or "here" in ticker.lower():
            continue
        valid.append(p)
    return valid


# ==========================================================================
# Main poll cycle
# ==========================================================================


def utc_now():
    now = datetime.now(timezone.utc)
    return now.isoformat(), now.timestamp()


def snapshot_pair(pair: dict) -> dict:
    ts_iso, ts = utc_now()

    meta = fetch_poly_market_meta(pair["poly_slug"])
    poly_yes_book = None
    poly_no_book = None
    if meta:
        poly_yes_book = fetch_poly_book(meta["yes_token_id"])
        poly_no_book = fetch_poly_book(meta["no_token_id"])

    kalshi_meta = fetch_kalshi_market(pair["kalshi_ticker"])
    kalshi_ob = fetch_kalshi_orderbook(pair["kalshi_ticker"])

    row = {
        "ts_iso": ts_iso,
        "ts": ts,
        "name": pair.get("name"),
        "poly_slug": pair["poly_slug"],
        "kalshi_ticker": pair["kalshi_ticker"],
        "poly": {
            "question": meta.get("question") if meta else None,
            "closed": meta.get("closed") if meta else None,
            "active": meta.get("active") if meta else None,
            "volume_24h": meta.get("volume_24h") if meta else None,
            "liquidity": meta.get("liquidity") if meta else None,
            "yes_book": poly_yes_book,
            "no_book": poly_no_book,
        },
        "kalshi": {
            "meta": kalshi_meta,
            "orderbook": kalshi_ob,
        },
    }

    row["arb"] = compute_arb(row["poly"], kalshi_meta or {}, kalshi_ob)
    return row


def write_jsonl(row: dict) -> None:
    with DATA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def main():
    log.info("=" * 60)
    log.info(f"cross_arb_scout v1.1 starting; cwd={SCOUT_DIR}")
    log.info(f"output: {DATA_FILE}")
    log.info(f"poll interval: {POLL_INTERVAL_SEC}s")

    pairs = load_pairs()
    if not pairs:
        log.error("no valid pairs configured; edit market_pairs.json and restart")
        sys.exit(1)

    log.info(f"loaded {len(pairs)} pairs: {[p['name'] for p in pairs]}")

    cycle = 0
    while not _stop:
        cycle += 1
        started = time.time()
        n_arb = 0

        for pair in pairs:
            if _stop:
                break
            try:
                row = snapshot_pair(pair)
                write_jsonl(row)
                gap = row["arb"].get("arb_gap")
                if gap is not None and gap > 0:
                    n_arb += 1
                    log.info(
                        f"ARB {pair['name']}: gap={gap:+.4f} "
                        f"via {row['arb']['best_direction']} (before fees)"
                    )
            except Exception as e:
                log.exception(f"snapshot_pair({pair.get('name')}) failed: {e}")

        elapsed = time.time() - started
        log.info(f"cycle #{cycle}: {len(pairs)} pairs in {elapsed:.1f}s, {n_arb} nominal arb windows")

        remaining = POLL_INTERVAL_SEC - elapsed
        while remaining > 0 and not _stop:
            chunk = min(1.0, remaining)
            time.sleep(chunk)
            remaining -= chunk

    log.info("clean shutdown")


if __name__ == "__main__":
    main()
