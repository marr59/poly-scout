#!/usr/bin/env python3
"""
poly_scout.py — autonomous observer that logs the time lag between
Bybit spot price movements and Polymarket 15-min Up/Down market reactions.

Read-only. No trading. No API keys required.
"""

import asyncio
import json
import signal
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp

# ── Config ───────────────────────────────────────────────────────────────────

BYBIT_WS = "wss://stream.bybit.com/v5/public/spot"
BYBIT_TICKERS = ["BTCUSDT", "ETHUSDT"]

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"

ROC_WINDOW = 60          # seconds of price history
ROC_THRESHOLD = 0.20     # percent — minimum abs(ROC) to emit a signal
TICK_SPIKE_THRESHOLD = 0.10  # percent — single-tick jump to emit TICK_SPIKE
POLY_POLL_SEC = 3        # how often we poll Polymarket orderbooks
MIDPOINT_SHIFT = 0.03    # minimum midpoint move to count as "reaction"
REACTION_TIMEOUT = 120   # seconds to wait for Polymarket reaction

ROC_COOLDOWN = 60        # seconds — debounce per coin for ROC signal
TICK_SPIKE_COOLDOWN = 30 # seconds — debounce per coin for TICK_SPIKE signal

OBI_THRESHOLD = 0.30        # abs(OBI) trigger level
OBI_COOLDOWN = 30           # seconds — debounce per coin for OBI_CROSS signal
OBI_WINDOW = 10             # seconds — rolling OBI history
OBI_CONFIRM_WINDOW = 5      # seconds — confirmation average window
OBI_MIN_CONFIRM_SAMPLES = 3 # min samples needed inside confirm window

COINS = ["btc", "eth"]
INTERVALS = {
    "15m": 900,
    "5m": 300,
}
MIN_REMAINING = 30       # seconds — don't use a market about to expire

LOG_FILE = "/root/poly_scout/poly_scout_log.jsonl"

# ── Shared state ─────────────────────────────────────────────────────────────

