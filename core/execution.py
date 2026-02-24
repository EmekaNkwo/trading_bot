import MetaTrader5 as mt5
from utils.logger import setup_logger, log_separator
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials

from utils.trade_reporter import LiveTradeReporter
from utils.indicators import atr
import pandas as pd


def _strategy_code(strategy: str) -> str:
    return {
        "xau_trend": "xt",
        "xau_scalper": "xs",
    }.get(strategy, "uk")


def _build_order_comment(strategy: str, risk_pct: float | None) -> str:
    # MT5 order comments have small length limits; keep this compact.
    # Format: pb|<strat>|r=<risk>
    if risk_pct is None:
        return f"pb|{_strategy_code(strategy)}"

    risk_str = f"{float(risk_pct):.4f}"
    return f"pb|{_strategy_code(strategy)}|r={risk_str}"

def _tf(tf: str):
    return {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }.get(tf, mt5.TIMEFRAME_M5)


def _parse_strategy_from_comment(comment: str | None) -> str:
    if not comment:
        return "unknown"
    c = str(comment)
    if not c.startswith("pb|"):
        return "unknown"
    parts = c.split("|")
    if len(parts) < 2:
        return "unknown"
    code = parts[1]
    return {"xt": "xau_trend", "xs": "xau_scalper"}.get(code, "unknown")


