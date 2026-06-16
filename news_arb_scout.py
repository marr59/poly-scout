#!/usr/bin/env python3
"""
news_arb_scout.py — read-only news-driven arbitrage scout for Polymarket

Watches RSS news feeds and a curated list of Polymarket markets.
On matching news, snapshots market prices at T+0, T+5, T+15, T+60 minutes.
Also emits a baseline snapshot for every watched market every minute,
so post-hoc analysis can compare news-triggered moves vs. background noise.

Configs:
    ~/poly_scout/news_markets.json     — markets to watch + keywords
    ~/poly_scout/news_sources.json     — RSS feed URLs

Outputs (single event-sourced JSONL):
    ~/poly_scout/news_arb_events.jsonl

Event types:
    - news_seen         : any news item observed (dedup'd by link+title hash)
    - news_matched      : news matched a market's keywords → triggers snapshots
    - market_snapshot   : price snapshot (t=0 after match, OR baseline)
    - snapshot_followup : price snapshot at T+5, T+15, T+60 after a match

Log:  ~/poly_scout/news_arb_scout.log
State (dedup, seen news): ~/poly_scout/news_arb_state.json

Requirements:
    pip install feedparser requests

N<30 rule applies.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

try:
    import feedparser  # type: ignore
except ImportError:
    print("ERROR: feedparser is required. Run: pip install feedparser", file=sys.stderr)
    sys.exit(2)

# ==========================================================================
# Config
# ==========================================================================

HOME = Path.home()
SCOUT_DIR = HOME / "poly_scout"
SCOUT_DIR.mkdir(exist_ok=True)

LOG_FILE = SCOUT_DIR / "news_arb_scout.log"
DATA_FILE = SCOUT_DIR / "news_arb_events.jsonl"
MARKETS_FILE = SCOUT_DIR / "news_markets.json"
SOURCES_FILE = SCOUT_DIR / "news_sources.json"
STATE_FILE = SCOUT_DIR / "news_arb_state.json"

NEWS_POLL_INTERVAL_SEC = 60        # poll RSS feeds
BASELINE_INTERVAL_SEC = 60         # emit baseline market snapshot
HTTP_TIMEOUT = 10

FOLLOWUP_DELAYS_SEC = [5 * 60, 15 * 60, 60 * 60]  # T+5, T+15, T+60

MAX_SEEN_NEWS = 5000  # cap the dedup set size

POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE = "https://clob.polymarket.com"

USER_AGENT = "news_arb_scout/1.0 (read-only research)"

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
log = logging.getLogger("news_arb")

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
            log.warning(f"429 from {url}")
            time.sleep(5)
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log.debug(f"GET failed {url}: {e}")
        return None
    except ValueError as e:
        log.debug(f"JSON decode failed {url}: {e}")
        return None


# ==========================================================================
# Polymarket fetchers (same style as cross_arb_scout)
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
        "mid": (
            round((best_bid_px + best_ask_px) / 2.0, 6)
            if (best_bid_px is not None and best_ask_px is not None)
            else None
        ),
    }


def snapshot_market(slug: str) -> dict:
    meta = fetch_poly_market_meta(slug)
    yes_book = fetch_poly_book(meta["yes_token_id"]) if meta else None
    no_book = fetch_poly_book(meta["no_token_id"]) if meta else None
    return {
        "slug": slug,
        "question": meta.get("question") if meta else None,
        "closed": meta.get("closed") if meta else None,
        "volume_24h": meta.get("volume_24h") if meta else None,
        "liquidity": meta.get("liquidity") if meta else None,
        "yes_book": yes_book,
        "no_book": no_book,
    }


# ==========================================================================
# State persistence (dedup + pending followups)
# ==========================================================================


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen_news": [], "pending_followups": []}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception as e:
        log.warning(f"state load failed: {e}; starting fresh")
        return {"seen_news": [], "pending_followups": []}


def save_state(state: dict) -> None:
    # Cap dedup list
    if len(state.get("seen_news", [])) > MAX_SEEN_NEWS:
        state["seen_news"] = state["seen_news"][-MAX_SEEN_NEWS:]
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(STATE_FILE)


# ==========================================================================
# Config loading
# ==========================================================================

EXAMPLE_MARKETS = [
    {
        "slug": "polymarket-slug-here",
        "keywords": ["replace", "with", "topic", "words"],
        "note": "what this market is about",
    }
]

EXAMPLE_SOURCES = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://apnews.com/hub/ap-top-news/rss",
]


def load_markets() -> list[dict]:
    if not MARKETS_FILE.exists():
        MARKETS_FILE.write_text(json.dumps(EXAMPLE_MARKETS, indent=2, ensure_ascii=False))
        log.warning(f"wrote example {MARKETS_FILE}; populate and restart")
        return []
    try:
        markets = json.loads(MARKETS_FILE.read_text())
    except Exception as e:
        log.error(f"failed to parse {MARKETS_FILE}: {e}")
        return []
    if not isinstance(markets, list):
        return []

    valid = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        slug = m.get("slug", "")
        kws = m.get("keywords", [])
        if not slug or "replace" in slug.lower() or "here" in slug.lower():
            continue
        if not isinstance(kws, list) or not kws:
            continue
        # normalize keywords
        m["_kws_lc"] = [str(k).lower().strip() for k in kws if str(k).strip()]
        valid.append(m)
    return valid


def load_sources() -> list[str]:
    if not SOURCES_FILE.exists():
        SOURCES_FILE.write_text(json.dumps(EXAMPLE_SOURCES, indent=2, ensure_ascii=False))
        log.warning(f"wrote example {SOURCES_FILE}; edit and restart")
        return []
    try:
        src = json.loads(SOURCES_FILE.read_text())
    except Exception as e:
        log.error(f"failed to parse {SOURCES_FILE}: {e}")
        return []
    return [u for u in src if isinstance(u, str) and u.startswith("http")]


# ==========================================================================
# Utilities
# ==========================================================================


def utc_now():
    now = datetime.now(timezone.utc)
    return now.isoformat(), now.timestamp()


def write_event(ev: dict) -> None:
    with DATA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        f.flush()


_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize_text(s: str) -> str:
    return " ".join(_WORD_RE.findall((s or "").lower()))


def hash_news(item: dict) -> str:
    """Stable dedup key. Uses guid/id/link+title fallback."""
    key = item.get("id") or item.get("guid") or (
        (item.get("link") or "") + "||" + (item.get("title") or "")
    )
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()


def match_markets(news_text: str, markets: list[dict]) -> list[dict]:
    """Return markets whose ALL top-level keyword groups match.

    Simple rule: for each market, ALL kw tokens must appear as substrings.
    This is strict-AND which gives fewer but cleaner matches.
    """
    matched = []
    for m in markets:
        kws = m.get("_kws_lc") or []
        if kws and all(k in news_text for k in kws):
            matched.append(m)
    return matched


# ==========================================================================
# Main event loop
# ==========================================================================


def main():
    log.info("=" * 60)
    log.info("news_arb_scout starting")
    log.info(f"data: {DATA_FILE}")
    log.info(f"state: {STATE_FILE}")

    markets = load_markets()
    sources = load_sources()
    if not markets or not sources:
        log.error("need at least one market + one source; edit configs and restart")
        sys.exit(1)

    log.info(f"{len(markets)} markets, {len(sources)} RSS sources")

    state = load_state()
    seen_ids = set(state.get("seen_news", []))
    # pending_followups: list of (due_ts, match_id, slug, delay_sec)
    pending: list[tuple[float, str, str, int]] = [
        tuple(x) for x in state.get("pending_followups", [])  # type: ignore
    ]
    heapq.heapify(pending)

    last_baseline_ts = 0.0
    last_rss_ts = 0.0

    while not _stop:
        now_ts = time.time()
        ts_iso, _ = utc_now()

        # -------------------------------------------------------------
        # 1. Fire due followup snapshots
        # -------------------------------------------------------------
        while pending and pending[0][0] <= now_ts:
            due_ts, match_id, slug, delay = heapq.heappop(pending)
            try:
                snap = snapshot_market(slug)
                write_event({
                    "type": "snapshot_followup",
                    "ts_iso": ts_iso,
                    "ts": now_ts,
                    "match_id": match_id,
                    "delay_sec": delay,
                    "market": snap,
                })
                log.info(f"followup T+{delay}s match={match_id[:8]} slug={slug}")
            except Exception as e:
                log.exception(f"followup failed {slug}: {e}")

        # -------------------------------------------------------------
        # 2. Baseline snapshots once per interval
        # -------------------------------------------------------------
        if now_ts - last_baseline_ts >= BASELINE_INTERVAL_SEC:
            last_baseline_ts = now_ts
            for m in markets:
                if _stop:
                    break
                try:
                    snap = snapshot_market(m["slug"])
                    write_event({
                        "type": "market_snapshot",
                        "subtype": "baseline",
                        "ts_iso": ts_iso,
                        "ts": now_ts,
                        "market": snap,
                    })
                except Exception as e:
                    log.exception(f"baseline snap failed {m['slug']}: {e}")

        # -------------------------------------------------------------
        # 3. Poll RSS feeds
        # -------------------------------------------------------------
        if now_ts - last_rss_ts >= NEWS_POLL_INTERVAL_SEC:
            last_rss_ts = now_ts
            for url in sources:
                if _stop:
                    break
                try:
                    # feedparser handles HTTP + parse in one call
                    feed = feedparser.parse(url, agent=USER_AGENT, request_headers={
                        "Accept": "application/rss+xml, application/xml, text/xml",
                    })
                    if feed.bozo and not feed.entries:
                        log.debug(f"bad feed {url}: {feed.bozo_exception}")
                        continue
                    for entry in feed.entries[:50]:
                        item = {
                            "id": entry.get("id"),
                            "guid": entry.get("guid"),
                            "link": entry.get("link"),
                            "title": entry.get("title"),
                            "summary": entry.get("summary") or entry.get("description"),
                            "published": entry.get("published") or entry.get("updated"),
                        }
                        nid = hash_news(item)
                        if nid in seen_ids:
                            continue
                        seen_ids.add(nid)
                        state["seen_news"] = list(seen_ids)[-MAX_SEEN_NEWS:]

                        write_event({
                            "type": "news_seen",
                            "ts_iso": ts_iso,
                            "ts": now_ts,
                            "source": url,
                            "id": nid,
                            "item": item,
                        })

                        # match against markets
                        text = _normalize_text(f"{item.get('title') or ''} {item.get('summary') or ''}")
                        matched = match_markets(text, markets)
                        for m in matched:
                            match_id = hashlib.sha1(
                                f"{nid}|{m['slug']}".encode()
                            ).hexdigest()
                            # t=0 snapshot + schedule followups
                            try:
                                snap = snapshot_market(m["slug"])
                                write_event({
                                    "type": "news_matched",
                                    "ts_iso": ts_iso,
                                    "ts": now_ts,
                                    "match_id": match_id,
                                    "news_id": nid,
                                    "source": url,
                                    "news_title": item.get("title"),
                                    "news_link": item.get("link"),
                                    "market_slug": m["slug"],
                                    "matched_keywords": m.get("_kws_lc"),
                                })
                                write_event({
                                    "type": "market_snapshot",
                                    "subtype": "news_t0",
                                    "ts_iso": ts_iso,
                                    "ts": now_ts,
                                    "match_id": match_id,
                                    "market": snap,
                                })
                                for d in FOLLOWUP_DELAYS_SEC:
                                    heapq.heappush(pending, (now_ts + d, match_id, m["slug"], d))
                                log.info(
                                    f"MATCH: '{item.get('title', '')[:80]}' → {m['slug']} "
                                    f"({len(FOLLOWUP_DELAYS_SEC)} followups scheduled)"
                                )
                            except Exception as e:
                                log.exception(f"match-triggered snapshot failed: {e}")

                except Exception as e:
                    log.debug(f"feed poll failed {url}: {e}")

            # persist state after RSS pass
            state["pending_followups"] = [list(x) for x in pending]
            try:
                save_state(state)
            except Exception as e:
                log.warning(f"state save failed: {e}")

        # -------------------------------------------------------------
        # 4. Sleep briefly
        # -------------------------------------------------------------
        for _ in range(5):
            if _stop:
                break
            time.sleep(1)

    # final state save
    state["pending_followups"] = [list(x) for x in pending]
    try:
        save_state(state)
    except Exception:
        pass
    log.info("clean shutdown")


if __name__ == "__main__":
    main()
