"""CLI helper for local Traiding Agent risk previews."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from traiding_agent import ProposedOrder, RiskConfig, RiskManager  # noqa: E402


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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
