from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from fastapi.testclient import TestClient

from api.server import create_app
from core.engine import TradingEngine
from core.execution import MT5Executor
from portfolio.health import SymbolHealthGuard
from portfolio.engine import PortfolioEngine
from portfolio.state import PortfolioState
from utils.operator_controls import OperatorControls


def _frame(rows: int = 120, *, end: str = "2026-04-08 12:00:00+00:00") -> pd.DataFrame:
    index = pd.date_range(end=pd.Timestamp(end), periods=rows, freq="5min", tz="UTC")
    close = pd.Series(range(rows), index=index, dtype=float) + 100.0
    open_ = close.shift(1).fillna(close.iloc[0] - 0.2)
    return pd.DataFrame(
        {
            "open": open_.astype(float),
            "high": (close + 0.4).astype(float),
            "low": (open_ - 0.4).astype(float),
            "close": close.astype(float),
            "tick_volume": 100.0,
        },
        index=index,
    )


class _DummyBroker:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def get_historical_data(self, symbol: str, timeframe: str, bars: int = 5000) -> pd.DataFrame:
        return self.frame.tail(bars)

    def get_symbol_snapshot(self, symbol: str) -> dict:
        return {"ok": True, "spread": 0.1}


class _DummyStrategy:
    def on_candle(self, df: pd.DataFrame):
        return None


