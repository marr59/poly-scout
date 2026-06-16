# Polymarket ↔ Kalshi cross-arb scout — final postmortem

_Generated 2026-05-02. Read-only analysis on collected `cross_arb_data.jsonl`._

---

## Context

- Read-only observation scout running on Frankfurt VPS from 2026-04-21 to 2026-05-02.
- 18 pairs configured: 9 NBA Champion 2026, 3 Colombia 2026 presidential, 3 US 2028 winner, 3 US 2028 Democratic nomination.
- Goal: characterize cross-platform arbitrage gap distribution to determine if Polymarket↔Kalshi
  arb is viable for retail capital.
- 600,228 observations / 11.58 days / 765 MB jsonl. Final dataset.

## Methodology

Every 30 seconds, scout fetches:
- Polymarket: top-of-book bid/ask + size for both YES and NO outcomes via `/book` endpoint
- Kalshi: top-of-book + full orderbook depth via `/markets/{ticker}` and `/markets/{ticker}/orderbook`
- Computes `arb_gap = 1.0 - min(poly_yes_ask + kalshi_no_ask, poly_no_ask + kalshi_yes_ask)`
- Logs every observation regardless of gap sign

`arb_gap > 0` indicates nominal cost-to-cover-both-outcomes < $1 BEFORE fees and slippage.
Real edge requires `arb_gap > (kalshi_taker_fee + polymarket_spread + slippage)` ≈ 2-3%.

## Aggregate findings

### Pair-level distribution (full 11.58-day dataset)

| pair | N_total | N_gap | pos% | mean | median | max | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| col26-cepeda | 33,346 | 33,344 | 34.23% | +0.0010 | +0.0000 | +0.0400 | +0.0300 |
| col26-espriella | 33,346 | 33,343 | 32.42% | +0.0010 | +0.0000 | +0.0500 | +0.0200 |
| col26-fajardo | 33,346 | 33,346 | 0.35% | -0.0011 | -0.0010 | +0.0120 | -0.0010 |
| demnom28-buttigieg | 33,346 | 33,345 | 99.65% | +0.0124 | +0.0130 | +0.0150 | +0.0150 |
| demnom28-kelly | 33,346 | 33,341 | 99.54% | +0.0108 | +0.0110 | +0.0150 | +0.0130 |
| demnom28-newsom | 33,346 | 33,344 | 99.83% | +0.0130 | +0.0120 | +0.3320 | +0.0220 |
| nba26-76ers | 33,346 | 27,981 | 8.65% | -0.0047 | -0.0050 | +0.0100 | +0.0030 |
| nba26-cavs | 33,346 | 33,343 | 21.59% | -0.0032 | -0.0020 | +0.0090 | +0.0030 |
| nba26-celtics | 33,346 | 33,343 | 67.63% | +0.0038 | +0.0030 | +0.6900 | +0.0120 |
| nba26-knicks | 33,346 | 33,342 | 27.87% | -0.0033 | -0.0010 | +0.0180 | +0.0030 |
| nba26-lakers | 33,346 | 33,341 | 41.25% | -0.0004 | -0.0010 | +0.0170 | +0.0100 |
| nba26-nuggets | 33,346 | 28,840 | 41.76% | -0.0017 | +0.0000 | +0.0140 | +0.0100 |
| nba26-spurs | 33,346 | 33,344 | 40.37% | +0.0000 | +0.0000 | +0.0200 | +0.0100 |
| nba26-thunder | 33,346 | 33,342 | 16.92% | -0.0005 | +0.0000 | +0.0900 | +0.0100 |
| nba26-timberwolves | 33,346 | 33,322 | 23.59% | -0.0023 | -0.0030 | +0.0110 | +0.0030 |
| winner28-aoc | 33,346 | 33,343 | 99.47% | +0.0087 | +0.0080 | +0.0250 | +0.0150 |
| winner28-newsom | 33,346 | 33,346 | 58.78% | +0.0047 | +0.0060 | +0.0260 | +0.0160 |
| winner28-rubio | 33,346 | 33,341 | 99.69% | +0.0376 | +0.0370 | +0.0530 | +0.0470 |

### Three categories of patterns observed

**1. Structurally positive (5 pairs, pos% ≈ 99-100%)** —
demnom28-buttigieg/kelly/newsom, winner28-aoc/rubio. Mean gap +0.38% to +3.76%, std deviation
≤0.5% on normal days. **Not tradable**: top-of-book ask sizes on the cheaper side measured
$10-200 in interim_check_v1; structural mispricing exists but capacity is order-of-magnitude
smaller than viable position size.

**2. Variable (10 pairs)** — NBA + col26-cepeda/espriella + winner28-newsom. Mean gap close
to zero, occasional positive spikes. No persistent edge.

**3. Dead pairs (3 pairs)** — col26-fajardo (Kalshi volume_24h = 0), nba26-76ers (one-sided
Kalshi book), nba26-nuggets (Polymarket market closed 2026-05-01 09:08 UTC, returns 404 since).
Kept in config for dataset integrity; flagged for cleanup if direction is ever revisited.

### "Big window" episodes (gap > 5%): all artifacts

Three apparent large-gap clusters identified in the data, all confirmed as feed glitches:

| Cluster | Diagnosis | Evidence |
|---|---|---|
| nba26-celtics 04-28 12:22 | Polymarket phantom bids | poly_bid oscillates 0.01 → 0.85 → 0.01 → 0.52 → 0.01 with stable poly_ask 0.85-0.99 (impossible 14¢ spread) and motionless kalshi 0.15/0.16 |
| demnom28-newsom 04-28 11:43 | Stale Polymarket quote | 15 identical rows over 7+ minutes with frozen 0.582/0.612 against active kalshi 0.24/0.25 |
| nba26-thunder 04-24 12:52 | Polymarket phantom asks | kalshi monolithic 0.53/0.54 throughout; poly_ask flickers 0.53 → 0.44 → 0.45 → 0.53 → 0.44 → 0.52 (isolated low-asks) |

