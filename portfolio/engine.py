import time

from portfolio.allocator import CapitalAllocator
from portfolio.exposure import ExposureTracker
from portfolio.config import PORTFOLIO

from core.broker import MT5Broker
from core.execution import MT5Executor
from core.engine import TradingEngine
from config.loader import load_config
from strategy.xau_trend import XAUTrendStrategy

from portfolio.guard import SymbolDrawdownGuard
from portfolio.cooldown import SymbolCooldown
from portfolio.state import PortfolioState

from strategy.registry import STRATEGY_REGISTRY



class PortfolioEngine:

    def __init__(self):

        # -------------------------------
        # CONFIG
        # -------------------------------
        self.cfg = PORTFOLIO

        self.allocator = CapitalAllocator(self.cfg["max_total_risk"])
        self.exposure = ExposureTracker()

        protection = self.cfg.get("protection", {})
        self.drawdown_guard = SymbolDrawdownGuard(
            max_drawdown_pct=protection.get("symbol_max_dd", 0.02)
        )
        self.cooldown = SymbolCooldown(
            max_losses=protection.get("max_losses", 3),
            cooldown_minutes=protection.get("cooldown_minutes", 1440),
        )

        self.state = PortfolioState()

        # -------------------------------
        # SHARED BROKER CONNECTION
        # -------------------------------
        self.broker = MT5Broker()
        self.broker.connect()

        self.engines = []

        config = load_config()

        # -------------------------------
        # BUILD SYMBOL ENGINES
        # -------------------------------
        for symbol, scfg in self.cfg["symbols"].items():

            strategy_name = scfg["strategy"]
            strategy_cls = STRATEGY_REGISTRY.get(strategy_name)

            if not strategy_cls:
                raise ValueError(f"Unknown strategy: {strategy_name}")

            strategy = strategy_cls(config)

            executor = MT5Executor(symbol=symbol)

            engine = TradingEngine(
                broker=self.broker,
                strategy=strategy,
                executor=executor,
                symbol=symbol,
                timeframe=scfg["timeframe"],
                candle_seconds=scfg.get("candle_seconds", 300),
            )

            self.engines.append((engine, scfg))

    # -------------------------------------------------
    # MAIN PORTFOLIO LOOP
    # -------------------------------------------------

    def run(self, allow_fn):

        while allow_fn():

            open_risk = self.exposure.total_open_risk()

            for engine, scfg in self.engines:

                symbol = engine.symbol

                # -------------------------------
                # SYMBOL SAFETY CHECKS
                # -------------------------------
                if not self.cooldown.allowed(symbol):
                    continue

                if not self.drawdown_guard.allowed(symbol):
                    continue

                # -------------------------------
                # RISK ALLOCATION
                # -------------------------------
                alloc = self.allocator.allocate(
                    scfg["risk"],
                    open_risk
                )

                if alloc <= 0:
                    continue

                engine.executor.override_risk(alloc)

                # -------------------------------
                # EXECUTE ONE CANDLE
                # -------------------------------
                engine.step_once()

                # -------------------------------
                # POST-TRADE FEEDBACK
                # -------------------------------
                pnl = engine.executor.last_trade_pnl

                if pnl is not None:
                    self.state.record(symbol, pnl)
                    self.drawdown_guard.update(symbol, pnl)
                    self.cooldown.record_trade(symbol, pnl)

            # prevent tight loop
            time.sleep(1)
