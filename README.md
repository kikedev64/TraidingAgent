# TraidingAgent

Codex skill and Python helpers for market research, paper-first Alpaca workflows, and guarded order preparation.

This project does not promise profit. It is designed to make every trading idea explicit, testable, risk-limited, and paper-trading-first before any real-money action is considered.

## What is included

- `SKILL.md`: Codex skill instructions for using `$traiding-agent`.
- `references/`: Alpaca connector notes and trading risk policy.
- `src/traiding_agent/`: Python primitives for config, risk checks, strategy signals, and Alpaca integration.
- `scripts/traiding_agent.py`: local CLI for risk previews.
- `tests/`: unit tests that do not contact Alpaca.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Fill `.env` with Alpaca paper keys. Keep `ALPACA_PAPER=true` unless you deliberately intend to configure live trading.

Preview a local risk decision:

```powershell
python scripts/traiding_agent.py preview --symbol SPY --side buy --notional 1000 --entry 500 --stop 490 --equity 100000
```

Run tests:

```powershell
python -m unittest discover -s tests
```
