# A-share Monitoring Script Spec

Use this reference when implementing or modifying an A-share intraday multimodal data monitor.

## Role

Act as a senior financial engineering specialist with A-share quantitative trading, NLP, and Python production experience.

## Core Goal

Write a stable, efficient Python script that polls Zhongyan Data realtime quote and 5-minute kline data. Generate a structured trading signal from live price/K-line evidence first, and use Zhongyan generated signals, volatility, and event fields as supplemental context.

## Technical Requirements

- Python 3.10+
- Zhongyan Data only. Use REST base URL `https://zyqqyj.xyz/api/data/v2`.
- Require `ZYQQYJ_API_KEY` at runtime. If it is missing, tell the user to get an API key at `https://zyqqyj.xyz/data` or `https://zyqqyj.xyz/api/data/register`.
- Use `X-API-Key` request-header authentication. Do not hardcode real keys.
- Use `schedule` or `asyncio` for repeated polling.
- Poll every 60 seconds during trading time.
- Provide `send_signal_to_webhook(payload)` as the future push-channel integration point.

## Data Collection

Live market monitor:

- Poll Zhongyan `GET /quote/{symbol}` for configured symbols on each cycle.
- Poll Zhongyan `GET /kline/{symbol}?period=5min&count=60` on each cycle to evaluate current 5-minute bars.
- Use live quote threshold moves and MA20 pullback/reclaim or fail-back rules as realtime trigger candidates.
- Use `GET /signals` only when the user asks for generated buy2/sell2 snapshots or when `--signal-source snapshot/both` behavior is desired.
- Optionally poll `GET /vix`, `GET /kline/{symbol}?period=day`, and `GET /symbols?q=` when the user asks for volatility, historical context, or symbol search.

News and catalyst monitor:

- Use Zhongyan fields such as `event_summary`, `event_events`, `event_score`, `event_direction`, and `event_expected` as the built-in catalyst context.
- Do not add third-party news sources by default. Add external news only if the user explicitly asks to extend beyond Zhongyan Data.
- Store any catalyst/event rows with timestamps so the script can reason about recency when the data includes dates or times.

## Cross-validation Algorithm

Emit an effective signal only when both sides match:

1. A live Zhongyan quote or 5-minute kline row triggers a configured condition, such as quote threshold movement or MA20 reclaim/fail-back.
2. Zhongyan event fields, optional generated snapshots, or user-provided catalyst keywords supply context for the stock, ETF, index, sector, or macro event.

Sensitive catalyst keywords include:

- 重组
- 出海
- 国产替代
- 大订单
- 政策发文
- 突发制裁

If the live quote/K-line evidence does not pass the user's configured threshold or MA rule, treat it as noise and filter it out.

## LLM Analysis Hook

When a signal is triggered:

1. Assemble stock metadata, anomaly reason, and matched catalyst news.
2. Call `call_llm_for_analysis(stock, news)`.
3. Require a concise trader-style Chinese interpretation, ideally 30 characters or fewer and never more than 40 Chinese characters unless the user changes the requirement.

## Robustness Requirements

- Wrap every network request and data-source call with `try-except`.
- Add explicit request timeouts.
- Keep the process alive when one API fails.
- Avoid duplicate alerts for the same stock and catalyst within a short cooldown window.
- Run only during A-share trading windows: 09:15-11:30 and 13:00-15:00.
- Sleep outside trading hours instead of repeatedly hammering APIs.

## Expected Code Shape

Prefer clear modules/functions:

- `is_trading_time(now)`
- `fetch_zhongyan_quote(symbol)`
- `fetch_zhongyan_kline(symbol, period="5min")`
- `detect_live_signal(quote, kline)`
- `fetch_zhongyan_signals()`
- `fetch_zhongyan_vix()`
- `match_catalysts(stock_event, recent_news)`
- `call_llm_for_analysis(stock, news)`
- `send_signal_to_webhook(payload)`
- `build_signal_payload(event, news, analysis)`
- `main_loop()` or async equivalent

For Chinese users, include detailed but useful Chinese comments around data access, matching logic, deduplication, and failure handling.