class MT5Executor:

    def __init__(self, symbol):
        self.symbol = symbol
        self.logger = setup_logger()
        self.reporter = LiveTradeReporter()
        tg = get_telegram_credentials()
        self.notifier = TelegramNotifier(tg.token, tg.chat_id)

        # portfolio-aware state
        self.current_lot = 0.01        # default fallback
        self.last_trade_pnl = None     # updated on execution
        self.last_risk_pct = None       # cache last risk percentage
        self.last_balance = None         # cache last balance for change detection
        self.last_recalc_time = None     # track last recalculation time
        self.recalc_interval = 60       # recalculate at most every 60 seconds

    # -------------------------------------------------
    # PORTFOLIO RISK OVERRIDE
    # -------------------------------------------------

    def override_risk(self, risk_pct, strategy="unknown"):
        """
        risk_pct: fraction of account balance (e.g. 0.01 = 1%)
        strategy: strategy name for strategy-aware lot sizing
        Converts risk percentage into a tradable lot size.
        """
        # Get current account info
        account = mt5.account_info()
        if not account:
            return
        
        current_balance = account.balance
        from datetime import datetime
        current_time = datetime.utcnow()
        
        # Only recalculate if:
        # 1. Risk percentage changed
        # 2. Balance changed significantly (>1%)
        # 3. Time interval passed (60 seconds)
        balance_change_threshold = 0.01  # 1% balance change threshold
        
        should_recalculate = False
        reason = "Unknown"
        
        if self.last_risk_pct != risk_pct:
            should_recalculate = True
            reason = "Risk percentage changed"
        elif (self.last_balance is not None and 
              abs(current_balance - self.last_balance) / self.last_balance > balance_change_threshold):
            should_recalculate = True
            reason = "Balance changed significantly"
        elif (self.last_recalc_time is None or 
              (current_time - self.last_recalc_time).seconds > self.recalc_interval):
            should_recalculate = True
            reason = "Time interval passed"
        
        if not should_recalculate:
            # No significant change - use cached lot size
            return
        
        # Significant change detected - recalculate
        prev_str = f"${self.last_balance:.2f}" if self.last_balance is not None else "N/A"
        log_separator(self.logger, "LOT RECALCULATION", char="-", width=50)
        self.logger.info(
            f"  Reason: {reason}\n"
            f"  Risk: {risk_pct*100:.1f}%\n"
            f"  Balance: ${current_balance:.2f}\n"
            f"  Previous: {prev_str}"
        )
        
        self.current_lot = self._risk_to_lot(risk_pct, strategy=strategy)
        self.last_risk_pct = risk_pct
        self.last_balance = current_balance
        self.last_recalc_time = current_time

    # -------------------------------------------------
    # TRAILING STOP MANAGEMENT (post-entry)
    # -------------------------------------------------

    def manage_trailing_stop(
        self,
        *,
        timeframe: str,
        atr_period: int,
        trailing_atr_multiplier: float,
        trailing_step: float,
        strategy: str | None = None,
        magic: int = 2601,
        bars: int = 200,
    ):
        """
        True trailing stop: modifies SL on open positions.
        Only tightens SL (never loosens).

        trailing_step: fraction of ATR required before sending another SL update
        """
        try:
            positions = mt5.positions_get(symbol=self.symbol)
        except Exception:
            return

        if not positions:
            return

        # Get ATR from recent candles
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, _tf(timeframe), 0, bars)
            if rates is None:
                return
            df = pd.DataFrame(rates)
            if df.empty:
                return
            atr_val = atr(df.rename(columns={"tick_volume": "tick_volume"}), atr_period).iloc[-1]
            if atr_val is None or pd.isna(atr_val) or float(atr_val) <= 0:
                return
            atr_val = float(atr_val)
        except Exception:
            return

        # Current market price
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return

        trail_dist = atr_val * float(trailing_atr_multiplier)
        step_dist = atr_val * float(trailing_step)
        if step_dist <= 0:
            step_dist = atr_val * 0.25

        for p in positions:
            if getattr(p, "magic", None) != magic:
                continue

            p_strategy = _parse_strategy_from_comment(getattr(p, "comment", None))
            if strategy and p_strategy != strategy:
                continue

            # Only apply to scalper by default
            if strategy is None and p_strategy != "xau_scalper":
                continue

            sl = float(getattr(p, "sl", 0.0) or 0.0)
            tp = float(getattr(p, "tp", 0.0) or 0.0)
            ticket = getattr(p, "ticket", None)
            if ticket is None:
                continue

            # Determine direction: MT5 uses 0 buy / 1 sell for position.type typically
            ptype = getattr(p, "type", None)
            is_buy = (ptype == mt5.POSITION_TYPE_BUY) if hasattr(mt5, "POSITION_TYPE_BUY") else (ptype == 0)

            if is_buy:
                price = float(tick.bid)
                new_sl = price - trail_dist
                if sl > 0 and new_sl <= sl + step_dist:
                    continue
                if sl > 0:
                    new_sl = max(new_sl, sl)
            else:
                price = float(tick.ask)
                new_sl = price + trail_dist
                if sl > 0 and new_sl >= sl - step_dist:
                    continue
                if sl > 0:
                    new_sl = min(new_sl, sl)

            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": int(ticket),
                "symbol": self.symbol,
                "sl": float(new_sl),
                "tp": float(tp) if tp > 0 else 0.0,
                "magic": magic,
                "comment": "pb|trail",
            }

            try:
                res = mt5.order_send(req)
                if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                    self.logger.info(f"TRAILING SL UPDATED | {self.symbol} | SL={new_sl:.3f}")
            except Exception:
                continue

    def _risk_to_lot(self, risk_pct, sl_ticks=None, strategy="unknown"):
        """
        Calculate lot size based on actual SL distance.
        Strategy-aware: uses different defaults and limits for trend vs scalper.
        If sl_ticks is provided, uses actual stop distance.
        Otherwise falls back to conservative default based on strategy type.
        """

        account = mt5.account_info()
        if not account:
            return self.current_lot

        balance = account.balance
        risk_amount = balance * risk_pct

        info = mt5.symbol_info(self.symbol)
        if not info:
            return self.current_lot

        # broker constraints
        min_lot = info.volume_min
        step = info.volume_step

        # Use appropriate tick value for XAUUSDm
        if self.symbol == "XAUUSDm":
            tick_value = 1.0  # $1.00 per pip (100 ticks)
            tick_size = 0.01   # 1 tick = $0.01
        else:
            tick_value = info.trade_tick_value or 0.01
            tick_size = info.trade_tick_size or 0.00001

        # Strategy-specific defaults and limits
        if strategy == "xau_trend":
            default_sl_ticks = 300  # Trend strategies use wider stops
            max_lot_strategy = 0.3    # Lower max lot for trend (wider stops)
        elif strategy == "xau_scalper":
            default_sl_ticks = 50   # Scalpers use tight stops
            max_lot_strategy = 0.5  # Higher max lot for scalping (tight stops)
        else:
            default_sl_ticks = 100  # Unknown strategy - conservative default
            max_lot_strategy = 0.3  # Conservative max lot

        # Use actual SL distance if provided, otherwise strategy-specific default
        if sl_ticks is not None and sl_ticks > 0:
            actual_sl_ticks = sl_ticks
        else:
            actual_sl_ticks = default_sl_ticks

        # Calculate lot size: risk_amount / (sl_ticks * tick_value)
        lot = risk_amount / (actual_sl_ticks * tick_value)
        lot = max(min_lot, lot)

        # normalize to broker step
        lot = round(lot / step) * step

        # Safety caps - never exceed these regardless of calculation
        max_lot_for_small_account = 0.1  # Maximum 0.1 lots for accounts under $2000
        if balance < 2000:
            lot = min(lot, max_lot_for_small_account)
        
        # Strategy-specific absolute max lot cap
        lot = min(lot, max_lot_strategy)

        # Log calculation details for verification
        log_separator(self.logger, f"LOT CALC - {strategy.upper()}", char="-", width=50)
        self.logger.info(
            f"  Balance: ${balance:.2f}\n"
            f"  Risk: {risk_pct*100:.1f}% (${risk_amount:.2f})\n"
            f"  SL Ticks: {actual_sl_ticks}\n"
            f"  Tick Value: ${tick_value:.4f}\n"
            f"  Calculated Lot: {lot:.3f}\n"
            f"  Max Lot: {max_lot_strategy}"
        )
        log_separator(self.logger, char="-", width=50)

        return lot

    def _validate_lot_size(self, lot):
        """Validate lot size against account constraints"""
        account = mt5.account_info()
        if not account:
            return {"valid": False, "reason": "No account info"}
        
        symbol_info = mt5.symbol_info(self.symbol)
        if not symbol_info:
            return {"valid": False, "reason": "No symbol info"}
        
        # Check broker constraints
        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        step_lot = symbol_info.volume_step
        
        if lot < min_lot:
            return {"valid": False, "reason": f"Lot {lot} below minimum {min_lot}"}
        
        if lot > max_lot:
            return {"valid": False, "reason": f"Lot {lot} above maximum {max_lot}"}
        
        # Check margin requirements
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return {"valid": False, "reason": "No tick data for margin calc"}
            
        margin_required = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY,
            self.symbol,
            lot,
            tick.ask
        )
        
        if margin_required is None:
            return {"valid": False, "reason": "Cannot calculate margin"}
        
        if margin_required > account.margin_free:
            return {"valid": False, "reason": f"Insufficient margin: need ${margin_required:.2f}, have ${account.margin_free:.2f}"}
        
        # Check position size limits (max 2% of account per trade)
        max_risk_per_trade = account.balance * 0.02
        position_value = lot * 100000  # XAUUSDm: 1 lot = 100,000 units
        
        if position_value > max_risk_per_trade * 10:  # 10x leverage consideration
            return {"valid": False, "reason": f"Position too large: ${position_value:.2f} > ${max_risk_per_trade * 10:.2f}"}
        
        return {"valid": True, "reason": "OK"}

    def _check_account_protection(self):
        """Check overall account protection levels"""
        account = mt5.account_info()
        if not account:
            return
        
        # Daily loss tracking
        if hasattr(self, '_daily_start_balance'):
            daily_pnl = account.balance - self._daily_start_balance
            max_daily_loss = account.balance * 0.05  # 5% daily loss limit
            
            if daily_pnl < -max_daily_loss:
                self.logger.error(f"DAILY LOSS LIMIT HIT | Loss: ${daily_pnl:.2f} | Limit: ${max_daily_loss:.2f}")
                return False
        else:
            self._daily_start_balance = account.balance
        
        # Equity protection
        equity_ratio = account.equity / account.balance if account.balance > 0 else 1
        if equity_ratio < 0.9:  # 10% equity drawdown
            self.logger.error(f"EQUITY PROTECTION | Equity ratio: {equity_ratio:.2f}")
            return False
        
        return True

    # -------------------------------------------------
    # EXECUTION
    # -------------------------------------------------

    def place_market_order(self, signal, lot=None, risk_pct=None):
        """
        Execute market order with proper lot sizing based on actual SL distance.
        If lot not provided, calculates it dynamically using the signal's SL.
        """
        
        strategy_name = signal.get("strategy", "unknown")
        self.logger.info(f"[EXECUTE START] {strategy_name} {signal['side']}")
        
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            self.logger.error("NO TICK DATA")
            return None

        if signal["side"] == "buy":
            price = tick.ask
        else:
            price = tick.bid
        
        # Calculate lot size if not explicitly provided
        if lot is None:
            # Get symbol info for tick size
            symbol_info = mt5.symbol_info(self.symbol)
            if not symbol_info:
                self.logger.error("NO SYMBOL INFO")
                return None
            
            tick_size = symbol_info.trade_tick_size or 0.01
            
            # Calculate SL distance in ticks
            sl_price = signal.get("sl")
            if sl_price:
                sl_distance = abs(price - sl_price)
                sl_ticks = int(sl_distance / tick_size)
            else:
                sl_ticks = None
            
            # Get strategy name for strategy-aware lot sizing
            strategy_name = signal.get("strategy", "unknown")
            
            # Calculate lot based on actual SL distance and strategy type
            calc_risk = risk_pct if risk_pct is not None else (self.last_risk_pct or 0.005)
            lot = self._risk_to_lot(calc_risk, sl_ticks, strategy_name)
            
            self.logger.info(
                f"DYNAMIC LOT CALC | Strategy: {strategy_name} | Price: {price} | SL: {sl_price} | "
                f"Distance: {sl_distance if sl_price else 'N/A'} | Ticks: {sl_ticks} | Lot: {lot}"
            )

        # Validate lot size before execution
        self.logger.info(f"[EXECUTE] Validating lot: {lot}")
        validation_result = self._validate_lot_size(lot)
        if not validation_result["valid"]:
            self.logger.error(f"[EXECUTE BLOCKED] Lot validation failed: {validation_result['reason']}")
            return None
        self.logger.info("[EXECUTE] Lot validation passed")

        # Check account protection levels
        self.logger.info("[EXECUTE] Checking account protection...")
        if not self._check_account_protection():
            self.logger.error("[EXECUTE BLOCKED] Account protection prevented trade")
            return None
        self.logger.info("[EXECUTE] Account protection passed")

        if signal["side"] == "buy":
            order_type = mt5.ORDER_TYPE_BUY
        else:
            order_type = mt5.ORDER_TYPE_SELL

        # Use compact comment to allow exposure/risk tracking from open positions.
        comment = _build_order_comment(strategy_name, risk_pct if risk_pct is not None else self.last_risk_pct)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": signal["sl"],
            "tp": signal["tp"],
            "deviation": 20,
            "magic": 2601,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        self.logger.info(f"[EXECUTE] Order send result: {result}")

        # ❌ FAILED ORDER
        if not result or result.retcode != mt5.TRADE_RETCODE_DONE:

            self.last_trade_pnl = None

            retcode = result.retcode if result else "NO_RESULT"
            comment = result.comment if result else "NO_RESPONSE"

            strategy_name = signal.get("strategy", "unknown")
            log_separator(self.logger, f"ORDER FAILED - {strategy_name.upper()}", char="-", width=50)
            self.logger.error(
                f"  Symbol: {self.symbol}\n"
                f"  Side: {signal['side'].upper()}\n"
                f"  Retcode: {retcode}\n"
                f"  Reason: {comment}"
            )
            log_separator(self.logger, char="-", width=50)

            self.notifier.send(
                f"ORDER FAILED | {strategy_name.upper()}\n"
                f"{self.symbol} {signal['side'].upper()}\n"
                f"Reason: {comment}"
            )

            self.reporter.record(
                symbol=self.symbol,
                side=signal["side"],
                lot=lot,
                price=price,
                sl=signal["sl"],
                tp=signal["tp"],
                ticket=None,
                retcode=retcode,
                comment=comment
            )

            return None

        # ✅ SUCCESSFUL ORDER
        strategy_name = signal.get("strategy", "unknown")
        log_separator(self.logger, f"TRADE EXECUTED - {strategy_name.upper()}", char="=", width=60)
        self.logger.info(
            f"  Symbol: {self.symbol}\n"
            f"  Side: {signal['side'].upper()}\n"
            f"  Lot: {lot}\n"
            f"  Price: {price}\n"
            f"  SL: {signal['sl']}\n"
            f"  TP: {signal['tp']}\n"
            f"  Ticket: {result.order}"
        )
        log_separator(self.logger, char="=", width=60)

        self.notifier.send(
            f"TRADE EXECUTED | {strategy_name.upper()}\n"
            f"{self.symbol} {signal['side'].upper()}\n"
            f"Lot: {lot}\n"
            f"Price: {price}\n"
            f"SL: {signal['sl']}\n"
            f"TP: {signal['tp']}\n"
            f"Ticket: {result.order}"
        )

        self.reporter.record(
            symbol=self.symbol,
            side=signal["side"],
            lot=lot,
            price=price,
            sl=signal["sl"],
            tp=signal["tp"],
            ticket=result.order,
            retcode=result.retcode,
            comment=result.comment
        )

        return result
