"""Environment-backed configuration for Traiding Agent."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean-like value")


def _decimal_from_env(name: str, default: str) -> Decimal:
    value = os.getenv(name, default)
    try:
        return Decimal(value)
    except Exception as exc:  # pragma: no cover - Decimal exception type differs by input
        raise ValueError(f"{name} must be a decimal value") from exc


@dataclass(frozen=True)
class AlpacaConfig:
    """Alpaca API settings.

    `paper` defaults to True. Live execution also requires the separate
    `allow_live_trading` setting and a confirmed runtime session.
    """

    api_key: str
    secret_key: str
    paper: bool = True
    allow_live_trading: bool = False
    raw_data: bool = False

    @classmethod
    def from_env(cls, *, require_keys: bool = True) -> "AlpacaConfig":
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        if require_keys and (not api_key or not secret_key):
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
        return cls(
            api_key=api_key,
            secret_key=secret_key,
            paper=_bool_from_env("ALPACA_PAPER", True),
            allow_live_trading=_bool_from_env("TRAIDING_AGENT_ALLOW_LIVE", False),
        )

    def assert_execution_allowed(self, *, user_confirmed_live: bool = False) -> None:
        if self.paper:
            return
        if not self.allow_live_trading:
            raise PermissionError("Live trading is disabled by TRAIDING_AGENT_ALLOW_LIVE")
        if not user_confirmed_live:
            raise PermissionError("Live execution requires confirmation at startup")


@dataclass(frozen=True)
class RiskConfig:
    """Conservative default risk limits."""

    max_position_pct: Decimal = Decimal("0.02")
    max_trade_risk_pct: Decimal = Decimal("0.005")
    max_daily_loss_pct: Decimal = Decimal("0.01")
    max_open_positions: int = 10
    min_order_notional: Decimal = Decimal("1")
    require_stop_loss: bool = True

    @classmethod
    def from_env(cls) -> "RiskConfig":
        return cls(
            max_position_pct=_decimal_from_env("TRAIDING_AGENT_MAX_POSITION_PCT", "0.02"),
            max_trade_risk_pct=_decimal_from_env("TRAIDING_AGENT_MAX_TRADE_RISK_PCT", "0.005"),
            max_daily_loss_pct=_decimal_from_env("TRAIDING_AGENT_MAX_DAILY_LOSS_PCT", "0.01"),
            max_open_positions=int(os.getenv("TRAIDING_AGENT_MAX_OPEN_POSITIONS", "10")),
            require_stop_loss=_bool_from_env("TRAIDING_AGENT_REQUIRE_STOP_LOSS", True),
        )
