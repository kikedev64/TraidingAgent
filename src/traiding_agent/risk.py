"""Risk checks for proposed orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import re
from typing import Literal

from .config import RiskConfig

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
PositionIntent = Literal["open", "close"]

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9./_-]{0,19}$")


class RiskError(ValueError):
    """Raised when an order cannot pass risk validation."""


@dataclass(frozen=True)
class ProposedOrder:
    """A broker-neutral order proposal that can be risk checked before submission."""

    symbol: str
    side: OrderSide
    notional: Decimal | None = None
    qty: Decimal | None = None
    order_type: OrderType = "market"
    intent: PositionIntent = "open"
    entry_price: Decimal | None = None
    limit_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    time_in_force: str = "day"

    def __post_init__(self) -> None:
        symbol = self.symbol.upper().strip()
        object.__setattr__(self, "symbol", symbol)
        if not _SYMBOL_PATTERN.match(symbol):
            raise RiskError(f"Invalid symbol: {self.symbol!r}")
        if (self.notional is None) == (self.qty is None):
            raise RiskError("Provide exactly one of notional or qty")
        if self.notional is not None and self.notional <= 0:
            raise RiskError("notional must be greater than zero")
        if self.qty is not None and self.qty <= 0:
            raise RiskError("qty must be greater than zero")
        if self.order_type == "limit" and self.limit_price is None:
            raise RiskError("limit orders require limit_price")

    @property
    def reference_price(self) -> Decimal | None:
        return self.entry_price or self.limit_price

    def estimated_notional(self) -> Decimal | None:
        if self.notional is not None:
            return self.notional
        if self.qty is None or self.reference_price is None:
            return None
        return self.qty * self.reference_price


@dataclass(frozen=True)
class RiskDecision:
    """Risk decision with explicit reasons for auditability."""

    approved: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    estimated_notional: Decimal | None = None
    planned_loss: Decimal | None = None

    def raise_if_rejected(self) -> None:
        if not self.approved:
            raise RiskError("; ".join(self.reasons))


class RiskManager:
    """Evaluates proposed orders against a conservative risk policy."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def evaluate_order(
        self,
        order: ProposedOrder,
        *,
        equity: Decimal,
        current_symbol_exposure: Decimal = Decimal("0"),
        existing_open_positions: int = 0,
        day_realized_pnl: Decimal = Decimal("0"),
    ) -> RiskDecision:
        reasons: list[str] = []
        planned_loss = self._planned_loss(order)
        estimated_notional = order.estimated_notional()

        if equity <= 0:
            reasons.append("equity must be greater than zero")

        if estimated_notional is None:
            reasons.append("entry_price or limit_price is required to estimate qty-based notional")
        elif estimated_notional < self.config.min_order_notional:
            reasons.append("estimated notional is below the minimum order notional")

        if order.intent == "open" and equity > 0 and day_realized_pnl < 0:
            daily_loss_pct = abs(day_realized_pnl) / equity
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                reasons.append("daily loss limit has already been reached")

        if order.intent == "open":
            if existing_open_positions >= self.config.max_open_positions:
                reasons.append("maximum open position count would be exceeded")
            if self.config.require_stop_loss and order.stop_loss_price is None:
                reasons.append("opening risk requires a stop_loss_price")

        if order.intent == "open" and estimated_notional is not None and equity > 0:
            max_position_notional = equity * self.config.max_position_pct
            resulting_exposure = abs(current_symbol_exposure) + estimated_notional
            if resulting_exposure > max_position_notional:
                reasons.append("single-symbol exposure exceeds the configured limit")

        if planned_loss is not None and equity > 0:
            max_planned_loss = equity * self.config.max_trade_risk_pct
            if planned_loss > max_planned_loss:
                reasons.append("planned loss exceeds the configured trade-risk limit")

        if order.stop_loss_price is not None:
            self._check_stop_direction(order, reasons)

        if order.take_profit_price is not None:
            self._check_take_profit_direction(order, reasons)

        return RiskDecision(
            approved=not reasons,
            reasons=tuple(reasons),
            estimated_notional=estimated_notional,
            planned_loss=planned_loss,
        )

    def _planned_loss(self, order: ProposedOrder) -> Decimal | None:
        if order.intent == "close" or order.stop_loss_price is None or order.reference_price is None:
            return None
        if order.qty is not None:
            units = order.qty
        elif order.notional is not None and order.reference_price > 0:
            units = order.notional / order.reference_price
        else:
            return None
        return abs(order.reference_price - order.stop_loss_price) * units

    @staticmethod
    def _check_stop_direction(order: ProposedOrder, reasons: list[str]) -> None:
        if order.reference_price is None:
            return
        if order.side == "buy" and order.stop_loss_price >= order.reference_price:
            reasons.append("buy stop_loss_price must be below the entry reference price")
        if order.side == "sell" and order.intent == "open" and order.stop_loss_price <= order.reference_price:
            reasons.append("short stop_loss_price must be above the entry reference price")

    @staticmethod
    def _check_take_profit_direction(order: ProposedOrder, reasons: list[str]) -> None:
        if order.reference_price is None:
            return
        if order.side == "buy" and order.take_profit_price <= order.reference_price:
            reasons.append("buy take_profit_price must be above the entry reference price")
        if order.side == "sell" and order.intent == "open" and order.take_profit_price >= order.reference_price:
            reasons.append("short take_profit_price must be below the entry reference price")