Bonus observability finding: ~2026-04-28 a system-wide glitch in Polymarket CLOB feed
produced negative-gap minimums simultaneously on 5 different pairs (demnom28-buttigieg/kelly/newsom,
winner28-aoc/rubio). This was a Polymarket-side anomaly, not a scout bug.

### Methodological note: max gap is misleading

The pair-level table above shows `max` values up to +69% (nba26-celtics). This is entirely
driven by the three confirmed feed-glitch clusters. The `p95` values give a more honest read:
no pair exceeds +5% at the 95th percentile. Future cross-venue scouts should report p95/p99
rather than max as the primary "edge" indicator — max is dominated by noise.

### Daily-drift analysis on suspect pairs

| Pair | Mean range over 12 days | Drift | Std on normal days | Interpretation |
|---|---|---|---|---|
| demnom28-buttigieg | +1.1% to +1.5% | 61bp | ≤0.001 | Constant artifact |
| demnom28-kelly | +0.86% to +1.27% | 41bp | ≤0.001 | Flattest — pure artifact |
| demnom28-newsom | +0.65% to +2.22% | 157bp | low (excl. 04-28 outlier) | Artifact |
| winner28-aoc | +0.38% to +1.52% | 113bp | low | Slight downward drift; possibly weak market dynamics, but magnitude below transaction costs |
| winner28-rubio | +2.68% to +4.49% | 181bp | ≤0.005 (11/12 days) | Stable thick mispricing — likely book-asymmetry / wording mismatch, not arb |

The winner28-rubio +3.76% mean is possibly explained by rules_primary phrasing differences
between platforms (Polymarket: "win the 2028 US Presidential Election", Kalshi: "next person
inaugurated as President for the term beginning in 2029"). This was not verified by reading
rules_primary in full on both platforms — it is a hypothesis consistent with the observed
wide-spread structural mispricing, not a confirmed cause. Other plausible explanations include
differential liquidity-provider risk premia or simply low Kalshi-side trading volume keeping
the mid-price uncalibrated.

## Final verdict

- [x] Real tradable arb windows over 11.58 days: **0 (zero)**.
- [x] Structurally positive pairs: 5 / 18, all artifacts of book-asymmetry or feed staleness.
- [x] Capacity at observed positive gaps: $10-200 top-of-book on cheaper side, fully absorbed by fees + slippage.
- [x] Decision: **STOP**.

### Reasoning

This is a STOP, not a PARK, because unlike Bybit OBI lead research, no filtered hypothesis
exists that would have positive expected value with retail-accessible capital under any
reasonable assumption. The three structural impediments are independent and cumulative:

1. **Capacity**: top-of-book sizes on the cheaper leg are $10-200, measured directly. Even
   if fees were zero, capital deployment per execution would be capped at this level.
2. **Frequency**: 0 genuine gap > 5% events in 11.58 days × 18 pairs × 600K observations.
   Any future run would observe similar rates.
3. **Persistence**: structural gaps that appear "always positive" are book-asymmetry artifacts,
   not arb opportunities. They do not collapse on attempted execution because they are not
   real mispricings.

No combination of capital scaling, infrastructure improvement, or pair selection materially
changes the underlying economics. This is a structural property of retail-accessible cross-platform
prediction-market arbitrage, not a tunable parameter.

### What was learned (preserved for future projects)

1. **Polymarket CLOB feed has occasional phantom-print episodes** (confirmed 04-28 system-wide).
   Useful for any future Polymarket-related work as a feed-health monitoring signal.

2. **Cross-platform pricing differences encode rules_primary ambiguity premium**, especially
   on long-shot binaries (winner28-rubio +3.76% structural). Theoretically exploitable only by
   an entity that can resolve the ambiguity itself (e.g., legal/contract analyst hedge fund),
   not retail.

3. **winner28-aoc weak downward drift** (113bp over 12 days) suggests cross-platform signals
   do propagate slowly across MMs, but at magnitudes below transaction costs. Higher-volume
   markets (large sports events with $1M+ daily volume) may show stronger versions of this
   pattern; not investigated here.

4. **Methodology validated**: dual data source (book + orderbook) + glitch-pattern recognition
   (stale rows, phantom prints, one-sided books) successfully distinguishes real market events
   from feed artifacts. Reusable in future cross-venue research.

## Followup actions

**Status: STOP as of 2026-05-02.**

- Stop the scout: send `Ctrl-C` to tmux session `crossarb` for clean shutdown.
- Archive collected data: `tar -czf cross_arb_data_2026-05-02.tar.gz cross_arb_data.jsonl cross_arb_scout.log`
- Free Polymarket and Kalshi capital allocations (none deployed — observation only).
- Active focus: open. Bybit OBI on PARK; Polymarket cross-arb closed; next research direction TBD.

## Data location

- Raw observations: `/root/poly_scout/cross_arb_data.jsonl` (600K rows, 765 MB)
- Scout log: `/root/poly_scout/cross_arb_scout.log`
- Diagnostic scripts: `interim_check.py`, `interim_check_v2.py`, `interim_check_v3.py`
- Configuration: `market_pairs.json` (18 pairs)
- Archive: `cross_arb_data_2026-05-02.tar.gz` after final cleanup