state = {
    # Bybit prices: {"BTC": deque([(ts, price), ...]), "ETH": ...}
    "prices": {},
    # Polymarket active markets: {"BTC_15m": {slug, market_id, token_id, ...}, ...}
    "poly_markets": {},
    # Latest midpoints: {"BTC": (ts, midpoint, best_bid, best_ask, liquidity), ...}
    "poly_mid": {},
    # Pending reactions being tracked: list of dicts
    "pending": [],
    # Cooldown per (coin, signal_type): {"BTC_ROC": ts, "BTC_TICK_SPIKE": ts, ...}
    "last_signal_ts": {},
    # Last tick price per coin for TICK_SPIKE comparison: {"BTC": (ts, price), ...}
    "last_tick": {},
    # Debug log throttle: {"BTC": last_debug_epoch, ...}
    "last_debug_ts": {},
    # Rolling OBI history per coin: {"BTC": deque([(ts, obi), ...]), ...}
    "obi_history": {},
    # Current top-of-book per coin: {"BTC": {"bid": (price, size), "ask": (price, size)}, ...}
    "ob_top": {},
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def now_ts():
    return time.time()


def coin_from_symbol(sym: str) -> str:
    if sym.startswith("BTC"):
        return "BTC"
    if sym.startswith("ETH"):
        return "ETH"
    return sym.replace("USDT", "")


async def fetch_json(session: aiohttp.ClientSession, url: str, params=None,
                     retries=5, base_delay=2):
    for attempt in range(retries):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
                text = await r.text()
                raise aiohttp.ClientError(f"HTTP {r.status}: {text[:200]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            delay = base_delay * (2 ** attempt)
            print(f"  [retry] {url} — {e} — retrying in {delay}s")
            await asyncio.sleep(delay)
    print(f"  [error] {url} — all {retries} retries exhausted")
    return None


def append_log(entry: dict):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def emit_signal(coin: str, signal_type: str, direction: str,
                change_pct: float, price: float, ts: float):
    """Enqueue one pending entry per active market for this coin."""
    for interval_label in INTERVALS:
        key = f"{coin}_{interval_label}"
        if key not in state["poly_markets"]:
            continue
        state["pending"].append({
            "coin": coin,
            "market_key": key,
            "signal_type": signal_type,
            "direction": direction,
            "roc": round(change_pct, 4),
            "price": price,
            "ts": now_iso(),
            "ts_epoch": ts,
        })


# ── Bybit WebSocket feed ────────────────────────────────────────────────────

def handle_orderbook_msg(msg: dict):
    """Update top-of-book, compute OBI, emit OBI_CROSS signal when confirmed."""
    data = msg.get("data") or {}
    symbol = data.get("s", "")
    if not symbol:
        return

    coin = coin_from_symbol(symbol)
    msg_type = msg.get("type", "snapshot")
    bids = data.get("b", [])
    asks = data.get("a", [])

    if coin not in state["ob_top"]:
        state["ob_top"][coin] = {"bid": None, "ask": None}
    ob = state["ob_top"][coin]

    if msg_type == "snapshot":
        if bids:
            ob["bid"] = (float(bids[0][0]), float(bids[0][1]))
        if asks:
            ob["ask"] = (float(asks[0][0]), float(asks[0][1]))
    else:  # delta
        if bids:
            size = float(bids[0][1])
            if size > 0:
                ob["bid"] = (float(bids[0][0]), size)
        if asks:
            size = float(asks[0][1])
            if size > 0:
                ob["ask"] = (float(asks[0][0]), size)

    if not ob["bid"] or not ob["ask"]:
        return

    bid_vol = ob["bid"][1]
    ask_vol = ob["ask"][1]
    total = bid_vol + ask_vol
    if total <= 0:
        return

    obi = (bid_vol - ask_vol) / total
    ts = now_ts()

    if coin not in state["obi_history"]:
        state["obi_history"][coin] = deque()
    dq = state["obi_history"][coin]
    if not dq or (ts - dq[-1][0]) >= 1.0:
        dq.append((ts, obi))
    while dq and (ts - dq[0][0]) > OBI_WINDOW:
        dq.popleft()

    if abs(obi) < OBI_THRESHOLD:
        return

    cutoff = ts - OBI_CONFIRM_WINDOW
    recent = [o for (t, o) in dq if t >= cutoff]
    if len(recent) < OBI_MIN_CONFIRM_SAMPLES:
        return
    avg = sum(recent) / len(recent)
    same_sign = (obi > 0 and avg > 0) or (obi < 0 and avg < 0)
    if not same_sign:
        return

    last_ts = state["last_signal_ts"].get(f"{coin}_OBI_CROSS", 0)
    if (ts - last_ts) < OBI_COOLDOWN:
        return

    state["last_signal_ts"][f"{coin}_OBI_CROSS"] = ts
    direction = "UP" if obi > 0 else "DOWN"
    mid = (ob["bid"][0] + ob["ask"][0]) / 2
    emit_signal(coin, "OBI_CROSS", direction, obi, mid, ts)
    print(f"[signal] OBI_CROSS {coin} {obi:+.2f} price={mid:.2f}")


async def bybit_feed():
    import websockets

    while True:
        try:
            print(f"[bybit] connecting to {BYBIT_WS} ...")
            async with websockets.connect(BYBIT_WS, ping_interval=20) as ws:
                sub_args = [f"tickers.{t}" for t in BYBIT_TICKERS] + \
                           [f"orderbook.1.{t}" for t in BYBIT_TICKERS]
                sub = {"op": "subscribe", "args": sub_args}
                await ws.send(json.dumps(sub))
                print(f"[bybit] subscribed to {sub_args}")

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("op") == "subscribe":
                        continue

                    topic = msg.get("topic", "")
                    if topic.startswith("orderbook."):
                        handle_orderbook_msg(msg)
                        continue
                    if not topic.startswith("tickers."):
                        continue

                    data = msg.get("data")
                    if not data:
                        continue

                    symbol = data.get("symbol", "")
                    last_price = data.get("lastPrice")
                    if not last_price:
                        continue

                    coin = coin_from_symbol(symbol)
                    price = float(last_price)
                    ts = now_ts()

                    # TICK_SPIKE: compare against previous tick (not throttled)
                    prev_tick = state["last_tick"].get(coin)
                    state["last_tick"][coin] = (ts, price)
                    if prev_tick:
                        _, prev_price = prev_tick
                        if prev_price > 0:
                            tick_pct = ((price - prev_price) / prev_price) * 100
                            if abs(tick_pct) >= TICK_SPIKE_THRESHOLD:
                                last_ts = state["last_signal_ts"].get(
                                    f"{coin}_TICK_SPIKE", 0)
                                if (ts - last_ts) >= TICK_SPIKE_COOLDOWN:
                                    direction = "UP" if tick_pct > 0 else "DOWN"
                                    state["last_signal_ts"][
                                        f"{coin}_TICK_SPIKE"] = ts
                                    emit_signal(coin, "TICK_SPIKE", direction,
                                                tick_pct, price, ts)
                                    print(
                                        f"[signal] TICK_SPIKE {coin} "
                                        f"{tick_pct:+.2f}% price={price}"
                                    )

                    # maintain rolling window — throttle to ~1 sample/sec
                    if coin not in state["prices"]:
                        state["prices"][coin] = deque()
                    dq = state["prices"][coin]
                    if not dq or (ts - dq[-1][0]) >= 1.0:
                        dq.append((ts, price))

                    # evict entries older than ROC_WINDOW
                    while dq and (ts - dq[0][0]) > ROC_WINDOW:
                        dq.popleft()

                    # compute ROC if we have enough data
                    if len(dq) >= 2:
                        oldest_ts, oldest_price = dq[0]
                        if ts - oldest_ts >= 5 and oldest_price > 0:
                            roc = ((price - oldest_price) / oldest_price) * 100
                            if abs(roc) >= ROC_THRESHOLD:
                                last_ts = state["last_signal_ts"].get(
                                    f"{coin}_ROC", 0)
                                if (ts - last_ts) < ROC_COOLDOWN:
                                    continue

                                direction = "UP" if roc > 0 else "DOWN"
                                state["last_signal_ts"][f"{coin}_ROC"] = ts
                                emit_signal(coin, "ROC", direction,
                                            roc, price, ts)
                                print(
                                    f"  >> SIGNAL {coin} {direction} "
                                    f"ROC={roc:+.3f}% price={price}"
                                )

        except Exception as e:
            print(f"[bybit] connection error: {e} — reconnecting in 5s")
            await asyncio.sleep(5)


# ── Polymarket HTTP feed ────────────────────────────────────────────────────

async def discover_markets(session: aiohttp.ClientSession):
    """Find current active Up/Down markets by computing exact slugs."""
    now = int(time.time())

    for coin in COINS:
        for interval_label, interval_sec in INTERVALS.items():
            key = f"{coin.upper()}_{interval_label}"

            end_unix = ((now // interval_sec) + 1) * interval_sec
            remaining = end_unix - now
            # If too close to expiry, use next interval
            if remaining <= MIN_REMAINING:
                end_unix += interval_sec
                remaining += interval_sec

            slug = f"{coin}-updown-{interval_label}-{end_unix}"
            data = await fetch_json(session, GAMMA_URL, params={"slug": slug})

            if not data or not isinstance(data, list) or len(data) == 0:
                print(f"[poly] {key}: slug {slug} not found on Gamma")
                continue

            mkt = data[0]

            tokens = mkt.get("clobTokenIds", [])
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            outcomes = mkt.get("outcomes", [])
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if not tokens:
                print(f"[poly] {key}: no clobTokenIds for {slug}")
                continue

            # find the "Up" token index
            up_idx = 0
            for i, o in enumerate(outcomes):
                if isinstance(o, str) and "up" in o.lower():
                    up_idx = i
                    break

            token_id = tokens[up_idx] if up_idx < len(tokens) else tokens[0]

            market_info = {
                "slug": slug,
                "market_id": str(mkt.get("id", mkt.get("conditionId", ""))),
                "token_id": token_id,
                "end_epoch": end_unix,
                "question": mkt.get("question", ""),
            }
            state["poly_markets"][key] = market_info
            print(
                f"[poly] {key}: {slug}  "
                f"token={token_id[:16]}...  remaining={remaining}s"
            )


async def poll_orderbook(session: aiohttp.ClientSession, key: str):
    info = state["poly_markets"].get(key)
    if not info:
        return

    data = await fetch_json(session, CLOB_BOOK_URL, params={
        "token_id": info["token_id"],
    })
    if not data:
        return

    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return

    best_bid = max(float(b["price"]) for b in bids)
    best_ask = min(float(a["price"]) for a in asks)
    midpoint = (best_bid + best_ask) / 2

    # crude liquidity = sum of bid sizes + ask sizes (top of book)
    liquidity = sum(float(b.get("size", 0)) for b in bids) + \
                sum(float(a.get("size", 0)) for a in asks)

    ts = now_ts()
    state["poly_mid"][key] = (ts, midpoint, best_bid, best_ask, liquidity)

    # debug log every 30 seconds per key
    last_dbg = state["last_debug_ts"].get(key, 0)
    if (ts - last_dbg) >= 30:
        state["last_debug_ts"][key] = ts
        info_end = info.get("end_epoch")
        remaining = int(info_end - ts) if info_end else "?"
        print(
            f"[poly] {key} mid={midpoint:.4f} bid={best_bid:.4f} "
            f"ask={best_ask:.4f} remaining={remaining}s"
        )


async def polymarket_feed():
    async with aiohttp.ClientSession() as session:
        # initial discovery
        await discover_markets(session)
        rediscover_counter = 0

        while True:
            try:
                # re-discover markets every ~60 polls (3 minutes)
                rediscover_counter += 1
                if rediscover_counter >= 60:
                    rediscover_counter = 0
                    await discover_markets(session)

                tasks = []
                for coin_lower in COINS:
                    for interval in INTERVALS:
                        key = f"{coin_lower.upper()}_{interval}"
                        if key in state["poly_markets"]:
                            tasks.append(poll_orderbook(session, key))
                if tasks:
                    await asyncio.gather(*tasks)

            except Exception as e:
                print(f"[poly] error: {e}")

            await asyncio.sleep(POLY_POLL_SEC)


# ── Lag detector ─────────────────────────────────────────────────────────────

async def lag_detector():
    while True:
        if not state["pending"]:
            await asyncio.sleep(0.5)
            continue

        signal = state["pending"].pop(0)
        coin = signal["coin"]
        key = signal["market_key"]
        ts_signal = signal["ts_epoch"]

        # grab baseline midpoint
        mid_entry = state["poly_mid"].get(key)
        if not mid_entry:
            print(f"  [lag] no midpoint for {key}, skipping signal")
            continue

        _, baseline_mid, _, _, _ = mid_entry

        print(
            f"  [lag] tracking {key} {signal['direction']} "
            f"ROC={signal['roc']:+.3f}% baseline_mid={baseline_mid:.4f}"
        )

        # wait for reaction
        ts_poly_react = None
        poly_mid_after = baseline_mid
        deadline = ts_signal + REACTION_TIMEOUT

        while now_ts() < deadline:
            await asyncio.sleep(POLY_POLL_SEC)
            mid_now = state["poly_mid"].get(key)
            if not mid_now:
                continue
            _, current_mid, _, _, _ = mid_now
            shift = abs(current_mid - baseline_mid)
            if shift >= MIDPOINT_SHIFT:
                ts_poly_react = now_ts()
                poly_mid_after = current_mid
                break

        # compute lag
        lag_ms = None
        if ts_poly_react is not None:
            lag_ms = int((ts_poly_react - ts_signal) * 1000)

        # gather Polymarket context
        mkt = state["poly_markets"].get(key, {})
        mid_final = state["poly_mid"].get(key)
        best_bid = mid_final[2] if mid_final else None
        best_ask = mid_final[3] if mid_final else None
        liquidity = mid_final[4] if mid_final else None

        time_remaining = None
        signal_before_close_sec = None
        if mkt.get("end_epoch"):
            time_remaining = max(0, int(mkt["end_epoch"] - ts_signal))
            signal_before_close_sec = round(mkt["end_epoch"] - ts_signal, 3)

        shift_signed = poly_mid_after - baseline_mid
        roc_val = signal["roc"]
        direction_match = (
            (roc_val > 0 and shift_signed > 0)
            or (roc_val < 0 and shift_signed < 0)
        )

        slug = mkt.get("slug", "")
        if "-5m-" in slug:
            market_type = "5m"
        elif "-15m-" in slug:
            market_type = "15m"
        else:
            market_type = None

        entry = {
            "ts_signal": signal["ts"],
            "ts_poly_react": (
                datetime.fromtimestamp(ts_poly_react, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                if ts_poly_react else None
            ),
            "lag_ms": lag_ms,
            "signal_type": signal["signal_type"],
            "signal_before_close_sec": signal_before_close_sec,
            "coin": coin,
            "direction": signal["direction"],
            "roc_pct": signal["roc"],
            "bybit_price": signal["price"],
            "poly_midpoint_before": round(baseline_mid, 6),
            "poly_midpoint_after": round(poly_mid_after, 6),
            "poly_market_id": mkt.get("market_id"),
            "poly_slug": mkt.get("slug"),
            "poly_time_remaining_sec": time_remaining,
            "poly_best_bid": best_bid,
            "poly_best_ask": best_ask,
            "poly_liquidity_at_signal": round(liquidity, 2) if liquidity else None,
            "direction_match": direction_match,
            "market_type": market_type,
        }

        append_log(entry)

        lag_str = f"{lag_ms}ms" if lag_ms is not None else "NO REACTION"
        print(
            f"  << LAG {key} {signal['direction']} [{signal['signal_type']}] | "
            f"lag={lag_str} | move={signal['roc']:+.3f}% | "
            f"mid {baseline_mid:.4f} -> {poly_mid_after:.4f}"
        )


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  poly_scout — Bybit / Polymarket lag observer")
    print(f"  ROC threshold: {ROC_THRESHOLD}% over {ROC_WINDOW}s window")
    print(f"  TICK_SPIKE threshold: {TICK_SPIKE_THRESHOLD}% (single tick)")
    print(f"  OBI_CROSS threshold: ±{OBI_THRESHOLD} (orderbook.1, "
          f"{OBI_CONFIRM_WINDOW}s confirm)")
    print(f"  Midpoint shift threshold: {MIDPOINT_SHIFT}")
    print(f"  Log file: {LOG_FILE}")
    print("=" * 60)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tasks = [
        asyncio.create_task(bybit_feed()),
        asyncio.create_task(polymarket_feed()),
        asyncio.create_task(lag_detector()),
    ]

    await stop_event.wait()
    print("\n[shutdown] signal received — cancelling tasks...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    print("[shutdown] done")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[shutdown] exiting")
