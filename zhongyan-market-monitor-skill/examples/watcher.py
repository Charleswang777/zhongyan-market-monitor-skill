#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zhongyan Data A-share watcher.

Runtime secret:
  set ZYQQYJ_API_KEY before running this script.

Examples:
  python watcher.py --once --ignore-trading-hours --symbols 510300
  python watcher.py --symbols 510300,510050 --interval 60
  python watcher.py --symbols 510300 --signal-source both
  python watcher.py --symbols 510300 --webhook-url https://example.com/webhook
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any
from urllib import error, parse, request

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9 fallback
    ZoneInfo = None


BASE_URL = "https://zyqqyj.xyz/api/data/v2"
DEFAULT_SYMBOLS = ("510300",)
SENSITIVE_KEYWORDS = ("重组", "出海", "国产替代", "大订单", "政策发文", "突发制裁")


def china_tz():
    if ZoneInfo is not None:
        return ZoneInfo("Asia/Shanghai")
    return timezone(timedelta(hours=8), name="Asia/Shanghai")


CN_TZ = china_tz()


class ApiError(RuntimeError):
    pass


class ZhongyanDataClient:
    """Small Zhongyan REST adapter."""

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: int = 10):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        retries: int = 2,
    ) -> dict[str, Any]:
        query = parse.urlencode(params or {})
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"

        headers = {"Accept": "application/json"}
        if auth:
            headers["X-API-Key"] = self.api_key

        for attempt in range(retries + 1):
            req = request.Request(url, headers=headers, method="GET")
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    charset = resp.headers.get_content_charset() or "utf-8"
                    text = raw.decode(charset, errors="replace")
                    return json.loads(text)
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise ApiError(f"HTTP {exc.code} for {path}: {detail}") from exc
            except error.URLError as exc:
                if attempt >= retries:
                    raise ApiError(f"Network error for {path}: {exc.reason}") from exc
                time.sleep(0.4 * (attempt + 1))
            except json.JSONDecodeError as exc:
                raise ApiError(f"Invalid JSON for {path}: {exc}") from exc
        raise ApiError(f"Unexpected request failure for {path}")

    def health(self) -> dict[str, Any]:
        return self.request_json("health", auth=False)

    def get_quote(self, symbol: str) -> dict[str, Any]:
        payload = self.request_json(f"quote/{symbol}")
        if not payload.get("ok"):
            raise ApiError(f"quote/{symbol} returned not-ok: {payload}")
        return payload

    def get_kline(self, symbol: str, period: str = "day", count: int = 60) -> dict[str, Any]:
        payload = self.request_json(f"kline/{symbol}", {"period": period, "count": count})
        if not payload.get("ok"):
            raise ApiError(f"kline/{symbol} returned not-ok: {payload}")
        return payload

    def get_signals(self) -> dict[str, Any]:
        payload = self.request_json("signals")
        if not payload.get("ok"):
            raise ApiError(f"signals returned not-ok: {payload}")
        return payload

    def get_vix(self) -> dict[str, Any]:
        payload = self.request_json("vix")
        if not payload.get("ok"):
            raise ApiError(f"vix returned not-ok: {payload}")
        return payload

    def search_symbols(self, query: str) -> dict[str, Any]:
        payload = self.request_json("symbols", {"q": query})
        if not payload.get("ok"):
            raise ApiError(f"symbols?q={query} returned not-ok: {payload}")
        return payload


@dataclass
class WatchConfig:
    symbols: tuple[str, ...]
    signal_source: str
    all_signals: bool
    include_sell: bool
    poll_quotes: bool
    kline_count: int
    ma_window: int
    interval: int
    cooldown: int
    timeout: int
    min_change_pct: float
    max_alerts_per_cycle: int
    webhook_url: str | None
    jsonl: bool
    once: bool
    ignore_trading_hours: bool


@dataclass
class WatchState:
    emitted_at: dict[str, datetime] = field(default_factory=dict)


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def is_trading_time(now: datetime | None = None) -> bool:
    now = now or now_cn()
    if now.weekday() >= 5:
        return False
    current = now.time()
    morning = dtime(9, 15) <= current <= dtime(11, 30)
    afternoon = dtime(13, 0) <= current <= dtime(15, 0)
    return morning or afternoon


def parse_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    return symbols or DEFAULT_SYMBOLS


