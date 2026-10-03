"""CLI helper for local Traiding Agent risk previews."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
import logging
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from traiding_agent import ProposedOrder, RiskConfig, RiskManager  # noqa: E402
from traiding_agent.alpaca_connector import AlpacaConnector  # noqa: E402
from traiding_agent.config import AlpacaConfig  # noqa: E402
from traiding_agent.daemon import DaemonConfig, DecisionStore, PaperTradingDaemon  # noqa: E402
from traiding_agent.streaming import RealtimeTradingAgent, StreamConfig  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared in pyproject.toml
    load_dotenv = None


def _decimal(value: str) -> Decimal:
    return Decimal(value)


def preview(args: argparse.Namespace) -> int:
    order = ProposedOrder(
        symbol=args.symbol,
        side=args.side,
        notional=args.notional,
        qty=args.qty,
        entry_price=args.entry,
        limit_price=args.limit,
        stop_loss_price=args.stop,
        take_profit_price=args.take_profit,
        order_type=args.order_type,
        intent=args.intent,
    )
    decision = RiskManager(RiskConfig.from_env()).evaluate_order(
        order,
        equity=args.equity,
        current_symbol_exposure=args.current_exposure,
        existing_open_positions=args.open_positions,
        day_realized_pnl=args.day_pnl,
    )
    payload = asdict(decision)
    payload["approved"] = decision.approved
    print(json.dumps(payload, default=str, indent=2))
    return 0 if decision.approved else 2


def _connector() -> AlpacaConnector:
    config = AlpacaConfig.from_env()
    return AlpacaConnector(config, risk_config=RiskConfig.from_env())


def check(_: argparse.Namespace) -> int:
    connector = _connector()
    account = connector.get_account()
    clock = connector.get_clock()
    bars = connector.get_stock_bars(["SPY"], timeframe="1Day", limit=1)
    latest_bars = bars.data.get("SPY", [])
    print(json.dumps({
        "connected": True,
        "paper": connector.config.paper,
        "account_status": str(account.status),
        "equity": str(account.equity),
        "market_open": bool(clock.is_open),
        "market_time": str(clock.timestamp),
        "market_data_available": bool(latest_bars),
        "latest_spy_bar": str(max(latest_bars, key=lambda bar: bar.timestamp).timestamp)
        if latest_bars else None,
    }, indent=2))
    return 0 if latest_bars else 1


def run_daemon(args: argparse.Namespace) -> int:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env", override=False)
    Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(args.log_file, encoding="utf-8")],
    )
    config = DaemonConfig(
        symbols=tuple(args.symbols.split(",")),
        timeframe=args.timeframe,
        interval_seconds=args.interval_seconds,
        database_path=Path(args.database),
        execute_paper_orders=not args.monitor_only,
    )
    PaperTradingDaemon(_connector(), config=config).run_forever(once=args.once)
    return 0


def run_stream(args: argparse.Namespace) -> int:
    connector = _connector()
    risk_config = RiskConfig.from_env()
    live_confirmed = False
    symbols = tuple(args.symbols.split(","))
    if not connector.config.paper:
        if not args.enable_live:
            raise PermissionError("Live credentials require the explicit --enable-live flag")
        if not connector.config.allow_live_trading:
            raise PermissionError("Set TRAIDING_AGENT_ALLOW_LIVE=true before enabling live mode")
        if args.monitor_only:
            raise ValueError("Use paper credentials for monitor-only mode")
        phrase = f"ENABLE LIVE {','.join(symbol.strip().upper() for symbol in symbols)}"
        print("LIVE MODE SCOPE")
        print(f"Symbols: {', '.join(symbol.strip().upper() for symbol in symbols)}")
        print(f"Strategy: {args.short_window}/{args.long_window} moving-average crossover on 1-minute bars")
        print(f"Max symbol exposure: {risk_config.max_position_pct:.2%} of equity")
        print(f"Max planned trade loss: {risk_config.max_trade_risk_pct:.2%} of equity")
        print(f"Daily loss limit: {risk_config.max_daily_loss_pct:.2%}; short selling: disabled")
        print("Opening orders are market orders with an attached 2% stop; sells only close long positions.")
        if not sys.stdin.isatty() or input(f"Type '{phrase}' to authorize this live session: ").strip() != phrase:
            raise PermissionError("Live session was not confirmed")
        live_confirmed = True
    elif args.enable_live:
        raise ValueError("--enable-live requires ALPACA_PAPER=false and live API keys")

    if args.test_seconds is not None and not args.monitor_only:
        raise ValueError("--test-seconds requires --monitor-only")

    config = StreamConfig(
        symbols=symbols,
        short_window=args.short_window,
        long_window=args.long_window,
        execute_orders=not args.monitor_only,
        live_session_confirmed=live_confirmed,
        database_path=Path(args.database),
    )
    RealtimeTradingAgent(
        connector,
        config=config,
        risk_config=risk_config,
    ).start(test_seconds=args.test_seconds)
    return 0


def status(args: argparse.Namespace) -> int:
    rows = DecisionStore(Path(args.database)).recent(args.limit)
    print(json.dumps(rows, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Traiding Agent local utilities")
    subparsers = parser.add_subparsers(required=True)

    preview_parser = subparsers.add_parser("preview", help="Preview local risk checks")
    preview_parser.add_argument("--symbol", required=True)
    preview_parser.add_argument("--side", choices=["buy", "sell"], required=True)
    preview_parser.add_argument("--notional", type=_decimal)
    preview_parser.add_argument("--qty", type=_decimal)
    preview_parser.add_argument("--entry", type=_decimal)
    preview_parser.add_argument("--limit", type=_decimal)
    preview_parser.add_argument("--stop", type=_decimal)
    preview_parser.add_argument("--take-profit", type=_decimal)
    preview_parser.add_argument("--order-type", choices=["market", "limit"], default="market")
    preview_parser.add_argument("--intent", choices=["open", "close"], default="open")
    preview_parser.add_argument("--equity", type=_decimal, required=True)
    preview_parser.add_argument("--current-exposure", type=_decimal, default=Decimal("0"))
    preview_parser.add_argument("--open-positions", type=int, default=0)
    preview_parser.add_argument("--day-pnl", type=_decimal, default=Decimal("0"))
    preview_parser.set_defaults(func=preview)

    check_parser = subparsers.add_parser("check", help="Check paper account and market clock")
    check_parser.set_defaults(func=check)

    run_parser = subparsers.add_parser("run", help="Run the paper-only market monitoring loop")
    run_parser.add_argument("--symbols", default="SPY", help="Comma-separated stock symbols")
    run_parser.add_argument(
        "--timeframe", choices=["1Min", "5Min", "15Min", "1Hour", "1Day"], default="1Day"
    )
    run_parser.add_argument("--interval-seconds", type=int, default=300)
    run_parser.add_argument("--database", default="data/traiding_agent.sqlite3")
    run_parser.add_argument("--log-file", default="logs/traiding_agent.log")
    run_parser.add_argument("--monitor-only", action="store_true")
    run_parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit")
    run_parser.set_defaults(func=run_daemon)

    stream_parser = subparsers.add_parser(
        "stream", help="Show a live Alpaca market dashboard and process minute-bar signals"
    )
    stream_parser.add_argument("--symbols", default="SPY", help="Comma-separated stock symbols")
    stream_parser.add_argument("--short-window", type=int, default=20)
    stream_parser.add_argument("--long-window", type=int, default=50)
    stream_parser.add_argument("--database", default="data/traiding_agent.sqlite3")
    stream_parser.add_argument("--monitor-only", action="store_true", help="Display signals without orders")
    stream_parser.add_argument("--enable-live", action="store_true", help="Enable the guarded live session")
    stream_parser.add_argument("--test-seconds", type=int, help="Connect for N seconds; requires monitor-only")
    stream_parser.set_defaults(func=run_stream)

    status_parser = subparsers.add_parser("status", help="Show recent audited decisions")
    status_parser.add_argument("--database", default="data/traiding_agent.sqlite3")
    status_parser.add_argument("--limit", type=int, default=20)
    status_parser.set_defaults(func=status)

    return parser


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env", override=False)
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, PermissionError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
