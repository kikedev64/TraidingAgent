"""Example strategy primitives.

These are intentionally simple. Treat them as explainable baselines, not as alpha.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import fmean
from typing import Literal, Sequence

from .config import RiskConfig

SignalAction = Literal["buy", "sell", "hold"]


@dataclass(frozen=True)
class MarketBar:
    timestamp: datetime
    close: Decimal


@dataclass(frozen=True)
class MarketSignal:
    symbol: str
    action: SignalAction
    confidence: Decimal
    rationale: str
    invalidation_price: Decimal | None = None
    suggested_notional: Decimal | None = None


class MovingAverageCrossoverStrategy:
    """Daily trend-following baseline with a small, fractional position size."""

    def __init__(
        self,
        *,
        short_window: int = 50,
        long_window: int = 200,
        allocation_pct: Decimal = Decimal("0.005"),
        stop_pct: Decimal = Decimal("0.05"),
    ) -> None:
        if short_window <= 1:
            raise ValueError("short_window must be greater than 1")
        if long_window <= short_window:
            raise ValueError("long_window must be greater than short_window")
        if allocation_pct <= 0 or stop_pct <= 0 or stop_pct >= 1:
            raise ValueError("allocation_pct and stop_pct must be positive; stop_pct must be below 1")
        self.short_window = short_window
        self.long_window = long_window
        self.allocation_pct = allocation_pct
        self.stop_pct = stop_pct

    def analyze(
        self,
        symbol: str,
        bars: Sequence[MarketBar],
        *,
        equity: Decimal,
        risk_config: RiskConfig | None = None,
    ) -> MarketSignal:
        if len(bars) < self.long_window + 1:
            return MarketSignal(
                symbol=symbol.upper(),
                action="hold",
                confidence=Decimal("0"),
                rationale="not enough bars for the configured moving-average windows",
            )

        closes = [float(bar.close) for bar in bars]
        prev_short = fmean(closes[-self.short_window - 1 : -1])
        prev_long = fmean(closes[-self.long_window - 1 : -1])
        current_short = fmean(closes[-self.short_window :])
        current_long = fmean(closes[-self.long_window :])
        latest_close = bars[-1].close
        risk = risk_config or RiskConfig()
        notional = min(equity * self.allocation_pct, equity * risk.max_position_pct)

        if prev_short <= prev_long and current_short > current_long:
            return MarketSignal(
                symbol=symbol.upper(),
                action="buy",
                confidence=Decimal("0.55"),
                rationale="short moving average crossed above long moving average",
                invalidation_price=latest_close * (Decimal("1") - self.stop_pct),
                suggested_notional=notional,
            )

        if prev_short >= prev_long and current_short < current_long:
            return MarketSignal(
                symbol=symbol.upper(),
                action="sell",
                confidence=Decimal("0.55"),
                rationale="short moving average crossed below long moving average",
                invalidation_price=latest_close * Decimal("1.02"),
                suggested_notional=notional,
            )

        return MarketSignal(
            symbol=symbol.upper(),
            action="hold",
            confidence=Decimal("0.25"),
            rationale="no moving-average crossover detected",
        )
