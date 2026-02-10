import time

from portfolio.allocator import CapitalAllocator
from portfolio.exposure import ExposureTracker
from portfolio.config import PORTFOLIO
from portfolio.guard import SymbolDrawdownGuard
from portfolio.cooldown import SymbolCooldown
from portfolio.state import PortfolioState

from core.broker import MT5Broker
from core.execution import MT5Executor
from core.engine import TradingEngine

from config.loader import load_config
from strategy.factory import build_strategy

from utils.logger import setup_logger


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

        # Shared broker connection
        self.broker = MT5Broker()
        self.broker.connect()

        self.engines = []

        config = load_config()

        # --------------------------------------------------
        # BUILD ONE ENGINE PER STRATEGY PER SYMBOL
        # --------------------------------------------------
        for symbol, symbol_cfg in self.cfg["symbols"].items():

            # Handle both single strategy and multiple strategies config
            if "strategies" in symbol_cfg:
                strategies_cfg = symbol_cfg["strategies"]
                for strategy_name, scfg in strategies_cfg.items():
                    strategy = build_strategy(strategy_name, config)
                    executor = MT5Executor(symbol=symbol)

                    engine = TradingEngine(
                        broker=self.broker,
                        strategy=strategy,
                        executor=executor,
                        symbol=symbol,
                        timeframe=scfg["timeframe"],
                        candle_seconds=scfg["candle_seconds"]
                    )

                    self.engines.append({
                        "engine": engine,
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "risk": scfg["risk"]
                    })
                    self.logger.info(f"Created engine: {symbol} - {strategy_name} (risk: {scfg['risk']})")
            else:
                # Single strategy per symbol
                strategy_name = symbol_cfg["strategy"]
                strategy = build_strategy(strategy_name, config)
                executor = MT5Executor(symbol=symbol)

                engine = TradingEngine(
                    broker=self.broker,
                    strategy=strategy,
                    executor=executor,
                    symbol=symbol,
                    timeframe=symbol_cfg["timeframe"],
                    candle_seconds=symbol_cfg["candle_seconds"]
                )

                self.engines.append({
                    "engine": engine,
                    "symbol": symbol,
                    "strategy": strategy_name,
                    "risk": symbol_cfg["risk"]
                })
                self.logger.info(f"Created engine: {symbol} - {strategy_name} (risk: {symbol_cfg['risk']})")

        self.logger.info(f"Portfolio initialized with {len(self.engines)} engines")

    # --------------------------------------------------
    # MAIN PORTFOLIO LOOP
    # --------------------------------------------------

    def run(self, allow_fn):

        self.logger.info("PORTFOLIO LIVE MODE ACTIVE")

        while allow_fn():

            try:
                open_risk = self.exposure.total_open_risk()

                for item in self.engines:

                    engine = item["engine"]
                    symbol = item["symbol"]
                    strategy_name = item["strategy"]
                    strategy_risk = item["risk"]

                    # ------------------------------
                    # Symbol drawdown protection
                    # ------------------------------
                    if not self.drawdown_guard.allowed(symbol):
                        continue

                    if not self.cooldown.allowed(symbol):
                        continue

                    # ------------------------------
                    # Capital allocation
                    # ------------------------------
                    alloc = self.allocator.allocate(
                        strategy_risk,
                        open_risk
                    )

                    if alloc <= 0:
                        continue

                    # Tell executor the risk size
                    engine.executor.override_risk(alloc)

                    # Run ONE step of the engine
                    engine.step_once()

                time.sleep(1)

            except Exception as e:
                self.logger.exception(f"PORTFOLIO ERROR: {e}")
