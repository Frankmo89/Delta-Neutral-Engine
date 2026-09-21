"""Unit tests for FundingRateScanner parse / filter / rank — no REST/WS I/O."""

from __future__ import annotations

import pandas as pd
import pytest

from data.scanner import FUNDING_INTERVALS_PER_DAY, FundingRateScanner


def _tickers():
    return [
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.0003",
            "nextFundingTime": "1",
            "volume24h": "8000000",
            "lastPrice": "65000",
        },
        {
            "symbol": "ETHUSDT",
            "fundingRate": "-0.0004",
            "nextFundingTime": "1",
            "volume24h": "6000000",
            "lastPrice": "3500",
        },
        {
            "symbol": "LOWVOLUSDT",
            "fundingRate": "0.0010",
            "nextFundingTime": "1",
            "volume24h": "100",
            "lastPrice": "1",
        },
        {
            "symbol": "TINYRATEUSDT",
            "fundingRate": "0.00001",
            "nextFundingTime": "1",
            "volume24h": "9000000",
            "lastPrice": "2",
        },
        {
            # incomplete row — missing symbol should be skipped
            "fundingRate": "0.01",
            "volume24h": "9999999",
        },
        {
            "symbol": "BADNUMUSDT",
            "fundingRate": "not-a-number",
            "volume24h": "9999999",
            "lastPrice": "1",
        },
    ]


class TestParseTickers:
    def test_parses_numeric_fields_and_derives_pct_and_apr(self):
        df = FundingRateScanner._parse_tickers(_tickers())
        assert "BADNUMUSDT" not in set(df["symbol"])
        assert list(df["symbol"]) == [
            "BTCUSDT",
            "ETHUSDT",
            "LOWVOLUSDT",
            "TINYRATEUSDT",
        ]
        btc = df.set_index("symbol").loc["BTCUSDT"]
        assert btc["funding_rate"] == pytest.approx(0.0003)
        assert btc["funding_rate_pct"] == pytest.approx(0.03)
        assert btc["volume_24h"] == pytest.approx(8_000_000)
        assert btc["apr_est"] == pytest.approx(abs(0.0003) * FUNDING_INTERVALS_PER_DAY * 365 * 100)

    def test_apr_uses_absolute_funding_rate(self):
        df = FundingRateScanner._parse_tickers(_tickers()).set_index("symbol")
        eth = df.loc["ETHUSDT"]
        assert eth["funding_rate"] == pytest.approx(-0.0004)
        assert eth["apr_est"] == pytest.approx(abs(-0.0004) * 3 * 365 * 100)

    def test_empty_input_returns_empty_frame(self):
        df = FundingRateScanner._parse_tickers([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


class TestFilter:
    def test_drops_low_volume_and_tiny_abs_rate(self):
        df = FundingRateScanner._parse_tickers(_tickers())
        filtered = FundingRateScanner._filter(df, min_volume=5_000_000, min_rate=0.0001)
        assert set(filtered["symbol"]) == {"BTCUSDT", "ETHUSDT"}

    def test_inclusive_thresholds(self):
        df = FundingRateScanner._parse_tickers(
            [
                {
                    "symbol": "EQUSDT",
                    "fundingRate": "0.0001",
                    "volume24h": "5000000",
                    "lastPrice": "1",
                    "nextFundingTime": "",
                }
            ]
        )
        filtered = FundingRateScanner._filter(df, min_volume=5_000_000, min_rate=0.0001)
        assert list(filtered["symbol"]) == ["EQUSDT"]


class TestRank:
    def test_orders_by_estimated_apr_descending(self):
        df = FundingRateScanner._parse_tickers(_tickers())
        ranked = FundingRateScanner._rank(df)
        assert list(ranked["symbol"]) == [
            "LOWVOLUSDT",  # 0.0010 abs → highest APR
            "ETHUSDT",     # 0.0004
            "BTCUSDT",     # 0.0003
            "TINYRATEUSDT",
        ]
        aprs = list(ranked["apr_est"])
        assert aprs == sorted(aprs, reverse=True)
