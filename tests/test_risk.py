from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from traiding_agent.config import RiskConfig
from traiding_agent.risk import ProposedOrder, RiskError, RiskManager


class RiskManagerTests(unittest.TestCase):
    def test_approves_small_paper_style_order_with_stop(self) -> None:
        order = ProposedOrder(
            symbol="SPY",
            side="buy",
            notional=Decimal("1000"),
            entry_price=Decimal("500"),
            stop_loss_price=Decimal("490"),
        )

        decision = RiskManager().evaluate_order(order, equity=Decimal("100000"))

        self.assertTrue(decision.approved)
        self.assertEqual(decision.estimated_notional, Decimal("1000"))
        self.assertEqual(decision.planned_loss, Decimal("20.00"))

    def test_rejects_order_without_stop_for_opening_risk(self) -> None:
        order = ProposedOrder(
            symbol="SPY",
            side="buy",
            notional=Decimal("1000"),
            entry_price=Decimal("500"),
        )

        decision = RiskManager().evaluate_order(order, equity=Decimal("100000"))

        self.assertFalse(decision.approved)
        self.assertIn("opening risk requires a stop_loss_price", decision.reasons)

    def test_rejects_oversized_symbol_exposure(self) -> None:
        order = ProposedOrder(
            symbol="SPY",
            side="buy",
            notional=Decimal("3000"),
            entry_price=Decimal("500"),
            stop_loss_price=Decimal("490"),
        )

        decision = RiskManager().evaluate_order(order, equity=Decimal("100000"))

        self.assertFalse(decision.approved)
        self.assertIn("single-symbol exposure exceeds the configured limit", decision.reasons)

    def test_rejects_trade_risk_over_limit(self) -> None:
        order = ProposedOrder(
            symbol="SPY",
            side="buy",
            notional=Decimal("1000"),
            entry_price=Decimal("500"),
            stop_loss_price=Decimal("100"),
        )

        decision = RiskManager().evaluate_order(order, equity=Decimal("100000"))

        self.assertFalse(decision.approved)
        self.assertIn("planned loss exceeds the configured trade-risk limit", decision.reasons)

    def test_rejects_invalid_symbol(self) -> None:
        with self.assertRaises(RiskError):
            ProposedOrder(symbol="bad symbol", side="buy", notional=Decimal("10"))

    def test_custom_config_can_disable_stop_requirement(self) -> None:
        order = ProposedOrder(
            symbol="SPY",
            side="buy",
            notional=Decimal("1000"),
            entry_price=Decimal("500"),
        )

        decision = RiskManager(RiskConfig(require_stop_loss=False)).evaluate_order(
            order,
            equity=Decimal("100000"),
        )

        self.assertTrue(decision.approved)

    def test_daily_loss_limit_does_not_block_a_position_close(self) -> None:
        order = ProposedOrder(
            symbol="SPY",
            side="sell",
            qty=Decimal("2"),
            intent="close",
            entry_price=Decimal("500"),
        )
        decision = RiskManager().evaluate_order(
            order,
            equity=Decimal("100000"),
            day_realized_pnl=Decimal("-1000"),
        )

        self.assertTrue(decision.approved)


if __name__ == "__main__":
    unittest.main()
