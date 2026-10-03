from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from traiding_agent.strategy import MarketBar, MovingAverageCrossoverStrategy


def _bars(values: list[str]) -> list[MarketBar]:
    start = datetime(2026, 1, 1)
    return [
        MarketBar(timestamp=start + timedelta(days=index), close=Decimal(value))
        for index, value in enumerate(values)
    ]


class MovingAverageCrossoverStrategyTests(unittest.TestCase):
    def test_hold_when_not_enough_bars(self) -> None:
        signal = MovingAverageCrossoverStrategy(short_window=3, long_window=5).analyze(
            "SPY",
            _bars(["1", "2", "3"]),
            equity=Decimal("100000"),
        )

        self.assertEqual(signal.action, "hold")

    def test_buy_on_bullish_cross(self) -> None:
        values = ["10", "10", "10", "10", "10", "10", "9", "8", "8", "20"]

        signal = MovingAverageCrossoverStrategy(short_window=2, long_window=4).analyze(
            "SPY",
            _bars(values),
            equity=Decimal("100000"),
        )

        self.assertEqual(signal.action, "buy")
        self.assertEqual(signal.suggested_notional, Decimal("2000.00"))


if __name__ == "__main__":
    unittest.main()