def compact_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def signal_is_buy(text: str) -> bool:
    lower = text.lower()
    return "二买" in text or "buy2" in lower


def signal_is_sell(text: str) -> bool:
    lower = text.lower()
    return "二卖" in text or "sell2" in lower


def summarize_events(row: dict[str, Any]) -> str:
    summary = compact_text(row.get("event_summary"))
    if summary:
        return summary

    events = row.get("event_events") or []
    titles: list[str] = []
    for item in events[:2]:
        if isinstance(item, dict):
            title = compact_text(item.get("title"))
            if title:
                titles.append(title)
    return "、".join(titles) if titles else "中衍数据技术信号"


def fetch_recent_news() -> list[dict[str, Any]]:
    """News hook. Replace this with Cailian Press, Eastmoney, or your own feed."""
    return []


def match_catalysts(event: dict[str, Any], recent_news: list[dict[str, Any]]) -> str:
    builtin = compact_text(event.get("context_news"))
    if builtin:
        return builtin

    name = compact_text(event.get("stock_name"))
    code = compact_text(event.get("stock_code"))
    for news in recent_news:
        text = f"{news.get('title', '')} {news.get('summary', '')}"
        if name and name in text:
            return compact_text(news.get("title"), "命中个股新闻")
        if code and code in text:
            return compact_text(news.get("title"), "命中代码新闻")
        if any(keyword in text for keyword in SENSITIVE_KEYWORDS):
            return compact_text(news.get("title"), "命中敏感催化词")
    return "中衍数据技术/事件信号"


def call_llm_for_analysis(event: dict[str, Any]) -> str:
    """LLM hook. Replace this function with a real model call when needed."""
    reason = compact_text(event.get("reason"))
    catalyst = compact_text(event.get("context_news"))
    direction = compact_text(event.get("event_direction")).lower()

    if "二买" in reason and direction == "bullish":
        return "二买叠加事件，资金试多"
    if "二买" in reason:
        return "回踩企稳，资金试探修复"
    if "快速拉升" in reason or "涨幅" in reason:
        return "价格走强，短线资金抢先手"
    if "跌幅" in reason:
        return "实盘走弱，资金先做防守"
    if "二卖" in reason:
        return "趋势转弱，资金先做风控"
    if catalyst:
        return "资金在试催化的持续性"
    return "信号初现，先看量能确认"


def build_signal_payload(event: dict[str, Any], catalyst: str) -> dict[str, Any]:
    analysis = call_llm_for_analysis({**event, "context_news": catalyst})
    return {
        "type": "a_share_signal",
        "source": "zhongyan-data",
        "created_at": now_cn().isoformat(timespec="seconds"),
        "stock_name": event.get("stock_name") or event.get("stock_code") or "未知标的",
        "stock_code": event.get("stock_code") or "",
        "reason": event.get("reason") or "信号触发",
        "context_news": catalyst,
        "analysis": analysis[:40],
        "evidence": event.get("evidence", {}),
    }


def alert_key(payload: dict[str, Any]) -> str:
    code = payload.get("stock_code", "")
    reason = payload.get("reason", "")
    catalyst = payload.get("context_news", "")[:48]
    return f"{code}|{reason}|{catalyst}"


def should_emit(payload: dict[str, Any], state: WatchState, cooldown_seconds: int) -> bool:
    key = alert_key(payload)
    now = now_cn()
    last = state.emitted_at.get(key)
    if last and (now - last).total_seconds() < cooldown_seconds:
        return False
    state.emitted_at[key] = now
    return True


def send_signal_to_webhook(payload: dict[str, Any], webhook_url: str | None, timeout: int = 8) -> bool:
    if not webhook_url:
        return False

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise ApiError(f"webhook failed with HTTP {resp.status}")
    return True


def format_signal(payload: dict[str, Any]) -> str:
    return (
        "【A股瞬时信号】\n"
        f"- 标的：{payload['stock_name']} ({payload['stock_code']})\n"
        f"- 异动：{payload['reason']}\n"
        f"- 催化：{payload['context_news']}\n"
        f"- 操盘手拆解：{payload['analysis']}"
    )


