import asyncio
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from portfolio.allocator import CapitalAllocator
from portfolio.exposure import ExposureTracker
from portfolio.config import PORTFOLIO
from portfolio.guard import SymbolDrawdownGuard
from portfolio.cooldown import SymbolCooldown
from portfolio.state import PortfolioState

from core.broker import MT5Broker
from core.execution import MT5Executor
from core.engine import TradingEngine
from core.market_state import MarketStateStore
from models.trade_intent import TradeIntent

from config.loader import load_config
from strategy.factory import build_strategy

from utils.logger import setup_logger
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
        self.deal_tracker = ClosedDealTracker(magic=2601, poll_lookback_minutes=240)
        self.deal_reporter = ClosedDealReporter()
        self._last_deal_poll = 0.0
        self._deal_poll_interval_s = 10.0
        self._last_manager_poll = 0.0
        self._manager_poll_interval_s = 5.0
        self._signal_poll_interval_s = 1.0
        self._mt5_lock = Lock()
        self._run_live = False

        # Shared broker connection
        self.broker = MT5Broker()
        self.broker.connect()

        self.engines = []

        config = load_config()
        self.scalper_cfg = config.get("scalper", {})
        risk_cfg = config.get("risk", {})
        self.breakeven_cfg = config.get("breakeven", {}) or {}
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
            self.state.record(e.symbol, e.pnl)

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
                if not self.drawdown_guard.allowed(symbol):
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                if not self.cooldown.allowed(symbol):
                    await asyncio.sleep(self._signal_poll_interval_s)
                    continue

                intent = await self._run_mt5_bound_async(engine.generate_trade_intent, None)
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

            await asyncio.sleep(self._signal_poll_interval_s)

    async def _process_intent_payload_async(self, payload: dict[str, Any]) -> None:
        engine = payload["engine"]
        symbol = payload["symbol"]
        strategy_name = payload["strategy"]
        strategy_risk = float(payload["risk"])
        intent: TradeIntent = payload["intent"]

        STATE.set_last_signal(intent.to_signal_dict())

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
            self._record_intent_status(
                intent,
                status="executed",
                approved_risk=alloc,
                order_ticket=getattr(result, "order", None),
            )
        else:
            self._record_intent_status(
                intent,
                status="failed",
                reason="execution_failed",
                approved_risk=alloc,
            )

    # --------------------------------------------------
    # MAIN PORTFOLIO LOOP
    # --------------------------------------------------

    def run(self, allow_fn):
        self.logger.info("PORTFOLIO LIVE MODE ACTIVE")
        asyncio.run(self.run_async(allow_fn))

    async def run_async(self, allow_fn):
        queue: asyncio.Queue = asyncio.Queue(maxsize=max(8, len(self.engines) * 4))
        self._run_live = True
        workers = [
            asyncio.create_task(self._signal_worker(item, queue), name=f"signal-{item['symbol']}-{item['strategy']}")
            for item in self.engines
        ]

        try:
            while self._run_live:
                try:
                    if not allow_fn():
                        self._run_live = False
                        break

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
            self._set_portfolio_runtime(queue_depth=0, worker_count=0, phase="stopped")
