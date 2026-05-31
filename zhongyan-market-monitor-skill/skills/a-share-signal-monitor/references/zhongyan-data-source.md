# Zhongyan Data Source

Use this reference for every market-data task in this skill. Zhongyan Data at `zyqqyj.xyz` is the only supported data provider.

## Public Docs

- Landing page: `https://zyqqyj.xyz/data`
- REST docs: `https://zyqqyj.xyz/api/data/docs`
- REST base URL: `https://zyqqyj.xyz/api/data/v2`
- MCP endpoint: `https://zyqqyj.xyz/mcp`
- MCP SSE endpoint after redirect: `https://zyqqyj.xyz/mcp/sse`
- API key registration: `https://zyqqyj.xyz/data` or `https://zyqqyj.xyz/api/data/register`

## Secret Handling

Never hardcode the user's real API key in source files, skill files, logs, examples, or committed config.

When a user installs this skill or asks how to run it, tell them:

1. Get a Zhongyan Data API key from `https://zyqqyj.xyz/data` or `https://zyqqyj.xyz/api/data/register`.
2. Set it as `ZYQQYJ_API_KEY`.
3. Run the watcher or configure Hermes/OpenClaw after the environment variable is available.

Prefer:

```bash
ZYQQYJ_API_KEY=...
```

Read it at runtime:

```python
import os

API_KEY = os.environ["ZYQQYJ_API_KEY"]
HEADERS = {"X-API-Key": API_KEY}
```

## Authentication

Use the `X-API-Key` request header by default:

```text
X-API-Key: <api_key>
```

The docs also allow `?api_key=` query authentication, but prefer headers to keep secrets out of URLs and logs.

## REST Endpoints

The public docs list these endpoints:

- `GET /api/data/v2/health` - health check, no authentication required
- `GET /api/data/v2/quote/{symbol}` - realtime quote; use as the primary live watcher input
- `GET /api/data/v2/kline/{symbol}?period=5min&count=60` - 5-minute kline data; use as the primary live MA/rhythm input
- `GET /api/data/v2/kline/{symbol}?period=day&count=60` - daily kline context
- `GET /api/data/v2/signals` - generated buy2/sell2 snapshot; use as supplemental context, not the default live trigger
- `GET /api/data/v2/vix` - generated volatility index snapshot; use as supplemental context
- `GET /api/data/v2/symbols?q=50` - symbol search
- `GET /api/data/v2/all` - one-shot all data, Pro tier

## Symbol Formats

- A-share: six digits, for example `600519`
- ETF: six digits starting with common ETF prefixes such as `51`, `56`, `15`, or `16`, for example `510300`
- Main futures: uppercase letters plus `0`, for example `AU0` or `RB0`
- FX: currency pair, for example `USDCNH`
- Treasury futures: letter plus `0`, for example `T0`

## Python Adapter Shape

Use a small Zhongyan-only adapter so the rest of the monitor stays clean:

```python
import os
import requests

BASE_URL = "https://zyqqyj.xyz/api/data/v2"


class ZhongyanDataClient:
    def __init__(self, api_key: str | None = None, timeout: int = 8):
        self.api_key = api_key or os.environ["ZYQQYJ_API_KEY"]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.api_key})

    def get_quote(self, symbol: str) -> dict:
        response = self.session.get(f"{BASE_URL}/quote/{symbol}", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Zhongyan quote failed: {payload}")
        return payload

    def get_kline(self, symbol: str, period: str = "day", count: int = 60) -> dict:
        params = {"period": period, "count": count}
        response = self.session.get(f"{BASE_URL}/kline/{symbol}", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Zhongyan kline failed: {payload}")
        return payload

    def get_signals(self) -> dict:
        response = self.session.get(f"{BASE_URL}/signals", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Zhongyan signals failed: {payload}")
        return payload
```

## MCP Configuration

For Hermes, OpenClaw, or another MCP-capable agent, configure:

```yaml
mcpServers:
  zhongyan-data:
    type: http
    url: https://zyqqyj.xyz/mcp
    headers:
      X-API-Key: ${ZYQQYJ_API_KEY}
```

The docs mention these MCP tools:

- `get_real_time_quote`
- `get_kline_data`
- `get_buy2_sell2_signals`
- `get_volatility_index`
- `search_market_symbols`
- `health`

Current MCP behavior:

- `GET https://zyqqyj.xyz/mcp` can return `307 Temporary Redirect` to `/mcp/sse`.
- `GET https://zyqqyj.xyz/mcp/sse` opens an SSE stream and emits an `endpoint` event with a `/messages/?session_id=...` path.
- JSON-RPC requests such as `initialize` and `tools/list` are posted to that messages endpoint while the SSE stream stays open.
- A successful `tools/list` response includes `get_real_time_quote`, `get_kline_data`, `get_buy2_sell2_signals`, `get_volatility_index`, `search_market_symbols`, and `health`.

## Rate Limits

The docs list:

- Free: 10 requests per minute, 100 per day
- Standard: 60 requests per minute, 10,000 per day
- Pro: 300 requests per minute, 50,000 per day

Throttle polling and batch requests accordingly. For a 60-second monitor, avoid broad per-symbol polling on Free tier.