def extract_signal_events(signals_payload: dict[str, Any], config: WatchConfig) -> list[dict[str, Any]]:
    data = signals_payload.get("data") or {}
    rows = data.get("signals") or []
    if not isinstance(rows, list):
        return []

    wanted = set(config.symbols)
    events: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        code = compact_text(row.get("code") or row.get("symbol")).upper()
        if not config.all_signals and code not in wanted:
            continue

        name = compact_text(row.get("name"), code)
        daily_signal = compact_text(row.get("daily_signal"))
        m5_signal = compact_text(row.get("m5_signal"))
        reason_parts: list[str] = []

        if signal_is_buy(daily_signal):
            reason_parts.append("日线二买")
        if signal_is_buy(m5_signal):
            reason_parts.append("5分钟二买")
        if config.include_sell and signal_is_sell(daily_signal):
            reason_parts.append("日线二卖")
        if config.include_sell and signal_is_sell(m5_signal):
            reason_parts.append("5分钟二卖")

        if not reason_parts:
            continue

        events.append(
            {
                "stock_name": name,
                "stock_code": code,
                "reason": " + ".join(reason_parts),
                "context_news": summarize_events(row),
                "event_direction": compact_text(row.get("event_direction")),
                "evidence": {
                    "daily_signal": daily_signal,
                    "daily_price": row.get("daily_price"),
                    "daily_ema20": row.get("daily_ema20"),
                    "m5_signal": m5_signal,
                    "m5_price": row.get("m5_price"),
                    "m5_ema20": row.get("m5_ema20"),
                    "m5_detail": row.get("m5_detail"),
                    "event_score": row.get("event_score"),
                    "event_summary": row.get("event_summary"),
                    "signals_timestamp": data.get("timestamp"),
                },
            }
        )

    return events


def extract_symbol_names(signals_payload: dict[str, Any]) -> dict[str, str]:
    data = signals_payload.get("data") or {}
    rows = data.get("signals") or []
    names: dict[str, str] = {}
    if not isinstance(rows, list):
        return names
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = compact_text(row.get("code") or row.get("symbol")).upper()
        name = compact_text(row.get("name"))
        if code and name:
            names[code] = name
    return names


def extract_search_name(symbols_payload: dict[str, Any], symbol: str) -> str:
    rows = symbols_payload.get("symbols") or symbols_payload.get("data") or []
    if not isinstance(rows, list):
        return ""
    wanted = symbol.upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = compact_text(row.get("code") or row.get("symbol")).upper()
        if code == wanted:
            return compact_text(row.get("name"))
    return ""


def extract_quote_event(
    quote_payload: dict[str, Any],
    min_change_pct: float,
    include_sell: bool,
    known_name: str = "",
) -> dict[str, Any] | None:
    data = quote_payload.get("data") or {}
    code = compact_text(quote_payload.get("symbol"))
    name = compact_text(data.get("name") or known_name, code)
    change_pct = data.get("change_pct")
    try:
        change = float(change_pct)
    except (TypeError, ValueError):
        return None

    if change >= min_change_pct:
        reason = f"日内涨幅达{min_change_pct:g}%"
    elif include_sell and change <= -min_change_pct:
        reason = f"日内跌幅达{min_change_pct:g}%"
    else:
        return None

    return {
        "stock_name": name,
        "stock_code": code,
        "reason": reason,
        "context_news": "中衍实时行情触发阈值",
        "evidence": {
            "price": data.get("price"),
            "change_pct": change,
            "change_amount": data.get("change_amount"),
            "open": data.get("open"),
            "high": data.get("high"),
            "low": data.get("low"),
            "volume": data.get("volume"),
            "amount": data.get("amount"),
            "quote_time": data.get("time"),
        },
    }


