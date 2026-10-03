---
name: traiding-agent
description: Study markets and prepare Alpaca paper-first trading workflows with explicit risk controls; use for market analysis, strategy planning, backtesting support, or guarded Alpaca order preparation, not for guaranteed financial advice.
metadata:
  short-description: Alpaca market research and guarded trading
---

# Traiding Agent

Use this skill when the user wants Codex to study markets, design a trading approach, review an Alpaca setup, prepare a paper-trading run, or build guarded automation around Alpaca.

The skill's north star is capital preservation first, opportunity second. Never promise profit, guaranteed returns, or a "safe" investment outcome. Treat every trade as a hypothesis that can be wrong, and make the invalidation, sizing, and exit conditions explicit before execution.

## Operating Rules

- Default to Alpaca paper trading. Live automation is available only when the user supplies live keys locally, sets `ALPACA_PAPER=false` and `TRAIDING_AGENT_ALLOW_LIVE=true`, and confirms the displayed symbol, strategy, order sizing, and risk limits in the terminal at startup.
- Never switch credentials or account mode on the user's behalf. Do not initiate a live session unless the user explicitly requests it and completes the terminal confirmation.
- Keep credentials out of source files, prompts, logs, and generated reports. Use environment variables for Alpaca keys.
- Prefer Alpaca's official `alpaca-py` SDK for Python integrations. Verify current SDK/API behavior before changing connector code or relying on a specific order feature.
- Use structured request models and enums from the SDK instead of hand-built HTTP payload strings when available.
- Every strategy proposal should include data source, timestamp range, assumptions, position size, stop or invalidation condition, max loss, and what would make the idea invalid.
- Account for paper/live differences: fills, slippage, fees, latency, order queue position, market impact, and data limitations can make live results diverge from simulations.

## Workflow

1. Understand the objective: asset universe, timeframe, risk tolerance, account mode, capital base, and whether the task is research, paper execution, or live execution.
2. Gather evidence from approved data sources. Label stale or incomplete data clearly.
3. Form a testable thesis with an invalidation condition. Avoid vague "buy because it may go up" reasoning.
4. Run or request backtests/walk-forward checks when the decision depends on historical performance. Include slippage and transaction-cost assumptions where practical.
5. Apply the risk policy before any order preview or submission. Read [references/risk-policy.md](references/risk-policy.md) when sizing, reviewing, or executing trades.
6. For Alpaca implementation details, read [references/alpaca-connector.md](references/alpaca-connector.md) before modifying connector code or preparing orders.
7. Produce a plan, preview, or paper-trading action first. Treat live execution as a separate, explicitly authorized step.

## Repository Helpers

This repository includes a Python package under `src/traiding_agent`:

- `config.py` loads environment-based Alpaca and risk settings.
- `risk.py` validates proposed orders against conservative risk limits.
- `strategy.py` contains a simple example strategy interface and moving-average signal.
- `alpaca_connector.py` wraps Alpaca clients and blocks unsafe execution paths.
- `agent.py` combines strategy, risk, and connector primitives for planning workflows.

The CLI `scripts/traiding_agent.py` can preview risk locally, check Alpaca connectivity, run a polled paper loop, stream live IEX trades and minute bars to a terminal dashboard, and show its SQLite audit log. Start with `check`; use `stream --monitor-only` to inspect the streaming dashboard without orders. The `stream` command defaults to paper execution after an approved signal; `--enable-live` requires live account settings and a typed startup confirmation.

## Hard Stops

Stop and ask the user before proceeding if a request requires a risk setting that exceeds the current policy or a jurisdiction-specific legal/tax answer. If the user asks for guaranteed profit, reframe the work as research, paper trading, or risk-managed strategy development.
