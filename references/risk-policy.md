# Risk Policy

Use this reference before sizing, approving, or submitting any trade.

## Defaults

- Paper trading first.
- Max single-symbol exposure: `2%` of account equity.
- Max single-trade planned loss: `0.5%` of account equity.
- Max daily realized loss: `1%` of account equity.
- Max open positions: `10`.
- Require a stop or explicit invalidation level for opening risk.
- No leverage, margin expansion, short selling, options, or crypto-specific assumptions unless the user explicitly requests them and accepts the added risk.

## Required Trade Thesis

Every proposed trade should state:

- Symbol and asset class.
- Direction and order type.
- Timeframe and expected holding period.
- Entry logic.
- Stop or invalidation level.
- Take-profit or exit review logic.
- Maximum planned loss in account currency and percent of equity.
- Data source and data timestamp range.
- Main reason the trade may fail.

## Approval Logic

Block or revise a proposed order when:

- It exceeds position, trade-risk, daily-loss, or open-position limits.
- It lacks an exit or invalidation condition for a new position.
- It depends on stale, missing, or unverified market data.
- It relies on a backtest that ignores survivorship bias, transaction costs, slippage, or liquidity constraints.
- The order class cannot carry the promised protection, such as a stop-loss leg, for the target asset.
- The user is requesting guaranteed profit or risk-free trading.

## Reporting

When presenting a plan, lead with the risk budget and invalidation condition. Profit targets are secondary. If confidence is uncertain, say so directly and prefer a smaller paper-trading experiment.
