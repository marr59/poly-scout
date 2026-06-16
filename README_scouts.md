# Polymarket research scouts — deployment guide

Two read-only scripts for 2-week data collection. Neither places trades.

- **`cross_arb_scout.py`** — polls paired Polymarket↔Kalshi markets every 30s, logs top-of-book bid/ask + depth, computes gross arb gap.
- **`news_arb_scout.py`** — polls RSS feeds, snapshots matched Polymarket markets at T+0/5/15/60 min, plus per-minute baseline for all watched markets.

---

## 1. Deploy to your VPS

```bash
scp cross_arb_scout.py news_arb_scout.py root@YOUR_VPS_IP:~/poly_scout/
scp market_pairs.example.json     root@YOUR_VPS_IP:~/poly_scout/market_pairs.json
scp news_markets.example.json     root@YOUR_VPS_IP:~/poly_scout/news_markets.json
scp news_sources.example.json     root@YOUR_VPS_IP:~/poly_scout/news_sources.json
```

On the server:

```bash
ssh root@YOUR_VPS_IP
cd ~/poly_scout
pip install feedparser requests   # feedparser is the only new dep
```

## 2. Populate configs (critical)

### `market_pairs.json` — cross-arb

Each entry pairs one Polymarket slug with one Kalshi ticker. Find them:

- **Polymarket**: open the market page, slug is in the URL after `/event/`.
- **Kalshi**: ticker is on the market page ("Ticker: XXX") or via `GET /markets?limit=100` search.

Start with 5–10 clean pairs. Focus on events where resolution criteria *exactly* match (same date, same reference price source, same wording). Mismatched resolution = basis risk, not arbitrage.

Candidate categories that historically overlap:
- Fed rate decision dates
- BTC/ETH price levels by end of month
- US presidential / congressional race outcomes
- Jobs report beat/miss
- Super Bowl / major sports outcomes

### `news_markets.json` — news-arb

Each entry: market slug + keyword list (AND-matched; all tokens must appear as substrings in title+summary). Use narrow keywords to avoid noise:

```json
{"slug": "fed-june-cut", "keywords": ["fed", "rate"]}
```

NOT:
```json
{"slug": "fed-june-cut", "keywords": ["fed"]}   // matches too much
```

### `news_sources.json` — RSS feeds

Start with the defaults. Some may 404 or return parse errors — that's fine, scout logs and continues. After 24h, check `news_arb_scout.log` and prune dead feeds.

## 3. Run under tmux

```bash
tmux new -s crossarb
cd ~/poly_scout && python3 cross_arb_scout.py
# Ctrl-b d to detach

tmux new -s newsarb
cd ~/poly_scout && python3 news_arb_scout.py
# Ctrl-b d
```

Check status: `tmux ls` → `tmux attach -t crossarb`

## 4. Outputs

```
~/poly_scout/
├── cross_arb_data.jsonl        # one JSON row per (pair, poll) — cross-arb
├── cross_arb_scout.log
├── news_arb_events.jsonl       # event-sourced: news + snapshots
├── news_arb_scout.log
├── news_arb_state.json         # dedup set + pending followups (persisted)
└── ...
```

### Cross-arb schema (one line per pair per 30s)

```json
{
  "ts_iso": "...", "ts": 1713600000.0,
  "name": "fed-june", "poly_slug": "...", "kalshi_ticker": "...",
  "poly": {
    "question": "...", "closed": false,
    "yes_book": {"bid": 0.52, "bid_size": 1200, "ask": 0.54, "ask_size": 800},
    "no_book":  {"bid": 0.45, "bid_size": 900,  "ask": 0.47, "ask_size": 1100}
  },
  "kalshi": {"yes_bid": 0.51, "yes_ask": 0.55, "no_bid": 0.44, "no_ask": 0.48, ...},
  "arb": {
    "cost_poly_yes_plus_kalshi_no": 1.02,
    "cost_poly_no_plus_kalshi_yes": 0.98,
    "best_direction": "poly_no + kalshi_yes",
    "best_cost": 0.98,
    "arb_gap": 0.02
  }
}
```

