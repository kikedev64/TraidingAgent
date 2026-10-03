# Alpaca Connector Reference

Use this reference before changing Alpaca integration code, preparing Alpaca orders, or debugging Alpaca connectivity.

## Official Sources

- Alpaca SDKs and tools: https://docs.alpaca.markets/us/docs/sdks-and-tools
- Alpaca Trading API overview: https://docs.alpaca.markets/us/docs/trading-api
- Alpaca paper trading: https://docs.alpaca.markets/us/docs/paper-trading
- Alpaca-py getting started: https://alpaca.markets/sdks/python/getting_started.html
- Alpaca-py trading docs: https://alpaca.markets/sdks/python/trading.html
- Alpaca-py order requests: https://alpaca.markets/sdks/python/api_reference/trading/requests.html
- Alpaca-py order client: https://alpaca.markets/sdks/python/api_reference/trading/orders.html

## SDK Choice

Use `alpaca-py` for new Python work. The SDK exposes separate clients for trading and market data, and uses request models such as `MarketOrderRequest` and `LimitOrderRequest`. Prefer SDK enums such as `OrderSide`, `TimeInForce`, and `OrderClass` over raw strings.

The `TradingClient` supports paper and live modes through its `paper` flag. This repository defaults to `paper=True`; do not invert that default.

## Environment Variables

Required for API-backed operations:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

Optional:

- `ALPACA_PAPER`: defaults to `true`; live accounts require `false`.
- `TRAIDING_AGENT_ALLOW_LIVE`: defaults to `false`; must be `true` as a second live-mode guard.
- `TRAIDING_AGENT_MAX_POSITION_PCT`: default `0.02`.
- `TRAIDING_AGENT_MAX_TRADE_RISK_PCT`: default `0.005`.
- `TRAIDING_AGENT_MAX_DAILY_LOSS_PCT`: default `0.01`.
- `TRAIDING_AGENT_MAX_OPEN_POSITIONS`: default `10`.

## Connector Expectations

- Keep paper mode as the default. The polled daemon is paper-only; live execution is available only in `stream` after the account flags and the terminal session confirmation are present.
- Paper order submission can be automated after the risk manager approves the order.
- Live order submission requires both live environment flags and explicit terminal confirmation of the displayed session scope.
- Use `client_order_id` for idempotency when submitting orders.
- Prefer bracket or OTO orders when an entry needs attached exits. If the desired order class is not supported for the asset class, do not silently downgrade to an unprotected entry.
- Request the IEX feed explicitly unless the configured account is verified to have SIP entitlement.
- Surface Alpaca API exceptions to the caller with enough context to know whether the order was accepted, rejected, or unknown.

## Paper Trading Caveats

Paper trading is useful for development and workflow testing, but it is still a simulation. Results can diverge from live trading because of fill assumptions, slippage, fees, latency, order queue position, information leakage, market impact, dividends, and data source differences.
