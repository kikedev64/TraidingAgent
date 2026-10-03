"""High-level planning facade for Traiding Agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Sequence

from .alpaca_connector import AlpacaConnector, OrderPreview
from .config import RiskConfig
from .risk import ProposedOrder
from .strategy import MarketBar, MovingAverageCrossoverStrategy


@dataclass(frozen=True)
class TradePlan:
    signal: dict
    preview: OrderPreview | None


class TraidingAgent:
    """Composes strategy signals and guarded order previews."""

    def __init__(
        self,
        *,
        connector: AlpacaConnector | None = None,
        risk_config: RiskConfig | None = None,
        strategy: MovingAverageCrossoverStrategy | None = None,
    ) -> None:
        self.connector = connector
        self.risk_config = risk_config or RiskConfig()
        self.strategy = strategy or MovingAverageCrossoverStrategy()

    def build_moving_average_plan(
        self,
        symbol: str,
        bars: Sequence[MarketBar],
        *,
        equity: Decimal,
    ) -> TradePlan:
        signal = self.strategy.analyze(symbol, bars, equity=equity, risk_config=self.risk_config)
        if signal.action == "hold" or signal.suggested_notional is None:
            return TradePlan(signal=asdict(signal), preview=None)

        order = ProposedOrder(
            symbol=signal.symbol,
            side=signal.action,
            notional=signal.suggested_notional,
            entry_price=bars[-1].close,
            stop_loss_price=signal.invalidation_price,
        )
        if self.connector is None:
            return TradePlan(signal=asdict(signal), preview=None)
        preview = self.connector.preview_order(order, equity=equity)
        return TradePlan(signal=asdict(signal), preview=preview)
