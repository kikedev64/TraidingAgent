# TraidingAgent

Codex skill and Python helpers for market research, paper-first Alpaca workflows, and guarded order preparation.

No strategy can guarantee profit. This agent runs only against Alpaca paper trading and applies explicit position, trade-loss, and daily-loss limits.

## What is included

- `SKILL.md`: Codex skill instructions for using `$traiding-agent`.
- `references/`: Alpaca connector notes and trading risk policy.
- `src/traiding_agent/`: Python primitives for config, risk checks, strategy signals, and Alpaca integration.
- `scripts/traiding_agent.py`: local CLI for risk previews.
- `src/traiding_agent/daemon.py`: continuous market polling, paper execution, and SQLite audit log.
- `src/traiding_agent/streaming.py`: IEX trade/minute-bar stream and visible terminal dashboard.
- `tests/`: unit tests that do not contact Alpaca.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Put Alpaca paper keys in `.env`. Live trading is rejected by the connector even if a live flag is set.

Check the account connection without placing orders:

```powershell
python scripts/traiding_agent.py check
```

Start the hourly-bar paper agent (Ctrl+C stops it):

```powershell
python scripts/traiding_agent.py run --symbols SPY,QQQ
```

To observe signals without submitting paper orders, add `--monitor-only`. The loop waits while the market is closed, evaluates only closed and recent bars, ignores symbols with an existing position or open order, and stores decisions in `data/traiding_agent.sqlite3`. Logs go to `logs/traiding_agent.log`.

Inspect recent decisions:

```powershell
python scripts/traiding_agent.py status
```

Run the live dashboard and paper execution using one-minute bars:

```powershell
python scripts/traiding_agent.py stream --symbols SPY
```

To display live prices and signals without sending orders:

```powershell
python scripts/traiding_agent.py stream --symbols SPY --monitor-only
```

Live-account execution is disabled by default. It requires live Alpaca keys in `.env`, `ALPACA_PAPER=false`, `TRAIDING_AGENT_ALLOW_LIVE=true`, and the exact terminal confirmation printed at startup. The strategy uses 20/50 one-minute moving-average crosses, buys long only with a 2% stop, and exits long positions on a bearish cross. Default limits cap symbol exposure at 2% of equity, planned loss at 0.5% per trade, and daily loss at 1%. Press Ctrl+C to stop the visible stream.

Preview a local risk decision:

```powershell
python scripts/traiding_agent.py preview --symbol SPY --side buy --notional 1000 --entry 500 --stop 490 --equity 100000
```

Run tests:

```powershell
python -m unittest discover -s tests
```
