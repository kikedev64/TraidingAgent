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
from traiding_agent.daemon import DecisionStore
from traiding_agent.streaming import RealtimeTradingAgent, StreamConfig
from traiding_agent.strategy import MarketBar


class FakeBrokerClient:
    def __init__(self, positions=None):
        self.submitted = None
        self.positions = positions or []

    def get_account(self):
        return SimpleNamespace(equity="100000", last_equity="100000")

    def get_all_positions(self):
        return self.positions

    def get_orders(self, filter):
        return []

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def submit_order(self, order_data):
        self.submitted = order_data
        return SimpleNamespace(id="paper-order-123")


class StreamingTests(unittest.TestCase):
    def test_records_streamed_bar_and_signal(self):
        with TemporaryDirectory() as directory:
            config = SimpleNamespace(paper=True, allow_live_trading=False)
            connector = SimpleNamespace(config=config)
            database = Path(directory) / "stream.sqlite3"
            store = DecisionStore(database)
            agent = RealtimeTradingAgent(
                connector,
                config=StreamConfig(
                    symbols=("SPY",),
                    execute_orders=False,
                    database_path=database,
                ),
                risk_config=RiskConfig(),
                store=store,
            )
            start = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
            agent.states["SPY"].bars.extend(
                MarketBar(start + timedelta(minutes=i), Decimal(100 + i) / Decimal(10))
                for i in range(51)
            )
            new_bar = SimpleNamespace(
                symbol="SPY",
                timestamp=start + timedelta(minutes=51),
                close=Decimal("10.2"),
            )

            agent._process_bar(new_bar)

            row = store.recent(1)[0]
            self.assertEqual(row["symbol"], "SPY")
            self.assertEqual(row["timeframe"], "1Min")
            self.assertEqual(agent.states["SPY"].price, Decimal("10.2"))
            self.assertEqual(row["action"], "HOLD")

    def test_paper_signal_submits_risk_approved_oto_with_stop(self):
        broker = FakeBrokerClient()
        connector = AlpacaConnector(
            AlpacaConfig("paper-key", "paper-secret", paper=True),
            risk_config=RiskConfig(),
            trading_client=broker,
        )
        agent = RealtimeTradingAgent(
            connector,
            config=StreamConfig(symbols=("SPY",), execute_orders=True),
            risk_config=RiskConfig(),
        )

        action, _, approved, order_id, risk = agent._act_on_signal(
            "SPY",
            MarketBar(datetime(2026, 10, 2, 14, tzinfo=timezone.utc), Decimal("100")),
            "buy",
            Decimal("98"),
            Decimal("2000"),
        )

        self.assertEqual(action, "PAPER BUY SUBMITTED")
        self.assertTrue(approved)
        self.assertEqual(order_id, "paper-order-123")
        self.assertEqual(broker.submitted.qty, 20)
        self.assertEqual(broker.submitted.stop_loss.stop_price, 98)
        self.assertIsNotNone(risk["planned_loss"])

    def test_bearish_signal_submits_sell_to_close(self):
        broker = FakeBrokerClient(
            [SimpleNamespace(symbol="SPY", qty="3", side="PositionSide.LONG")]
        )
        connector = AlpacaConnector(
            AlpacaConfig("paper-key", "paper-secret", paper=True),
            risk_config=RiskConfig(),
            trading_client=broker,
        )
        agent = RealtimeTradingAgent(
            connector,
            config=StreamConfig(symbols=("SPY",), execute_orders=True),
            risk_config=RiskConfig(),
        )

        action, _, approved, order_id, _ = agent._act_on_signal(
            "SPY",
            MarketBar(datetime(2026, 10, 2, 14, tzinfo=timezone.utc), Decimal("100")),
            "sell",
            None,
            None,
        )

        self.assertEqual(action, "PAPER SELL TO CLOSE SUBMITTED")
        self.assertTrue(approved)
        self.assertEqual(order_id, "paper-order-123")
        self.assertEqual(broker.submitted.qty, 3)

    def test_live_stream_requires_runtime_confirmation(self):
        connector = SimpleNamespace(
            config=AlpacaConfig("key", "secret", paper=False, allow_live_trading=True)
        )
        with self.assertRaises(PermissionError):
            RealtimeTradingAgent(connector, config=StreamConfig())


if __name__ == "__main__":
    unittest.main()
