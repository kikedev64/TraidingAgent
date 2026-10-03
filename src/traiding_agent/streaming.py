"""Live Alpaca market stream with a continuously refreshed terminal dashboard."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from .alpaca_connector import AlpacaConnector
from .config import RiskConfig
from .daemon import DecisionStore
from .risk import ProposedOrder
from .strategy import MarketBar, MovingAverageCrossoverStrategy


@dataclass(frozen=True)
class StreamConfig:
    symbols: tuple[str, ...] = ("SPY",)
    short_window: int = 20
    long_window: int = 50
    execute_orders: bool = True
    live_session_confirmed: bool = False
    database_path: Path = Path("data/traiding_agent.sqlite3")

    def __post_init__(self) -> None:
        symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in self.symbols))
        if not symbols or any(not symbol for symbol in symbols):
            raise ValueError("At least one non-empty symbol is required")
        if self.short_window <= 1 or self.long_window <= self.short_window:
            raise ValueError("Windows must satisfy 1 < short_window < long_window")
        object.__setattr__(self, "symbols", symbols)


@dataclass
class SymbolState:
    bars: deque[MarketBar]
    price: Decimal | None = None
    signal: str = "warming up"
    rationale: str = "loading minute bars"
    action: str = "waiting for stream"
    updated_at: datetime | None = None


class RealtimeTradingAgent:
    """Streams IEX trades and minute bars; order execution follows account mode."""

    def __init__(
        self,
        connector: AlpacaConnector,
        *,
        config: StreamConfig | None = None,
        risk_config: RiskConfig | None = None,
        store: DecisionStore | None = None,
        console: Console | None = None,
    ) -> None:
        self.connector = connector
        self.config = config or StreamConfig()
        self.risk_config = risk_config or RiskConfig.from_env()
        self.strategy = MovingAverageCrossoverStrategy(
            short_window=self.config.short_window,
            long_window=self.config.long_window,
        )
        self.store = store or DecisionStore(self.config.database_path)
        self.console = console or Console()
        self.states = {
            symbol: SymbolState(deque(maxlen=self.config.long_window + 2))
            for symbol in self.config.symbols
        }
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._errors: list[str] = []
        self._workers: list[threading.Thread] = []
        self._data_stream: Any | None = None
        self._trading_stream: Any | None = None
        self._last_snapshot = 0.0
        self._equity = Decimal("0")
        self._day_pnl = Decimal("0")
        self._position_map: dict[str, Any] = {}
        self._pending_symbols: set[str] = set()
        self._market_open = False
        self._connection_state = "starting"
        self._last_message: datetime | None = None
        self._recent_events: deque[str] = deque(maxlen=5)

        if not connector.config.paper:
            if not connector.config.allow_live_trading:
                raise PermissionError("Live execution requires TRAIDING_AGENT_ALLOW_LIVE=true")
            if config is None or not config.live_session_confirmed:
                raise PermissionError("Live execution requires the startup confirmation")

    def start(self, *, test_seconds: int | None = None) -> None:
        if test_seconds is not None and self.config.execute_orders:
            raise ValueError("Stream smoke tests must use --monitor-only")
        self._refresh_account(force=True)
        self._load_history()
        self._data_stream = self.connector.stock_stream
        self._data_stream.subscribe_trades(self._on_trade, *self.config.symbols)
        self._data_stream.subscribe_bars(self._on_bar, *self.config.symbols)
        self._workers = [
            threading.Thread(
                target=self._run_stream,
                args=("market-data", self._data_stream),
                name="alpaca-market-data",
                daemon=True,
            )
        ]
        if self.config.execute_orders:
            self._trading_stream = self.connector.trading_stream
            self._trading_stream.subscribe_trade_updates(self._on_trade_update)
            self._workers.append(
                threading.Thread(
                    target=self._run_stream,
                    args=("trading-updates", self._trading_stream),
                    name="alpaca-trading-updates",
                    daemon=True,
                )
            )

        try:
            self._connection_state = "streams started; waiting for market data"
            for worker in self._workers:
                worker.start()
            started = time.monotonic()
            with Live(self.render(), console=self.console, refresh_per_second=4) as live:
                while not self._stop_event.is_set():
                    live.update(self.render())
                    self._refresh_account()
                    if test_seconds is not None and time.monotonic() - started >= test_seconds:
                        break
                    if self._errors:
                        break
                    time.sleep(0.25)
        except KeyboardInterrupt:
            self._recent_events.append("shutdown requested")
        finally:
            self.stop()
        if self._errors:
            raise RuntimeError("; ".join(self._errors))

    def stop(self) -> None:
        self._stop_event.set()
        for stream in (self._data_stream, self._trading_stream):
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
        for worker in self._workers:
            if worker.is_alive():
                worker.join(timeout=3)

    async def _on_trade(self, trade: Any) -> None:
        symbol = str(trade.symbol).upper()
        if symbol not in self.states:
            return
        with self._lock:
            state = self.states[symbol]
            state.price = Decimal(str(trade.price))
            state.updated_at = trade.timestamp
            self._last_message = datetime.now(timezone.utc)
            self._connection_state = "receiving live trades"

    async def _on_bar(self, bar: Any) -> None:
        await asyncio.to_thread(self._process_bar, bar)

    async def _on_trade_update(self, update: Any) -> None:
        event = str(getattr(update, "event", "trade update"))
        order = getattr(update, "order", None)
        symbol = str(getattr(order, "symbol", "")) if order else ""
        status = str(getattr(order, "status", "")) if order else ""
        with self._lock:
            self._recent_events.append(f"{event} {symbol} {status}".strip())
            self._last_message = datetime.now(timezone.utc)

    def _load_history(self) -> None:
        response = self.connector.get_stock_bars(
            list(self.config.symbols),
            timeframe="1Min",
            limit=self.config.long_window + 2,
        )
        cutoff = datetime.now(timezone.utc)
        for symbol, raw_bars in response.data.items():
            if symbol not in self.states:
                continue
            bars = sorted(raw_bars, key=lambda bar: bar.timestamp)
            state = self.states[symbol]
            for bar in bars:
                timestamp = self._aware(bar.timestamp)
                if timestamp + timedelta(minutes=1) <= cutoff:
                    state.bars.append(MarketBar(timestamp, Decimal(str(bar.close))))
            if state.bars:
                state.price = state.bars[-1].close
                state.updated_at = state.bars[-1].timestamp
                state.signal = "warming up"
                state.rationale = f"loaded {len(state.bars)}/{self.config.long_window + 1} closed bars"

    def _process_bar(self, raw_bar: Any) -> None:
        symbol = str(raw_bar.symbol).upper()
        if symbol not in self.states:
            return
        timestamp = self._aware(raw_bar.timestamp)
        bar = MarketBar(timestamp, Decimal(str(raw_bar.close)))

        with self._lock:
            state = self.states[symbol]
            existing = next((item for item in state.bars if item.timestamp == timestamp), None)
            if existing is not None:
                if state.bars and state.bars[-1].timestamp == timestamp:
                    state.bars[-1] = bar
                else:
                    return
            else:
                if state.bars and timestamp < state.bars[-1].timestamp:
                    return
                state.bars.append(bar)
            state.price = bar.close
            state.updated_at = timestamp
            self._last_message = datetime.now(timezone.utc)
            self._connection_state = "receiving live bars"
            bars = tuple(state.bars)

        signal = self.strategy.analyze(
            symbol,
            bars,
            equity=self._equity,
            risk_config=self.risk_config,
        )
        action = signal.action.upper()
        rationale = signal.rationale
        risk_detail: dict[str, Any] = {}
        approved: bool | None = None
        order_id: str | None = None

        if len(bars) >= self.config.long_window + 1 and signal.action in {"buy", "sell"}:
            try:
                action, rationale, approved, order_id, risk_detail = self._act_on_signal(
                    symbol,
                    bar,
                    signal.action,
                    signal.invalidation_price,
                    signal.suggested_notional,
                )
            except Exception as exc:
                action = "ERROR / CHECK BROKER"
                rationale = f"{type(exc).__name__}; inspect Alpaca before retrying"
                risk_detail["error_type"] = type(exc).__name__

        with self._lock:
            state = self.states[symbol]
            state.signal = signal.action.upper()
            state.rationale = rationale
            state.action = action
        self.store.record(
            symbol=symbol,
            timeframe="1Min",
            timestamp=timestamp,
            action=action,
            rationale=rationale,
            risk_approved=approved,
            order_id=order_id,
            detail={"signal": asdict(signal), "risk": risk_detail, "close": bar.close},
        )
        with self._lock:
            self._recent_events.append(f"{symbol} {timestamp:%H:%M:%S} {action}")

    def _act_on_signal(
        self,
        symbol: str,
        bar: MarketBar,
        signal_action: str,
        stop_price: Decimal | None,
        suggested_notional: Decimal | None,
    ) -> tuple[str, str, bool | None, str | None, dict[str, Any]]:
        self._refresh_account(force=True)
        clock = self.connector.get_clock()
        if not clock.is_open:
            return "SIGNAL / MARKET CLOSED", "signal ignored outside regular market hours", None, None, {}

        positions = self._position_map
        if symbol in self._pending_symbols:
            return "SIGNAL / ORDER PENDING", "an open order already exists for this symbol", None, None, {}

        if signal_action == "buy":
            if symbol in positions:
                return "BUY BLOCKED", "a position already exists; no pyramiding", None, None, {}
            if suggested_notional is None or stop_price is None:
                return "BUY BLOCKED", "signal has no sizing or stop information", False, None, {}
            qty = (suggested_notional / bar.close).to_integral_value(rounding=ROUND_FLOOR)
            if qty < 1:
                return "BUY SKIPPED", "risk-sized allocation is below one whole share", False, None, {}
            order = ProposedOrder(
                symbol=symbol,
                side="buy",
                qty=qty,
                entry_price=bar.close,
                stop_loss_price=stop_price,
            )
            order_qty = qty
        else:
            position = positions.get(symbol)
            if position is None:
                return "SELL IGNORED", "no long position to close; short selling is disabled", None, None, {}
            if str(getattr(position, "side", "long")).lower().endswith("short"):
                return "SELL BLOCKED", "short positions are outside this strategy", None, None, {}
            order = ProposedOrder(
                symbol=symbol,
                side="sell",
                qty=Decimal(str(position.qty)),
                intent="close",
                entry_price=bar.close,
            )
            order_qty = Decimal(str(position.qty))

        preview = self.connector.preview_order(
            order,
            equity=self._equity,
            current_symbol_exposure=Decimal("0"),
            existing_open_positions=len(positions),
            day_realized_pnl=self._day_pnl,
        )
        risk_detail = asdict(preview.decision)
        if not preview.decision.approved:
            return "RISK BLOCKED", "; ".join(preview.decision.reasons), False, None, risk_detail
        if not self.config.execute_orders:
            return "SIGNAL ONLY", "monitor-only; no order submitted", True, None, risk_detail

        client_order_id = self._client_order_id(symbol, bar.timestamp)
        try:
            result = self.connector.submit_order(
                order,
                equity=self._equity,
                existing_open_positions=len(positions),
                day_realized_pnl=self._day_pnl,
                client_order_id=client_order_id,
                user_confirmed_live=self.config.live_session_confirmed,
            )
        except Exception as exc:
            risk_detail["submission_error"] = type(exc).__name__
            return (
                "ORDER OUTCOME UNKNOWN",
                "Alpaca did not confirm; inspect order history before retrying",
                True,
                None,
                risk_detail,
            )
        order_id = str(getattr(result, "id", "")) or None
        prefix = "PAPER" if self.connector.config.paper else "LIVE"
        verb = "BUY SUBMITTED" if signal_action == "buy" else "SELL TO CLOSE SUBMITTED"
        return (
            f"{prefix} {verb}",
            f"{order_qty} shares; order {order_id or client_order_id}",
            True,
            order_id,
            risk_detail,
        )

    def _refresh_account(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_snapshot < 30:
            return
        account = self.connector.get_account()
        equity = Decimal(str(account.equity))
        last_equity = Decimal(str(getattr(account, "last_equity", account.equity)))
        positions = {str(p.symbol).upper(): p for p in self.connector.get_all_positions()}
        orders = self.connector.get_open_orders()
        with self._lock:
            self._equity = equity
            self._day_pnl = equity - last_equity
            self._position_map = positions
            self._pending_symbols = {str(order.symbol).upper() for order in orders}
            self._market_open = bool(self.connector.get_clock().is_open)
            self._last_snapshot = now

    def _load_worker(self, name: str, stream: Any) -> None:
        try:
            stream.run()
        except Exception as exc:
            self._errors.append(f"{name}: {type(exc).__name__}: {exc}")
            self._stop_event.set()

    def _run_stream(self, name: str, stream: Any) -> None:
        self._load_worker(name, stream)

    def render(self) -> Group:
        mode = "PAPER" if self.connector.config.paper else "LIVE"
        table = Table(
            title=f"TRAIDING AGENT | {mode} | ALPACA IEX STREAM",
            box=box.SIMPLE_HEAVY,
            expand=True,
        )
        table.add_column("Symbol", style="bold")
        table.add_column("Last", justify="right")
        table.add_column("Signal")
        table.add_column("Action / status")
        table.add_column("Bar UTC")
        with self._lock:
            for symbol, state in self.states.items():
                table.add_row(
                    symbol,
                    str(state.price or "-"),
                    state.signal,
                    state.action,
                    state.updated_at.strftime("%H:%M:%S") if state.updated_at else "-",
                )
            pnl_color = "green" if self._day_pnl >= 0 else "red"
            summary = (
                f"Stream: {self._connection_state} | Market: {'OPEN' if self._market_open else 'CLOSED'}\n"
                f"Equity: ${self._equity:,.2f} | Day PnL: [{pnl_color}]${self._day_pnl:,.2f}[/{pnl_color}] | "
                f"Positions: {len(self._position_map)} | Open orders: {len(self._pending_symbols)}"
            )
            events = "\n".join(self._recent_events) or "Waiting for first market event"
            if self._errors:
                events = "\n".join(self._errors[-2:])
        event_panel = Panel(events, title="Recent activity", border_style="cyan")
        return Group(table, Panel(summary, border_style="green" if self.connector.config.paper else "yellow"), event_panel)

    @staticmethod
    def _aware(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    @staticmethod
    def _client_order_id(symbol: str, timestamp: datetime) -> str:
        digest = hashlib.sha256(f"{symbol}:{timestamp.isoformat()}".encode()).hexdigest()[:16]
        return f"ta-{symbol.lower()}-{digest}"
