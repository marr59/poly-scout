# Poly Scout — Project Rules

## Что это
poly_scout.py — автономный скрипт-наблюдатель. Логирует временной лаг между движением цены на Bybit и реакцией Polymarket 15-min/5-min Up/Down рынков. НЕ торгует.

## Правила

1. НЕЗАВИСИМОСТЬ: poly_scout.py не импортирует и не зависит от bot.py (Bybit бот в ~/). Это отдельный проект.

2. NO TRADE: скрипт только наблюдает. Никаких ордеров, ключей, приватных ключей, кошельков.

3. POLYMARKET API: Gamma API (https://gamma-api.polymarket.com) — публичный, без аутентификации. CLOB API (https://clob.polymarket.com) — ордербук читается без ключей. Поля clobTokenIds, outcomes, outcomePrices приходят как JSON-строки — всегда json.loads() если isinstance(str).

4. BYBIT WS: wss://stream.bybit.com/v5/public/spot, подписка на tickers.BTCUSDT и tickers.ETHUSDT. ROC считается за скользящее окно 60 сек, семплирование 1 раз/сек.

5. РЫНКИ: slug формат: {coin}-updown-15m-{unix_ts} и {coin}-updown-5m-{unix_ts}. Token ID меняется каждые 15/5 минут при создании нового рынка. Скрипт должен переоткрывать рынки каждые ~3 мин.

6. LOG FORMAT: poly_scout_log.jsonl — один JSON на строку. Обязательные поля: ts_signal, ts_poly_react (или null), lag_ms (или null), coin, direction, roc_pct, bybit_price, poly_midpoint_before, poly_midpoint_after, poly_market_id, poly_slug, poly_time_remaining_sec, poly_best_bid, poly_best_ask, poly_liquidity_at_signal.

7. N<30 — НЕТ ВЕРДИКТОВ: не делать выводов и не менять параметры пока нет минимум 30 сигналов.

8. RETRY: все HTTP-запросы с exponential backoff (5 попыток). WebSocket с auto-reconnect.

9. SHUTDOWN: graceful по SIGINT и SIGTERM.

10. БУДУЩЕЕ: после сбора данных этот скрипт станет основой для polybot.py (торговый бот Polymarket). Signal service будет общим с bot.py через Redis/файл.

11. MARKET DISCOVERY: рынки ищутся по slug через вычисление timestamp, НЕ через order/limit. Slug = {coin}-updown-{interval}m-{end_unix}. end_unix = ((now // interval_sec) + 1) * interval_sec. Если remaining <= 30 сек — берём следующий интервал.

12. SIGNAL TYPES: ROC (60s window, threshold 0.20%) и TICK_SPIKE (single tick >= 0.10%, cooldown 30s). Оба публикуют в pending и логируются одинаково.

13. OBI_CROSS: ордербук Bybit orderbook.1 (top-1 level), OBI threshold ±0.30, скользящее среднее 5с для подтверждения, cooldown 30с. Самый быстрый сигнал — реагирует до движения цены.