class RuntimeSafetyTests(unittest.TestCase):
    def test_symbol_health_guard_trips_and_restores(self) -> None:
        guard = SymbolHealthGuard(max_failures=2, cooldown_minutes=15)
        self.assertFalse(guard.record_failure("BTCUSDm", "stale_candle"))
        self.assertTrue(guard.record_failure("BTCUSDm", "stale_candle"))
        self.assertFalse(guard.allowed("BTCUSDm"))

        restored = SymbolHealthGuard(max_failures=2, cooldown_minutes=15)
        restored.restore(guard.snapshot())
        self.assertFalse(restored.allowed("BTCUSDm"))
        self.assertEqual(restored.status("BTCUSDm").last_reason, "stale_candle")

    def test_portfolio_state_persists_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = PortfolioState(path=str(Path(tmpdir) / "portfolio_runtime.json"))
            state.record("BTCUSDm", 12.5)
            state.set_engine_last_candle("BTCUSDm|btc_bos_retest|M5", "2026-04-08T12:00:00+00:00")
            state.set_cooldown_state({"loss_count": {"BTCUSDm": 1}})
            state.set_drawdown_state({"symbol_equity": {"BTCUSDm": 15.0}})
            state.set_health_state({"BTCUSDm": {"last_reason": "startup_health:missing_tick"}})

            reloaded = PortfolioState(path=str(Path(tmpdir) / "portfolio_runtime.json"))
            self.assertEqual(reloaded.last_trade_result["BTCUSDm"], 12.5)
            self.assertEqual(
                reloaded.get_engine_last_candle("BTCUSDm|btc_bos_retest|M5"),
                "2026-04-08T12:00:00+00:00",
            )
            self.assertEqual(reloaded.cooldown_state["loss_count"]["BTCUSDm"], 1)
            self.assertEqual(reloaded.drawdown_state["symbol_equity"]["BTCUSDm"], 15.0)
            self.assertEqual(
                reloaded.health_state["BTCUSDm"]["last_reason"],
                "startup_health:missing_tick",
            )

    @patch("core.engine.get_telegram_credentials")
    @patch("core.engine.TelegramNotifier")
    def test_trading_engine_restores_last_candle_and_blocks_stale_data(
        self,
        notifier_cls: Mock,
        creds_mock: Mock,
    ) -> None:
        creds_mock.return_value = Mock(token="", chat_id="")
        notifier_cls.return_value = Mock()
        frame = _frame()
        engine = TradingEngine(
            broker=_DummyBroker(frame),
            strategy=_DummyStrategy(),
            executor=Mock(),
            symbol="BTCUSDm",
            timeframe="M5",
            candle_seconds=300,
            strategy_name="btc_bos_retest",
            safety_cfg={
                "min_candles_required": 80,
                "max_candle_age_factor": 0.1,
                "max_spread_to_bar_range": 10.0,
            },
        )
        engine.restore_runtime_state({"last_candle_time_utc": "2026-04-08T11:55:00+00:00"})
        self.assertEqual(engine.export_runtime_state()["last_candle_time_utc"], "2026-04-08T11:55:00+00:00")

        intent = engine.generate_trade_intent()
        self.assertIsNone(intent)
        self.assertIn("stale_candle", engine.pop_runtime_issue() or "")

    @patch("core.execution.load_config")
    @patch("core.execution.get_telegram_credentials")
    @patch("core.execution.TelegramNotifier")
    @patch("core.execution.mt5")
    def test_executor_blocks_wide_spread_relative_to_sl(
        self,
        mt5_mock: Mock,
        notifier_cls: Mock,
        creds_mock: Mock,
        load_config_mock: Mock,
    ) -> None:
        load_config_mock.return_value = {
            "production_safety": {
                "max_spread_to_sl_ratio": 0.2,
                "symbol_max_spread_points": {},
            }
        }
        creds_mock.return_value = Mock(token="", chat_id="")
        notifier_cls.return_value = Mock()
        mt5_mock.symbol_info.return_value = Mock(trade_mode=1, point=0.01, trade_tick_size=0.01)
        mt5_mock.symbol_info_tick.return_value = Mock(bid=100.0, ask=101.0)

        executor = MT5Executor("GER30m")
        ok, reason, metrics = executor.market_safety_check({"side": "buy", "sl": 99.0})

        self.assertFalse(ok)
        self.assertIn("spread_to_sl_exceeded", reason or "")
        self.assertGreater(metrics["spread"], 0.0)

    def test_portfolio_engine_manual_kill_switch_blocks_entry(self) -> None:
        engine = PortfolioEngine.__new__(PortfolioEngine)
        engine.static_kill_symbols = {"BTCUSDm"}
        engine.operator_controls = {"killed_symbols": {}, "global_pause": False}
        engine.safety_cfg = {}
        engine._live_bot_exposure = Mock(return_value={})

        self.assertEqual(engine._symbol_entry_block_reason("BTCUSDm"), "manual_symbol_kill_switch")

    def test_automated_broker_actions_stop_for_manual_kill(self) -> None:
        engine = PortfolioEngine.__new__(PortfolioEngine)
        engine.static_kill_symbols = {"BTCUSDm"}
        engine.operator_controls = {"killed_symbols": {}, "global_pause": False}
        engine._account_guard_allows = Mock(return_value=(True, None))

        allowed, reason = engine._automated_broker_actions_allowed("BTCUSDm")

        self.assertFalse(allowed)
        self.assertEqual(reason, "manual_symbol_kill_switch")
        engine._account_guard_allows.assert_not_called()

    @patch("portfolio.engine.STATE")
    @patch("portfolio.engine.mt5")
    def test_account_guard_latches_on_low_equity_ratio(
        self,
        mt5_mock: Mock,
        state_mock: Mock,
    ) -> None:
        engine = PortfolioEngine.__new__(PortfolioEngine)
        engine.safety_cfg = {
            "min_equity_balance_ratio": 0.90,
            "min_free_margin_ratio": 0.25,
            "max_open_bot_positions": 5,
        }
        engine._account_guard_state = {"latched": False, "reason": None, "metrics": {}}
        engine._live_bot_exposure = Mock(return_value={})
        engine.logger = Mock()

        mt5_mock.account_info.return_value = Mock(balance=1000.0, equity=850.0, margin_free=400.0)

        allowed, reason = engine._account_guard_allows()

        self.assertFalse(allowed)
        self.assertIn("equity_ratio_breach", reason or "")
        self.assertTrue(engine._account_guard_state["latched"])
        state_mock.set_error.assert_called_once()

    def test_startup_reconcile_quarantines_unexpected_strategy(self) -> None:
        engine = PortfolioEngine.__new__(PortfolioEngine)
        engine.static_kill_symbols = set()
        engine.operator_controls = {"killed_symbols": {}, "global_pause": False}
        engine.engines = [{"symbol": "BTCUSDm", "strategy": "btc_bos_retest"}]
        engine._live_bot_exposure = Mock(
            return_value={
                "BTCUSDm": {
                    "positions": 1,
                    "orders": 0,
                    "strategies": ["multi_asset_regime"],
                    "position_tickets": [123],
                    "order_tickets": [],
                }
            }
        )
        engine._quarantine_symbol = Mock()

        engine._startup_reconcile_broker_state()

        engine._quarantine_symbol.assert_called_once()
        self.assertEqual(engine._broker_reconciliation["issues"][0]["symbol"], "BTCUSDm")
        self.assertIn(
            "unexpected_live_strategy",
            engine._broker_reconciliation["issues"][0]["reason"],
        )

    def test_operator_controls_persist_pause_kill_and_clear_nonce(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            controls = OperatorControls(path=str(Path(tmpdir) / "operator_controls.json"))
            controls.set_global_pause(True, reason="maintenance")
            controls.kill_symbol("BTCUSDm", reason="manual pause")
            controls.request_account_brake_clear(reason="reviewed")

            restored = OperatorControls(path=str(Path(tmpdir) / "operator_controls.json"))
            snap = restored.snapshot()
            self.assertTrue(snap["global_pause"])
            self.assertEqual(snap["global_pause_reason"], "maintenance")
            self.assertIn("BTCUSDm", snap["killed_symbols"])
            self.assertEqual(snap["account_brake_clear_nonce"], 1)

    def test_api_controls_endpoints_update_operator_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            controls = OperatorControls(path=str(Path(tmpdir) / "operator_controls.json"))
            app = create_app()
            headers = {"Authorization": "Bearer test-token"}

            with patch("api.server.CONTROLS", controls), patch.dict("os.environ", {"API_TOKEN": "test-token"}):
                client = TestClient(app)
                pause_res = client.post("/controls/pause", json={"paused": True, "reason": "ops"}, headers=headers)
                kill_res = client.post("/controls/symbols/BTCUSDm/kill", json={"reason": "halt"}, headers=headers)
                clear_res = client.post("/controls/account-brake/clear", json={"reason": "checked"}, headers=headers)
                unkill_res = client.post("/controls/symbols/BTCUSDm/unkill", headers=headers)
                status_res = client.get("/controls", headers=headers)

            self.assertEqual(pause_res.status_code, 200)
            self.assertEqual(kill_res.status_code, 200)
            self.assertEqual(clear_res.status_code, 200)
            self.assertEqual(unkill_res.status_code, 200)
            self.assertEqual(status_res.status_code, 200)

            snap = status_res.json()["controls"]
            self.assertTrue(snap["global_pause"])
            self.assertEqual(snap["global_pause_reason"], "ops")
            self.assertNotIn("BTCUSDm", snap["killed_symbols"])
            self.assertEqual(snap["account_brake_clear_nonce"], 1)

    def test_refresh_operator_controls_clears_latched_account_brake(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            controls = OperatorControls(path=str(Path(tmpdir) / "operator_controls.json"))
            engine = PortfolioEngine.__new__(PortfolioEngine)
            engine.operator_controls = controls.snapshot()
            engine._last_account_brake_clear_nonce = 0
            engine._account_guard_state = {"latched": True, "reason": "test", "metrics": {}}
            engine.logger = Mock()

            with patch("portfolio.engine.CONTROLS", controls), patch("portfolio.engine.STATE") as state_mock:
                controls.request_account_brake_clear(reason="manual")
                engine._refresh_operator_controls()

            self.assertFalse(engine._account_guard_state["latched"])
            self.assertEqual(engine._account_guard_state["reason"], None)
            state_mock.set_operator_controls.assert_called()


if __name__ == "__main__":
    unittest.main()
