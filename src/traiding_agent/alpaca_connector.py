"""Guarded Alpaca connector.

The module imports Alpaca SDK objects lazily so unit tests and risk previews can run
without installed credentials or network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .config import AlpacaConfig, RiskConfig
from .risk import ProposedOrder, RiskDecision, RiskManager


@dataclass(frozen=True)
class OrderPreview:
    order: ProposedOrder
    decision: RiskDecision
    paper: bool
    live_enabled: bool


class AlpacaConnector:
    """Small wrapper around Alpaca trading operations with risk gates."""

    def __init__(
        self,
        config: AlpacaConfig,
        *,
        risk_config: RiskConfig | None = None,
        trading_client: Any | None = None,
    ) -> None:
        self.config = config
        self.risk_manager = RiskManager(risk_config)
        self._trading_client = trading_client

    @classmethod
    def from_env(cls) -> "AlpacaConnector":
        return cls(AlpacaConfig.from_env())

    @property
    def trading_client(self) -> Any:
        if self._trading_client is None:
            from alpaca.trading.client import TradingClient

            self._trading_client = TradingClient(
                self.config.api_key,
                self.config.secret_key,
                paper=self.config.paper,
                raw_data=self.config.raw_data,
            )
        return self._trading_client

    def get_account(self) -> Any:
        return self.trading_client.get_account()

    def get_all_positions(self) -> Any:
        return self.trading_client.get_all_positions()

    def preview_order(
        self,
        order: ProposedOrder,
        *,
        equity: Decimal,
        current_symbol_exposure: Decimal = Decimal("0"),
        existing_open_positions: int = 0,
        day_realized_pnl: Decimal = Decimal("0"),
    ) -> OrderPreview:
        decision = self.risk_manager.evaluate_order(
            order,
            equity=equity,
            current_symbol_exposure=current_symbol_exposure,
            existing_open_positions=existing_open_positions,
            day_realized_pnl=day_realized_pnl,
        )
        return OrderPreview(
            order=order,
            decision=decision,
            paper=self.config.paper,
            live_enabled=self.config.allow_live_trading,
        )

    def submit_order(
        self,
        order: ProposedOrder,
        *,
        equity: Decimal,
        current_symbol_exposure: Decimal = Decimal("0"),
        existing_open_positions: int = 0,
        day_realized_pnl: Decimal = Decimal("0"),
        client_order_id: str | None = None,
        user_confirmed_live: bool = False,
    ) -> Any:
        self.config.assert_execution_allowed(user_confirmed_live=user_confirmed_live)
        preview = self.preview_order(
            order,
            equity=equity,
            current_symbol_exposure=current_symbol_exposure,
            existing_open_positions=existing_open_positions,
            day_realized_pnl=day_realized_pnl,
        )
        preview.decision.raise_if_rejected()
        order_data = self._to_alpaca_order_request(
            order,
            client_order_id=client_order_id or f"traiding-agent-{uuid4()}",
        )
        return self.trading_client.submit_order(order_data=order_data)

    def _to_alpaca_order_request(self, order: ProposedOrder, *, client_order_id: str) -> Any:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        request_cls = MarketOrderRequest if order.order_type == "market" else LimitOrderRequest
        kwargs: dict[str, Any] = {
            "symbol": order.symbol,
            "side": OrderSide.BUY if order.side == "buy" else OrderSide.SELL,
            "time_in_force": self._time_in_force(order.time_in_force, TimeInForce),
            "client_order_id": client_order_id,
        }
        if order.qty is not None:
            kwargs["qty"] = float(order.qty)
        if order.notional is not None:
            kwargs["notional"] = float(order.notional)
        if order.limit_price is not None:
            kwargs["limit_price"] = float(order.limit_price)
        if order.stop_loss_price is not None:
            kwargs["stop_loss"] = StopLossRequest(stop_price=float(order.stop_loss_price))
        if order.take_profit_price is not None:
            kwargs["take_profit"] = TakeProfitRequest(limit_price=float(order.take_profit_price))
        if order.stop_loss_price is not None and order.take_profit_price is not None:
            kwargs["order_class"] = OrderClass.BRACKET
        elif order.stop_loss_price is not None or order.take_profit_price is not None:
            kwargs["order_class"] = OrderClass.OTO
        return request_cls(**kwargs)

    @staticmethod
    def _time_in_force(value: str, enum_cls: Any) -> Any:
        normalized = value.strip().upper()
        try:
            return getattr(enum_cls, normalized)
        except AttributeError as exc:
            raise ValueError(f"Unsupported time_in_force: {value!r}") from exc