Key invariant: `arb_gap > 0` means nominal arb exists **before fees/slippage**. To be tradeable, `arb_gap` must exceed:
- Polymarket fees + gas
- Kalshi fees
- Slippage (compare `size` fields to your intended trade size)

### News-arb schema

One JSONL file, typed events. Filter by `type`:

- `news_seen` — every new RSS item
- `news_matched` — a news item matched a market's keywords
- `market_snapshot` subtype `baseline` — per-minute baseline snap
- `market_snapshot` subtype `news_t0` — triggered T+0 snap
- `snapshot_followup` — T+5, T+15, T+60 after a match

`match_id` links `news_matched` → `market_snapshot(news_t0)` → `snapshot_followup` rows.

## 5. Quick analysis recipes (after 2 weeks)

### Cross-arb: distribution of gross arb gaps

```python
import json, statistics
gaps = []
with open("cross_arb_data.jsonl") as f:
    for line in f:
        row = json.loads(line)
        g = row["arb"].get("arb_gap")
        if g is not None:
            gaps.append(g)
print(f"N={len(gaps)}  positive={sum(1 for g in gaps if g > 0)}")
print(f"median={statistics.median(gaps):.4f}  p95={sorted(gaps)[int(len(gaps)*0.95)]:.4f}")
# then subtract expected fees+slippage to see if anything survives
```

### Cross-arb: persistence of windows (how long do they live?)

```python
# Group by (name, contiguous poll ids where arb_gap > fees_threshold),
# measure duration. If all windows < 5 seconds → you can't hit them manually.
```

### News-arb: t0→t60 price drift per match, vs. baseline

```python
# For each match_id:
#   t0_mid, t5_mid, t15_mid, t60_mid  (yes_book.mid)
#   delta_60 = abs(t60_mid - t0_mid)
# Baseline distribution: for all baseline snaps, |mid(t+60min) - mid(t)|.
# Test: is delta_60 for matched snaps significantly > baseline distribution?
# Need N>=30 matches per direction before stat testing.
```

## 6. Kill / restart

```bash
tmux attach -t crossarb
# Ctrl-C for clean shutdown (signal handler drains)
```

State is persisted on clean exit for news_arb. On hard kill, worst case you re-process ~5000 last news items (dedup catches them).

## 7. Sanity checks during the run

Every few days, tail the logs:

```bash
tail -f ~/poly_scout/cross_arb_scout.log
tail -f ~/poly_scout/news_arb_scout.log
```

Expected patterns:
- `cross_arb`: cycles complete in <10s each; `nominal arb windows` count hopefully non-zero occasionally.
- `news_arb`: `MATCH:` lines appearing 1–10×/day if keywords are well-tuned; lots of `followup T+...` lines.

If `news_arb` logs no matches in 48h → keywords too strict. Loosen one market at a time.

---

## Design notes & honest caveats

- **`arb_gap > 0` is NOT trade-ready edge.** It's a gross signal. Real edge = gap − fees − slippage > 0 AND window persists long enough to execute both legs.
- **Strict AND keyword match** in news-arb is intentional: fewer false positives, clearer analysis. If you find you miss obvious news, split a market into multiple entries with different keyword sets.
- **Depth matters.** The `*_size` fields on top-of-book tell you how much capital you could actually put to work at that price. Tiny `ask_size` = arb_gap is a mirage.
- **Pro MMs on both platforms.** Sub-second lag is typical on liquid markets. If any edge exists, it's in illiquid/hands-off markets — which may also have wide bid/ask that eats the edge. Let the data decide.
- **N<30 rule.** Do not draw conclusions about edge presence/absence until at least 30 clean observations per hypothesis.
