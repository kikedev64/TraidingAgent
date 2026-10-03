"""Paper-only polling agent with durable decisions and order deduplication."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .alpaca_connector import AlpacaConnector
from .config import RiskConfig
from .risk import ProposedOrder
from .strategy import MarketBar, MovingAverageCrossoverStrategy

LOGGER = logging.getLogger("traiding_agent.daemon")
_TIMEFRAME_DURATION = {
    "1Min": timedelta(minutes=1),
    "5Min": timedelta(minutes=5),
    "15Min": timedelta(minutes=15),
    "1Hour": timedelta(hours=1),
    "1Day": timedelta(days=1),
}


@dataclass(frozen=True)
class DaemonConfig:
    symbols: tuple[str, ...] = ("SPY",)
    timeframe: str = "1Day"
    interval_seconds: int = 300
    database_path: Path = Path("data/traiding_agent.sqlite3")
    execute_paper_orders: bool = True
    short_window: int = 50
    long_window: int = 200

    def __post_init__(self) -> None:
        symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in self.symbols))
        if not symbols or any(not symbol for symbol in symbols):
            raise ValueError("At least one non-empty symbol is required")
        if self.timeframe not in _TIMEFRAME_DURATION:
            raise ValueError(f"Unsupported timeframe: {self.timeframe}")
        if self.interval_seconds < 15:
            raise ValueError("interval_seconds must be at least 15")
        object.__setattr__(self, "symbols", symbols)


class DecisionStore:
    """SQLite audit log and durable per-candle idempotency keys."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS decisions (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    bar_timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    risk_approved INTEGER,
                    order_id TEXT,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, bar_timestamp)
                )"""
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def has_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM decisions WHERE symbol=? AND timeframe=? AND bar_timestamp=?",
                (symbol, timeframe, timestamp.isoformat()),
            ).fetchone()
        return row is not None

    def record(
        self,
        *,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        action: str,
        rationale: str,
        risk_approved: bool | None,
        order_id: str | None,
        detail: dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO decisions
                (symbol, timeframe, bar_timestamp, action, rationale, risk_approved,
                 order_id, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    timeframe,
                    timestamp.isoformat(),
                    action,
                    rationale,
                    None if risk_approved is None else int(risk_approved),
                    order_id,
                    json.dumps(detail, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


class PaperTradingDaemon:
    def __init__(
        self,
        connector: AlpacaConnector,
        *,
        config: DaemonConfig | None = None,
        risk_config: RiskConfig | None = None,
        strategy: MovingAverageCrossoverStrategy | None = None,
        store: DecisionStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.connector = connector
        self.config = config or DaemonConfig()
        if not connector.config.paper:
            raise PermissionError("The daemon requires ALPACA_PAPER=true")
        self.risk_config = risk_config or RiskConfig.from_env()
        self.strategy = strategy or MovingAverageCrossoverStrategy(
            short_window=self.config.short_window,
            long_window=self.config.long_window,
        )
        self.store = store or DecisionStore(self.config.database_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> list[dict[str, Any]]:
        account = self.connector.get_account()
        equity = Decimal(str(account.equity))
        last_equity = Decimal(str(getattr(account, "last_equity", account.equity)))
        day_pnl = equity - last_equity
        positions = {str(p.symbol).upper(): p for p in self.connector.get_all_positions()}
        open_orders = self.connector.get_open_orders()
        pending_symbols = {str(o.symbol).upper() for o in open_orders}
        response = self.connector.get_stock_bars(
            list(self.config.symbols),
            timeframe=self.config.timeframe,
            limit=self.config.long_window + 2,
            end=self.clock(),
        )
        bar_sets = response.data
        results: list[dict[str, Any]] = []

        for symbol in self.config.symbols:
            raw_bars = bar_sets.get(symbol, [])
            bars = [MarketBar(timestamp=bar.timestamp, close=Decimal(str(bar.close))) for bar in raw_bars]
            bars.sort(key=lambda bar: bar.timestamp)
            if not bars:
                LOGGER.info("%s: Alpaca returned no bars", symbol)
                continue

            eligible = [bar for bar in bars if self._is_closed_and_fresh(bar.timestamp)]
            if not eligible:
                continue
            latest = eligible[-1]
            if self.store.has_bar(symbol, self.config.timeframe, latest.timestamp):
                continue

            signal = self.strategy.analyze(
                symbol,
                eligible,
                equity=equity,
                risk_config=self.risk_config,
            )
            detail: dict[str, Any] = {"signal": asdict(signal)}
            action = "hold"
            approved: bool | None = None
            order_id: str | None = None
            rationale = signal.rationale

            if signal.action == "buy" and signal.suggested_notional is not None:
                if symbol in positions:
                    rationale = "buy signal ignored because a position already exists"
                elif symbol in pending_symbols:
                    rationale = "buy signal ignored because an order is already open"
                elif self.config.execute_paper_orders:
                    if signal.suggested_notional < self.risk_config.min_order_notional:
                        action = "buy_signal_skipped"
                        rationale = "risk-sized allocation is below Alpaca's minimum order notional"
                        order = None
                    else:
                        order = ProposedOrder(
                            symbol=symbol,
                            side="buy",
                            notional=signal.suggested_notional,
                            entry_price=latest.close,
                            stop_loss_price=signal.invalidation_price,
                            time_in_force="day",
                        )
                    if order is not None:
                        preview = self.connector.preview_order(
                            order,
                            equity=equity,
                            existing_open_positions=len(positions),
                            day_realized_pnl=day_pnl,
                        )
                        approved = preview.decision.approved
                        detail["risk"] = asdict(preview.decision)
                        if approved:
                            try:
                                response_order = self.connector.submit_order(
                                    order,
                                    equity=equity,
                                    existing_open_positions=len(positions),
                                    day_realized_pnl=day_pnl,
                                    client_order_id=self._client_order_id(symbol, latest.timestamp),
                                )
                            except Exception as exc:
                                action = "paper_order_outcome_unknown"
                                rationale = "Alpaca did not confirm submission; inspect order history before retry"
                                detail["submission_error"] = type(exc).__name__
                                LOGGER.exception("Paper buy submission failed for %s", symbol)
                            else:
                                order_id = str(getattr(response_order, "id", "")) or None
                                action = "paper_buy_submitted"
                                rationale = signal.rationale
                else:
                    action = "buy_signal"
                    rationale = signal.rationale

            elif signal.action == "sell":
                position = positions.get(symbol)
                if position is not None and symbol not in pending_symbols:
                    if self.config.execute_paper_orders:
                        qty = Decimal(str(position.qty))
                        order = ProposedOrder(
                            symbol=symbol,
                            side="sell",
                            qty=qty,
                            order_type="market",
                            intent="close",
                            entry_price=latest.close,
                        )
                        preview = self.connector.preview_order(
                            order,
                            equity=equity,
                            current_symbol_exposure=Decimal("0"),
                            existing_open_positions=len(positions),
                            day_realized_pnl=day_pnl,
                        )
                        approved = preview.decision.approved
                        detail["risk"] = asdict(preview.decision)
                        if approved:
                            try:
                                response_order = self.connector.submit_order(
                                    order,
                                    equity=equity,
                                    current_symbol_exposure=Decimal("0"),
                                    existing_open_positions=len(positions),
                                    day_realized_pnl=day_pnl,
                                    client_order_id=self._client_order_id(symbol, latest.timestamp),
                                )
                            except Exception as exc:
                                action = "paper_order_outcome_unknown"
                                rationale = "Alpaca did not confirm submission; inspect order history before retry"
                                detail["submission_error"] = type(exc).__name__
                                LOGGER.exception("Paper closing order failed for %s", symbol)
                            else:
                                order_id = str(getattr(response_order, "id", "")) or None
                                action = "paper_sell_to_close_submitted"
                                rationale = signal.rationale
                elif position is None:
                    rationale = "sell signal ignored because there is no long position to close"
                else:
                    rationale = "sell signal ignored because an order is already open"

            detail["price"] = latest.close
            self.store.record(
                symbol=symbol,
                timeframe=self.config.timeframe,
                timestamp=latest.timestamp,
                action=action,
                rationale=rationale,
                risk_approved=approved,
                order_id=order_id,
                detail=detail,
            )
            result = {
                "symbol": symbol,
                "bar_timestamp": latest.timestamp,
                "action": action,
                "rationale": rationale,
                "order_id": order_id,
                "risk_approved": approved,
            }
            results.append(result)
            LOGGER.info("%s", json.dumps(result, default=str))
        return results

    def run_forever(self, *, once: bool = False) -> None:
        LOGGER.info(
            "Starting paper daemon symbols=%s timeframe=%s execute=%s",
            ",".join(self.config.symbols),
            self.config.timeframe,
            self.config.execute_paper_orders,
        )
        failures = 0
        while True:
            try:
                clock = self.connector.get_clock()
                if clock.is_open:
                    self.run_once()
                else:
                    LOGGER.info("Market closed; waiting for the next poll")
                failures = 0
            except KeyboardInterrupt:
                LOGGER.info("Shutdown requested")
                return
            except Exception:
                failures += 1
                LOGGER.exception("Polling cycle failed (%s/5)", failures)
                if failures >= 5:
                    raise
            if once:
                return
            time.sleep(self.config.interval_seconds)

    def _is_closed_and_fresh(self, timestamp: datetime) -> bool:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = self.clock() - timestamp
        duration = _TIMEFRAME_DURATION[self.config.timeframe]
        if self.config.timeframe == "1Day":
            market_tz = ZoneInfo("America/New_York")
            bar_date = timestamp.astimezone(market_tz).date()
            current_date = self.clock().astimezone(market_tz).date()
            return bar_date < current_date and age <= timedelta(days=7)
        return duration <= age <= duration * 2 + timedelta(minutes=5)

    @staticmethod
    def _client_order_id(symbol: str, timestamp: datetime) -> str:
        source = f"{symbol}:{timestamp.isoformat()}".encode()
        digest = hashlib.sha256(source).hexdigest()[:16]
        return f"ta-{symbol.lower()}-{digest}"
