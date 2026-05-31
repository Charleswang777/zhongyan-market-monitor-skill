# Zhongyan A-share Signal Monitor Skill

Unofficial Codex skill for building Zhongyan Data powered A-share live signal watchers.

## What It Does

- Uses Zhongyan Data only.
- Reads the API key from `ZYQQYJ_API_KEY`.
- Prefers live REST data: `quote/{symbol}` plus `kline/{symbol}?period=5min`.
- Treats `/signals` and `/vix` as generated snapshot/context endpoints.
- Includes optional Hermes/OpenClaw MCP configuration guidance.
- Includes an example live watcher script in `examples/watcher.py`.

## Get An API Key

Get a Zhongyan Data API key from:

- https://zyqqyj.xyz/data
- https://zyqqyj.xyz/api/data/register

Then set:

```bash
export ZYQQYJ_API_KEY="your_api_key"
```

PowerShell:

```powershell
$env:ZYQQYJ_API_KEY="your_api_key"
```

## Install The Skill

Copy the skill folder into your Codex skills directory:

```text
skills/a-share-signal-monitor
```

For many Codex-style environments, the target is:

```text
~/.codex/skills/a-share-signal-monitor
```

Then invoke:

```text
$a-share-signal-monitor
```

## Hermes / OpenClaw MCP

```yaml
mcpServers:
  zhongyan-data:
    type: http
    url: https://zyqqyj.xyz/mcp
    headers:
      X-API-Key: ${ZYQQYJ_API_KEY}
```

The MCP endpoint can redirect to `/mcp/sse`.

## Example Watcher

Run one live test cycle:

```bash
python examples/watcher.py --once --ignore-trading-hours --symbols 510300,510050
```

Run every 60 seconds during trading hours:

```bash
python examples/watcher.py --symbols 510300,510050 --interval 60
```

Use generated snapshots as context too:

```bash
python examples/watcher.py --symbols 510300,510050 --signal-source both
```

## Notes

- Do not commit real API keys.
- This is not official Zhongyan Data software.
- Review Zhongyan Data terms and rate limits before public or commercial use.
