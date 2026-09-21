"""Unit tests for risk.position_sizer — pure math, no Bybit I/O.

These lock the risk formulas so a later refactor of PositionSizer cannot
silently change break-even, APR, sizing, or viability gates.
"""

from __future__ import annotations

import math

import pytest

from risk.position_sizer import (
    DEFAULT_SLIPPAGE_EST,
    DEFAULT_TAKER_FEE,
    PositionSizer,
)


def _sizer(**kwargs) -> PositionSizer:
    defaults = dict(
        max_position_usdt=1000.0,
        taker_fee=DEFAULT_TAKER_FEE,
        slippage=DEFAULT_SLIPPAGE_EST,
        max_breakeven_periods=12.0,
        min_notional_usdt=10.0,
    )
    defaults.update(kwargs)
    return PositionSizer(**defaults)


class TestGrossApr:
    def test_annualizes_abs_rate_at_three_settlements_per_day(self):
        sizer = _sizer()
        # |0.0003| * 3 * 365 * 100 = 32.85
        assert sizer._gross_apr(0.0003) == pytest.approx(32.85)

    def test_uses_absolute_value_so_negative_funding_still_has_apr(self):
        sizer = _sizer()
        assert sizer._gross_apr(-0.0003) == pytest.approx(sizer._gross_apr(0.0003))

    def test_zero_rate_is_zero_apr(self):
        assert _sizer()._gross_apr(0.0) == 0.0


class TestFrictionAndBreakeven:
    def test_round_trip_friction_is_four_legs_of_fee_plus_slippage(self):
        sizer = _sizer()
        expected = (DEFAULT_TAKER_FEE + DEFAULT_SLIPPAGE_EST) * 2 * 2
        assert sizer._friction_per_round_trip() == pytest.approx(0.003)
        assert sizer._friction_per_round_trip() == pytest.approx(expected)

    def test_breakeven_periods_is_friction_over_abs_rate(self):
        sizer = _sizer()
        # 0.003 / 0.0003 = 10
        assert sizer._breakeven_periods(0.0003) == pytest.approx(10.0)

    def test_zero_funding_is_infinite_breakeven(self):
        assert math.isinf(_sizer()._breakeven_periods(0.0))


class TestCalculateQty:
    def test_converts_notional_to_base_qty_truncated_to_4_decimals(self):
        assert _sizer().calculate_qty(250.0, 50_000.0) == 0.005

    def test_rounds_half_behavior_to_4_decimals(self):
        # 100 / 3 = 33.3333...
        assert _sizer().calculate_qty(100.0, 3.0) == 33.3333

    def test_rejects_non_positive_price(self):
        with pytest.raises(ValueError, match="last_price"):
            _sizer().calculate_qty(100.0, 0.0)
        with pytest.raises(ValueError, match="last_price"):
            _sizer().calculate_qty(100.0, -1.0)


class TestPositionSize:
    def test_splits_capital_across_both_legs_and_respects_max(self):
        sizer = _sizer(max_position_usdt=1000.0)
        # available 2000 → half is 1000, max/2 is 500 → 500
        assert sizer._position_size(2000.0) == 500.0

    def test_clips_to_available_balance_when_smaller_than_max(self):
        sizer = _sizer(max_position_usdt=1000.0)
        # available 100 → half is 50
        assert sizer._position_size(100.0) == 50.0

    def test_rounds_to_two_decimals(self):
        assert _sizer(max_position_usdt=1000.0)._position_size(33.33) == 16.66


class TestEvaluateViability:
    def test_viable_when_funding_covers_friction_inside_max_periods(self):
        report = _sizer(max_breakeven_periods=12.0).evaluate(
            symbol="BTCUSDT",
            funding_rate=0.0003,
            available_balance_usdt=2000.0,
        )
        assert report.is_viable is True
        assert report.rejection_reason is None
        assert report.symbol == "BTCUSDT"
        assert report.gross_apr_pct == pytest.approx(32.85)
        assert report.total_friction_pct == pytest.approx(0.3)
        assert report.breakeven_periods == pytest.approx(10.0)
        assert report.position_size_usdt == 500.0

    def test_rejects_non_positive_funding(self):
        report = _sizer().evaluate("BTCUSDT", 0.0, 2000.0)
        assert report.is_viable is False
        assert report.rejection_reason == "Funding rate no favorable"

        report = _sizer().evaluate("BTCUSDT", -0.0003, 2000.0)
        assert report.is_viable is False
        assert report.rejection_reason == "Funding rate no favorable"

    def test_rejects_when_sized_below_min_notional(self):
        report = _sizer(min_notional_usdt=100.0).evaluate(
            "ETHUSDT",
            0.0005,
            available_balance_usdt=20.0,  # size = 10
        )
        assert report.is_viable is False
        assert "mínimo requerido" in (report.rejection_reason or "")

    def test_rejects_when_breakeven_exceeds_max_periods(self):
        # default friction 0.003 / 0.0003 = 10 periods
        report = _sizer(max_breakeven_periods=3.0).evaluate(
            "BTCUSDT",
            0.0003,
            2000.0,
        )
        assert report.is_viable is False
        assert "Break-even" in (report.rejection_reason or "")

    def test_evaluate_existing_position_reuses_same_math_without_resizing(self):
        report = _sizer(max_breakeven_periods=12.0).evaluate_existing_position(
            symbol="SOLUSDT",
            funding_rate=0.0003,
            position_size_usdt=321.5,
        )
        assert report.is_viable is True
        assert report.position_size_usdt == 321.5
        assert report.breakeven_periods == pytest.approx(10.0)
