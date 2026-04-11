import asyncio
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import MetaTrader5 as mt5

from portfolio.allocator import CapitalAllocator
from portfolio.exposure import ExposureTracker
from portfolio.config import PORTFOLIO
from portfolio.guard import SymbolDrawdownGuard
from portfolio.cooldown import SymbolCooldown
from portfolio.health import SymbolHealthGuard
from portfolio.state import PortfolioState

from core.broker import MT5Broker
from core.execution import MT5Executor, _parse_strategy_from_comment
from core.engine import TradingEngine
from core.market_state import MarketStateStore
from models.trade_intent import TradeIntent

from config.loader import load_config
from strategy.factory import build_strategy

from utils.logger import setup_logger
from utils.operator_controls import CONTROLS
from utils.deal_tracker import ClosedDealTracker
from utils.trade_reporter import ClosedDealReporter
from utils.runtime_state import STATE


class PortfolioEngine:

    def __init__(self):

        self.logger = setup_logger()
        self.cfg = PORTFOLIO

        # Shared portfolio components
        self.allocator = CapitalAllocator(self.cfg["max_total_risk"])
        self.exposure = ExposureTracker()
        self.drawdown_guard = SymbolDrawdownGuard(max_drawdown_pct=0.02)
        self.cooldown = SymbolCooldown(max_losses=3)
        self.state = PortfolioState()
        self.health_guard = SymbolHealthGuard()
        self.deal_tracker = ClosedDealTracker(magic=2601, poll_lookback_minutes=240)
        self.deal_reporter = ClosedDealReporter()
        self._last_deal_poll = 0.0
        self._deal_poll_interval_s = 10.0
        self._last_manager_poll = 0.0
        self._manager_poll_interval_s = 5.0
        self._signal_poll_interval_s = 1.0
        self._mt5_lock = Lock()
        self._run_live = False
        self._account_guard_state = {
            "latched": False,
            "reason": None,
            "metrics": {},
        }
        self._broker_reconciliation = {
            "summary": {},
            "issues": [],
        }
        self._last_account_brake_clear_nonce = 0

        # Shared broker connection
        self.broker = MT5Broker()
        self.broker.connect()

        self.engines = []

        config = load_config()
        self.scalper_cfg = config.get("scalper", {})
        risk_cfg = config.get("risk", {})
        self.breakeven_cfg = config.get("breakeven", {}) or {}
        self.safety_cfg = dict(config.get("production_safety", {}) or {})
        self.static_kill_symbols = {
            str(symbol)
            for symbol in self.safety_cfg.get("symbol_kill_switches", []) or []
            if str(symbol).strip()
        }
        self.operator_controls = CONTROLS.reload()
        try:
            self._last_account_brake_clear_nonce = int(
                self.operator_controls.get("account_brake_clear_nonce", 0) or 0
            )
        except Exception:
            self._last_account_brake_clear_nonce = 0
        STATE.set_operator_controls(self.operator_controls)
        self.health_guard = SymbolHealthGuard(
            max_failures=int(self.safety_cfg.get("max_symbol_failures", 3) or 3),
            cooldown_minutes=int(self.safety_cfg.get("symbol_cooldown_minutes", 30) or 30),
        )
        self.market_state = MarketStateStore(config)
        rotation_cfg = config.get("rotation", {}) or {}
        self.selected_strategies = dict(rotation_cfg.get("selected_strategies", {}) or {})
        self.selected_execution = dict(rotation_cfg.get("selected_execution", {}) or {})

        # --------------------------------------------------
        # BUILD ONE ENGINE PER STRATEGY PER SYMBOL
        # --------------------------------------------------
        for symbol, symbol_cfg in self.cfg["symbols"].items():

            # Handle both single strategy and multiple strategies config
            if "strategies" in symbol_cfg:
                strategies_cfg = dict(symbol_cfg["strategies"])
                selected_strategies = self._selected_strategy_names(symbol)
                if selected_strategies:
                    valid_selected = [name for name in selected_strategies if name in strategies_cfg]
                    if valid_selected:
                        strategies_cfg = {name: strategies_cfg[name] for name in valid_selected}
                        self.logger.info(f"Rotation selected strategies for {symbol}: {valid_selected}")
                    else:
                        self.logger.warning(
                            f"Rotation selected unknown strategies for {symbol}: {selected_strategies}. "
                            "Falling back to configured strategy set."
                        )
                for strategy_name, scfg in strategies_cfg.items():
                    effective_cfg = dict(scfg)
                    execution_override = self._symbol_execution_override(symbol, strategy_name)
                    effective_cfg.update({k: v for k, v in execution_override.items() if k in {"timeframe", "candle_seconds", "risk"}})
                    strategy = build_strategy(strategy_name, config, symbol=symbol)
                    executor = MT5Executor(symbol=symbol)

                    engine = TradingEngine(
                        broker=self.broker,
                        strategy=strategy,
                        executor=executor,
                        symbol=symbol,
                        timeframe=effective_cfg["timeframe"],
                        candle_seconds=effective_cfg["candle_seconds"],
                        risk_cfg=risk_cfg,
                        market_state=self.market_state,
                        strategy_name=strategy_name,
                        safety_cfg=self.safety_cfg,
                    )

                    self.engines.append({
                        "engine": engine,
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "risk": effective_cfg["risk"],
                        "timeframe": effective_cfg["timeframe"],
                    })
                    self.logger.info(
                        f"Created engine: {symbol} - {strategy_name} "
                        f"(risk: {effective_cfg['risk']}, tf: {effective_cfg['timeframe']})"
                    )
            else:
                # Single strategy per symbol
                strategy_name = symbol_cfg["strategy"]
                selected_strategies = self._selected_strategy_names(symbol)
                if selected_strategies and selected_strategies[0] != strategy_name:
                    self.logger.info(
                        f"Rotation overriding single strategy for {symbol}: "
                        f"{strategy_name} -> {selected_strategies[0]}"
                    )
                    strategy_name = selected_strategies[0]
                try:
                    strategy = build_strategy(strategy_name, config, symbol=symbol)
                except ValueError:
                    fallback_name = symbol_cfg["strategy"]
                    self.logger.warning(
                        f"Rotation selected unknown strategy for {symbol}: {strategy_name}. "
                        f"Falling back to {fallback_name}."
                    )
                    strategy_name = fallback_name
                    strategy = build_strategy(strategy_name, config, symbol=symbol)
                effective_cfg = {
                    "timeframe": symbol_cfg["timeframe"],
                    "candle_seconds": symbol_cfg["candle_seconds"],
                    "risk": symbol_cfg["risk"],
                }
                execution_override = self._symbol_execution_override(symbol, strategy_name)
                effective_cfg.update({k: v for k, v in execution_override.items() if k in {"timeframe", "candle_seconds", "risk"}})
                executor = MT5Executor(symbol=symbol)

                engine = TradingEngine(
                    broker=self.broker,
                    strategy=strategy,
                    executor=executor,
                    symbol=symbol,
                    timeframe=effective_cfg["timeframe"],
                    candle_seconds=effective_cfg["candle_seconds"],
                    risk_cfg=risk_cfg,
                    market_state=self.market_state,
                    strategy_name=strategy_name,
                    safety_cfg=self.safety_cfg,
                )

                self.engines.append({
                    "engine": engine,
                    "symbol": symbol,
                    "strategy": strategy_name,
                    "risk": effective_cfg["risk"],
                    "timeframe": effective_cfg["timeframe"],
                })
                self.logger.info(
                    f"Created engine: {symbol} - {strategy_name} "
                    f"(risk: {effective_cfg['risk']}, tf: {effective_cfg['timeframe']})"
                )

        self._restore_runtime_state()
        self.logger.info(f"Portfolio initialized with {len(self.engines)} engines")

    def _selected_strategy_names(self, symbol: str) -> list[str]:
        selected = self.selected_strategies.get(symbol)
        if selected is None:
            return []
        if isinstance(selected, str):
            return [selected]
        if isinstance(selected, (list, tuple)):
            return [str(name) for name in selected if str(name).strip()]
        return []

    def _symbol_execution_override(self, symbol: str, strategy_name: str) -> dict[str, Any]:
        symbol_plan = dict(self.selected_execution.get(symbol, {}) or {})
        if not symbol_plan:
            return {}
        strategies = dict(symbol_plan.get("strategies", {}) or {})
        override = dict(strategies.get(strategy_name, {}) or {})
        if not override:
            return {}
        return override

    def _engine_key(self, item: dict[str, Any]) -> str:
        return f"{item['symbol']}|{item['strategy']}|{item.get('timeframe') or item['engine'].timeframe}"

    def _managed_strategies_by_symbol(self) -> dict[str, set[str]]:
        managed: dict[str, set[str]] = {}
        for item in self.engines:
            managed.setdefault(item["symbol"], set()).add(item["strategy"])
        return managed

    def _effective_kill_symbols(self) -> set[str]:
        killed = {
            str(symbol)
            for symbol in dict(self.operator_controls.get("killed_symbols", {}) or {}).keys()
            if str(symbol).strip()
        }
        return set(self.static_kill_symbols) | killed

    def _global_pause_active(self) -> bool:
        return bool(self.operator_controls.get("global_pause", False))

    def _refresh_operator_controls(self) -> None:
        self.operator_controls = CONTROLS.reload()
        STATE.set_operator_controls(self.operator_controls)
        try:
            clear_nonce = int(self.operator_controls.get("account_brake_clear_nonce", 0) or 0)
        except Exception:
            clear_nonce = self._last_account_brake_clear_nonce

        if clear_nonce > self._last_account_brake_clear_nonce:
            self._last_account_brake_clear_nonce = clear_nonce
            if bool(self._account_guard_state.get("latched")):
                reason = str(
                    self.operator_controls.get("last_account_brake_clear_reason")
                    or "operator_requested_clear"
                )
                self._account_guard_state["latched"] = False
                self._account_guard_state["reason"] = None
                self.logger.warning(f"ACCOUNT BRAKE CLEARED BY OPERATOR | {reason}")

    @staticmethod
    def _is_bot_trade(item: Any) -> bool:
        comment = str(getattr(item, "comment", "") or "")
        try:
            magic = int(getattr(item, "magic", 0) or 0)
        except Exception:
            magic = 0
        return magic == 2601 or comment.startswith("pb|")

    def _live_bot_exposure(self) -> dict[str, dict[str, Any]]:
        try:
            positions = mt5.positions_get() or []
        except Exception:
            positions = []
        try:
            orders = mt5.orders_get() or []
        except Exception:
            orders = []

        summary: dict[str, dict[str, Any]] = {}

        def _ensure(symbol: str) -> dict[str, Any]:
            return summary.setdefault(
                str(symbol),
                {
                    "positions": 0,
                    "orders": 0,
                    "strategies": set(),
                    "position_tickets": [],
                    "order_tickets": [],
                },
            )

        for position in positions:
            if not self._is_bot_trade(position):
                continue
            symbol = str(getattr(position, "symbol", "unknown") or "unknown")
            item = _ensure(symbol)
            item["positions"] += 1
            item["strategies"].add(_parse_strategy_from_comment(getattr(position, "comment", None)))
            ticket = getattr(position, "ticket", None)
            if ticket is not None:
                item["position_tickets"].append(int(ticket))

        for order in orders:
            if not self._is_bot_trade(order):
                continue
            symbol = str(getattr(order, "symbol", "unknown") or "unknown")
            item = _ensure(symbol)
            item["orders"] += 1
            item["strategies"].add(_parse_strategy_from_comment(getattr(order, "comment", None)))
            ticket = getattr(order, "ticket", None)
            if ticket is not None:
                item["order_tickets"].append(int(ticket))

        return {
            symbol: {
                "positions": int(item["positions"]),
                "orders": int(item["orders"]),
                "strategies": sorted(str(strategy) for strategy in item["strategies"] if strategy),
                "position_tickets": sorted(set(int(ticket) for ticket in item["position_tickets"])),
                "order_tickets": sorted(set(int(ticket) for ticket in item["order_tickets"])),
            }
            for symbol, item in summary.items()
        }

    def _persist_runtime_state(self) -> None:
        for item in self.engines:
            engine_state = item["engine"].export_runtime_state()
            self.state.set_engine_last_candle(
                self._engine_key(item),
                engine_state.get("last_candle_time_utc"),
                persist=False,
            )
        self.state.set_cooldown_state(self.cooldown.snapshot(), persist=False)
        self.state.set_drawdown_state(self.drawdown_guard.snapshot(), persist=False)
        self.state.set_health_state(self.health_guard.snapshot(), persist=False)
        self.state.persist()

    def _restore_runtime_state(self) -> None:
        self.cooldown.restore(self.state.cooldown_state)
        self.drawdown_guard.restore(self.state.drawdown_state)
        self.health_guard.restore(self.state.health_state)
        for item in self.engines:
            candle_time = self.state.get_engine_last_candle(self._engine_key(item))
            if candle_time:
                item["engine"].restore_runtime_state({"last_candle_time_utc": candle_time})

    def _startup_reconcile_broker_state(self) -> None:
        summary = self._live_bot_exposure()
        managed = self._managed_strategies_by_symbol()
        issues: list[dict[str, Any]] = []

        for symbol, data in summary.items():
            reason = None
            expected = managed.get(symbol)
            strategies = [s for s in data.get("strategies", []) if s]

            if symbol in self._effective_kill_symbols():
                reason = "manual_symbol_kill_switch_with_live_exposure"
            elif expected is None:
                reason = "unmanaged_live_bot_exposure"
            elif "unknown" in strategies:
                reason = "unknown_strategy_in_live_exposure"
            else:
                unexpected = [strategy for strategy in strategies if strategy not in expected]
                if unexpected:
                    reason = f"unexpected_live_strategy:{','.join(unexpected)}"

            if reason:
                issues.append({"symbol": symbol, "reason": reason, "summary": data})
                if symbol in managed:
                    self._quarantine_symbol(symbol, f"startup_reconcile:{reason}")

        if issues:
            first = issues[0]
            STATE.set_error(
                f"STARTUP RECONCILIATION ISSUE | {first['symbol']} | {first['reason']}"
            )

        self._broker_reconciliation = {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "issues": issues,
        }

    def _account_guard_allows(self) -> tuple[bool, str | None]:
        if bool(self._account_guard_state.get("latched")):
            return False, str(self._account_guard_state.get("reason") or "account_guard_latched")

        account = mt5.account_info()
        if not account:
            self._account_guard_state["metrics"] = {"status": "missing_account_info"}
            return False, "missing_account_info"

        balance = float(getattr(account, "balance", 0.0) or 0.0)
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        margin_free = float(getattr(account, "margin_free", 0.0) or 0.0)
        equity_ratio = (equity / balance) if balance > 0 else 1.0
        free_margin_ratio = (margin_free / equity) if equity > 0 else 0.0
        live_exposure = self._live_bot_exposure()
        bot_positions = sum(int(item.get("positions", 0)) for item in live_exposure.values())

        self._account_guard_state["metrics"] = {
            "balance": balance,
            "equity": equity,
            "margin_free": margin_free,
            "equity_balance_ratio": equity_ratio,
            "free_margin_ratio": free_margin_ratio,
            "bot_positions": bot_positions,
        }

        try:
            min_equity_ratio = float(self.safety_cfg.get("min_equity_balance_ratio", 0.90) or 0.90)
        except Exception:
            min_equity_ratio = 0.90
        try:
            min_free_margin_ratio = float(self.safety_cfg.get("min_free_margin_ratio", 0.25) or 0.25)
        except Exception:
            min_free_margin_ratio = 0.25
        try:
            max_open_bot_positions = int(self.safety_cfg.get("max_open_bot_positions", 5) or 5)
        except Exception:
            max_open_bot_positions = 5

        trip_reason = None
        if balance > 0 and equity_ratio < min_equity_ratio:
            trip_reason = f"equity_ratio_breach:{equity_ratio:.3f}<{min_equity_ratio:.3f}"
        elif equity > 0 and free_margin_ratio < min_free_margin_ratio:
            trip_reason = f"free_margin_ratio_breach:{free_margin_ratio:.3f}<{min_free_margin_ratio:.3f}"
        elif bot_positions > max_open_bot_positions:
            trip_reason = f"open_bot_positions_breach:{bot_positions}>{max_open_bot_positions}"

        if trip_reason:
            self._account_guard_state["latched"] = True
            self._account_guard_state["reason"] = trip_reason
            STATE.set_error(f"ACCOUNT EMERGENCY BRAKE | {trip_reason}")
            self.logger.error(f"ACCOUNT EMERGENCY BRAKE | {trip_reason}")
            return False, trip_reason

        self._account_guard_state["reason"] = None
        return True, None

    def _automated_broker_actions_allowed(self, symbol: str) -> tuple[bool, str | None]:
        if self._global_pause_active():
            return False, "operator_global_pause"
        if symbol in self._effective_kill_symbols():
            return False, "manual_symbol_kill_switch"
        return self._account_guard_allows()

    def _symbol_entry_block_reason(self, symbol: str) -> str | None:
        if self._global_pause_active():
            return "operator_global_pause"
        if symbol in self._effective_kill_symbols():
            return "manual_symbol_kill_switch"

        live_exposure = self._live_bot_exposure().get(symbol, {})
        if bool(self.safety_cfg.get("block_new_entries_with_pending_bot_order", True)) and int(
            live_exposure.get("orders", 0)
        ) > 0:
            return "pending_bot_order_exists"

        if bool(self.safety_cfg.get("block_new_entries_with_open_bot_position", False)) and int(
            live_exposure.get("positions", 0)
        ) > 0:
            return "open_bot_position_exists"

        return None

    def _quarantine_symbol(self, symbol: str, reason: str) -> None:
        self.health_guard.quarantine(symbol, reason)
        self.logger.warning(f"SYMBOL QUARANTINED | {symbol} | {reason}")
        STATE.set_error(f"SYMBOL QUARANTINED | {symbol} | {reason}")
        self._persist_runtime_state()

    def _startup_health_check(self) -> None:
        startup_bars = int(self.safety_cfg.get("startup_history_bars", 120) or 120)
        healthy_symbols: set[str] = set()
        unhealthy_symbols: dict[str, str] = {}

        for item in self.engines:
            symbol = item["symbol"]
            timeframe = item.get("timeframe") or item["engine"].timeframe
            snapshot = self.broker.get_symbol_snapshot(symbol)
            if not snapshot.get("ok"):
                unhealthy_symbols[symbol] = str(snapshot.get("reason") or "symbol_snapshot_failed")
                continue
            try:
                df = self.broker.get_historical_data(symbol=symbol, timeframe=timeframe, bars=startup_bars)
            except Exception as e:
                unhealthy_symbols[symbol] = f"history_fetch_failed:{e}"
                continue
            if df.empty:
                unhealthy_symbols[symbol] = "empty_history"
                continue
            issue = item["engine"]._validate_market_data(df, df.index[-1])
            if issue:
                unhealthy_symbols[symbol] = issue
                continue
            healthy_symbols.add(symbol)

        for symbol, reason in unhealthy_symbols.items():
            if symbol not in healthy_symbols:
                self._quarantine_symbol(symbol, f"startup_health:{reason}")

        self._startup_reconcile_broker_state()

        enabled = [item for item in self.engines if self.health_guard.allowed(item["symbol"])]
        if not enabled:
            raise RuntimeError("All symbols failed startup health checks")

    def _record_intent_status(
        self,
        intent: TradeIntent,
        *,
        status: str,
        reason: str | None = None,
        approved_risk: float | None = None,
        order_ticket: int | None = None,
    ) -> None:
        payload: dict[str, Any] = intent.to_dict()
        payload["status"] = str(status)
        if reason is not None:
            payload["reason"] = str(reason)
        if approved_risk is not None:
            payload["approved_risk"] = float(approved_risk)
        if order_ticket is not None:
            payload["order_ticket"] = int(order_ticket)
        STATE.set_last_intent(payload)

    def _set_portfolio_runtime(self, *, queue_depth: int, worker_count: int, phase: str) -> None:
        STATE.set_portfolio_runtime(
            {
                "phase": str(phase),
                "queue_depth": int(max(0, queue_depth)),
                "worker_count": int(max(0, worker_count)),
                "symbols": {
                    symbol: self.health_guard.status(symbol).__dict__
                    for symbol in sorted({item["symbol"] for item in self.engines})
                },
                "manual_kill_symbols": sorted(self._effective_kill_symbols()),
                "account_guard": dict(self._account_guard_state),
                "broker_reconciliation": dict(self._broker_reconciliation),
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _run_mt5_bound(self, func, *args, **kwargs):
        with self._mt5_lock:
            return func(*args, **kwargs)

    async def _run_mt5_bound_async(self, func, *args, **kwargs):
        return await asyncio.to_thread(self._run_mt5_bound, func, *args, **kwargs)

    def _run_post_entry_managers(self, engine, strategy_name: str, timeframe: str | None) -> None:
        # ------------------------------
        # Post-entry trailing stop (scalper)
        # ------------------------------
        scalper_cfg = getattr(self, "scalper_cfg", {}) or {}
        if strategy_name == "xau_scalper" and scalper_cfg.get("trailing_stop", True):
            engine.executor.manage_trailing_stop(
                timeframe=timeframe or "M5",
                atr_period=int(scalper_cfg.get("atr_period", 14)),
                trailing_atr_multiplier=float(scalper_cfg.get("trailing_atr_multiplier", 1.0)),
                trailing_step=float(scalper_cfg.get("trailing_step", 0.5)),
                strategy="xau_scalper",
            )

        # ------------------------------
        # Post-entry breakeven stop (all strategies, configurable)
        # ------------------------------
        be_cfg = getattr(self, "breakeven_cfg", {}) or {}
        if bool(be_cfg.get("enabled", True)):
            engine.executor.manage_breakeven_stop(
                trigger_r=float(be_cfg.get("trigger_r", 0.6)),
                offset_points=int(be_cfg.get("offset_points", 10)),
                offset_spread_mult=float(be_cfg.get("offset_spread_mult", 1.0)),
                min_move_points=int(be_cfg.get("min_move_points", 30)),
                strategy=strategy_name,
            )

    async def _poll_deals_if_due_async(self) -> None:
        now_s = time.time()
        if now_s - self._last_deal_poll < self._deal_poll_interval_s:
            return

        self._last_deal_poll = now_s
        events = await self._run_mt5_bound_async(self.deal_tracker.poll)
        for e in events:
            self.deal_reporter.record(
                timestamp=e.timestamp_utc,
                symbol=e.symbol,
                side=e.side,
                volume=e.volume,
                price=e.price,
                pnl=e.pnl,
                balance=e.balance,
                magic=e.magic,
                deal_ticket=e.deal_ticket,
                order_ticket=e.order_ticket,
                comment=e.comment,
            )
            STATE.set_last_deal(
                {
                    "timestamp_utc": e.timestamp_utc,
                    "symbol": e.symbol,
                    "side": e.side,
                    "volume": e.volume,
                    "price": e.price,
                    "pnl": e.pnl,
                    "balance": e.balance,
                    "magic": e.magic,
                    "deal_ticket": e.deal_ticket,
                    "order_ticket": e.order_ticket,
                    "comment": e.comment,
                }
            )
            self.cooldown.record_trade(e.symbol, e.pnl)
            self.drawdown_guard.update(e.symbol, e.pnl, account_balance=e.balance)
            self.state.record(e.symbol, e.pnl, persist=False)
            self.health_guard.record_success(e.symbol)
        if events:
            self._persist_runtime_state()

    async def _run_post_entry_managers_if_due_async(self) -> None:
        now_s = time.time()
        if now_s - self._last_manager_poll < self._manager_poll_interval_s:
            return

        self._last_manager_poll = now_s
        for item in self.engines:
            engine = item["engine"]
            strategy_name = item["strategy"]
            timeframe = item.get("timeframe")
            symbol = item["symbol"]
            allowed, reason = self._automated_broker_actions_allowed(symbol)
            if not allowed:
                continue
            try:
                await self._run_mt5_bound_async(self._run_post_entry_managers, engine, strategy_name, timeframe)
            except Exception as e:
                self.logger.exception(f"POST-ENTRY MANAGER ERROR | {symbol} | {strategy_name} | {e}")
                STATE.set_error(f"POST-ENTRY MANAGER ERROR | {symbol} | {strategy_name} | {e}")

    async def _signal_worker(self, item: dict[str, Any], queue: asyncio.Queue) -> None:
        engine = item["engine"]
        symbol = item["symbol"]
        strategy_name = item["strategy"]

        while self._run_live:
            try:
                self._refresh_operator_controls()

                if symbol in self._effective_kill_symbols():
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                if not self.health_guard.allowed(symbol):
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                if not self.drawdown_guard.allowed(symbol):
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                if not self.cooldown.allowed(symbol):
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                intent = await self._run_mt5_bound_async(engine.generate_trade_intent, None)
                runtime_issue = engine.pop_runtime_issue()
                if runtime_issue:
                    tripped = self.health_guard.record_failure(symbol, runtime_issue)
                    self.logger.warning(f"SYMBOL HEALTH FAILURE | {symbol} | {strategy_name} | {runtime_issue}")
                    if tripped:
                        self.logger.warning(f"SYMBOL CIRCUIT OPEN | {symbol} | {runtime_issue}")
                    self._persist_runtime_state()
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                if bool(getattr(engine, "last_data_advanced", False)):
                    self.health_guard.record_success(symbol)
                    self._persist_runtime_state()
                if intent is not None:
                    await queue.put(
                        {
                            "engine": engine,
                            "symbol": symbol,
                            "strategy": strategy_name,
                            "risk": item["risk"],
                            "timeframe": item.get("timeframe"),
                            "intent": intent,
                        }
                    )
            except Exception as e:
                self.logger.exception(f"SIGNAL WORKER ERROR | {symbol} | {strategy_name} | {e}")
                STATE.set_error(f"SIGNAL WORKER ERROR | {symbol} | {strategy_name} | {e}")
                tripped = self.health_guard.record_failure(symbol, f"signal_worker_exception:{e}")
                if tripped:
                    self.logger.warning(f"SYMBOL CIRCUIT OPEN | {symbol} | signal_worker_exception:{e}")
                self._persist_runtime_state()

            await asyncio.sleep(self._signal_poll_interval_s)

    async def _process_intent_payload_async(self, payload: dict[str, Any]) -> None:
        engine = payload["engine"]
        symbol = payload["symbol"]
        strategy_name = payload["strategy"]
        strategy_risk = float(payload["risk"])
        intent: TradeIntent = payload["intent"]

        STATE.set_last_signal(intent.to_signal_dict())

        if not self.health_guard.allowed(symbol):
            self._record_intent_status(intent, status="rejected", reason="symbol_health_guard")
            return

        account_ok, account_reason = self._account_guard_allows()
        if not account_ok:
            self._record_intent_status(intent, status="rejected", reason=account_reason or "account_guard")
            return

        symbol_block_reason = self._symbol_entry_block_reason(symbol)
        if symbol_block_reason:
            self._record_intent_status(intent, status="rejected", reason=symbol_block_reason)
            return

        if not self.drawdown_guard.allowed(symbol):
            self._record_intent_status(intent, status="rejected", reason="symbol_drawdown_guard")
            return

        if not self.cooldown.allowed(symbol):
            self._record_intent_status(intent, status="rejected", reason="symbol_cooldown")
            return

        open_risk = float(await self._run_mt5_bound_async(self.exposure.total_open_risk))
        alloc = self.allocator.allocate(strategy_risk, open_risk)
        if alloc <= 0:
            self._record_intent_status(intent, status="rejected", reason="no_portfolio_risk_available", approved_risk=0.0)
            return

        intent.risk_request = float(alloc)
        await self._run_mt5_bound_async(engine.executor.override_risk, alloc, strategy_name)

        approved, reason = engine.approve_trade_intent(intent)
        if not approved:
            self.logger.warning(
                f"INTENT REJECTED | {symbol} | {strategy_name} | {reason}"
            )
            engine.notifier.send(f"TRADE BLOCKED | {reason}")
            self._record_intent_status(intent, status="rejected", reason=reason, approved_risk=alloc)
            return

        self.logger.info(
            f"INTENT APPROVED | {symbol} | {strategy_name} | risk={alloc:.4f}"
        )
        self._record_intent_status(intent, status="approved", approved_risk=alloc)

        result = await self._run_mt5_bound_async(engine.execute_trade_intent, intent)
        if result:
            self.health_guard.record_success(symbol)
            self._record_intent_status(
                intent,
                status="executed",
                approved_risk=alloc,
                order_ticket=getattr(result, "order", None),
            )
        else:
            tripped = self.health_guard.record_failure(symbol, "execution_failed")
            if tripped:
                self.logger.warning(f"SYMBOL CIRCUIT OPEN | {symbol} | execution_failed")
            self._record_intent_status(
                intent,
                status="failed",
                reason="execution_failed",
                approved_risk=alloc,
            )
        self._persist_runtime_state()

    # --------------------------------------------------
    # MAIN PORTFOLIO LOOP
    # --------------------------------------------------

    def run(self, allow_fn):
        self.logger.info("PORTFOLIO LIVE MODE ACTIVE")
        asyncio.run(self.run_async(allow_fn))

    async def run_async(self, allow_fn):
        queue: asyncio.Queue = asyncio.Queue(maxsize=max(8, len(self.engines) * 4))
        self._run_live = True
        await self._run_mt5_bound_async(self._startup_health_check)
        self._persist_runtime_state()
        workers = [
            asyncio.create_task(self._signal_worker(item, queue), name=f"signal-{item['symbol']}-{item['strategy']}")
            for item in self.engines
            if self.health_guard.allowed(item["symbol"]) and item["symbol"] not in self._effective_kill_symbols()
        ]

        try:
            while self._run_live:
                try:
                    if not allow_fn():
                        self._run_live = False
                        break

                    self._refresh_operator_controls()

                    self._account_guard_allows()

                    self._set_portfolio_runtime(
                        queue_depth=queue.qsize(),
                        worker_count=len(workers),
                        phase="live_async",
                    )

                    await self._poll_deals_if_due_async()
                    await self._run_post_entry_managers_if_due_async()

                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        await self._process_intent_payload_async(payload)
                    except Exception as e:
                        symbol = payload.get("symbol", "unknown")
                        strategy_name = payload.get("strategy", "unknown")
                        self.logger.exception(f"INTENT PROCESSING ERROR | {symbol} | {strategy_name} | {e}")
                        STATE.set_error(f"INTENT PROCESSING ERROR | {symbol} | {strategy_name} | {e}")
                    finally:
                        queue.task_done()
                except Exception as e:
                    self.logger.exception(f"PORTFOLIO ERROR: {e}")
                    STATE.set_error(f"PORTFOLIO ERROR: {e}")
                    await asyncio.sleep(1.0)
        finally:
            self._run_live = False
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self._persist_runtime_state()
            self._set_portfolio_runtime(queue_depth=0, worker_count=0, phase="stopped")
