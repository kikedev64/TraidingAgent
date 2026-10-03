from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from traiding_agent.alpaca_connector import AlpacaConnector
from traiding_agent.config import AlpacaConfig, RiskConfig
from traiding_agent.daemon import DaemonConfig, DecisionStore, PaperTradingDaemon
from traiding_agent.strategy import MarketSignal


class FakeTradingClient:
    def get_account(self):
        return SimpleNamespace(equity="100000", last_equity="100000", status="ACTIVE")

    def get_all_positions(self):
        return []

    def get_orders(self, filter):
        return []


class FakeDataClient:
    def __init__(self, bars):
        self.bars = bars

    def get_stock_bars(self, request):
        return SimpleNamespace(data={"SPY": self.bars})


class FixedStrategy:
    def analyze(self, symbol, bars, *, equity, risk_config):
        return MarketSignal(
            symbol=symbol,
            action="buy",
            confidence=Decimal("0.55"),
            rationale="test signal",
            invalidation_price=bars[-1].close * Decimal("0.98"),
            suggested_notional=Decimal("1000"),
        )


class DaemonTests(unittest.TestCase):
    def test_monitor_only_records_signal_once_per_closed_bar(self):
        with TemporaryDirectory() as directory:
            now = datetime(2026, 10, 3, 18, tzinfo=timezone.utc)
            bar_time = now - timedelta(hours=1)
            bars = [SimpleNamespace(timestamp=bar_time, close=Decimal("500"))]
            connector = AlpacaConnector(
                AlpacaConfig("paper-key", "paper-secret", paper=True),
                risk_config=RiskConfig(),
                trading_client=FakeTradingClient(),
            )
            connector._data_client = FakeDataClient(bars)
            store = DecisionStore(Path(directory) / "state.sqlite3")
            daemon = PaperTradingDaemon(
                connector,
                config=DaemonConfig(
                    symbols=("SPY",),
                    timeframe="1Hour",
                    database_path=Path(directory) / "state.sqlite3",
                    execute_paper_orders=False,
                ),
                strategy=FixedStrategy(),
                store=store,
                clock=lambda: now,
            )

            first = daemon.run_once()
            second = daemon.run_once()

            self.assertEqual(first[0]["action"], "buy_signal")
            self.assertEqual(second, [])
            self.assertEqual(len(store.recent()), 1)

    def test_daemon_rejects_live_configuration(self):
        connector = AlpacaConnector(AlpacaConfig("key", "secret", paper=False))
        with self.assertRaises(PermissionError):
            PaperTradingDaemon(connector)


if __name__ == "__main__":
    unittest.main()
