# Poly Scout

Two research tools for finding tradable inefficiencies between prediction markets (Polymarket, Kalshi) and, separately, between crypto spot/derivatives markets and Polymarket — built to answer one question: is there actually exploitable arbitrage here, or does it just look like there should be?

## Status: Both lines of research closed — no exploitable edge found

This isn't a "the bot wasn't profitable yet" situation like the companion [Bybit OBI bot](https://github.com/marr59/bybit-bot) — these are two cleanly negative results, and they're published as negative results on purpose.

**Crypto-tick → Polymarket lag arbitrage** (`poly_scout.py`): watched Bybit price/order-book signals and measured how long Polymarket crypto-price markets took to react, looking for a window to trade ahead of the repricing. Ran for about a week. Direction-match rate on the signal landed at 51% — statistically indistinguishable from a coin flip, and below the ~2% commission breakeven once Polymarket's fees were accounted for.

**Cross-platform arbitrage, Polymarket ↔ Kalshi** (`cross_arb_scout.py`): polled matching event pairs across both platforms every 30 seconds for 11.5 days across 18 pairs, looking for moments where the same outcome was priced differently enough on each platform to trade the gap profitably. Every apparent gap that looked promising turned out, on closer inspection (`interim_check.py`, `interim_check_v2.py`), to be either a feed glitch (one platform's price briefly stale or wrong) or a book-asymmetry artifact (the visible gap wasn't actually fillable at that size). See `CROSS_ARB_FINDINGS.md` for the full breakdown across all 18 pairs.

A third tool, `news_arb_scout.py`, was built to test a related idea — whether breaking news creates a brief window before Polymarket prices catch up — but wasn't run long enough to produce a published finding; it's included here as a complete, runnable script rather than results.

## What's in this repo

- **`poly_scout.py`** — async watcher: streams Bybit signals (rate-of-change, tick direction, order book imbalance), checks how Polymarket's matching market reacts, and logs the lag.
- **`cross_arb_scout.py`** — polls Polymarket and Kalshi for matching event pairs every 30 seconds and computes the gross arbitrage gap between them. Produced roughly 600K logged observations over the research period.
- **`news_arb_scout.py`** — watches RSS feeds (Reuters, BBC, AP, CoinDesk, Cointelegraph, MarketWatch, CNN, FT — see `news_sources.example.json`) for keyword matches, then snapshots the relevant Polymarket price at T+0/+5/+15/+60 minutes to see whether news moves the market with a lag.
- **`find_pairs.py`** — CLI tool to search both platforms for markets matching a keyword, ranked by volume. Used to build the pair lists the scouts ran against.
- **`kalshi_event.py`** — utility to inspect every market under a given Kalshi event ticker.
- **`interim_check.py`, `interim_check_v2.py`, `interim_check_v3.py`** — three iterations of the same job: parse the large JSONL output from `cross_arb_scout.py` and summarize it (gap distribution, which pairs "died" mid-run, percentile breakdowns). Kept as three separate files because each version answered a slightly different question while the research was live, rather than being refactored into one tool after the fact.
- **`market_pairs.example.json`, `news_markets.example.json`, `news_sources.example.json`** — config templates. The real configs used during research aren't included since they reference specific live market IDs, but the structure is identical.

## What this demonstrates

Both scouts ran long enough, and across enough pairs, to make the negative result credible rather than a snap judgment after a few hours of watching. The real finding here isn't "I built an arbitrage bot" — it's "I built the instrumentation to actually test whether the arbitrage existed, ran it long enough to trust the answer, and concluded it didn't, instead of convincing myself otherwise."

## Setup

```bash
cp market_pairs.example.json market_pairs.json
cp news_markets.example.json news_markets.json
cp news_sources.example.json news_sources.json
# edit the above with the specific market pairs / sources you want to track
pip install feedparser requests aiohttp  # feedparser only needed for news_arb_scout.py
python3 cross_arb_scout.py
```

No API keys are required for either Polymarket's Gamma/CLOB API or Kalshi's public market data — both are read-only and unauthenticated for this use case.
