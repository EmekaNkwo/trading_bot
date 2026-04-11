from __future__ import annotations

import unittest

import pandas as pd

from config.loader import load_config
from strategy.factory import build_strategy


def _build_frame(*, rows: int, start: str, freq: str, base: float, slope: float) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=rows, freq=freq, tz="UTC")
    close = pd.Series([base + (i * slope) for i in range(rows)], index=index, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0] - slope)
    high = pd.concat([open_, close], axis=1).max(axis=1) + abs(slope * 2.0) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - abs(slope * 2.0) - 0.5
    return pd.DataFrame(
        {
            "open": open_.astype(float),
            "high": high.astype(float),
            "low": low.astype(float),
            "close": close.astype(float),
            "tick_volume": 100.0,
        },
        index=index,
    )


class StrategySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_rotated_winners_are_pinned(self) -> None:
        rotation_cfg = self.config.get("rotation", {})
        self.assertEqual(
            rotation_cfg.get("selected_strategies", {}).get("GER30m"),
            ["ger30_three_pin_breakout"],
        )
        self.assertCountEqual(
            rotation_cfg.get("selected_strategies", {}).get("BTCUSDm", []),
            ["btc_bos_retest", "multi_asset_regime"],
        )

    def test_surviving_custom_strategies_build(self) -> None:
        ger = build_strategy("ger30_three_pin_breakout", self.config, symbol="GER30m")
        btc = build_strategy("btc_bos_retest", self.config, symbol="BTCUSDm")
        regime = build_strategy("multi_asset_regime", self.config, symbol="BTCUSDm")

        self.assertEqual(type(ger).__name__, "GER30TranscriptionStrategy")
        self.assertEqual(type(btc).__name__, "BTCTranscriptionStrategy")
        self.assertEqual(type(regime).__name__, "MultiAssetRegimeStrategy")

    def test_surviving_custom_strategies_handle_candles(self) -> None:
        ger = build_strategy("ger30_three_pin_breakout", self.config, symbol="GER30m")
        btc = build_strategy("btc_bos_retest", self.config, symbol="BTCUSDm")
        regime = build_strategy("multi_asset_regime", self.config, symbol="BTCUSDm")

        ger_frame = _build_frame(
            rows=260,
            start="2026-04-01 06:00:00",
            freq="5min",
            base=23000.0,
            slope=0.8,
        )
        btc_frame = _build_frame(
            rows=500,
            start="2026-04-01 12:00:00",
            freq="5min",
            base=70000.0,
            slope=4.5,
        )

        for strategy, frame in ((ger, ger_frame), (btc, btc_frame), (regime, btc_frame)):
            signal = strategy.on_candle(frame)
            self.assertTrue(signal is None or isinstance(signal, dict))


if __name__ == "__main__":
    unittest.main()