def normalize_kline_row(row: dict[str, Any]) -> dict[str, Any] | None:
    close = to_float(row.get("close"))
    high = to_float(row.get("high"))
    low = to_float(row.get("low"))
    open_ = to_float(row.get("open"))
    if close is None or high is None or low is None or open_ is None:
        return None
    return {
        "time": row.get("day") or row.get("date") or row.get("time"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": to_float(row.get("volume")),
    }


def extract_live_kline_event(
    symbol: str,
    quote_payload: dict[str, Any],
    kline_payload: dict[str, Any],
    config: WatchConfig,
    known_name: str = "",
) -> dict[str, Any] | None:
    rows = kline_payload.get("data") or []
    if not isinstance(rows, list):
        return None

    normalized = [item for row in rows if isinstance(row, dict) for item in [normalize_kline_row(row)] if item]
    if len(normalized) < config.ma_window:
        return None

    closes = [row["close"] for row in normalized]
    latest = normalized[-1]
    previous = normalized[-2] if len(normalized) >= 2 else normalized[-1]
    ma = sum(closes[-config.ma_window :]) / config.ma_window
    previous_ma = sum(closes[-config.ma_window - 1 : -1]) / config.ma_window if len(closes) > config.ma_window else ma

    quote_data = quote_payload.get("data") or {}
    name = compact_text(quote_data.get("name") or known_name, symbol)
    change = to_float(quote_data.get("change_pct"))
    reason = ""

    # This is a live candidate rule based on current 5-minute bars. It is not a
    # replacement for Zhongyan's generated snapshot signal; it is a realtime trigger.
    if latest["low"] <= ma <= latest["close"] and latest["close"] >= latest["open"]:
        reason = "实盘5分钟二买候选"
    elif config.include_sell and latest["high"] >= ma >= latest["close"] and latest["close"] <= latest["open"]:
        reason = "实盘5分钟二卖候选"
    elif change is not None and change >= config.min_change_pct:
        reason = f"实盘涨幅达{config.min_change_pct:g}%"
    elif config.include_sell and change is not None and change <= -config.min_change_pct:
        reason = f"实盘跌幅达{config.min_change_pct:g}%"

    if not reason:
        return None

    return {
        "stock_name": name,
        "stock_code": symbol,
        "reason": reason,
        "context_news": "中衍实盘quote+5分钟K线触发",
        "evidence": {
            "quote_price": quote_data.get("price"),
            "quote_change_pct": change,
            "quote_volume": quote_data.get("volume"),
            "quote_source": quote_data.get("source"),
            "kline_period": kline_payload.get("period"),
            "kline_time": latest["time"],
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "close": latest["close"],
            "volume": latest["volume"],
            "ma_window": config.ma_window,
            "ma": round(ma, 4),
            "previous_close": previous["close"],
            "previous_ma": round(previous_ma, 4),
        },
    }


def emit_payload(payload: dict[str, Any], config: WatchConfig) -> None:
    print(format_signal(payload), flush=True)
    if config.jsonl:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    if config.webhook_url:
        send_signal_to_webhook(payload, config.webhook_url, timeout=config.timeout)


def run_cycle(client: ZhongyanDataClient, config: WatchConfig, state: WatchState) -> int:
    recent_news = fetch_recent_news()
    events: list[dict[str, Any]] = []
    symbol_names: dict[str, str] = {}

    if config.signal_source in ("snapshot", "both"):
        try:
            signals_payload = client.get_signals()
            symbol_names = extract_symbol_names(signals_payload)
            events.extend(extract_signal_events(signals_payload, config))
        except ApiError as exc:
            print(f"[WARN] signals fetch failed: {exc}", file=sys.stderr, flush=True)
    elif not config.all_signals:
        for symbol in config.symbols:
            try:
                name = extract_search_name(client.search_symbols(symbol), symbol)
                if name:
                    symbol_names[symbol] = name
            except ApiError:
                pass

    if config.signal_source in ("live", "both") and config.all_signals:
        print("[WARN] --all-signals only applies to /signals snapshots; live mode needs explicit --symbols.", file=sys.stderr, flush=True)

    if config.signal_source in ("live", "both") and not config.all_signals:
        for symbol in config.symbols:
            try:
                quote_payload = client.get_quote(symbol)
                kline_payload = client.get_kline(symbol, period="5min", count=config.kline_count)
                live_event = extract_live_kline_event(
                    symbol,
                    quote_payload,
                    kline_payload,
                    config,
                    known_name=symbol_names.get(symbol, ""),
                )
                if live_event:
                    events.append(live_event)
            except ApiError as exc:
                print(f"[WARN] live fetch failed for {symbol}: {exc}", file=sys.stderr, flush=True)

    if config.poll_quotes and not config.all_signals and config.signal_source in ("snapshot", "both"):
        for symbol in config.symbols:
            try:
                quote_payload = client.get_quote(symbol)
                quote_event = extract_quote_event(
                    quote_payload,
                    config.min_change_pct,
                    config.include_sell,
                    known_name=symbol_names.get(symbol, ""),
                )
                if quote_event:
                    events.append(quote_event)
            except ApiError as exc:
                print(f"[WARN] quote fetch failed for {symbol}: {exc}", file=sys.stderr, flush=True)

    emitted = 0
    for event in events:
        catalyst = match_catalysts(event, recent_news)
        payload = build_signal_payload(event, catalyst)
        if not should_emit(payload, state, config.cooldown):
            continue
        try:
            emit_payload(payload, config)
            emitted += 1
        except Exception as exc:  # Keep the watcher alive if push fails.
            print(f"[WARN] emit failed for {payload.get('stock_code')}: {exc}", file=sys.stderr, flush=True)
        if emitted >= config.max_alerts_per_cycle:
            break

    if emitted == 0:
        print(f"[{now_cn().isoformat(timespec='seconds')}] no new signal", flush=True)
    return emitted


def sleep_until_next_open(interval: int) -> None:
    now = now_cn()
    print(f"[{now.isoformat(timespec='seconds')}] outside trading hours; sleeping {interval}s", flush=True)
    time.sleep(interval)


def parse_args(argv: list[str]) -> WatchConfig:
    parser = argparse.ArgumentParser(description="A-share signal watcher using Zhongyan Data REST API.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols, default: 510300")
    parser.add_argument(
        "--signal-source",
        choices=("live", "snapshot", "both"),
        default="live",
        help="live uses quote+5min kline; snapshot uses /signals; both combines them.",
    )
    parser.add_argument("--all-signals", action="store_true", help="Scan all rows returned by /signals snapshot.")
    parser.add_argument("--include-sell", action="store_true", help="Also emit sell-side signals and negative quote moves.")
    parser.add_argument("--no-quotes", dest="poll_quotes", action="store_false", help="Do not add quote-threshold events in snapshot/both mode.")
    parser.add_argument("--kline-count", type=int, default=60, help="5-minute kline bars to fetch for live mode.")
    parser.add_argument("--ma-window", type=int, default=20, help="Moving-average window for live 5-minute candidate signals.")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds.")
    parser.add_argument("--cooldown", type=int, default=900, help="Deduplicate identical alerts for this many seconds.")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout in seconds.")
    parser.add_argument("--min-change-pct", type=float, default=1.0, help="Quote alert threshold for absolute day change.")
    parser.add_argument("--max-alerts-per-cycle", type=int, default=20, help="Hard cap alerts per cycle.")
    parser.add_argument("--webhook-url", default=os.getenv("WATCHER_WEBHOOK_URL"), help="Optional webhook URL.")
    parser.add_argument("--jsonl", action="store_true", help="Also print each payload as JSONL.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--ignore-trading-hours", action="store_true", help="Run even outside A-share trading time.")
    args = parser.parse_args(argv)
    ma_window = max(2, args.ma_window)

    return WatchConfig(
        symbols=parse_symbols(args.symbols),
        signal_source=args.signal_source,
        all_signals=args.all_signals,
        include_sell=args.include_sell,
        poll_quotes=args.poll_quotes,
        kline_count=max(ma_window + 1, args.kline_count),
        ma_window=ma_window,
        interval=max(5, args.interval),
        cooldown=max(0, args.cooldown),
        timeout=max(1, args.timeout),
        min_change_pct=max(0.0, args.min_change_pct),
        max_alerts_per_cycle=max(1, args.max_alerts_per_cycle),
        webhook_url=args.webhook_url,
        jsonl=args.jsonl,
        once=args.once,
        ignore_trading_hours=args.ignore_trading_hours,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv or sys.argv[1:])
    api_key = os.getenv("ZYQQYJ_API_KEY")
    if not api_key:
        print("Missing ZYQQYJ_API_KEY environment variable.", file=sys.stderr)
        return 2

    client = ZhongyanDataClient(api_key=api_key, timeout=config.timeout)
    state = WatchState()

    while True:
        if config.ignore_trading_hours or is_trading_time():
            run_cycle(client, config, state)
            if config.once:
                return 0
            time.sleep(config.interval)
        else:
            if config.once:
                print(f"[{now_cn().isoformat(timespec='seconds')}] outside trading hours; use --ignore-trading-hours to test now")
                return 0
            sleep_until_next_open(config.interval)


if __name__ == "__main__":
    raise SystemExit(main())
